"""Character card import / list / delete / select / edit."""
from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import urllib.parse
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Any

import character_card as cc
import db
from auth import current_user
from config import get_settings
from upload import save_image

router = APIRouter(prefix="/api")


# Vision captioning is CPU/GPU- and upstream-heavy. A single global
# semaphore serialises all recaption work (single + batch, any character)
# so repeated clicks or multiple characters can't stack concurrent
# vision calls and trigger timeouts.
_caption_semaphore: asyncio.Semaphore | None = None


def _get_caption_semaphore() -> asyncio.Semaphore:
    global _caption_semaphore
    if _caption_semaphore is None:
        _caption_semaphore = asyncio.Semaphore(1)
    return _caption_semaphore


# ---------- custom scenes (角色自定义场景) ----------

class SceneBody(BaseModel):
    name: str
    description: str


class ScenePatchBody(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None


class SceneActiveBody(BaseModel):
    scene_id: str = ""  # 空 = 自动跟随时间推断的内置场景


@router.get("/characters/{char_id}/scenes")
def list_scenes_api(char_id: str, user_id: str = Depends(current_user)):
    return {
        "scenes": db.list_scenes(user_id=user_id, character_id=char_id),
        "active_scene_id": db.get_active_scene_id(user_id=user_id, character_id=char_id),
    }


@router.post("/characters/{char_id}/scenes")
def add_scene_api(char_id: str, body: SceneBody, user_id: str = Depends(current_user)):
    name = body.name.strip()
    desc = body.description.strip()
    if not name or not desc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="场景名和描述不能为空")
    return db.add_scene(user_id=user_id, character_id=char_id, name=name, description=desc)


@router.patch("/characters/{char_id}/scenes/{scene_id}")
def update_scene_api(
    char_id: str, scene_id: str, body: ScenePatchBody,
    user_id: str = Depends(current_user),
):
    ok = db.update_scene(
        user_id=user_id, character_id=char_id, scene_id=scene_id,
        name=(body.name.strip() if body.name else None),
        description=(body.description.strip() if body.description else None),
        enabled=body.enabled,
    )
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"updated": True, "id": scene_id}


@router.delete("/characters/{char_id}/scenes/{scene_id}")
def delete_scene_api(char_id: str, scene_id: str, user_id: str = Depends(current_user)):
    ok = db.delete_scene(user_id=user_id, character_id=char_id, scene_id=scene_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"deleted": True, "id": scene_id}


@router.put("/characters/{char_id}/scenes/active")
def set_active_scene_api(
    char_id: str, body: SceneActiveBody, user_id: str = Depends(current_user),
):
    scene_id = body.scene_id.strip()
    if scene_id and db.get_scene(user_id=user_id, character_id=char_id, scene_id=scene_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="场景不存在")
    db.set_active_scene(user_id=user_id, character_id=char_id, scene_id=scene_id)
    return {"active_scene_id": scene_id}


# ---------- prompt rules (动态提示词库) ----------

class PromptRuleBody(BaseModel):
    name: str
    content: str
    match_conditions: str = "{}"
    priority: int = 100
    enabled: bool = True


class PromptRulePatchBody(BaseModel):
    name: str | None = None
    content: str | None = None
    match_conditions: str | None = None
    priority: int | None = None
    enabled: bool | None = None


@router.get("/characters/{char_id}/prompt-rules")
def list_prompt_rules_api(char_id: str, user_id: str = Depends(current_user)):
    if not db.get_character(char_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"rules": db.list_prompt_rules(user_id=user_id, character_id=char_id)}


@router.post("/characters/{char_id}/prompt-rules")
def add_prompt_rule_api(char_id: str, body: PromptRuleBody, user_id: str = Depends(current_user)):
    if not db.get_character(char_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    name = body.name.strip()
    content = body.content.strip()
    if not name or not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="名称和内容不能为空")
    return db.add_prompt_rule(
        user_id=user_id, character_id=char_id, name=name, content=content,
        match_conditions=body.match_conditions, priority=body.priority, enabled=body.enabled,
    )


@router.patch("/characters/{char_id}/prompt-rules/{rule_id}")
def update_prompt_rule_api(
    char_id: str, rule_id: str, body: PromptRulePatchBody,
    user_id: str = Depends(current_user),
):
    ok = db.update_prompt_rule(
        user_id=user_id, character_id=char_id, rule_id=rule_id,
        name=(body.name.strip() if body.name else None),
        content=(body.content.strip() if body.content else None),
        match_conditions=body.match_conditions,
        priority=body.priority, enabled=body.enabled,
    )
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"updated": True, "id": rule_id}


@router.delete("/characters/{char_id}/prompt-rules/{rule_id}")
def delete_prompt_rule_api(char_id: str, rule_id: str, user_id: str = Depends(current_user)):
    ok = db.delete_prompt_rule(user_id=user_id, character_id=char_id, rule_id=rule_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"deleted": True, "id": rule_id}


