"""Tests for the chat routes — focused on the synthetic-fallback filter.

When the LLM produces nothing (timeout / error), /api/chat/send emits
"哈哈哈" to the user so the conversation never stalls. But that turn —
both the user's question AND the synthetic AI reply — must NOT enter
history or memU, otherwise a question that hangs the model will keep
producing "哈哈哈" forever (the AI reply gets recalled, which then
triggers the same hang). ``db.recent_messages_excluding_fallback`` is
the defensive cleanup for any rows written before that whole-turn skip
existed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import db


@pytest.fixture
def tmp_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.sqlite3"))
    monkeypatch.setenv("MEMU_DB_PATH", str(tmp_path / "memu.db"))
    from config import get_settings

    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_is_fallback_reply_matches_known_string():
    assert db._is_fallback_reply("哈哈哈") is True


def test_is_fallback_reply_tolerates_whitespace():
    assert db._is_fallback_reply("  哈哈哈  ") is True


def test_is_fallback_reply_ignores_real_messages():
    assert db._is_fallback_reply("早安呀") is False
    assert db._is_fallback_reply("哈") is False  # substring doesn't count
    assert db._is_fallback_reply("哈哈哈，我刚到") is False


def test_is_fallback_reply_handles_empty():
    assert db._is_fallback_reply("") is False
    assert db._is_fallback_reply(None) is False


# ---------- _StreamImageFilter (streamed [IMAGE: ...] placeholder detection) ----------

def _collect(filter_cls, deltas):
    f = filter_cls()
    events: list[tuple[str, str]] = []
    for d in deltas:
        events.extend(f.feed(d))
    events.extend(f.flush())
    return events


def test_stream_filter_plain_text_across_deltas():
    import routes.chat_routes as cr
    events = _collect(cr._StreamImageFilter, ["你好", "呀，", "在干嘛"])
    assert events == [("text", "你好"), ("text", "呀，"), ("text", "在干嘛")]


def test_stream_filter_complete_placeholder_in_one_delta():
    import routes.chat_routes as cr
    events = _collect(cr._StreamImageFilter, ["我想你了 [IMAGE: 自拍] 么么哒"])
    assert events == [("text", "我想你了 "), ("image", "自拍"), ("text", " 么么哒")]


def test_stream_filter_placeholder_split_across_deltas():
    import routes.chat_routes as cr
    events = _collect(cr._StreamImageFilter, ["看这个 [IM", "AGE: 咖", "啡馆] 好看吗"])
    assert events == [("text", "看这个 "), ("image", "咖啡馆"), ("text", " 好看吗")]


def test_stream_filter_square_bracket_not_placeholder():
    import routes.chat_routes as cr
    events = _collect(cr._StreamImageFilter, ["[引用] 这样也行"])
    assert events == [("text", "[引用] 这样也行")]


def test_stream_filter_placeholder_at_end_no_flush_text():
    import routes.chat_routes as cr
    events = _collect(cr._StreamImageFilter, ["来张图 [IMAGE: 笑脸]"])
    assert events == [("text", "来张图 "), ("image", "笑脸")]


def test_recent_messages_excluding_fallback_drops_synthetic_assistant_rows(tmp_data_dir):
    """Defensive cleanup: an assistant row whose content IS the synthetic
    fallback must be hidden from history the LLM sees."""
    cid = db.create_character(
        user_id="alice", name="Sora", spec="v2",
        card={"name": "Sora"}, avatar_path=None,
    )[0]
    db.append_message(user_id="alice", character_id=cid, role="user", content="早安")
    db.append_message(user_id="alice", character_id=cid, role="assistant", content="早呀,睡得好吗?")
    db.append_message(user_id="alice", character_id=cid, role="user", content="在吗")
    db.append_message(user_id="alice", character_id=cid, role="assistant", content="哈哈哈")  # synthetic
    db.append_message(user_id="alice", character_id=cid, role="user", content="你还好吗")

    cleaned = db.recent_messages_excluding_fallback(
        user_id="alice", character_id=cid, limit=100,
    )
    # The synthetic assistant row is gone, surrounding user turns survive,
    # so the LLM still has conversation context.
    contents = [r["content"] for r in cleaned]
    assert "哈哈哈" not in contents
    assert contents == ["早安", "早呀,睡得好吗?", "在吗", "你还好吗"]


def test_recent_messages_excluding_fallback_keeps_other_laughter(tmp_data_dir):
    """User-laugh and assistant-laugh (real model output) must survive."""
    cid = db.create_character(
        user_id="alice", name="Sora", spec="v2",
        card={"name": "Sora"}, avatar_path=None,
    )[0]
    db.append_message(user_id="alice", character_id=cid, role="user", content="哈哈")
    db.append_message(user_id="alice", character_id=cid, role="assistant", content="哈哈哈好笑吗")

    cleaned = db.recent_messages_excluding_fallback(
        user_id="alice", character_id=cid, limit=100,
    )
    # Only the EXACT synthetic string is filtered — substrings and
    # surrounding laughter from real conversation stay.
    assert len(cleaned) == 2


def test_recent_messages_excluding_fallback_no_match_unchanged(tmp_data_dir):
    cid = db.create_character(
        user_id="alice", name="Sora", spec="v2",
        card={"name": "Sora"}, avatar_path=None,
    )[0]
    db.append_message(user_id="alice", character_id=cid, role="user", content="hi")
    db.append_message(user_id="alice", character_id=cid, role="assistant", content="hey")

    cleaned = db.recent_messages_excluding_fallback(
        user_id="alice", character_id=cid, limit=100,
    )
    assert [r["content"] for r in cleaned] == ["hi", "hey"]


# ---------- whole-turn skip: when LLM produces nothing ----------

class _DummyRequest:
    """Minimal stand-in for starlette.requests.Request — only what
    ``send_message`` reads from it (``app.state.memu``)."""

    def __init__(self, memu):
        class _State:
            pass

        state = _State()
        state.memu = memu
        app = type("_App", (), {"state": state})()
        self.app = app


def _drain_sse(response) -> list[tuple[str, dict]]:
    """Collect (event, data) pairs from a Starlette StreamingResponse."""
    import asyncio
    import json as _json

    body_chunks: list[bytes] = []

    async def _consume():
        async for chunk in response.body_iterator:
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            body_chunks.append(chunk)

    asyncio.run(_consume())
    text = b"".join(body_chunks).decode("utf-8")
    events: list[tuple[str, dict]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        try:
            events.append(("data", _json.loads(payload)))
        except Exception:
            events.append(("raw", payload))
    return events


def test_send_skips_whole_turn_when_llm_produces_nothing(tmp_data_dir, monkeypatch):
    """When stream_chat yields nothing before raising, /send must:

    - emit the synthetic "哈哈哈" so the client never stalls
    - emit a ``skipped`` event so the frontend drops both user & AI rows
    - NOT persist ANY row for this turn (otherwise history pollutes the
      next turn and the failure loop self-reinforces — that's exactly
      what the user asked us to prevent)
    - NOT call memu.commit_results (otherwise the bad turn gets recalled)
    """
    import asyncio
    import db as _db
    import routes.chat_routes as chat_routes

    cid = _db.create_character(
        user_id="alice", name="Sora", spec="v2",
        card={"name": "Sora", "description": "温柔的女孩"}, avatar_path=None,
    )[0]

    # Stub: LLM stream raises immediately, producing no reply at all.
    async def _broken_stream(_messages, **kwargs):
        raise RuntimeError("simulated upstream timeout")
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(chat_routes, "stream_chat", _broken_stream)

    # Stub memu so we don't need embedding config.
    commit_calls: list[dict] = []
    retrieve_calls: list[dict] = []

    class _FakeMemu:
        async def progressive_retrieve(self, query, *, where):
            retrieve_calls.append({"query": query, "where": where})
            return {"segments": [], "files": []}

        async def commit_results(self, *, recall_files, user):
            commit_calls.append({"recall_files": recall_files, "user": user})

    # Also stub the image picker so it doesn't touch disk/network.
    async def _no_pick(**_kwargs):
        return None

    monkeypatch.setattr(chat_routes, "_maybe_pick_pool_image", _no_pick)

    # Stub embedding_enabled so memu path is exercised.
    monkeypatch.setattr(chat_routes, "embedding_enabled", lambda: True)

    # Drive the route directly. We invoke the inner generator so we can
    # inspect every yielded event without going through uvicorn.
    request = _DummyRequest(_FakeMemu())
    response = asyncio.run(chat_routes.send_message(
        request=request,
        character_id=cid,
        message="一句话导致 LLM 挂掉",
        image_paths=None,
        user_id="alice",
    ))

    events = _drain_sse(response)
    event_payloads = [p for kind, p in events if kind == "data"]

    # 1. The user gets a fallback token so the UI doesn't stall.
    tokens = [p for p in event_payloads if p.get("t") == "哈哈哈"]
    assert len(tokens) == 1, f"expected 1 fallback token, got events: {event_payloads}"

    # 2. A ``skipped`` event tells the frontend to drop both DOM rows.
    skipped = [p for p in event_payloads if p.get("reason") == "llm_fallback"]
    assert len(skipped) == 1, f"expected exactly one skipped event, got: {event_payloads}"

    # 3. A ``done`` event closes the stream normally (no error event).
    assert any(set(p.keys()) == set() for p in event_payloads), (
        f"expected empty 'done' event, got: {event_payloads}"
    )
    assert not any("error" in str(p).lower() for p in event_payloads), (
        f"should not surface a connection-level error: {event_payloads}"
    )

    # 4. memu.commit_results MUST NOT be called for the failed turn.
    assert commit_calls == [], (
        f"memu.commit_results should be skipped on fallback; got: {commit_calls}"
    )

    # 5. Failed turn IS persisted so the user can see it in the conversation
    # log, but both rows are flagged excluded_from_context so they stay out
    # of the next LLM history window (avoiding re-triggering the hang).
    rows = _db.recent_messages(user_id="alice", character_id=cid, limit=100)
    assert len(rows) == 2, f"expected user+assistant rows persisted, got {rows}"
    assert all(r["excluded_from_context"] for r in rows), (
        f"failed turn must be flagged excluded_from_context; got {rows}"
    )


def test_send_persists_assistant_when_llm_produces_some_text(tmp_data_dir, monkeypatch):
    """Contrast case: when the LLM returns a valid JSON reply, /send
    must persist the assistant reply (it's real model output, not the
    synthetic fallback). The skipped/fallback path is reserved for the
    case where the model produces no usable reply at all."""
    import asyncio
    import db as _db
    import routes.chat_routes as chat_routes

    cid = _db.create_character(
        user_id="alice", name="Sora", spec="v2",
        card={"name": "Sora"}, avatar_path=None,
    )[0]

    # Main reply: streamed tokens (true streaming via stream_chat).
    async def _good_stream(_messages, **kwargs):
        for chunk in ["嗯…", "让我想想"]:
            yield chunk

    # Post-reply state update via the update_state tool.
    _tool_input = {"mood": {"happy": 60, "miss": 70}, "spatial": {"description": "坐在床边", "outfit": "白色睡衣"}}

    async def _good_state_call(_messages, **kwargs):
        return "嗯…让我想想", _tool_input

    monkeypatch.setattr(chat_routes, "stream_chat", _good_stream)
    monkeypatch.setattr(chat_routes, "call_with_state_tool", _good_state_call)

    async def _no_pick(**_kwargs):
        return None

    monkeypatch.setattr(chat_routes, "_maybe_pick_pool_image", _no_pick)
    monkeypatch.setattr(chat_routes, "embedding_enabled", lambda: False)

    class _FakeMemu:
        async def progressive_retrieve(self, query, *, where):
            return {"segments": [], "files": []}

        async def commit_results(self, *, recall_files, user):
            return None

    request = _DummyRequest(_FakeMemu())
    response = asyncio.run(chat_routes.send_message(
        request=request,
        character_id=cid,
        message="讲个笑话",
        image_paths=None,
        user_id="alice",
    ))
    events = _drain_sse(response)
    event_payloads = [p for kind, p in events if kind == "data"]

    # No skipped event, no synthetic fallback token.
    assert not any(p.get("reason") == "llm_fallback" for p in event_payloads), (
        f"partial-output turn must not be skipped: {event_payloads}"
    )
    assert not any(p.get("t") == "哈哈哈" for p in event_payloads), (
        f"partial-output turn must not fall back to 哈哈哈: {event_payloads}"
    )

    # The partial assistant reply IS persisted — that's the whole point of
    # distinguishing "LLM produced nothing" from "LLM produced some".
    rows = _db.recent_messages(user_id="alice", character_id=cid, limit=100)
    contents = [r["content"] for r in rows]
    assert "嗯…让我想想" in contents, (
        f"partial assistant reply must survive; got: {contents}"
    )
    # Streamed tokens reached the client as separate SSE token events.
    streamed = "".join(p["t"] for p in event_payloads if "t" in p)
    assert streamed == "嗯…让我想想", f"streamed text mismatch: {streamed!r}"

    # Post-reply state update: tool input was parsed and persisted.
    mood_row = _db.get_mood(user_id="alice", character_id=cid)
    assert mood_row is not None and mood_row["mood"]["happy"] == 60, (
        f"update_state mood should be persisted; got: {mood_row}"
    )
    spatial_row = _db.get_spatial_state(user_id="alice", character_id=cid)
    assert spatial_row is not None and spatial_row["description"] == "坐在床边", (
        f"update_state spatial should be persisted; got: {spatial_row}"
    )