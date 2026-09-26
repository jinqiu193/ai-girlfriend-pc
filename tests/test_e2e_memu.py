"""End-to-end tests for memU integration + DB persistence + scope isolation.

No network — uses an in-memory SQLite for both app DB and memU DB, plus a
fake embedding client registered into memU's ClientPool.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Iterator

import db
import pytest

from memu.app import MemoryService


# ---- fixtures ----

@pytest.fixture
def tmp_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Force config to point at a fresh per-test directory."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.sqlite3"))
    monkeypatch.setenv("MEMU_DB_PATH", str(tmp_path / "memu.db"))
    from config import get_settings
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def memu_service(tmp_data_dir: Path) -> MemoryService:
    """Build a memU service with a fake embedding client (no network)."""
    from memu_setup import build_memory_service

    svc = build_memory_service()

    class FakeEmbedding:
        embed_model = "fake"

        async def embed(self, inputs):
            return [[float(len(t))] for t in inputs], None

    svc._embedding_pool._cache["default"] = FakeEmbedding()
    svc._embedding_pool._cache["embedding"] = FakeEmbedding()
    return svc


# ---- DB layer ----

def test_db_round_trip(tmp_data_dir: Path):
    cid = db.create_character(
        user_id="alice",
        name="Sora",
        spec="v2",
        card={"name": "Sora", "spec": "chara_card_v2"},
        avatar_path=None,
    )[0]
    rows = db.list_characters("alice")
    assert any(r["id"] == cid for r in rows)

    # Append + retrieve messages
    mid = db.append_message(
        user_id="alice", character_id=cid, role="user", content="hi"
    )
    msgs = db.recent_messages(user_id="alice", character_id=cid, limit=10)
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"

    # Delete character
    assert db.delete_character(cid, "alice")
    assert not db.list_characters("alice")


# ---- memU integration ----

@pytest.mark.asyncio
async def test_memu_commit_and_retrieve_isolated_by_character(memu_service: MemoryService):
    svc = memu_service
    # Alice chats with character "Sora" about coffee
    await svc.commit_results(
        recall_files=[{
            "name": "conv1",
            "track": "memory",
            "description": "coffee chat",
            "content": "user likes iced coffee\nSora knows about brewing",
        }],
        user={"user_id": "alice", "character_id": "sora"},
    )
    # Bob chats with the same character "Sora" about tea
    await svc.commit_results(
        recall_files=[{
            "name": "conv2",
            "track": "memory",
            "description": "tea chat",
            "content": "user prefers green tea\nSora recommends oolong",
        }],
        user={"user_id": "bob", "character_id": "sora"},
    )
    # Different character for alice (cross-character isolation)
    await svc.commit_results(
        recall_files=[{
            "name": "conv3",
            "track": "memory",
            "description": "coding chat",
            "content": "user likes vim\ncoding with Sora-the-coder",
        }],
        user={"user_id": "alice", "character_id": "coder-sora"},
    )

    # Alice + Sora should only see coffee
    r = await svc.progressive_retrieve(
        "iced coffee", where={"user_id": "alice", "character_id": "sora"}
    )
    texts = [s["text"] for s in r["segments"]]
    assert any("iced coffee" in t for t in texts)
    assert all("vim" not in t for t in texts), "leaked cross-character memory"
    assert all("green tea" not in t for t in texts), "leaked cross-user memory"

    # Bob + Sora should only see tea
    r = await svc.progressive_retrieve(
        "green tea", where={"user_id": "bob", "character_id": "sora"}
    )
    texts = [s["text"] for s in r["segments"]]
    assert any("green tea" in t for t in texts)
    assert all("iced coffee" not in t for t in texts), "leaked cross-user memory"


@pytest.mark.asyncio
async def test_memu_rejects_unknown_scope_field(memu_service: MemoryService):
    svc = memu_service
    with pytest.raises(ValueError, match="Unknown filter field"):
        await svc.list_all_recall_files(where={"nonexistent_field": "x"})