@router.post("/characters/{char_id}/prompt-rules/preseed-circadian")
def preseed_circadian_api(char_id: str, user_id: str = Depends(current_user)):
    """为角色一键预置 6 条昼夜节律提示词规则（带时间匹配条件）。"""
    if not db.get_character(char_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    presets = [
        ("深夜状态", "22:00", "06:00", "## 你的状态\n现在是深夜，你有些困了。语气慵懒，偶尔打哈欠(*打了个哈欠*)，回复偏短，但还是在陪用户熬夜。可以表达一点点困意，但不要拒绝对话。"),
        ("清晨状态", "06:00", "09:00", "## 你的状态\n现在是清晨，你刚醒不久，声音还带着一点睡意，但精神在恢复。"),
        ("上午状态", "09:00", "12:00", "## 你的状态\n现在是上午，你精神不错，语气轻快有活力。"),
        ("中午状态", "12:00", "14:00", "## 你的状态\n现在是中午，你状态正常，可能刚吃过午饭有点犯困。"),
        ("下午状态", "14:00", "18:00", "## 你的状态\n现在是下午，你状态正常。"),
        ("晚上状态", "18:00", "22:00", "## 你的状态\n现在是晚上，你比较放松，语气温柔。"),
    ]
    created = []
    for name, ts, te, content in presets:
        mc = json.dumps({"time_start": ts, "time_end": te})
        rule = db.add_prompt_rule(
            user_id=user_id, character_id=char_id, name=name, content=content,
            match_conditions=mc, priority=50, enabled=True,
        )
        created.append(rule)
    return {"created": len(created), "rules": created}


@router.get("/characters")
def list_chars(include_cards: bool = False, user_id: str = Depends(current_user)):
    rows = db.list_characters(user_id)
    out = []
    for r in rows:
        last_msg = db.last_message_for_character(user_id=user_id, character_id=r["id"])
        item = {
            "id": r["id"],
            "name": r["name"],
            "spec": r["spec"],
            "card_type": r.get("card_type", "character"),
            "avatar_path": r["avatar_path"],
            "cover_url": r.get("cover_url"),
            "created_at": r["created_at"],
            "last_message": last_msg,
        }
        if include_cards:
            item["card"] = json.loads(r["card_json"]) if r.get("card_json") else {}
        out.append(item)
    out.sort(key=lambda c: c["last_message"]["created_at"] if c["last_message"] else c["created_at"], reverse=True)
    return {"characters": out}


@router.get("/characters/{char_id}/moments")
def get_moments(char_id: str, user_id: str = Depends(current_user)):
    if not db.get_character(char_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"moments": db.list_moments(user_id=user_id, character_id=char_id)}


@router.get("/moments/all")
def get_all_moments(user_id: str = Depends(current_user)):
    return {"moments": db.list_all_moments(user_id=user_id)}


class UserMomentBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=500)


@router.post("/moments")
async def post_user_moment(body: UserMomentBody, user_id: str = Depends(current_user)):
    content = body.content.strip()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="内容不能为空")
    moment_id = db.add_user_moment(user_id=user_id, content=content)
    return {"id": moment_id, "content": content}


@router.post("/moments/{moment_id}/comments")
async def post_comment(
    moment_id: str, request: Request, user_id: str = Depends(current_user),
):
    body = await request.json()
    content = str(body.get("content", "")).strip()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="评论内容不能为空")
    result = db.add_comment(
        moment_id=moment_id, user_id=user_id,
        commenter_type="user", character_id="",
        content=content[:200],
    )
    if result is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="评论失败")
    return result


@router.post("/moments/{moment_id}/likes")
async def post_like(moment_id: str, user_id: str = Depends(current_user)):
    result = db.add_like(
        moment_id=moment_id, user_id=user_id,
        liker_type="user", character_id="",
    )
    if result is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="已经点赞过了")
    return result


@router.get("/characters/{char_id}/relationship")
def get_relationship_endpoint(char_id: str, user_id: str = Depends(current_user)):
    if not db.get_character(char_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"relationship": db.get_relationship(user_id=user_id, character_id=char_id)}


@router.get("/characters/{char_id}/news-digests")
def get_news_digests(char_id: str, user_id: str = Depends(current_user)):
    if not db.get_character(char_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"digests": db.list_news_digests(user_id=user_id, character_id=char_id)}


@router.post("/characters/{char_id}/relationship")
async def set_relationship(char_id: str, request: Request, user_id: str = Depends(current_user)):
    if not db.get_character(char_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    body = await request.json()
    relationship = body.get("relationship", "")
    if not relationship:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="relationship is required")
    db.upsert_relationship(user_id=user_id, character_id=char_id, relationship=relationship)
    return {"ok": True}


@router.get("/characters/{char_id}/evolution")
def get_evolution(char_id: str, user_id: str = Depends(current_user)):
    if db.get_character(char_id, user_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"evolutions": db.get_evolutions(user_id=user_id, character_id=char_id)}


@router.get("/characters/{char_id}")
def get_char(char_id: str, user_id: str = Depends(current_user)):
    char = db.get_character(char_id, user_id)
    if char is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    card = json.loads(char["card_json"])
    return {
        "id": char["id"],
        "name": char["name"],
        "spec": char["spec"],
        "avatar_path": char["avatar_path"],
        "created_at": char["created_at"],
        "card": card,
    }


@router.get("/characters/{char_id}/milestones")
def list_milestones_api(char_id: str, user_id: str = Depends(current_user)):
    if not db.get_character(char_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"milestones": db.list_milestones(user_id=user_id, character_id=char_id)}


@router.get("/characters/{char_id}/stats")
def char_stats(char_id: str, user_id: str = Depends(current_user)):
    char = db.get_character(char_id, user_id)
    if char is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return db.character_stats(user_id=user_id, character_id=char_id)


@router.post("/characters/import")
async def import_char(
    request: Request,
    file: UploadFile = File(...),
    user_id: str = Depends(current_user),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="empty file")
    # v2 full-package export (card + avatar + messages + pool + memories)?
    try:
        peek = json.loads(raw)
        if isinstance(peek, dict) and peek.get("format") == "ai-girlfriend-export-v2":
            return await _import_v2(request, peek, user_id)
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    try:
        card = cc.parse_card(raw)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"parse failed: {exc}") from exc

    name = card.get("name") or "Unnamed"
    if not name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="card missing name")

    char_id, action = db.create_character(
        user_id=user_id,
        name=name,
        spec="v3" if card["spec"] == "chara_card_v3" else "v2",
        card=card,
        avatar_path=None,
    )

    # Try to extract / save the main icon
    avatar_path = None
    main_icons = [a for a in cc.pick_main_icon_assets(card) if a.get("name") == "main"] or cc.pick_main_icon_assets(card)
    if main_icons:
        icon = main_icons[0]
        uri = icon.get("uri") or ""
        if uri.startswith("data:"):
            # data URL: data:image/png;base64,xxxx
            try:
                header, b64 = uri.split(",", 1)
                ext = icon.get("ext") or _ext_from_mime(header)
                bin_bytes = _b64decode(b64)
                avatar_path = await _save_avatar(bin_bytes, ext, user_id, char_id)
            except Exception:
                avatar_path = None

    if avatar_path:
        # Update avatar_path
        with db.connect() as conn:
            conn.execute(
                "UPDATE characters SET avatar_path = ? WHERE id = ?",
                (avatar_path, char_id),
            )

    request.session["active_character_id"] = char_id

    total_for_user = db.count_characters(user_id)
    return {
        "id": char_id,
        "name": name,
        "avatar_path": avatar_path,
        "action": action,            # "inserted" or "updated"
        "db_count": total_for_user,  # how many chars this user now has in DB
        "persisted": True,
    }


