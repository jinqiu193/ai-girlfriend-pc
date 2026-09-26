"""Chat endpoints: history fetch + SSE streaming reply.

The streaming endpoint walks this pipeline:
1. persist user message
2. memU progressive_retrieve -> top segments/files
3. assemble messages via prompts.build_messages
4. AsyncOpenAI stream, emit SSE events (retrieval / token / image_ref / done)
5. persist assistant reply
6. commit a memory entry into memU summarising the turn
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("ai-girlfriend.chat")

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse

import character_card as cc
import db

from auth import current_user
from chat_loaders import iter_messages
from config import get_settings
from llm import call_once, call_with_state_tool, sse_pack, stream_chat
from memu.app import MemoryService
from memu_setup import embedding_enabled
from prompts import IMAGE_PLACEHOLDER_RE, _take_recent_turns, annotate_history_scenes, build_messages, build_state_update_messages, build_story_choice_messages, build_story_scene_messages, build_story_state_messages, build_summary_messages, clarify_memory_perspective, detect_emotion_key, render_story_state_prompt
from mood import DEFAULT_MOOD, apply_idle_decay, render_mood_prompt
from events import extract_events, render_events_prompt
from intimacy import auto_relationship, compute_intimacy, render_intimacy_prompt
from relationship import render_relationship_prompt
from moments import generate_moment, generate_cross_interaction
from news import fetch_daily_digest
from evolution import analyze_evolution, render_evolution_prompt
from scene import current_scene, parse_msg_time, scene_prompt
from spatial import render_spatial_prompt, render_story_scene_prompt
from prompt_rules import build_match_context, match_and_render

# The main LLM sometimes emits a bare "(图片)" token as image intent even
# though images are delivered out-of-band via image_ref events. Strip those
# literals so the user never sees a dangling placeholder.
#
# Each turn calls the LLM exactly once (never retried). If the reply doesn't
# finish within this window, or the call fails outright with nothing emitted,
# the user gets a fixed fallback so the conversation never stalls.
LLM_REPLY_TIMEOUT = get_settings().chat_reply_timeout
# Fixed reply emitted when the LLM call produces nothing (timeout / error).
# The whole turn (user + this AI reply) is dropped from history and
# memU — see ``if was_fallback`` below and
# ``db.recent_messages_excluding_fallback`` for the defensive cleanup
# of pre-existing rows.
LLM_FALLBACK_REPLY = "哈哈哈"

# Post-reply state update (update_state tool call) budget.
STATE_UPDATE_TIMEOUT = get_settings().chat_state_timeout

router = APIRouter(prefix="/api/chat")


# ---------- background task governance ----------
# A single chat turn fires 6-8 fire-and-forget LLM tasks (memory commit,
# event extraction, moment generation, cross-interaction, news, relation,
# evolution). Without bounds these stampede the upstream and trigger
# gateway timeouts. We (a) keep strong references so tasks aren't
# garbage-collected mid-flight, and (b) cap concurrency globally.
_bg_tasks: set[asyncio.Task] = set()
_bg_semaphore: asyncio.Semaphore | None = None


def _get_bg_semaphore() -> asyncio.Semaphore:
    global _bg_semaphore
    if _bg_semaphore is None:
        _bg_semaphore = asyncio.Semaphore(get_settings().max_concurrent_background)
    return _bg_semaphore


async def _run_bounded(coro) -> None:
    async with _get_bg_semaphore():
        try:
            await coro
        except Exception as exc:  # noqa: BLE001
            log.warning("background task failed: %s", exc)


def _spawn_bg(coro) -> None:
    """Schedule a bounded background task with a strong reference."""
    task = asyncio.create_task(_run_bounded(coro))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


class _StreamImageFilter:
    """Incrementally split a streamed reply into text and ``[IMAGE: caption]``.

    Text is emitted as soon as it cannot be part of a placeholder; a trailing
    ``[`` that might start an ``[IMAGE:`` tag is buffered until the closing
    bracket (or end of stream) disambiguates it.
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, delta: str) -> list[tuple[str, str]]:
        """Consume a delta; return ``[("text", s) | ("image", caption)]`` events."""
        self._buf += delta
        return self._drain(final=False)

    def flush(self) -> list[tuple[str, str]]:
        """Emit whatever is left in the buffer (end of stream)."""
        events = self._drain(final=True)
        if self._buf:
            events.append(("text", self._buf))
            self._buf = ""
        return events

    def _drain(self, *, final: bool) -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        while True:
            m = IMAGE_PLACEHOLDER_RE.search(self._buf)
            if m:
                if m.start() > 0:
                    events.append(("text", self._buf[: m.start()]))
                events.append(("image", m.group(1).strip()))
                self._buf = self._buf[m.end():]
                continue
            if final:
                return events
            cut = self._safe_cut()
            if cut > 0:
                events.append(("text", self._buf[:cut]))
                self._buf = self._buf[cut:]
            return events

    def _safe_cut(self) -> int:
        """Length of the buffer that can be emitted as plain text now.

        A trailing ``[`` that could be the beginning of an ``[IMAGE: ...]``
        placeholder is held back until we know either way."""
        pos = self._buf.rfind("[")
        if pos == -1:
            return len(self._buf)
        tail = self._buf[pos:]
        if "[IMAGE:".startswith(tail) or tail.startswith("[IMAGE:"):
            return pos
        return len(self._buf)


def _get_memu(request: Request) -> MemoryService:
    return request.app.state.memu


def _is_sleeping() -> bool:
    """当前是否处于 AI 睡眠时段。"""
    s = get_settings()
    if not s.sleep_enabled:
        return False
    now = datetime.now()
    try:
        sh, sm = map(int, s.sleep_time.split(":"))
        wh, wm = map(int, s.wake_time.split(":"))
    except (ValueError, AttributeError):
        return False
    sleep_min = sh * 60 + sm
    wake_min = wh * 60 + wm
    now_min = now.hour * 60 + now.minute
    if sleep_min <= wake_min:
        return sleep_min <= now_min < wake_min
    # 跨午夜：now >= sleep_time 或 now < wake_time
    return now_min >= sleep_min or now_min < wake_min


@router.get("/history")
def get_history(
    character_id: str = Query(...),
    limit: int | None = Query(default=None),
    offset: int = Query(default=0),
    user_id: str = Depends(current_user),
):
    if not db.get_character(character_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    settings = get_settings()
    fetch_limit = limit or settings.history_window
    rows = db.recent_messages(
        user_id=user_id, character_id=character_id, limit=fetch_limit, offset=offset
    )
    total = db.count_messages(user_id=user_id, character_id=character_id)
    return {
        "total": total,
        "messages": [
            {
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "image_paths": (r["image_paths"] or "").split(",") if r["image_paths"] else [],
                "excluded_from_context": bool(r.get("excluded_from_context")),
                "created_at": r["created_at"],
                "debug_ctx": r.get("debug_ctx"),
            }
            for r in rows
        ]
    }


@router.post("/clear")
async def clear_chat(
    character_id: str = Query(...),
    user_id: str = Depends(current_user),
):
    """Mark all messages of this character as ``excluded_from_context``.

    The rows stay in the DB (visible on refresh with .skipped styling)
    but won't enter the LLM context, so new character settings take
    full effect on the next message.
    """
    count = db.exclude_all_messages(user_id=user_id, character_id=character_id)
    db.delete_conversation_summary(user_id=user_id, character_id=character_id)
    return {"cleared": count}


@router.post("/import-messages")
async def import_messages(
    character_id: str = Query(...),
    file: UploadFile = File(...),
    user_id: str = Depends(current_user),
):
    """Import chat history directly into an existing character's message log.

    Unlike ``/api/import_chat`` this does NOT create a character card — it
    parses the uploaded file and appends every text message to the given
    character's history, so the user can continue chatting with imported
    context.
    """
    char = db.get_character(character_id, user_id)
    if char is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "character not found")
    raw = await file.read()
    try:
        segments = list(iter_messages(raw))
    except Exception as exc:
        log.warning("import-messages parse failed: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "could not parse chat file")
    imported = 0
    for seg in segments:
        for msg in seg.messages:
            if msg.role == "system":
                continue
            if msg.message_type != "text" or not msg.content.strip():
                continue
            db.append_message(
                user_id=user_id,
                character_id=character_id,
                role=msg.role,
                content=msg.content,
            )
            imported += 1
    log.info("imported %d messages into character %s", imported, character_id)
    return {"imported": imported}


@router.delete("/messages/{message_id}")
async def delete_message(
    message_id: int,
    character_id: str = Query(...),
    user_id: str = Depends(current_user),
):
    if not db.delete_message(message_id=message_id, user_id=user_id, character_id=character_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "message not found")
    return {"deleted": True}


@router.post("/messages/batch-delete")
async def batch_delete_messages(
    request: Request,
    character_id: str = Query(...),
    user_id: str = Depends(current_user),
):
    body = await request.json()
    ids = body.get("ids", [])
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ids must be a non-empty list")
    count = db.delete_messages(message_ids=ids, user_id=user_id, character_id=character_id)
    return {"deleted": count}


@router.get("/send")
async def send_message(
    request: Request,
    character_id: str = Query(...),
    message: str = Query(""),
    image_paths: list[str] | None = Query(default=None),
    user_id: str = Depends(current_user),
):
    char = db.get_character(character_id, user_id)
    if char is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    card = json.loads(char["card_json"])

    image_paths = image_paths or []
    # NB: the user turn is persisted AFTER the LLM call succeeds (see
    # below). When the model produces nothing — i.e. we'd emit the
    # synthetic "哈哈哈" — the user's question is often the trigger (a
    # prompt that hangs the model). Persisting "user: <that> · AI: 哈哈哈"
    # would (a) pollute next-turn history, (b) embed a useless "哈哈哈"
    # into memU recall, and (c) reinforce the failure loop. We drop the
    # whole turn instead.

    memu: MemoryService = _get_memu(request)
    settings = get_settings()

    async def gen():
        # 0. 睡眠拦截：AI 睡眠时段不回复，只存用户消息，起床后补回
        #    故事卡不受昼夜节律限制，随时可回复
        if _is_sleeping() and card.get("card_type", "character") != "story":
            db.append_message(
                user_id=user_id, character_id=character_id, role="user",
                content=message, image_paths=image_paths or None,
            )
            s = get_settings()
            yield sse_pack("sleeping", {
                "sleep_time": s.sleep_time,
                "wake_time": s.wake_time,
            })
            yield sse_pack("done", {"reason": "sleeping"})
            return

        # 0. history — fetched early so retrieval can use recent context as query
        history = db.recent_messages_for_context(
            user_id=user_id, character_id=character_id, limit=settings.history_window
        )

        # 1. retrieval (emit before any tokens so the UI can show "recall")
        segments: list[dict[str, Any]] = []
        files: list[dict[str, Any]] = []
        use_memu = embedding_enabled()
        if not use_memu:
            yield sse_pack(
                "warning",
                {
                    "where": "retrieval",
                    "msg": "embedding key not configured — memU skipped",
                },
            )
        else:
            try:
                # 结合最近 2 条用户消息 + 当前消息作为检索 query，
                # 避免"那后来呢"等短消息查不到相关记忆
                recent_user_msgs = [
                    m["content"] for m in history[-6:]
                    if m.get("role") == "user" and m.get("content")
                ]
                if message:
                    retrieve_query = " ".join(recent_user_msgs[-2:] + [message])
                else:
                    retrieve_query = "(greeting)"
                r = await asyncio.wait_for(
                    memu.progressive_retrieve(
                        retrieve_query,
                        where={"user_id": user_id, "character_id": character_id},
                    ),
                    timeout=get_settings().chat_retrieval_timeout,
                )
                segments = r.get("segments", [])
                files = r.get("files", [])
            except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
                # Retrieval is enhancement, not core. Failure here must not abort the
                # reply — keep going with empty recall and emit a 'warning' event
                # (renamed from 'error' so the browser's EventSource does not treat
                # it as a connection-level failure that auto-reconnects or closes).
                yield sse_pack("warning", {"where": "retrieval", "msg": str(exc)})
        yield sse_pack("retrieval", {"segments": segments[:5], "files": files[:3]})

        # 2. assemble messages (with mood state)
        mood_row = db.get_mood(user_id=user_id, character_id=character_id)
        if mood_row:
            current_mood = mood_row["mood"]
            try:
                last_updated = datetime.fromisoformat(mood_row["updated_at"].replace("Z", "+00:00"))
                idle_min = int((datetime.now(last_updated.tzinfo) - last_updated).total_seconds() / 60)
            except Exception:
                idle_min = 0
            current_mood = apply_idle_decay(current_mood, idle_min)
        else:
            current_mood = DEFAULT_MOOD
        mood_prompt = render_mood_prompt(current_mood, state_config=card.get("state_config"))

        # 事件追问：查找到期事件，注入 prompt
        pending_events = db.list_pending_events(user_id=user_id, character_id=character_id)
        events_prompt = render_events_prompt(pending_events)
        if pending_events:
            db.mark_events_asked(event_ids=[e["id"] for e in pending_events])

        # 亲密度/关系阶段 — intimacy=0（无对话历史）时跳过，行为守则已覆盖
        stats = db.character_stats(user_id=user_id, character_id=character_id)
        intimacy = compute_intimacy(stats.get("user_count", 0))
        intimacy_prompt = render_intimacy_prompt(intimacy) if intimacy > 0 else ""

        # 关系类型 — 陌生人+低亲密度时与亲密度提示冗余，跳过节约上下文
        relationship = db.get_relationship(user_id=user_id, character_id=character_id)
        if relationship == "陌生人" and intimacy < 11:
            relationship_prompt = ""
        else:
            relationship_prompt = render_relationship_prompt(relationship)

        # 上一条消息的时间 → 用于场景切换检测（时空推移）。
        # DB 存的是 UTC，parse_msg_time 统一转本地时间再推断场景。
        prev_msg_dt: datetime | None = None
        if history:
            prev_msg_dt = parse_msg_time(history[-1].get("created_at"))

        card_type = card.get("card_type", "character")

        # 对话时空状态：从 DB 读取上一次的描述注入 system prompt。
        # 新的时空状态由主回复 LLM 的 JSON 输出直接更新，不再单独调用 LLM。
        # 故事卡复用 spatial_state 表存「场景状态」，但用故事专用的渲染函数
        # （强约束锚点，防止已离场角色被拉回同一场景）。
        spatial_state = db.get_spatial_state(user_id=user_id, character_id=character_id)
        if card_type == "story":
            spatial_prompt = render_story_scene_prompt(spatial_state)
        else:
            spatial_prompt = render_spatial_prompt(spatial_state)

        # 故事卡数值状态系统：从 DB 读取上一次的数值 + 阶段，渲染注入 system prompt。
        # 新的数值由回复完成后的独立短上下文 LLM 调用更新，阶段由后端按阈值计算。
        story_state_prompt = ""
        story_state_config: dict[str, Any] | None = None
        story_state_row: dict[str, Any] | None = None
        if card_type == "story":
            story_state_config = card.get("story_state_config") or {}
            if story_state_config.get("enabled", True) and (story_state_config.get("dimensions") or []):
                story_state_row = db.get_story_state(user_id=user_id, character_id=character_id)
                if not story_state_row:
                    story_state_row = db.init_story_state(
                        user_id=user_id, character_id=character_id, config=story_state_config,
                    )
                story_state_prompt = render_story_state_prompt(story_state_row, story_state_config)

        # 角色自定义场景（用户在角色详情维护的）；未激活则自动跟随内置时间场景
        custom_scene = db.get_active_scene(user_id=user_id, character_id=character_id)

        # 动态提示词库：按当前上下文匹配规则并注入
        now = datetime.now()
        emotion_key = detect_emotion_key(message, card)
        if custom_scene:
            scene_key = custom_scene.get("id", "")
        else:
            cs = current_scene(now)
            scene_key = cs.key if cs else None
        rules_ctx = build_match_context(
            now=now, emotion=emotion_key, scene_key=scene_key,
            relationship=relationship, intimacy=intimacy, user_msg=message,
        )
        prompt_rules_list = db.list_prompt_rules(user_id=user_id, character_id=character_id)
        _, prompt_rules_prompt = match_and_render(prompt_rules_list, rules_ctx)
        story_memory = None
        story_chapters = None
        triggered_texts: list[str] = []
        if card_type == "story":
            story_memory = db.list_story_memory(user_id, character_id, min_importance=3)
            story_chapters = db.list_story_chapters(user_id, character_id)

            # 情景触发：用 embedding 检索匹配用户消息的触发器。
            # 只有余弦相似度达到阈值（默认 0.9）才触发，避免误触发。
            try:
                triggers = db.list_story_triggers(user_id, character_id)
                active_triggers = [t for t in triggers if t.get("is_active")]
                if active_triggers and message.strip():
                    import numpy as np
                    from local_embed_server import _load_model, _encode_texts

                    threshold = settings.story_trigger_similarity
                    texts = [message] + [t["condition_text"] for t in active_triggers]
                    vecs = _encode_texts(_load_model(settings.embed_model_name), texts)
                    q_vec = np.asarray(vecs[0], dtype="float32")
                    for idx, t in enumerate(active_triggers):
                        c_vec = np.asarray(vecs[idx + 1], dtype="float32")
                        sim = float(np.dot(q_vec, c_vec))
                        if sim >= threshold:
                            triggered_texts.append(t["trigger_text"])
                            db.mark_trigger_fired(user_id, character_id, t["id"])
                            log.info("story trigger fired (sim=%.3f): %s", sim, t["condition_text"][:40])
            except Exception as exc:  # noqa: BLE001
                log.warning("story trigger matching failed: %s", exc)

        # 滚动摘要：从 DB 加载早期对话的压缩摘要，注入 system prompt
        conversation_summary_text: str | None = None
        if settings.summary_enabled:
            summary_row = db.get_conversation_summary(user_id=user_id, character_id=character_id)
            if summary_row and summary_row.get("content"):
                conversation_summary_text = summary_row["content"]

        messages = build_messages(
            card=card,
            user_name=user_id,
            history=history,
            user_msg=message,
            user_images=image_paths,
            recalled_segments=segments,
            recalled_files=files,
            recent_turns=settings.recent_turns,
            mood_prompt=mood_prompt,
            events_prompt=events_prompt,
            intimacy_prompt=intimacy_prompt,
            relationship_prompt=relationship_prompt,
            prev_msg_dt=prev_msg_dt,
            spatial_prompt=spatial_prompt,
            story_state_prompt=story_state_prompt,
            custom_scene=custom_scene,
            prompt_rules_prompt=prompt_rules_prompt or None,
            story_memory=story_memory,
            story_chapters=story_chapters,
            triggered_texts=triggered_texts or None,
            conversation_summary=conversation_summary_text,
        )

        # 发射调试上下文：system prompt 全文 + 各层组件
        _history_msgs = []
        for m in messages[1:]:
            c = m.get("content", "")
            if isinstance(c, list):
                c = " ".join(p.get("text", "") for p in c if p.get("type") == "text")
            _history_msgs.append({"role": m.get("role", ""), "content": c[:1000]})
        yield sse_pack("debug_ctx", {
            "system_prompt": messages[0]["content"] if messages else "",
            "history_turns": len([m for m in messages if m["role"] != "system"]),
            "history_messages": _history_msgs,
            "recalled_segments": [
                {"text": s.get("text", ""), "score": s.get("score", 0)}
                for s in segments[:5]
            ],
            "mood_prompt": mood_prompt or "",
            "events_prompt": events_prompt or "",
            "intimacy_prompt": intimacy_prompt or "",
            "relationship_prompt": relationship_prompt or "",
            "spatial_prompt": spatial_prompt or "",
            "story_state_prompt": story_state_prompt or "",
            "custom_scene": custom_scene,
            "scene_name": (custom_scene or {}).get("name", "") or "内置时间场景",
            "prompt_rules_prompt": prompt_rules_prompt or "",
            "skipped_layers": [
                layer for layer, val in [
                    ("intimacy", intimacy_prompt),
                    ("relationship", relationship_prompt),
                    ("mood", mood_prompt),
                    ("events", events_prompt),
                    ("spatial", spatial_prompt),
                    ("story_state", story_state_prompt),
                    ("circadian", "" if custom_scene else "active"),
                    ("prompt_rules", prompt_rules_prompt),
                ] if not val
            ],
        })

        # 3.5 — AI self-photo picker.
        # Throttle so we don't slow every casual reply; when triggered,
        # the LLM (multimodal) picks one image out of the top-3
        # embedding-ranked candidates. Failures collapse silently —
        # the user just sees a text reply with no picture.
        # NB: chosen_image_urls / emitted_images are declared HERE so the
        # picker block can append into them before the LLM stream starts.
        log.info("picker: msg=%r has_user_images=%s history_len=%d",
                 message[:30], bool(image_paths), len(history))
        emitted_images: set[str] = set()  # avoid duplicate triggers
        chosen_image_urls: list[str] = []
        pool_pick: dict[str, Any] | None = None
        try:
            pool_pick = await asyncio.wait_for(
                _maybe_pick_pool_image(
                    user_id=user_id,
                    character_id=character_id,
                    card=card,
                    history=history,
                    user_msg=message,
                    has_user_images=bool(image_paths),
                ),
                # 池内匹配 ~1s；池无匹配时走 MiniMax 图像生成（15-40s），
                # 用户明确要图，等得起。
                timeout=get_settings().chat_pool_pick_timeout,
            )
        except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
            import traceback
            log.warning("pool image pick failed: %s\n%s", exc, traceback.format_exc())
        log.info("picker result: %s", pool_pick.get('caption')[:30] if pool_pick else None)
        if pool_pick:
            chosen_image_urls.append(pool_pick["url"])
            emitted_images.add(pool_pick["url"])
            yield sse_pack(
                "image_ref",
                {
                    "url": pool_pick["url"],
                    "caption": pool_pick.get("caption", ""),
                    "source": pool_pick.get("source", "pool_vision"),
                },
            )

        # 4. Stream the conversational reply token-by-token (true streaming:
        #    the first characters reach the user while the model is still
        #    writing). [IMAGE: ...] placeholders are detected incrementally
        #    and swapped for image_ref events.
        visible_acc = ""    # placeholder-stripped text we send to the user
        was_fallback = False  # True iff we got no usable reply
        state_mood: dict | None = None
        state_spatial: dict | None = None
        reply_ok = False
        try:
            async with asyncio.timeout(LLM_REPLY_TIMEOUT):
                img_filter = _StreamImageFilter()
                async for delta in stream_chat(messages, max_tokens=4096):
                    for kind, payload in img_filter.feed(delta):
                        if kind == "text":
                            visible_acc += payload
                            yield sse_pack("token", {"t": payload})
                        else:
                            url = _pick_image_for(card, payload, emitted_images)
                            if url:
                                chosen_image_urls.append(url)
                                yield sse_pack("image_ref", {"url": url, "caption": payload})
                log.info("stream_chat loop ended normally, visible_acc=%d chars", len(visible_acc))
                for kind, payload in img_filter.flush():
                    if kind == "text":
                        visible_acc += payload
                        yield sse_pack("token", {"t": payload})
                    else:
                        url = _pick_image_for(card, payload, emitted_images)
                        if url:
                            chosen_image_urls.append(url)
                            yield sse_pack("image_ref", {"url": url, "caption": payload})
                reply_ok = True
        except asyncio.CancelledError:
            raise
        except (RuntimeError, Exception) as exc:  # noqa: BLE001
            log.warning("stream exception: %s, visible_acc=%d chars", exc, len(visible_acc))
            if visible_acc.strip():
                reply_ok = True
            else:
                log.warning("llm reply stream failed: %s", exc)
                err_msg = str(exc)
                if "Context size has been exceeded" in err_msg:
                    yield sse_pack("error", {"reason": "context_exceeded", "message": "模型上下文窗口不足，请在 LM Studio 中增大 context size"})
                elif "401" in err_msg or "Authentication" in err_msg:
                    yield sse_pack("error", {"reason": "auth_failed", "message": "API 认证失败，请检查 API Key"})
                else:
                    yield sse_pack("error", {"reason": "llm_error", "message": err_msg[:200]})

        # 4.5 — Post-reply state update: mood/spatial via the update_state
        # tool. Deliberately a SEPARATE, short-context call: DeepSeek only
        # reliably emits tool calls when the context is small, so
        # piggybacking on the (long) main conversation made state updates
        # silently disappear after a few turns.
        # 故事卡跳过状态更新（无心情/时空/里程碑概念）

        if reply_ok and visible_acc.strip() and card_type != "story":
            try:
                state_messages = build_state_update_messages(
                    card=card,
                    user_name=user_id,
                    current_mood=current_mood,
                    spatial_state=spatial_state,
                    user_msg=message,
                    ai_reply=visible_acc,
                )
                async with asyncio.timeout(STATE_UPDATE_TIMEOUT):
                    _, tool_input = await call_with_state_tool(
                        state_messages, max_tokens=512
                    )
                if tool_input:
                    if "mood" in tool_input:
                        new_mood = dict(current_mood)
                        for k in ("happy", "miss", "jealous", "annoyed", "excited", "bored", "libido"):
                            if k in tool_input["mood"]:
                                new_mood[k] = max(0, min(100, int(tool_input["mood"][k])))
                        if "description" in tool_input["mood"]:
                            new_mood["description"] = str(tool_input["mood"]["description"])[:200]
                        db.upsert_mood(user_id=user_id, character_id=character_id, mood=new_mood)
                        state_mood = new_mood
                    if "spatial" in tool_input:
                        desc = str(tool_input["spatial"].get("description", "")).strip()[:300]
                        outfit = str(tool_input["spatial"].get("outfit", "")).strip()[:200]
                        if desc:
                            db.upsert_spatial_state(user_id=user_id, character_id=character_id, description=desc, outfit=outfit)
                            state_spatial = {"description": desc, "outfit": outfit}
                    if "milestone" in tool_input and tool_input["milestone"]:
                        ms = tool_input["milestone"]
                        ms_type = str(ms.get("type", "custom")).strip()
                        ms_title = str(ms.get("title", "")).strip()[:100]
                        ms_desc = str(ms.get("description", "")).strip()[:300]
                        if ms_title:
                            result = db.add_milestone(
                                user_id=user_id,
                                character_id=character_id,
                                type=ms_type,
                                title=ms_title,
                                description=ms_desc,
                            )
                            if result:
                                log.info("milestone recorded: %s — %s", ms_type, ms_title)
                                yield sse_pack("milestone", result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("state update failed: %s", exc)

        # 故事卡场景状态更新：独立短上下文 LLM 调用，推断「地点 + 在场角色 +
        # 正在发生的事」并持久化到 spatial_state 表。下一轮注入为强约束锚点，
        # 防止已离场角色被拉回同一场景。与角色卡 update_state 同构但语义不同。
        if reply_ok and visible_acc.strip() and card_type == "story":
            async def _story_scene_bg() -> None:
                try:
                    scene_messages = build_story_scene_messages(
                        card=card,
                        user_msg=message,
                        ai_reply=visible_acc,
                        prev_scene=(spatial_state or {}).get("description", ""),
                    )
                    raw = await asyncio.wait_for(
                        call_once(scene_messages, max_tokens=8000),
                        timeout=get_settings().chat_choice_timeout,
                    )
                    raw_stripped = raw.strip()
                    if raw_stripped.startswith("```"):
                        raw_stripped = re.sub(r"^```(?:json)?\s*", "", raw_stripped)
                        raw_stripped = re.sub(r"\s*```$", "", raw_stripped)
                    data = json.loads(raw_stripped)
                    location = str(data.get("location", "")).strip()
                    present = str(data.get("present", "")).strip()
                    action = str(data.get("action", "")).strip()
                    parts: list[str] = []
                    if location:
                        parts.append(f"地点：{location}")
                    if present:
                        parts.append(f"在场角色：{present}")
                    if action:
                        parts.append(f"正在发生：{action}")
                    desc = "；".join(parts)
                    if desc:
                        prev_desc = (spatial_state or {}).get("description", "")
                        if desc == prev_desc:
                            log.info("story scene unchanged, skip update: %s", desc[:80])
                        else:
                            db.upsert_spatial_state(
                                user_id=user_id,
                                character_id=character_id,
                                description=desc,
                                outfit="",
                            )
                            log.info("story scene updated: %s", desc[:80])
                except Exception as exc:  # noqa: BLE001
                    log.warning("story scene update failed: %s", exc)

            _spawn_bg(_story_scene_bg())

            # 故事卡数值状态更新：独立短上下文 LLM 调用，推断各维度数值变化。
            # 阶段由后端按阈值计算，不交给 LLM。与场景更新同构但语义不同。
            if story_state_config and story_state_row:
                async def _story_state_bg() -> None:
                    try:
                        state_messages = build_story_state_messages(
                            card=card,
                            user_msg=message,
                            ai_reply=visible_acc,
                            current_state=story_state_row.get("state", {}),
                            stage=story_state_row.get("stage", 1),
                            state_config=story_state_config,
                        )
                        raw = await asyncio.wait_for(
                            call_once(state_messages, max_tokens=8000),
                            timeout=get_settings().chat_choice_timeout,
                        )
                        raw_stripped = raw.strip()
                        if raw_stripped.startswith("```"):
                            raw_stripped = re.sub(r"^```(?:json)?\s*", "", raw_stripped)
                            raw_stripped = re.sub(r"\s*```$", "", raw_stripped)
                        data = json.loads(raw_stripped)
                        if not isinstance(data, dict):
                            return
                        valid_ids = {d.get("id") for d in (story_state_config.get("dimensions") or []) if d.get("id")}
                        new_state = dict(story_state_row.get("state", {}))
                        for k, v in data.items():
                            if k in valid_ids:
                                try:
                                    new_state[k] = max(0, min(100, int(v)))
                                except (TypeError, ValueError):
                                    pass
                        new_stage = db.compute_story_stage(
                            new_state, story_state_config.get("stages") or [],
                        )
                        db.upsert_story_state(
                            user_id=user_id, character_id=character_id,
                            state=new_state, stage=new_stage,
                        )
                        log.info("story state updated: stage=%d, state=%s", new_stage, new_state)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("story state update failed: %s", exc)

                _spawn_bg(_story_state_bg())

        if not visible_acc.strip():
            was_fallback = True
            visible_acc = LLM_FALLBACK_REPLY
            yield sse_pack("token", {"t": LLM_FALLBACK_REPLY})

        # 5. persist assistant message — but NOT if the whole turn was a
        # synthetic fallback. When the LLM fails to answer at all, the
        # user's question is often the trigger (e.g. something that hangs
        # the model). We still persist both rows so the user can SEE the
        # failed turn in the conversation log, but flag them
        # excluded_from_context so they are kept out of the next LLM
        # history window (avoiding re-triggering the hang) and out of memU.
        if was_fallback:
            db.append_message(
                user_id=user_id, character_id=character_id, role="user",
                content=message, image_paths=image_paths or None,
                excluded_from_context=True,
            )
            db.append_message(
                user_id=user_id, character_id=character_id, role="assistant",
                content=visible_acc, image_paths=chosen_image_urls or None,
                excluded_from_context=True,
            )
            yield sse_pack("skipped", {"reason": "llm_fallback"})
            yield sse_pack("done", {})
            return

        # 5. persist both turns now that the LLM actually answered.
        # user_message / image_paths were deferred from the route entry
        # so a whole-turn skip can drop BOTH rows when the model hangs.
        db.append_message(
            user_id=user_id,
            character_id=character_id,
            role="user",
            content=message,
            image_paths=image_paths or None,
        )
        _history_msgs = []
        for m in messages[1:]:
            c = m.get("content", "")
            if isinstance(c, list):
                c = " ".join(p.get("text", "") for p in c if p.get("type") == "text")
            _history_msgs.append({"role": m.get("role", ""), "content": c[:1000]})
        _debug_ctx = {
            "system_prompt": messages[0]["content"] if messages else "",
            "history_turns": len([m for m in messages if m["role"] != "system"]),
            "history_messages": _history_msgs,
            "recalled_segments": [
                {"text": s.get("text", ""), "score": s.get("score", 0)}
                for s in segments[:5]
            ],
            "mood_prompt": mood_prompt or "",
            "events_prompt": events_prompt or "",
            "intimacy_prompt": intimacy_prompt or "",
            "relationship_prompt": relationship_prompt or "",
            "spatial_prompt": spatial_prompt or "",
            "new_mood": state_mood,
            "new_spatial": state_spatial,
            "custom_scene": custom_scene,
            "scene_name": (custom_scene or {}).get("name", "") or "内置时间场景",
            "prompt_rules_prompt": prompt_rules_prompt or "",
            "skipped_layers": [
                layer for layer, val in [
                    ("intimacy", intimacy_prompt),
                    ("relationship", relationship_prompt),
                    ("mood", mood_prompt),
                    ("events", events_prompt),
                    ("spatial", spatial_prompt),
                    ("circadian", "" if custom_scene else "active"),
                    ("prompt_rules", prompt_rules_prompt),
                ] if not val
            ],
        }
        assistant_id = db.append_message(
            user_id=user_id,
            character_id=character_id,
            role="assistant",
            content=visible_acc,
            image_paths=chosen_image_urls or None,
            debug_ctx=json.dumps(_debug_ctx, ensure_ascii=False, default=str),
        )

        # 5.4 — Rolling summary compression: when unsummarized messages
        #        exceed the chunk threshold, compress the oldest chunk via
        #        LLM and merge into the conversation summary. Fire-and-forget.
        if settings.summary_enabled and reply_ok:
            try:
                summary_row = db.get_conversation_summary(user_id=user_id, character_id=character_id)
                summarized_up_to = summary_row["summarized_up_to"] if summary_row else 0
                existing_summary = summary_row["content"] if summary_row else None
                unsummarized_count = db.count_unsummarized_messages(
                    user_id=user_id, character_id=character_id, summarized_up_to=summarized_up_to,
                )
                chunk_msg_count = settings.summary_chunk_turns * 2  # turns → messages
                if unsummarized_count >= chunk_msg_count + settings.recent_turns * 2:
                    async def _summary_bg() -> None:
                        try:
                            chunk = db.get_unsummarized_messages(
                                user_id=user_id, character_id=character_id,
                                summarized_up_to=summarized_up_to, limit=chunk_msg_count,
                            )
                            if not chunk:
                                return
                            char_name = card.get("nickname") or card.get("name") or "Character"
                            summary_messages = build_summary_messages(
                                card_type=card_type,
                                char_name=char_name,
                                user_name=user_id,
                                existing_summary=existing_summary,
                                messages_to_summarize=chunk,
                            )
                            new_summary = await asyncio.wait_for(
                                call_once(summary_messages, max_tokens=settings.summary_max_tokens),
                                timeout=get_settings().chat_choice_timeout,
                            )
                            new_summary = new_summary.strip()
                            if new_summary and len(new_summary) > 10:
                                last_id = chunk[-1]["id"]
                                db.upsert_conversation_summary(
                                    user_id=user_id,
                                    character_id=character_id,
                                    content=new_summary,
                                    summarized_up_to=last_id,
                                )
                                log.info("conversation summary updated for %s (up to msg %d): %s",
                                         character_id, last_id, new_summary[:60])
                        except Exception as exc:  # noqa: BLE001
                            log.warning("rolling summary compression failed: %s", exc)

                    _spawn_bg(_summary_bg())
            except Exception:  # noqa: BLE001
                pass

        # 5.5 — Story choice generation: independent AI call to produce
        #        2-3 user-role action options based on the latest reply.
        if card_type == "story" and reply_ok and visible_acc.strip():

            # 5.5a — Auto story memory: every 5 turns (10 messages) generate
            #        a concise summary of recent events as a story memory entry.
            #        Every 15 turns (30 messages) generate a full archive summary
            #        covering world state, character relationships, and plot progress.
            #        Fire-and-forget background task — never blocks the reply.
            try:
                _total_msgs = db.count_messages(user_id=user_id, character_id=character_id)
                if _total_msgs > 0 and _total_msgs % 10 == 0:
                    _is_archive = _total_msgs % 30 == 0
                    async def _story_mem_bg() -> None:
                        try:
                            if _is_archive:
                                recent = db.recent_messages(
                                    user_id=user_id, character_id=character_id, limit=30,
                                )
                                transcript = "\n".join(
                                    f"{'用户' if m['role'] == 'user' else '旁白/NPC'}：{m['content'][:200]}"
                                    for m in recent
                                )
                                mem_messages = [
                                    {"role": "system", "content": (
                                        "你是故事存档摘要器。根据以下最近15轮对话，生成一份完整的存档摘要。\n"
                                        "要求：\n"
                                        "- 包含：当前剧情进度、各角色关系状态、已发生的关键事件、未解决的冲突或悬念\n"
                                        "- 用简洁的条目式描述，每条一行，便于后续参考\n"
                                        "- 用第三人称叙事，不要用'你'或'我'\n"
                                        "- 200字以内\n"
                                        "只输出摘要内容本身，不要加前缀或解释。"
                                    )},
                                    {"role": "user", "content": f"最近对话：\n{transcript}"},
                                ]
                                summary = await asyncio.wait_for(
                                    call_once(mem_messages, max_tokens=8000),
                                    timeout=get_settings().chat_choice_timeout,
                                )
                                summary = summary.strip()
                                if summary and len(summary) > 10:
                                    db.add_story_memory(
                                        user_id=user_id,
                                        character_id=character_id,
                                        memory_type="archive",
                                        content=summary,
                                        importance=8,
                                    )
                                    log.info("archive story memory created for %s: %s", character_id, summary[:60])
                            else:
                                recent = db.recent_messages(
                                    user_id=user_id, character_id=character_id, limit=10,
                                )
                                transcript = "\n".join(
                                    f"{'用户' if m['role'] == 'user' else '旁白/NPC'}：{m['content'][:300]}"
                                    for m in recent
                                )
                                mem_messages = [
                                    {"role": "system", "content": (
                                        "你是故事记忆摘要器。根据以下最近5轮对话，提取1条关键剧情记忆。\n"
                                        "要求：\n"
                                        "- 用一句话概括发生了什么重要事件、转折或信息揭示\n"
                                        "- 只记录对后续剧情有持续影响的内容，跳过闲聊和过渡\n"
                                        "- 用第三人称叙事，不要用'你'或'我'\n"
                                        "- 50字以内\n"
                                        "只输出记忆内容本身，不要加前缀或解释。"
                                    )},
                                    {"role": "user", "content": f"最近对话：\n{transcript}"},
                                ]
                                summary = await asyncio.wait_for(
                                    call_once(mem_messages, max_tokens=8000),
                                    timeout=get_settings().chat_choice_timeout,
                                )
                                summary = summary.strip()
                                if summary and len(summary) > 5:
                                    db.add_story_memory(
                                        user_id=user_id,
                                        character_id=character_id,
                                        memory_type="auto",
                                        content=summary,
                                        importance=5,
                                    )
                                    log.info("auto story memory created for %s: %s", character_id, summary[:60])
                        except Exception as exc:  # noqa: BLE001
                            log.warning("auto story memory failed: %s", exc)

                    _spawn_bg(_story_mem_bg())
            except Exception:  # noqa: BLE001
                pass

            try:
                choice_messages = build_story_choice_messages(
                    card=card,
                    user_msg=message,
                    ai_reply=visible_acc,
                )
                raw_choices = await asyncio.wait_for(
                    call_once(
                        choice_messages,
                        max_tokens=8000,
                    ),
                    timeout=get_settings().chat_choice_timeout,
                )
                log.info("story choice raw response: %s", raw_choices[:200] if raw_choices else "(empty)")
                # 解析 JSON（兼容 markdown 代码块包裹的 JSON）
                raw_stripped = raw_choices.strip()
                if raw_stripped.startswith("```"):
                    raw_stripped = re.sub(r"^```(?:json)?\s*", "", raw_stripped)
                    raw_stripped = re.sub(r"\s*```$", "", raw_stripped)
                parsed_choices = json.loads(raw_stripped)
                choices = parsed_choices.get("choices", [])
                if choices and isinstance(choices, list):
                    yield sse_pack("story_choices", {"choices": choices})
                    log.info("story choices generated: %d options", len(choices))
                else:
                    log.info("story choices: no options returned (empty list)")
            except Exception as exc:  # noqa: BLE001
                log.warning("story choice generation failed: %s", exc)

        # 6. commit to memU as a "conv_<timestamp>" memory file (fire-and-forget
        #    so a slow or unavailable embedding provider never blocks the reply).
        #    We deliberately keep `content` as ONE line so memU's auto-segmenter
        #    produces a single searchable segment carrying both turns. Splitting
        #    on '\n' left every assistant sentence orphaned in the memory tab.
        #
        #    Skip trivial turns (嗯/哈哈/哦) so the memory store stays focused
        #    on meaningful exchanges, not noise that dilutes retrieval quality.
        _trivial = {"嗯", "嗯嗯", "哈哈", "哈哈哈", "哈哈哈哈", "哦", "哦哦", "好", "好的",
                    "ok", "OK", "行", "行吧", "是", "是的", "对", "嗯好", "嘿", "嘻嘻",
                    "呃", "啊", "啊啊", "额", "em", "emm", "？", "?", "。"}
        _is_trivial = (message.strip() in _trivial) or (len(message.strip()) < 3 and not image_paths)

        mem_name = f"conv_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{assistant_id}"

        def _topic_of(text: str, limit: int = 16) -> str:
            """Pull a short, human-readable topic out of free-form RP text.

            Skips leading asterisk action blocks (*动作描写*), collapses
            whitespace, takes the first `limit` characters. Falls back to a
            generic label when both turns are pure action text."""
            if not text:
                return ""
            t = text.strip()
            t = re.sub(r"\*[^*]+\*", " ", t)
            t = re.sub(r"（[^）]*）", " ", t)
            t = re.sub(r"\([^)]*\)", " ", t)
            t = re.sub(r"\s+", " ", t).strip()
            if not t:
                return ""
            if len(t) > limit:
                t = t[:limit].rstrip() + "…"
            return t

        user_topic = _topic_of(message, 16)
        bot_topic = _topic_of(visible_acc, 24) if visible_acc else ""
        if user_topic and bot_topic:
            topic = f"{user_topic} · {bot_topic}"
        else:
            topic = user_topic or bot_topic or "一段对话"

        user_excerpt = _topic_of(message, 200) or "(无文本)"
        bot_excerpt = _topic_of(visible_acc, 300) if visible_acc else ""
        # 记忆条目带时空前缀（本地时间 + 场景名），Recall 检索回来时
        # 自带时空信息，AI 能记得"这事是什么时候、在哪聊的"。
        # 用明确的第三人称标签（用户说/角色名回）——不能用「你说/她回」，
        # 否则注入 system prompt（你=AI）时会造成角色混淆。
        _now_local = datetime.now()
        _char_label = card.get("nickname") or card.get("name") or "她"
        _mem_prefix = (
            f"[{_now_local.strftime('%m-%d %H:%M')}·{current_scene(_now_local).name}] "
        )
        if bot_excerpt:
            fact_line = (
                f"{_mem_prefix}用户说：{user_excerpt} · {_char_label}回：{bot_excerpt}"
            )
        else:
            fact_line = f"{_mem_prefix}用户说：{user_excerpt}"

        if use_memu and not _is_trivial:
            async def _commit_bg() -> None:
                try:
                    await asyncio.wait_for(
                        memu.commit_results(
                            recall_files=[
                                {
                                    "name": mem_name,
                                    "track": "memory",
                                    "description": topic,
                                    "content": fact_line,
                                }
                            ],
                            user={"user_id": user_id, "character_id": character_id},
                        ),
                        timeout=10.0,
                    )
                    db.record_memory_commit(
                        user_id=user_id,
                        character_id=character_id,
                        memu_name=mem_name,
                        topic=topic,
                    )
                except Exception as exc:  # noqa: BLE001
                    import logging
                    logging.warning("memu commit failed for %s: %s", mem_name, exc)

            _spawn_bg(_commit_bg())

        # 7. 情绪状态已由主回复 LLM 的 JSON 输出直接更新（见上方 state_json_buf 解析）

        # 8. 异步提取事件（fire-and-forget）
        # 故事卡跳过事件提取
        if card_type != "story":
            async def _events_bg() -> None:
                try:
                    new_events = await asyncio.wait_for(
                        extract_events(user_msg=message, ai_reply=visible_acc),
                        timeout=10.0,
                    )
                    for ev in new_events:
                        db.add_event(
                            user_id=user_id, character_id=character_id,
                            event_text=ev["text"], event_date=ev.get("date"),
                        )
                except Exception as exc:  # noqa: BLE001
                    log.warning("event extraction failed: %s", exc)

            _spawn_bg(_events_bg())

        # 9. 异步生成动态（每日最多一条，基于今日对话内容）
        # 故事卡跳过动态生成
        if card_type != "story":
            async def _moment_bg() -> None:
                try:
                    last = db.last_moment_time(user_id=user_id, character_id=character_id)
                    now = datetime.now(timezone.utc).astimezone()
                    if last and last.date() == now.date():
                        return
                    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                    today_msgs = db.messages_since(
                        user_id=user_id, character_id=character_id, since=today_start
                    )
                    content = await asyncio.wait_for(
                        generate_moment(card=card, mood=current_mood, today_messages=today_msgs),
                        timeout=15.0,
                    )
                    if content:
                        moment_id = db.add_moment(user_id=user_id, character_id=character_id, content=content)
                        _spawn_bg(_cross_interaction_bg(moment_id, content, card))
                except Exception as exc:  # noqa: BLE001
                    log.warning("moment generation failed: %s", exc)

            async def _cross_interaction_bg(moment_id: str, moment_content: str, author_card: dict[str, Any]) -> None:
                try:
                    author_name = author_card.get("nickname") or author_card.get("name") or "角色"
                    all_chars = db.list_characters(user_id)
                    others = [c for c in all_chars if c["id"] != character_id]
                    for other in others:
                        if random.random() > 0.6:
                            continue
                        other_card = json.loads(db.get_character(other["id"], user_id)["card_json"])
                        result = await asyncio.wait_for(
                            generate_cross_interaction(
                                commenter_card=other_card,
                                moment_content=moment_content,
                                moment_author_name=author_name,
                            ),
                            timeout=15.0,
                        )
                        action = result.get("action", "skip")
                        if action == "comment":
                            db.add_comment(
                                moment_id=moment_id, user_id=user_id,
                                commenter_type="character", character_id=other["id"],
                                content=result["content"],
                            )
                        elif action == "like":
                            db.add_like(
                                moment_id=moment_id, user_id=user_id,
                                liker_type="character", character_id=other["id"],
                            )
                except Exception as exc:  # noqa: BLE001
                    log.warning("cross interaction failed: %s", exc)

            _spawn_bg(_moment_bg())

        # 9.5. 异步每日新闻摘要（每日最多一条，通过 Tavily 搜索 + LLM 总结）
        # 故事卡跳过新闻摘要
        if card_type != "story":
            async def _news_bg() -> None:
                try:
                    today = datetime.now().strftime("%Y-%m-%d")
                    if db.has_news_digest(user_id=user_id, character_id=character_id, digest_date=today):
                        return
                    settings = get_settings()
                    result = await asyncio.wait_for(
                        fetch_daily_digest(
                            tavily_api_key=settings.tavily_api_key,
                            user_profession=settings.user_profession,
                            user_hobbies=settings.user_hobbies,
                            character_card=card,
                        ),
                        timeout=30.0,
                    )
                    if result and result.get("summary"):
                        digest = db.add_news_digest(
                            user_id=user_id,
                            character_id=character_id,
                            digest_date=today,
                            summary=result["summary"],
                            raw_titles=result.get("raw_titles", ""),
                        )
                        if digest and use_memu:
                            memu = _get_memu(request)
                            try:
                                await asyncio.wait_for(
                                    memu.commit_results(
                                        recall_files=[
                                            {
                                                "name": f"news_{today}_{character_id[:8]}",
                                                "track": "memory",
                                                "description": f"每日新闻摘要 {today}",
                                                "content": f"[{today}] 今日新闻摘要：{result['summary']}",
                                            }
                                        ],
                                        user={"user_id": user_id, "character_id": character_id},
                                    ),
                                    timeout=10.0,
                                )
                            except Exception as exc:  # noqa: BLE001
                                log.warning("news digest memu commit failed: %s", exc)
                except Exception as exc:  # noqa: BLE001
                    log.warning("news digest generation failed: %s", exc)

            _spawn_bg(_news_bg())

        # 10. 异步自动推进关系类型（根据亲密度自动设置，无需用户手动）
        # 故事卡跳过关系推进
        if card_type != "story":
            async def _relationship_bg() -> None:
                try:
                    new_rel = auto_relationship(intimacy)
                    current_rel = db.get_relationship(user_id=user_id, character_id=character_id)
                    if new_rel != current_rel:
                        db.upsert_relationship(user_id=user_id, character_id=character_id, relationship=new_rel)
                        log.info("auto relationship: %s -> %s (intimacy=%d)", current_rel, new_rel, intimacy)
                except Exception as exc:  # noqa: BLE001
                    log.warning("auto relationship failed: %s", exc)

            _spawn_bg(_relationship_bg())

        # 11. 异步性格演化分析（fire-and-forget）
        # 故事卡跳过性格演化
        if card_type != "story":
            async def _evolution_bg() -> None:
                try:
                    current_personality = card.get("personality", "")
                    result = await asyncio.wait_for(
                        analyze_evolution(
                            current_personality=current_personality,
                            user_msg=message,
                            ai_reply=visible_acc,
                            char_name=card.get("nickname") or card.get("name") or "角色",
                        ),
                        timeout=10.0,
                    )
                    if result:
                        db.add_evolution(
                            user_id=user_id, character_id=character_id,
                            change=result["change"],
                            old_personality=current_personality,
                            new_personality=result["new_personality"],
                        )
                        db.update_character_personality(
                            user_id=user_id, character_id=character_id,
                            personality=result["new_personality"],
                        )
                        log.info("evolution: %s", result["change"])
                except Exception as exc:  # noqa: BLE001
                    log.warning("evolution failed: %s", exc)

            _spawn_bg(_evolution_bg())

        yield sse_pack("done", {"debug_ctx": _debug_ctx})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _build_greet_prompt(
    card: dict[str, Any],
    user_name: str,
    recalled_segments: list[dict[str, Any]],
    idle_minutes: int,
    history_tail: list[dict[str, Any]],
    trigger_reason: str = "idle",
    spatial_state: dict[str, Any] | None = None,
    custom_scene: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Compose the messages list for a proactive greeting.

    No current user message — the assistant opens the conversation by itself.
    """
    char_name = card.get("nickname") or card.get("name") or "Character"
    sys_parts: list[str] = [_card_system_prompt_inline(card, user_name)]
    if card.get("system_prompt"):
        sys_parts.append(
            "## 系统补充\n"
            + _render_placeholders_inline(card["system_prompt"], char_name=char_name, user_name=user_name)
        )
    if card.get("post_history_instructions"):
        sys_parts.append(
            "## 行为锚\n"
            + _render_placeholders_inline(card["post_history_instructions"], char_name=char_name, user_name=user_name)
        )
    if recalled_segments:
        bullet = "\n".join(
            f"- {clarify_memory_perspective(s.get('text', ''), char_name)}"
            for s in recalled_segments[:5] if s.get("text")
        )
        if bullet:
            sys_parts.append("## [Recall] 你隐约记得关于用户和你们之间的事\n" + bullet)

    # 对话推导的时空状态（用户在哪 / 是否在一起）
    _spatial = render_spatial_prompt(spatial_state)
    if _spatial:
        sys_parts.append(_spatial)
    # 角色自定义场景（激活时替代内置时间场景）
    if custom_scene:
        sys_parts.append(
            f"## 当前场景（{custom_scene.get('name', '')}）\n"
            + custom_scene.get("description", "")
            + "\n- 你的行为、语气、正在做的事都要符合上面描述的环境。"
        )

    # nudge: proactive greeting rules + situational context
    _now = datetime.now()
    _h = _now.hour
    if _h < 6 or _h >= 22:
        _circadian = "深夜,你有些困了,语气慵懒"
    elif _h < 9:
        _circadian = "清晨,刚醒不久,带着一点睡意"
    elif _h < 12:
        _circadian = "上午,精神不错"
    elif _h < 14:
        _circadian = "中午,可能有点犯困"
    elif _h < 18:
        _circadian = "下午,状态正常"
    else:
        _circadian = "晚上,比较放松"
    ctx_lines = [
        f"- 当前时间: {_now.strftime('%Y-%m-%d %H:%M:%S')}（{_circadian}）",
        f"- 用户上次发言距今约 {idle_minutes} 分钟",
    ]
    if trigger_reason == "morning":
        opener = "现在是早上,你主动跟用户打个招呼,开启新的一天。\n"
    elif trigger_reason == "evening":
        opener = "现在是傍晚,你主动找用户聊聊今天过得怎么样。\n"
    elif trigger_reason == "wake_up":
        opener = "你刚醒来,发现用户在你睡觉时发了消息,回复TA,自然地衔接消息内容。\n"
    else:
        opener = "用户已经有一段时间没说话了,你主动开口。\n"
    sys_parts.append(
        "## 主动问好\n"
        + opener
        + "- 仔细阅读上面的对话历史,衔接最近的聊天内容继续往下说\n"
        "- 可以接着之前的话题继续聊,或者对之前的对话做自然回应\n"
        "- 历史中 user 角色的消息是用户说的,assistant 才是你说的;不要替用户发言,不要把你的职业/经历安到用户头上\n"
        "- 用符合人设的方式,语气和风格保持一致\n"
        "- 自然流畅,1-5 句,150 字以内\n"
        "- 可以提一个相关的问题或分享一个想法,让对话继续\n"
        "- 不要复述历史内容,而是自然地延续\n"
        "- 禁止使用 markdown 列表或加粗,纯口语\n\n"
        "当前情境:\n" + "\n".join(ctx_lines)
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "\n\n".join(sys_parts)},
    ]
    # Append real history as proper message turns so the LLM can continue
    # the conversation naturally instead of seeing a truncated summary.
    # 跨场景/跨日的消息动态带 [场景 · 时间] 标注。
    for m in annotate_history_scenes(history_tail):
        messages.append({"role": m.get("role", "user"), "content": m.get("content", "")})
    # Anthropic Messages API requires at least one non-system turn, so seed
    # a placeholder user message that the system prompt already explains.
    messages.append({"role": "user", "content": "(silence — you open the conversation)"})
    return messages


def _card_system_prompt_inline(card: dict[str, Any], user_name: str) -> str:
    """Same shape as prompts._card_system_prompt but local to avoid a circular import."""
    name = card.get("name") or "角色"
    nick = card.get("nickname") or name
    parts: list[str] = []
    desc = card.get("description")
    if desc:
        parts.append(f"## 角色设定\n{_render_placeholders_inline(desc, char_name=nick, user_name=user_name)}")
    personality = card.get("personality")
    if personality:
        parts.append(f"## 性格\n{personality}")
    scenario = card.get("scenario")
    if scenario:
        parts.append(f"## 当前场景\n{_render_placeholders_inline(scenario, char_name=nick, user_name=user_name)}")
    first = card.get("first_mes")
    if first:
        parts.append(f"## 开场白参考(只用于风格,不要原样复述)\n{_render_placeholders_inline(first, char_name=nick, user_name=user_name)}")
    return "\n\n".join(parts) if parts else f"你是 {name}。"


_PLACEHOLDER_RE = re.compile(r"\{\{(char|user|original)\}\}")


def _render_placeholders_inline(text: str, *, char_name: str, user_name: str) -> str:
    return _PLACEHOLDER_RE.sub(
        lambda m: {"char": char_name, "user": user_name, "original": user_name}.get(m.group(1), m.group(0)),
        text,
    )


@router.get("/greet")
async def proactive_greet(
    request: Request,
    character_id: str = Query(...),
    user_id: str = Depends(current_user),
):
    """Proactive greeting: AI opens with a short opener based on character
    card + recalled memories. Streams SSE tokens like ``/send`` but does NOT
    persist a user turn and does NOT commit a memory file."""
    char = db.get_character(character_id, user_id)
    if char is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    card = json.loads(char["card_json"])
    settings = get_settings()
    memu: MemoryService = _get_memu(request)

    # How long has the user been silent?
    # Use the context-aware reader so excluded (failed) turns don't pretend
    # to be the user's last real turn.
    history = db.recent_messages_for_context(
        user_id=user_id, character_id=character_id, limit=max(100, settings.history_window * 5)
    )
    last_user_ts: datetime | None = None
    for m in reversed(history):
        if m["role"] == "user":
            try:
                last_user_ts = parse_msg_time(m["created_at"])
            except Exception:  # noqa: BLE001
                last_user_ts = None
            break
    if last_user_ts is None:
        idle_minutes = 60 * 24  # never spoke — treat as a long absence
    else:
        idle_minutes = max(1, int((datetime.now(timezone.utc).astimezone() - last_user_ts).total_seconds() / 60))

    # recall memory (best-effort, time-bounded)
    segments: list[dict[str, Any]] = []
    if embedding_enabled():
        try:
            r = await asyncio.wait_for(
                memu.progressive_retrieve(
                    "(proactive greeting)",
                    where={"user_id": user_id, "character_id": character_id},
                ),
                timeout=5.0,
            )
            segments = r.get("segments", [])
        except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
            log.warning("greet retrieval failed: %s", exc)

    messages = _build_greet_prompt(
        card=card,
        user_name=user_id,
        recalled_segments=segments,
        idle_minutes=idle_minutes,
        history_tail=_take_recent_turns(history, settings.recent_turns),
        spatial_state=db.get_spatial_state(user_id=user_id, character_id=character_id),
        custom_scene=db.get_active_scene(user_id=user_id, character_id=character_id),
    )

    async def gen():
        yield sse_pack(
            "greet_meta",
            {
                "character_id": character_id,
                "idle_minutes": idle_minutes,
                "recalled": len(segments),
            },
        )
        try:
            async for delta in stream_chat(messages):
                yield sse_pack("token", {"t": delta})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            yield sse_pack("warning", {"where": "greet", "msg": str(exc)})
        yield sse_pack("done", {})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


PROACTIVE_IDLE_THRESHOLD = 5  # minutes — 用户 5 分钟没回复即触发
SCHEDULED_HOURS = (9, 18)     # 每天 9 点、18 点定时主动发消息
SCHEDULED_WINDOW = 10         # 定时窗口宽度（分钟），9:00-9:10 / 18:00-18:10


@router.get("/proactive")
async def proactive_message(
    request: Request,
    character_id: str = Query(...),
    user_id: str = Depends(current_user),
):
    """AI 主动联系：用户长时间没消息时，AI 主动发一条并持久化。

    只在用户超过 ``PROACTIVE_IDLE_THRESHOLD`` 分钟没发言时才触发，
    否则返回一个只含 ``done`` 事件的空流（前端据此跳过）。
    """
    char = db.get_character(character_id, user_id)
    if char is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    card = json.loads(char["card_json"])
    settings = get_settings()
    memu: MemoryService = _get_memu(request)

    history = db.recent_messages_for_context(
        user_id=user_id, character_id=character_id, limit=max(100, settings.history_window * 5)
    )
    last_user_ts: datetime | None = None
    for m in reversed(history):
        if m["role"] == "user":
            try:
                last_user_ts = parse_msg_time(m["created_at"])
            except Exception:  # noqa: BLE001
                last_user_ts = None
            break
    if last_user_ts is None:
        idle_minutes = 60 * 24
    else:
        idle_minutes = max(1, int((datetime.now(timezone.utc).astimezone() - last_user_ts).total_seconds() / 60))

    async def _no_op(reason: str):
        yield sse_pack("done", {"reason": reason})

    now = datetime.now(timezone.utc).astimezone()

    # 最近一条 AI 消息距今多少分钟（用于避免重复发送）
    recent_ai_minutes = 999.0
    if history and history[-1]["role"] == "assistant":
        try:
            last_ts = parse_msg_time(history[-1]["created_at"])
            recent_ai_minutes = (now - last_ts).total_seconds() / 60
        except Exception:  # noqa: BLE001
            pass

    # 定时触发窗口：9:00-9:10 或 18:00-18:10
    scheduled = now.hour in SCHEDULED_HOURS and now.minute < SCHEDULED_WINDOW
    trigger_reason = "idle"

    if scheduled:
        # 定时触发：最近 30 分钟 AI 没发过消息才触发，避免重复
        if recent_ai_minutes < 30:
            return StreamingResponse(_no_op("recent_ai"), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        trigger_reason = "morning" if now.hour == 9 else "evening"
    elif idle_minutes < PROACTIVE_IDLE_THRESHOLD:
        # 空闲触发：用户还没到 5 分钟没回复
        return StreamingResponse(_no_op("too_soon"), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    elif history and history[-1]["role"] == "assistant":
        # AI 已经在用户最后发言之后发过主动消息了，不重复发（等用户回复再重新计时）
        return StreamingResponse(_no_op("already_sent"), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # 起床补回：如果用户最后一条消息是在睡眠时段发的，用 wake_up 触发
    if last_user_ts and not _is_sleeping() and settings.sleep_enabled:
        try:
            lu_min = last_user_ts.hour * 60 + last_user_ts.minute
            sh, sm = map(int, settings.sleep_time.split(":"))
            wh, wm = map(int, settings.wake_time.split(":"))
            s_min, w_min = sh * 60 + sm, wh * 60 + wm
            if s_min <= w_min:
                was_sleep = s_min <= lu_min < w_min
            else:
                was_sleep = lu_min >= s_min or lu_min < w_min
            if was_sleep:
                trigger_reason = "wake_up"
        except Exception:  # noqa: BLE001
            pass

    segments: list[dict[str, Any]] = []
    if embedding_enabled():
        try:
            r = await asyncio.wait_for(
                memu.progressive_retrieve(
                    "(proactive message)",
                    where={"user_id": user_id, "character_id": character_id},
                ),
                timeout=5.0,
            )
            segments = r.get("segments", [])
        except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
            log.warning("proactive retrieval failed: %s", exc)

    messages = _build_greet_prompt(
        card=card,
        user_name=user_id,
        recalled_segments=segments,
        idle_minutes=idle_minutes,
        history_tail=_take_recent_turns(history, settings.recent_turns),
        trigger_reason=trigger_reason,
        spatial_state=db.get_spatial_state(user_id=user_id, character_id=character_id),
        custom_scene=db.get_active_scene(user_id=user_id, character_id=character_id),
    )

    async def gen():
        yield sse_pack("greet_meta", {
            "character_id": character_id,
            "idle_minutes": idle_minutes,
            "recalled": len(segments),
        })
        visible_acc = ""
        try:
            async with asyncio.timeout(LLM_REPLY_TIMEOUT):
                async for delta in stream_chat(messages):
                    visible_acc += delta
                    yield sse_pack("token", {"t": delta})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            yield sse_pack("warning", {"where": "proactive", "msg": str(exc)})

        if visible_acc:
            db.append_message(
                user_id=user_id, character_id=character_id,
                role="assistant", content=visible_acc,
            )
        yield sse_pack("done", {})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _pick_image_for(
    card: dict[str, Any], caption: str, already: set[str]
) -> str | None:
    """Pick an emotion / icon asset whose name loosely matches the caption.

    We don't generate images in MVP — we only re-use images already inside the
    character card (assets of type 'icon' or 'emotion' with an embedded data URL).
    Returns a /uploads/ path or None.
    """
    from upload import save_image  # noqa: F401 — keep import lazy to avoid cycles

    icons = [
        a
        for a in card.get("assets", [])
        if a.get("type") in ("icon", "emotion") and a.get("uri", "").startswith("data:")
    ]
    if not icons:
        return None

    caption_low = caption.lower()
    scored: list[tuple[int, dict]] = []
    for a in icons:
        name = (a.get("name") or "").lower()
        score = 0
        for tok in caption_low.split():
            if tok and tok in name:
                score += 1
        scored.append((score, a))
    scored.sort(key=lambda x: x[0], reverse=True)
    chosen = scored[0][1] if scored and scored[0][0] > 0 else random.choice(icons)

    uri = chosen["uri"]
    if uri in already:
        return None
    already.add(uri)
    # Persist to /uploads/ so the browser can fetch it
    settings = get_settings()
    header, b64 = uri.split(",", 1)
    import base64

    raw = base64.b64decode(b64)
    ext = chosen.get("ext") or ("png" if "png" in header else "jpeg" if "jpeg" in header else "png")

    from pathlib import Path

    target_dir = settings.data_dir / "uploads" / "_emotes"
    target_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{abs(hash(uri)) & 0xffffffff:x}.{ext}"
    (target_dir / fname).write_bytes(raw)
    return f"/uploads/_emotes/{fname}"


# ---------- pool image picker (vision) ----------

# Tokens that suggest the user is talking about a scene / appearance /
# activity the AI could naturally share a photo of. Bilingual, expanded
# as more phrases come up in real chats.
_POOL_TRIGGERS_ZH = (
    "照片", "自拍", "拍照", "看看你", "给我看", "长什么样", "穿什么",
    "什么样子", "发个图", "发张图", "来张照片", "发个照片", "看看",
    "你穿", "你的样子", "拍给我", "让我看看",
)
_POOL_TRIGGERS_EN = (
    "photo", "pic", "selfie", "show me", "what do you look like",
    "send a picture", "send a photo", "let me see",
)


def _should_try_send_image(
    history: list[dict[str, Any]],
    user_msg: str,
    has_user_images: bool,
) -> bool:
    """只在用户明确想看图或发了图片时才触发，不自动轮换。"""
    if has_user_images:
        return True
    msg_low = (user_msg or "").lower()
    if any(tok in msg_low for tok in _POOL_TRIGGERS_EN):
        return True
    if any(tok in user_msg for tok in _POOL_TRIGGERS_ZH):
        return True
    return False


async def _maybe_pick_pool_image(
    *,
    user_id: str,
    character_id: str,
    card: dict[str, Any],
    history: list[dict[str, Any]],
    user_msg: str,
    has_user_images: bool,
) -> dict[str, Any] | None:
    """Return the pool image whose caption embedding is closest to the
    conversation context, or None to skip silently.

    池子为空或相似度低于阈值时，改为调用 MiniMax 图像生成实时生成一张
    （配置了 IMAGE_GEN_API_KEY 才生效）；生成结果自动入池，下次相似
    请求直接命中，不再重复生成。
    """
    if not _should_try_send_image(history, user_msg, has_user_images):
        return None

    char_name = card.get("nickname") or card.get("name") or "角色"
    pool = db.list_pool_images(user_id, character_id)
    matched: dict[str, Any] | None = None
    if pool:
        # Embed the conversation context (current user message + a scene
        # hint naming the character) and take the closest match directly.
        # No vision round-trip: the external vision gateway rejects pool
        # thumbnails with a content-filter 500, and the extra ~2s latency
        # isn't worth a last-mile pick.
        query_text = f"{char_name} 现在的场景:{user_msg}"
        from image_pool import embed_query

        q_vec = await asyncio.to_thread(embed_query, query_text)
        if q_vec is not None:
            top = db.pick_pool_by_embedding(user_id, character_id, q_vec, top_k=1)
            if top:
                score = float(top[0].get("score", 0.0))
                if score >= 0.35:
                    log.info("picker picked: score=%.3f %s", score, (top[0].get("caption") or "")[:40])
                    matched = top[0]
                else:
                    log.info("picker: pool match too weak (score=%.3f) for %r — generate", score, user_msg[:30])
            else:
                log.info("picker: no embedding rows for %r — generate", user_msg[:30])
        else:
            log.info("picker: embed_query failed — generate")

    if matched is not None:
        return matched
    return await _generate_fallback_image(
        user_id=user_id, character_id=character_id, card=card, user_msg=user_msg,
    )


async def _generate_fallback_image(
    *,
    user_id: str,
    character_id: str,
    card: dict[str, Any],
    user_msg: str,
) -> dict[str, Any] | None:
    """图像池无匹配时用 MiniMax image-01 现生成一张角色照片。

    成功后 fire-and-forget 地把生成图写入图片池（caption embedding 用
    生成 prompt），之后同样的请求会直接命中池子。
    """
    from image_pool import embed_text, generate_image

    char_name = card.get("nickname") or card.get("name") or "角色"
    desc = re.sub(r"\s+", " ", (card.get("description") or "")).strip()[:150]
    scene = current_scene()
    prompt = (
        f"{char_name}的自拍照。{desc}。"
        f"此刻场景:{scene.name}。用户说:{user_msg[:60]}。"
        "真实感手机自拍，自然光，生活感构图，竖版照片。"
    )
    result = await asyncio.to_thread(
        generate_image, prompt, user_id=user_id, character_id=character_id,
    )
    if result is None:
        return None
    public_url, thumb_url = result
    caption = f"{char_name}的自拍 · {scene.name}"

    def _seed_pool() -> None:
        try:
            emb = embed_text(prompt)
            db.add_pool_image(
                user_id=user_id, character_id=character_id,
                url=public_url, thumb_url=thumb_url,
                caption=caption, embedding=emb, source="generated",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("generated image pool seed failed: %s", exc)

    _spawn_bg(asyncio.to_thread(_seed_pool))
    log.info("picker generated: %s", public_url)
    return {"url": public_url, "thumb_url": thumb_url, "caption": caption, "source": "generated"}



