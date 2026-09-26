"""Tests for the relationship type system."""
from __future__ import annotations

from pathlib import Path

import pytest

import db
import relationship


@pytest.fixture
def tmp_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.sqlite3"))
    monkeypatch.setenv("MEMU_DB_PATH", str(tmp_path / "memu.db"))
    from config import get_settings
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


# ---------- pure functions ----------

def test_render_relationship_prompt():
    prompt = relationship.render_relationship_prompt("热恋")
    assert "热恋" in prompt
    assert "你们的关系" in prompt


def test_render_relationship_prompt_unknown_uses_default():
    prompt = relationship.render_relationship_prompt("不存在的类型")
    assert "陌生人" in prompt


def test_all_types_have_descriptions():
    for name, desc in relationship.RELATIONSHIP_TYPES.items():
        assert isinstance(name, str) and len(name) > 0
        assert isinstance(desc, str) and len(desc) > 0


# ---------- DB storage ----------

def test_db_relationship_default(tmp_data_dir):
    cid = db.create_character(
        user_id="alice", name="Sora", spec="v2",
        card={"name": "Sora"}, avatar_path=None,
    )[0]
    assert db.get_relationship(user_id="alice", character_id=cid) == "陌生人"


def test_db_relationship_upsert_and_get(tmp_data_dir):
    cid = db.create_character(
        user_id="alice", name="Sora", spec="v2",
        card={"name": "Sora"}, avatar_path=None,
    )[0]
    db.upsert_relationship(user_id="alice", character_id=cid, relationship="热恋")
    assert db.get_relationship(user_id="alice", character_id=cid) == "热恋"


def test_db_relationship_overwrite(tmp_data_dir):
    cid = db.create_character(
        user_id="alice", name="Sora", spec="v2",
        card={"name": "Sora"}, avatar_path=None,
    )[0]
    db.upsert_relationship(user_id="alice", character_id=cid, relationship="朋友")
    db.upsert_relationship(user_id="alice", character_id=cid, relationship="分手")
    assert db.get_relationship(user_id="alice", character_id=cid) == "分手"