async def _import_v2(request: Request, data: dict, user_id: str) -> dict:
    """Import a v2 full-package export: card + avatar + messages + pool + memories."""
    import base64
    import uuid

    settings = get_settings()
    char_data = data["character"]
    card = char_data["card"]
    name = char_data["name"]
    spec = char_data.get("spec", "v2")

    char_id, action = db.create_character(
        user_id=user_id, name=name, spec=spec, card=card, avatar_path=None,
    )

    # avatar
    av = data.get("avatar")
    if av and av.get("b64"):
        try:
            bin_bytes = base64.b64decode(av["b64"])
            avatar_path = await _save_avatar(bin_bytes, av.get("ext", "png"), user_id, char_id)
            with db.connect() as conn:
                conn.execute("UPDATE characters SET avatar_path = ? WHERE id = ?", (avatar_path, char_id))
        except Exception:  # noqa: BLE001
            pass

    # messages
    for m in data.get("messages", []):
        img_paths = m.get("image_paths", [])
        if isinstance(img_paths, list):
            img_paths = [p for p in img_paths if p]
        db.append_message(
            user_id=user_id, character_id=char_id,
            role=m["role"], content=m["content"],
            image_paths=img_paths if img_paths else None,
        )

    # pool images
    for img in data.get("pool_images", []):
        try:
            image_b64 = img.get("image_b64")
            if not image_b64:
                continue
            bin_bytes = base64.b64decode(image_b64)
            ext = img.get("ext", "jpg")
            target_dir = settings.data_dir / "uploads" / user_id / char_id
            target_dir.mkdir(parents=True, exist_ok=True)
            fname = f"{uuid.uuid4().hex}.{ext}"
            (target_dir / fname).write_bytes(bin_bytes)
            url = f"/uploads/{user_id}/{char_id}/{fname}"
            thumb_b64 = img.get("thumb_b64")
            if thumb_b64:
                thumb_bytes = base64.b64decode(thumb_b64)
                (target_dir / "_thumbs").mkdir(parents=True, exist_ok=True)
                (target_dir / "_thumbs" / fname).write_bytes(thumb_bytes)
                thumb_url = f"/uploads/{user_id}/{char_id}/_thumbs/{fname}"
            else:
                thumb_url = url
            db.add_pool_image(
                user_id=user_id, character_id=char_id,
                url=url, thumb_url=thumb_url,
                caption=img.get("caption", ""),
                source=img.get("source", "manual"),
            )
        except Exception:  # noqa: BLE001
            pass

    # long-term memories → memU
    memories = data.get("memories", [])
    if memories:
        try:
            memu = request.app.state.memu
            recall_files = [
                {
                    "name": m.get("name") or f"imported_{uuid.uuid4().hex[:8]}",
                    "track": "memory",
                    "description": m.get("description", ""),
                    "content": m.get("content", ""),
                }
                for m in memories
            ]
            await memu.commit_results(
                recall_files=recall_files,
                user={"user_id": user_id, "character_id": char_id},
            )
            for m in memories:
                db.record_memory_commit(
                    user_id=user_id, character_id=char_id,
                    memu_name=m.get("name", ""),
                    topic=None,
                )
        except Exception:  # noqa: BLE001
            pass

    request.session["active_character_id"] = char_id
    char = db.get_character(char_id, user_id)
    return {
        "id": char_id,
        "name": name,
        "avatar_path": char.get("avatar_path") if char else None,
        "action": action,
        "db_count": db.count_characters(user_id),
        "persisted": True,
    }


@router.get("/storage")
def storage_health(user_id: str = Depends(current_user)):
    """Diagnostic endpoint so the user can verify persistence themselves.

    Shows where the SQLite file lives, how big it is, how many characters
    and messages it holds. No message bodies are returned.
    """
    info = db.storage_info()
    info["my_character_count"] = db.count_characters(user_id)
    return info


def _ext_from_mime(header: str) -> str:
    if "png" in header:
        return "png"
    if "jpeg" in header or "jpg" in header:
        return "jpeg"
    if "webp" in header:
        return "webp"
    return "png"


def _b64decode(s: str) -> bytes:
    import base64

    return base64.b64decode(s)


async def _save_avatar(data: bytes, ext: str, user_id: str, char_id: str) -> str:
    settings = get_settings()
    target_dir = settings.data_dir / "uploads" / user_id / char_id
    target_dir.mkdir(parents=True, exist_ok=True)
    fname = f"avatar.{ext}"
    (target_dir / fname).write_bytes(data)
    return f"/uploads/{user_id}/{char_id}/{fname}"


@router.delete("/characters/{char_id}")
def delete_char(char_id: str, user_id: str = Depends(current_user)):
    ok = db.delete_character(char_id, user_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"deleted": True}


