"""End-to-end test: parse a fake chat export, then verify the imported
messages actually land in memU and are retrievable by topic."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import chat_import
import db
from chat_loaders import iter_messages
from chat_loaders.base import ChatSegment, NormalizedMessage
from memu.app import MemoryService


# 80 messages, half by me, half by Alice — plenty for slicing.
def _make_segment() -> ChatSegment:
    seg = ChatSegment(
        chat_id="fake", chat_type="private", title="Alice", my_user_id="me",
    )
    pairs = [
        ("user", "me", "今天好累啊"),
        ("assistant", "Alice", "怎么啦?要不要我陪你聊聊"),
        ("user", "me", "刚加完班,想喝咖啡"),
        ("assistant", "Alice", "我给你点一杯冰美式好不好?"),
        ("user", "me", "好呀 谢谢亲爱的"),
        ("assistant", "Alice", "不客气 乖乖等我送来~"),
    ]
    base_ts = 1715000000  # 2024-05-06 12:53 UTC
    for i in range(15):  # 15 cycles = 90 messages
        ts = base_ts + i * 60
        for role, sender, content in pairs:
            iso = f"2024-05-06T12:{53 + i % 10}:{ts % 60:02d}Z"
            seg.messages.append(
                NormalizedMessage(role=role, content=content, timestamp=iso, sender_name=sender)
            )
    return seg


@pytest.fixture
def memu_service(tmp_data_dir: Path) -> MemoryService:
    from memu_setup import build_memory_service

    svc = build_memory_service()

    class FakeEmbed:
        embed_model = "fake"

        async def embed(self, inputs):
            # Topic-discriminative: tokens "咖啡" -> high in dim 0; "累" -> dim 1
            out = []
            for t in inputs:
                v = [0.0, 0.0, 0.0]
                if "咖啡" in t or "美式" in t:
                    v[0] = 1.0
                if "累" in t or "加班" in t:
                    v[1] = 1.0
                v[2] = float(len(t) % 7) / 10.0
                out.append(v)
            return out, None

    svc._embedding_pool._cache["default"] = FakeEmbed()
    svc._embedding_pool._cache["embedding"] = FakeEmbed()
    return svc


@pytest.fixture
def tmp_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.sqlite3"))
    monkeypatch.setenv("MEMU_DB_PATH", str(tmp_path / "memu.db"))
    from config import get_settings

    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ingest_segment_writes_to_memu_and_retrieves_topic(
    tmp_data_dir: Path, memu_service: MemoryService
):
    seg = _make_segment()

    # 1. pick_candidate works
    cand = chat_import.pick_candidate(seg)
    assert cand is not None
    assert cand["name"] == "Alice"

    # 2. create a character row so memU commit has a valid scope
    cid = db.create_character(
        user_id="me", name=cand["name"], spec="v2", card={"name": cand["name"]}, avatar_path=None,
    )[0]

    # 3. ingest
    result = await chat_import.ingest_segment_to_memu(
        segment=seg, user_id="me", character_id=cid,
        character_name=cand["name"], memu=memu_service, slice_size=30,
    )
    # 90 text messages, 30 per slice → 3 slices
    assert result.memory_files_committed == 3
    assert result.recall_segments_written == 90

    # 4. retrieval picks up coffee topic
    r = await memu_service.progressive_retrieve(
        "咖啡", where={"user_id": "me", "character_id": cid}
    )
    texts = [s["text"] for s in r["segments"]]
    assert any("美式" in t or "咖啡" in t for t in texts), \
        "expected coffee topic to be retrievable after import"


@pytest.mark.asyncio
async def test_ingest_isolated_across_users(
    tmp_data_dir: Path, memu_service: MemoryService
):
    seg = _make_segment()
    cid = db.create_character(
        user_id="alice", name="Alice", spec="v2", card={}, avatar_path=None,
    )[0]
    cid2 = db.create_character(
        user_id="bob", name="Alice", spec="v2", card={}, avatar_path=None,
    )[0]

    await chat_import.ingest_segment_to_memu(
        segment=seg, user_id="alice", character_id=cid, character_name="Alice",
        memu=memu_service, slice_size=30,
    )
    # Bob never imported anything — should see zero hits
    r = await memu_service.progressive_retrieve(
        "咖啡", where={"user_id": "bob", "character_id": cid2}
    )
    assert r["segments"] == [], "bob should see no segments (alice's chat leaked!)"


def test_export_then_reimport_round_trip(tmp_data_dir: Path):
    """Round-trip: write messages to DB, build the export payload the same
    way /api/characters/{id}/export does, then reload it via the generic
    chat loader to make sure the format stays import-compatible.

    Catches future regressions where the export shape drifts away from
    what ``GenericJSONLoader`` accepts (or where ``db.get_character``
    returns card_json as a raw string instead of a decoded dict).
    """
    cid = db.create_character(
        user_id="me", name="Alice", spec="v2",
        card={"name": "Alice", "description": "她是我的女朋友"},
        avatar_path=None,
    )[0]

    # Seed two messages the way chat_routes would.
    db.append_message(user_id="me", character_id=cid, role="user", content="早安")
    db.append_message(user_id="me", character_id=cid, role="assistant", content="早呀,睡得好吗?")

    # Mirror the export endpoint's payload structure.
    char = db.get_character(cid, "me")
    assert isinstance(char["card"], dict), "get_character must decode card_json to dict"
    history = db.recent_messages(user_id="me", character_id=cid, limit=1_000_000)
    payload = {
        "format": "ai-girlfriend-export-v1",
        "exported_at": "2026-01-01T00:00:00Z",
        "character": {
            "name": char["name"], "spec": char.get("spec", "v2"),
            "card": char.get("card") or {},
            "created_at": char.get("created_at"),
        },
        "messages": [
            {"role": r["role"], "content": r["content"], "image_paths": [], "created_at": r["created_at"]}
            for r in history
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    # Re-import via the generic loader — same path a fresh /api/import_chat
    # call would take on a downloaded export file.
    segs = list(iter_messages(raw, format_key="generic"))
    assert len(segs) == 1
    seg = segs[0]
    assert seg.chat_type == "private"
    assert len(seg.messages) == 2
    assert seg.messages[0].role == "user"
    assert seg.messages[0].content == "早安"
    assert seg.messages[1].role == "assistant"
    assert seg.messages[1].content == "早呀,睡得好吗?"

    # pick_candidate works on the round-tripped chat — it counts assistant
    # messages specifically, so with one user + one assistant turn the
    # candidate surfaces the assistant side. The round-trip guarantees we
    # got back the same shape we exported.
    cand = chat_import.pick_candidate(seg)
    assert cand is not None
    assert cand["message_count"] == 1
    assert cand["user_message_count"] == 1