@router.get("/characters/{char_id}/export")
async def export_character(
    char_id: str, request: Request, user_id: str = Depends(current_user)
):
    """Export a full character package as a portable JSON blob (v2).

    Includes the character card, avatar, chat history, AI self-photo pool
    (with image bytes base64-embedded), and long-term memories from memU.
    Round-trip compatible with ``POST /api/characters/import``.
    """
    import base64

    settings = get_settings()
    char = db.get_character(char_id, user_id)
    if not char:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    # --- messages ---
    history = db.recent_messages(
        user_id=user_id, character_id=char_id, limit=1_000_000
    )
    messages_out = [
        {
            "role": r["role"],
            "content": r["content"],
            "image_paths": (r["image_paths"] or "").split(",") if r["image_paths"] else [],
            "created_at": r["created_at"],
            "debug_ctx": r.get("debug_ctx"),
        }
        for r in history
    ]

    # --- avatar ---
    avatar_data = None
    if char.get("avatar_path"):
        av_disk = settings.data_dir / char["avatar_path"].lstrip("/")
        if av_disk.exists():
            avatar_data = {
                "b64": base64.b64encode(av_disk.read_bytes()).decode(),
                "ext": av_disk.suffix.lstrip("."),
            }

    # --- pool images (base64-embedded) ---
    pool_out = []
    for img in db.list_pool_images(user_id, char_id):
        entry: dict[str, Any] = {
            "caption": img.get("caption", ""),
            "source": img.get("source", "manual"),
            "created_at": img.get("created_at"),
        }
        for b64_key, url_key in [("image_b64", "url"), ("thumb_b64", "thumb_url")]:
            url = img.get(url_key, "")
            if url:
                disk_path = settings.data_dir / url.lstrip("/")
                entry[b64_key] = (
                    base64.b64encode(disk_path.read_bytes()).decode()
                    if disk_path.exists() else None
                )
            else:
                entry[b64_key] = None
        entry["ext"] = img.get("url", "").rsplit(".", 1)[-1] if img.get("url") else "jpg"
        pool_out.append(entry)

    # --- long-term memories (memU) ---
    memories_out: list[dict[str, Any]] = []
    try:
        memu = request.app.state.memu
        repo = memu.database.recall_file_repo
        rows_map = repo.list_recall_files(where={"user_id": user_id, "character_id": char_id})
        rows = list(rows_map.values()) if isinstance(rows_map, dict) else list(rows_map)
        for r in rows:
            if getattr(r, "track", "memory") != "memory":
                continue
            memories_out.append({
                "name": getattr(r, "name", ""),
                "description": getattr(r, "description", ""),
                "content": getattr(r, "content", ""),
                "created_at": str(getattr(r, "created_at", "")),
            })
    except Exception:  # noqa: BLE001
        pass  # memories are best-effort

    payload = {
        "format": "ai-girlfriend-export-v2",
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "character": {
            "name": char["name"],
            "spec": char.get("spec", "v2"),
            "card": char.get("card") or {},
            "created_at": char.get("created_at"),
        },
        "avatar": avatar_data,
        "messages": messages_out,
        "pool_images": pool_out,
        "memories": memories_out,
    }
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in char["name"])[:40] or "character"
    ascii_name = "".join(c if c.isascii() and (c.isalnum() or c in "-_") else "_" for c in char["name"])[:40] or "character"
    date = datetime.utcnow().strftime('%Y%m%d')
    ascii_fname = f"ai-girlfriend-{ascii_name}-{date}.json"
    utf8_fname = f"ai-girlfriend-{safe}-{date}.json"
    return JSONResponse(
        payload,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_fname}"; '
                f"filename*=UTF-8''{urllib.parse.quote(utf8_fname)}"
            ),
        },
    )
    messages_out = [
        {
            "role": r["role"],
            "content": r["content"],
            "image_paths": (r["image_paths"] or "").split(",") if r["image_paths"] else [],
            "created_at": r["created_at"],
        }
        for r in history
    ]
    # We keep the raw V2 card so the exporter carries the persona, but
    # we DO NOT export pool images / lorebook / extensions — those have
    # absolute paths in the SQLite DB that don't survive a copy.
    payload = {
        "format": "ai-girlfriend-export-v1",
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "character": {
            "name": char["name"],
            "spec": char.get("spec", "v2"),
            "card": char.get("card") or {},
            "created_at": char.get("created_at"),
        },
        "messages": messages_out,
    }
    # File name uses the character name sanitised. ASCII fallback keeps
    # the Content-Disposition header valid under latin-1; the UTF-8
    # ``filename*`` variant (RFC 5987) is added so non-ASCII names like
    # 中文角色 still download with the original characters.
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in char["name"])[:40] or "character"
    ascii_name = "".join(c if c.isascii() and (c.isalnum() or c in "-_") else "_" for c in char["name"])[:40] or "character"
    date = datetime.utcnow().strftime('%Y%m%d')
    ascii_fname = f"ai-girlfriend-{ascii_name}-{date}.json"
    utf8_fname = f"ai-girlfriend-{safe}-{date}.json"
    return JSONResponse(
        payload,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_fname}"; '
                f"filename*=UTF-8''{urllib.parse.quote(utf8_fname)}"
            ),
        },
    )


# ---------- image pool (AI self-photos per character) ----------

@router.get("/characters/{char_id}/pool")
def list_pool(char_id: str, user_id: str = Depends(current_user)):
    if not db.get_character(char_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"images": db.list_pool_images(user_id, char_id)}


@router.post("/characters/{char_id}/pool/match")
def match_pool(
    char_id: str,
    payload: dict,
    user_id: str = Depends(current_user),
):
    if not db.get_character(char_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    text = (payload or {}).get("text", "").strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "text is required")
    from image_pool import embed_query
    vec = embed_query(text)
    if vec is None:
        return {"image": None}
    results = db.pick_pool_by_embedding(user_id, char_id, vec, top_k=1)
    if not results:
        return {"image": None}
    best = results[0]
    return {
        "image": {
            "id": best["id"],
            "url": best["url"],
            "thumb_url": best["thumb_url"],
            "caption": best["caption"],
            "score": best.get("score", 0.0),
        }
    }


@router.post("/characters/{char_id}/pool")
async def upload_pool(
    char_id: str,
    file: UploadFile = File(...),
    user_id: str = Depends(current_user),
):
    if not db.get_character(char_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    try:
        public_url, thumb_url = await save_image(
            upload=file, user_id=user_id, character_id=char_id
        )
    except HTTPException:
        raise
    # Strip the "/uploads/" prefix from the public URL so the path
    # matches what users expect inside the pool dir (matches the
    # existing save_image behaviour, where public_url is
    # "/uploads/{user_id}/{char_id}/..."). save_image already builds
    # that, so we just take its result.

    pool_id = db.add_pool_image(
        user_id=user_id,
        character_id=char_id,
        url=public_url,
        thumb_url=thumb_url,
        caption="",
        embedding=None,
        source="manual",
    )
    # Caption + embedding generation runs in the background so the
    # upload response stays fast. Failure is logged, not raised — the
    # row is still queryable, just ranked last in retrieval.
    import asyncio
    from image_pool import generate_caption_async, embed_text

    async def _backfill():
        async with _get_caption_semaphore():
            try:
                cap = await generate_caption_async(thumb_url)
                # generate_caption_async returns "[无法描述]" when vision
                # definitively failed or timed out — persist that label and
                # skip embedding (no usable caption to encode).
                if cap == "[无法描述]":
                    db.update_pool_caption(
                        user_id, char_id, pool_id,
                        caption="无法描述",
                        embedding=None,
                    )
                    return
                # cap == "" is reserved for "still pending" — shouldn't happen
                # here (the function only returns "" by raising) but be safe.
                emb = await asyncio.to_thread(embed_text, cap) if cap else None
                db.update_pool_caption(
                    user_id, char_id, pool_id,
                    caption=cap or "",
                    embedding=emb,
                )
            except Exception as exc:  # noqa: BLE001
                import logging
                logging.getLogger("ai-girlfriend.pool").warning(
                    "caption backfill failed for %s: %s", pool_id, exc
                )

    asyncio.create_task(_backfill())

    return {
        "id": pool_id,
        "url": public_url,
        "thumb_url": thumb_url,
        "caption": "",
        "source": "manual",
        "caption_pending": True,
    }


@router.delete("/characters/{char_id}/pool")
def delete_all_pool(char_id: str, user_id: str = Depends(current_user)):
    """Delete every pool image of this character (all sources) and unlink
    the local files. Used by the pool panel's "删除全部" button."""
    removed = db.delete_all_pool_images(user_id, char_id)
    settings = get_settings()
    unlinked = 0
    for rec in removed:
        for url in (rec.get("url"), rec.get("thumb_url")):
            if not url or not url.startswith("/uploads/"):
                continue
            rel = url[len("/uploads/"):]
            try:
                (settings.data_dir / "uploads" / rel).unlink(missing_ok=True)
                unlinked += 1
            except OSError:
                pass
    return {"deleted": len(removed), "files_unlinked": unlinked}


@router.delete("/characters/{char_id}/pool/{pool_id}")
def delete_pool(
    char_id: str,
    pool_id: str,
    user_id: str = Depends(current_user),
):
    removed = db.delete_pool_image(user_id, char_id, pool_id)
    if removed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    # Best-effort file unlink.
    settings = get_settings()
    for url in (removed.get("url"), removed.get("thumb_url")):
        if not url or not url.startswith("/uploads/"):
            continue
        rel = url[len("/uploads/"):]
        try:
            (settings.data_dir / "uploads" / rel).unlink(missing_ok=True)
        except OSError:
            pass
    return {"deleted": True, "id": pool_id}


@router.post("/characters/{char_id}/pool/{pool_id}/recaption")
async def recaption_pool(
    char_id: str,
    pool_id: str,
    user_id: str = Depends(current_user),
):
    """Re-run the vision captioner + embedding on a pool image. Used
    after the user manually edits tags or after a failed backfill."""
    rec = db.get_pool_image(user_id, char_id, pool_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    import asyncio
    from image_pool import generate_caption_async, embed_text

    async def _run():
        async with _get_caption_semaphore():
            try:
                cap = await generate_caption_async(rec["thumb_url"])
                if cap == "[无法描述]":
                    db.update_pool_caption(
                        user_id, char_id, pool_id,
                        caption="无法描述",
                        embedding=None,
                    )
                    return
                emb = await asyncio.to_thread(embed_text, cap) if cap else None
                db.update_pool_caption(
                    user_id, char_id, pool_id,
                    caption=cap or "",
                    embedding=emb,
                )
            except Exception as exc:  # noqa: BLE001
                import logging
                logging.getLogger("ai-girlfriend.pool").warning(
                    "recaption failed for %s: %s", pool_id, exc
                )

    asyncio.create_task(_run())
    return {"queued": True, "id": pool_id}


@router.post("/characters/{char_id}/pool/recaption-all")
async def recaption_all_pool(
    char_id: str,
    user_id: str = Depends(current_user),
):
    """Re-run the vision captioner + embedding on pool images whose captions
    are empty, failed, or non-Chinese. Processes images serially to avoid
    overwhelming the local LLM with concurrent vision requests."""
    images = db.list_pool_images(user_id, char_id)
    if not images:
        return {"queued": 0}

    import asyncio
    import logging
    from image_pool import generate_caption_async, embed_text

    log = logging.getLogger("ai-girlfriend.pool")

    def _is_chinese_caption(text: str) -> bool:
        if not text.strip() or text.strip() == "无法描述":
            return False
        return any("\u4e00" <= ch <= "\u9fff" for ch in text)

    pending = [
        img for img in images
        if not _is_chinese_caption(img.get("caption") or "")
    ]
    if not pending:
        return {"queued": 0}

    async def _run_serial():
        async with _get_caption_semaphore():
            for img in pending:
                pid = img["id"]
                url = img["thumb_url"]
                try:
                    cap = await generate_caption_async(url)
                    if cap == "[无法描述]":
                        db.update_pool_caption(
                            user_id, char_id, pid,
                            caption="无法描述",
                            embedding=None,
                        )
                        log.info("recaption-all: %s -> 无法描述", pid)
                        continue
                    emb = await asyncio.to_thread(embed_text, cap) if cap else None
                    db.update_pool_caption(
                        user_id, char_id, pid,
                        caption=cap or "",
                        embedding=emb,
                    )
                    log.info("recaption-all: %s -> %s", pid, (cap or "")[:40])
                except Exception as exc:  # noqa: BLE001
                    log.warning("recaption-all failed for %s: %s", pid, exc)

    asyncio.create_task(_run_serial())
    return {"queued": len(pending)}


class PoolCaptionBody(BaseModel):
    caption: str = Field(..., min_length=0, max_length=200)


@router.patch("/characters/{char_id}/pool/{pool_id}/caption")
async def edit_pool_caption(
    char_id: str,
    pool_id: str,
    body: PoolCaptionBody,
    user_id: str = Depends(current_user),
):
    """Manually overwrite the caption of one pool image.

    Embedding is recomputed from the new caption (synchronously, on the
    threadpool) so retrieval ranking picks up the change immediately.
    An empty caption is allowed — we drop the embedding so the row falls
    to the bottom of cosine ranking instead of being a random hit."""
    rec = db.get_pool_image(user_id, char_id, pool_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    text = body.caption.strip()
    if text == "无法描述":
        # Treat the user-typed failure label like a vision failure: no embedding.
        db.edit_pool_caption(
            user_id, char_id, pool_id,
            caption="无法描述",
            embedding=None,
        )
        return {"id": pool_id, "caption": "无法描述", "embedding_updated": False}
    if not text:
        db.edit_pool_caption(
            user_id, char_id, pool_id,
            caption="",
            embedding=None,
        )
        return {"id": pool_id, "caption": "", "embedding_updated": False}
    import asyncio
    from image_pool import embed_text

    emb = await asyncio.to_thread(embed_text, text)
    db.edit_pool_caption(
        user_id, char_id, pool_id,
        caption=text,
        embedding=emb,
    )
    return {
        "id": pool_id,
        "caption": text,
        "embedding_updated": emb is not None,
    }


_EDITABLE_SCALAR_FIELDS = (
    "nickname",
    "basic_info",
    "personality",
    "love_values",
    "background",
    "habits",
    "speech_style",
    "scenario",
    "first_mes",
    "mes_example",
    "system_prompt",
    "post_history_instructions",
    "behaviour_rules",
)
# ``tags`` / ``creator`` / ``character_version`` / ``creator_notes`` /
# ``extensions`` are parsed from the source card and stored in the DB
# (import path is untouched), but they have no effect on the conversation
# and are no longer editable from the UI. Reject PATCH payloads that try
# to write them anyway so the on-disk schema doesn't drift.
_EDITABLE_LIST_FIELDS = ("alternate_greetings",)
_EDITABLE_DICT_FIELDS = ("circadian_overrides", "emotion_overrides", "state_config")


@router.patch("/characters/{char_id}")
async def edit_char(
    char_id: str,
    request: Request,
    file: UploadFile | None = File(None),
    payload: str | None = Form(None),
    user_id: str = Depends(current_user),
):
    """Edit an existing character.

    Accepts multipart/form-data with two optional parts:
    - ``file``: a new avatar image (replaces the existing one on disk)
    - ``payload``: a JSON string with any of ``name`` (top-level rename),
      plus card fields ``nickname``, ``basic_info``, ``personality``,
      ``love_values``, ``background``, ``habits``, ``speech_style``,
      ``scenario``, ``first_mes``, ``mes_example``, ``system_prompt``,
      ``post_history_instructions``, ``alternate_greetings`` (list[str]),
      ``character_book`` (dict|None).

    At least one of ``payload`` / ``file`` must be present. Missing card
    fields are preserved on the stored ``card_json``; renaming uses the
    same uniqueness rule as import. List and dict fields are rejected if
    the JSON value is the wrong shape — the frontend validates these
    first, the backend is the second line of defence.

    Note: ``tags`` / ``creator`` / ``character_version`` / ``creator_notes``
    / ``extensions`` are still stored on the card (import path is
    untouched), but are no longer accepted as PATCH keys; they have no
    effect on the conversation and the UI no longer exposes them.
    """
    char = db.get_character(char_id, user_id)
    if char is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    new_avatar_path: str | None = None
    old_avatar_path = char.get("avatar_path")
    if file is not None:
        new_url, _ = await save_image(file, user_id=user_id, character_id=char_id)
        new_avatar_path = new_url

    if payload is None and new_avatar_path is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="nothing to update")

    new_card: dict | None = None
    name_update: str | None = None
    if payload is not None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail=f"invalid payload JSON: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="payload must be a JSON object")

        new_card = json.loads(char["card_json"])
        for key in _EDITABLE_SCALAR_FIELDS:
            if key in data:
                new_card[key] = data[key]
        for key in _EDITABLE_LIST_FIELDS:
            if key in data:
                value = data[key]
                if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        detail=f"{key!r} must be a list of strings",
                    )
                new_card[key] = list(value)
        for key in _EDITABLE_DICT_FIELDS:
            if key in data:
                value = data[key]
                if value is not None and not isinstance(value, dict):
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        detail=f"{key!r} must be a JSON object or null",
                    )
                new_card[key] = value
        if "name" in data:
            new_name = (data.get("name") or "").strip()
            if not new_name:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="name cannot be empty")
            if new_name != char["name"]:
                new_card["name"] = new_name
                name_update = new_name

    try:
        ok = db.update_character(
            char_id=char_id,
            user_id=user_id,
            card=new_card,
            name=name_update,
            avatar_path=new_avatar_path,
        )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"a character named {name_update!r} already exists",
        )
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    if new_avatar_path and old_avatar_path and old_avatar_path != new_avatar_path:
        _maybe_unlink_avatar(old_avatar_path, user_id, char_id)

    final_name = name_update or char["name"]
    final_avatar = new_avatar_path or old_avatar_path
    return {"updated": True, "name": final_name, "avatar_path": final_avatar, "card": new_card or json.loads(char["card_json"])}


def _maybe_unlink_avatar(avatar_url: str, user_id: str, char_id: str) -> None:
    """Best-effort delete of an old avatar file under
    ``uploads/{user_id}/{char_id}/``. Skips anything outside that directory
    so shared assets (e.g. ``_emotes``) are never touched."""
    if not avatar_url.startswith("/uploads/"):
        return
    rel = avatar_url[len("/uploads/"):]
    parts = rel.split("/")
    if len(parts) < 3 or parts[0] != user_id or parts[1] != char_id:
        return
    settings = get_settings()
    abs_path = settings.data_dir / "uploads" / user_id / char_id / parts[2]
    try:
        abs_path.unlink(missing_ok=True)
    except OSError:
        pass


@router.post("/characters/{char_id}/select")
def select_char(char_id: str, request: Request, user_id: str = Depends(current_user)):
    if not db.get_character(char_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    request.session["active_character_id"] = char_id
    return {"selected": char_id}


# ---------- runtime state (mood / spatial / intimacy) ----------

@router.get("/characters/{char_id}/runtime-state")
def get_runtime_state(char_id: str, user_id: str = Depends(current_user)):
    if not db.get_character(char_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    mood_row = db.get_mood(user_id=user_id, character_id=char_id)
    spatial = db.get_spatial_state(user_id=user_id, character_id=char_id)
    stats = db.character_stats(user_id=user_id, character_id=char_id)
    import intimacy as intimacy_mod
    intim = intimacy_mod.compute_intimacy(stats.get("user_count", 0))
    return {
        "mood": mood_row["mood"] if mood_row else None,
        "mood_updated_at": mood_row["updated_at"] if mood_row else None,
        "spatial": spatial,
        "intimacy": intim,
        "user_message_count": stats.get("user_count", 0),
    }


class MoodBody(BaseModel):
    happy: int | None = None
    miss: int | None = None
    jealous: int | None = None
    annoyed: int | None = None
    excited: int | None = None
    bored: int | None = None
    libido: int | None = None
    description: str | None = None


class SpatialBody(BaseModel):
    description: str | None = None
    outfit: str | None = None


class RuntimeStateBody(BaseModel):
    mood: MoodBody | None = None
    spatial: SpatialBody | None = None


@router.put("/characters/{char_id}/runtime-state")
def put_runtime_state(char_id: str, body: RuntimeStateBody, user_id: str = Depends(current_user)):
    if not db.get_character(char_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if body.mood:
        existing = db.get_mood(user_id=user_id, character_id=char_id)
        mood = existing["mood"] if existing else {}
        for k in ("happy", "miss", "jealous", "annoyed", "excited", "bored", "libido", "description"):
            v = getattr(body.mood, k)
            if v is not None:
                mood[k] = v
        db.upsert_mood(user_id=user_id, character_id=char_id, mood=mood)
    if body.spatial:
        existing = db.get_spatial_state(user_id=user_id, character_id=char_id) or {}
        desc = body.spatial.description if body.spatial.description is not None else existing.get("description", "")
        outfit = body.spatial.outfit if body.spatial.outfit is not None else existing.get("outfit", "")
        db.upsert_spatial_state(user_id=user_id, character_id=char_id, description=desc, outfit=outfit)
    return {"ok": True}

# ---------- story cards ----------

class StoryNpcBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    personality: str = Field(default="", max_length=2000)


class StoryCreateBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    genre: str = Field(default="", max_length=100)
    world_setting: str = Field(default="", max_length=4000)
    plot_summary: str = Field(default="", max_length=4000)
    user_role: str = Field(default="", max_length=2000)
    ai_role: str = Field(default="", max_length=2000)
    opening: str = Field(default="", max_length=4000)
    story_rules: str = Field(default="", max_length=4000)
    npcs: list[StoryNpcBody] = Field(default_factory=list)
    story_prompt: str = Field(default="", max_length=8000)
    story_choice_prompt: str = Field(default="", max_length=8000)


_STORY_EDITABLE_FIELDS = (
    "title", "genre", "world_setting", "plot_summary", "user_role", "ai_role",
    "opening", "story_rules", "story_prompt", "story_choice_prompt",
    "initial_spatial", "initial_mood", "initial_intimacy",
)


@router.get("/stories")
def list_stories(user_id: str = Depends(current_user)):
    rows = db.list_characters(user_id, card_type="story")
    out = []
    for r in rows:
        card = json.loads(r["card_json"]) if r.get("card_json") else {}
        out.append({
            "id": r["id"],
            "name": r["name"],
            "spec": r["spec"],
            "avatar_path": r["avatar_path"],
            "cover_url": r.get("cover_url"),
            "story_status": r.get("story_status", "ongoing"),
            "card_type": r.get("card_type", "story"),
            "genre": card.get("genre", ""),
            "created_at": r["created_at"],
        })
    return {"stories": out}


@router.post("/stories")
def create_story(body: StoryCreateBody, user_id: str = Depends(current_user)):
    card: dict[str, Any] = {
        "card_type": "story",
        "name": body.title,
        "title": body.title,
        "genre": body.genre,
        "world_setting": body.world_setting,
        "plot_summary": body.plot_summary,
        "user_role": body.user_role,
        "ai_role": body.ai_role,
        "opening": body.opening,
        "story_rules": body.story_rules,
        "npcs": [npc.model_dump() for npc in body.npcs],
        "story_prompt": body.story_prompt,
        "initial_spatial": "",
        "initial_mood": "",
        "initial_intimacy": 0,
    }
    char_id, action = db.create_character(
        user_id=user_id,
        name=body.title,
        spec="story",
        card=card,
        avatar_path=None,
        card_type="story",
    )
    return {"id": char_id, "name": body.title, "action": action}


@router.get("/stories/{story_id}")
def get_story(story_id: str, user_id: str = Depends(current_user)):
    char = db.get_character(story_id, user_id)
    if char is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if char.get("card_type") != "story":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="not a story card")
    card = char.get("card") or json.loads(char["card_json"])
    from prompts import _DEFAULT_STORY_PROMPT, _DEFAULT_STORY_CHOICE_PROMPT, _render_placeholders
    user_role = card.get("user_role") or "故事中的角色"
    ai_role = card.get("ai_role") or "旁白和所有 NPC"
    if not (card.get("story_prompt") or "").strip():
        card["story_prompt"] = _render_placeholders(
            _DEFAULT_STORY_PROMPT, char_name="", user_name="",
            ai_role=ai_role, user_role=user_role
        )
    if not (card.get("story_choice_prompt") or "").strip():
        card["story_choice_prompt"] = _DEFAULT_STORY_CHOICE_PROMPT
    return {
        "id": char["id"],
        "name": char["name"],
        "avatar_path": char["avatar_path"],
        "created_at": char["created_at"],
        "card": card,
    }


@router.patch("/stories/{story_id}")
async def update_story(
    story_id: str,
    request: Request,
    user_id: str = Depends(current_user),
):
    char = db.get_character(story_id, user_id)
    if char is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if char.get("card_type") != "story":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="not a story card")

    body = await request.json()
    new_card = json.loads(char["card_json"])
    name_update: str | None = None

    for field in _STORY_EDITABLE_FIELDS:
        if field in body:
            new_card[field] = body[field]
    if "npcs" in body:
        npcs = body["npcs"]
        if not isinstance(npcs, list):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="npcs must be a list")
        new_card["npcs"] = npcs
    if "title" in body:
        new_title = (body.get("title") or "").strip()
        if not new_title:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="title cannot be empty")
        new_card["title"] = new_title
        new_card["name"] = new_title
        name_update = new_title

    ok = db.update_character(
        char_id=story_id,
        user_id=user_id,
        card=new_card,
        name=name_update,
    )
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"updated": True, "name": name_update or char["name"]}


@router.delete("/stories/{story_id}")
def delete_story(story_id: str, user_id: str = Depends(current_user)):
    char = db.get_character(story_id, user_id)
    if char is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if char.get("card_type") != "story":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="not a story card")
    ok = db.delete_character(story_id, user_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"deleted": True}

# ========== Story templates ==========

@router.get("/story-templates")
def list_story_templates():
    return {"templates": db.list_story_templates()}


# ========== Story chapters ==========

@router.get("/stories/{story_id}/chapters")
def list_story_chapters(story_id: str, user_id: str = Depends(current_user)):
    if not db.get_character(story_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"chapters": db.list_story_chapters(user_id, story_id)}


@router.post("/stories/{story_id}/chapters")
def add_story_chapter(story_id: str, body: dict, user_id: str = Depends(current_user)):
    if not db.get_character(story_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    title = (body or {}).get("title", "").strip()
    if not title:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "title is required")
    summary = (body or {}).get("summary", "")
    chapter_order = (body or {}).get("chapter_order", 0)
    cid = db.add_story_chapter(
        user_id=user_id, character_id=story_id,
        title=title, summary=summary, chapter_order=chapter_order,
    )
    return {"id": cid}


@router.patch("/stories/{story_id}/chapters/{chapter_id}/complete")
def complete_story_chapter(story_id: str, chapter_id: str, user_id: str = Depends(current_user)):
    ok = db.complete_story_chapter(user_id, story_id, chapter_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"completed": True}


# ========== Story endings ==========

@router.get("/stories/{story_id}/ending")
def get_story_ending(story_id: str, user_id: str = Depends(current_user)):
    if not db.get_character(story_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    ending = db.get_story_ending(user_id, story_id)
    return {"ending": ending}


@router.post("/stories/{story_id}/ending")
def set_story_ending(story_id: str, body: dict, user_id: str = Depends(current_user)):
    if not db.get_character(story_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    ending_type = (body or {}).get("ending_type", "").strip()
    if not ending_type:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ending_type is required")
    description = (body or {}).get("description", "")
    eid = db.set_story_ending(
        user_id=user_id, character_id=story_id,
        ending_type=ending_type, description=description,
    )
    return {"id": eid}


# ========== Story memory ==========

@router.get("/stories/{story_id}/memory")
def list_story_memory(story_id: str, user_id: str = Depends(current_user)):
    if not db.get_character(story_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"memories": db.list_story_memory(user_id, story_id)}


@router.post("/stories/{story_id}/memory")
def add_story_memory(story_id: str, body: dict, user_id: str = Depends(current_user)):
    if not db.get_character(story_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    content = (body or {}).get("content", "").strip()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "content is required")
    memory_type = (body or {}).get("memory_type", "plot")
    importance = (body or {}).get("importance", 5)
    mid = db.add_story_memory(
        user_id=user_id, character_id=story_id,
        memory_type=memory_type, content=content, importance=importance,
    )
    return {"id": mid}


@router.delete("/stories/{story_id}/memory/{memory_id}")
def delete_story_memory(story_id: str, memory_id: str, user_id: str = Depends(current_user)):
    ok = db.delete_story_memory(user_id, story_id, memory_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"deleted": True}


# ========== Story triggers ==========

@router.get("/stories/{story_id}/triggers")
def list_story_triggers(story_id: str, user_id: str = Depends(current_user)):
    if not db.get_character(story_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"triggers": db.list_story_triggers(user_id, story_id)}


@router.post("/stories/{story_id}/triggers")
def add_story_trigger(story_id: str, body: dict, user_id: str = Depends(current_user)):
    if not db.get_character(story_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    condition_text = (body or {}).get("condition_text", "").strip()
    trigger_text = (body or {}).get("trigger_text", "").strip()
    if not condition_text or not trigger_text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "condition_text and trigger_text are required")
    is_active = (body or {}).get("is_active", 1)
    tid = db.add_story_trigger(
        user_id=user_id, character_id=story_id,
        condition_text=condition_text, trigger_text=trigger_text, is_active=is_active,
    )
    return {"id": tid}


@router.patch("/stories/{story_id}/triggers/{trigger_id}")
def update_story_trigger(story_id: str, trigger_id: str, body: dict, user_id: str = Depends(current_user)):
    if not db.get_character(story_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    ok = db.update_story_trigger(
        user_id=user_id, character_id=story_id, trigger_id=trigger_id,
        condition_text=(body or {}).get("condition_text"),
        trigger_text=(body or {}).get("trigger_text"),
        is_active=(body or {}).get("is_active"),
    )
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"updated": True}


@router.delete("/stories/{story_id}/triggers/{trigger_id}")
def delete_story_trigger(story_id: str, trigger_id: str, user_id: str = Depends(current_user)):
    ok = db.delete_story_trigger(user_id, story_id, trigger_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"deleted": True}


# ========== Story cover & status ==========

@router.post("/stories/{story_id}/cover/upload")
async def upload_story_cover(
    story_id: str,
    file: UploadFile = File(...),
    user_id: str = Depends(current_user),
):
    char = db.get_character(story_id, user_id)
    if char is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if char.get("card_type") != "story":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "not a story card")
    try:
        public_url, _ = await save_image(
            upload=file, user_id=user_id, character_id=story_id
        )
    except HTTPException:
        raise
    db.update_story_cover(user_id, story_id, public_url)
    return {"cover_url": public_url}


@router.patch("/stories/{story_id}/cover")
async def update_story_cover(story_id: str, request: Request, user_id: str = Depends(current_user)):
    char = db.get_character(story_id, user_id)
    if char is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if char.get("card_type") != "story":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "not a story card")
    body = await request.json()
    cover_url = (body or {}).get("cover_url", "")
    ok = db.update_story_cover(user_id, story_id, cover_url)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"updated": True}


@router.patch("/stories/{story_id}/status")
async def update_story_status(story_id: str, request: Request, user_id: str = Depends(current_user)):
    char = db.get_character(story_id, user_id)
    if char is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if char.get("card_type") != "story":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "not a story card")
    body = await request.json()
    status_val = (body or {}).get("status", "ongoing")
    if status_val not in ("ongoing", "completed", "abandoned"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid status")
    ok = db.update_story_status(user_id, story_id, status_val)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"updated": True}