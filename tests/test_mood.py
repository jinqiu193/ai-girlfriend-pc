"""Tests for the mood (emotional state) system."""
from __future__ import annotations


from pathlib import Path

import pytest

import db
import mood


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


def test_apply_idle_decay_increases_miss_and_bored():
    m = {"happy": 80, "miss": 10, "excited": 60, "bored": 5, "jealous": 0, "annoyed": 0}
    result = mood.apply_idle_decay(m, idle_minutes=60)
    assert result["miss"] > m["miss"]
    assert result["bored"] > m["bored"]
    assert result["happy"] < m["happy"]
    assert result["excited"] < m["excited"]


def test_apply_idle_decay_zero_minutes_is_noop():
    m = {"happy": 50, "miss": 20, "excited": 30, "bored": 10, "jealous": 0, "annoyed": 0}
    result = mood.apply_idle_decay(m, idle_minutes=0)
    assert result == m


def test_apply_idle_decay_caps_at_100():
    m = {"happy": 50, "miss": 90, "excited": 30, "bored": 90, "jealous": 0, "annoyed": 0}
    result = mood.apply_idle_decay(m, idle_minutes=100)
    assert result["miss"] <= 100
    assert result["bored"] <= 100



def test_render_mood_prompt_with_description():
    m = {"happy": 80, "miss": 72, "jealous": 0, "annoyed": 0, "excited": 20, "bored": 10, "description": "你很开心"}
    prompt = mood.render_mood_prompt(m)
    assert "你当前的情绪状态" in prompt
    assert "你很开心" in prompt
    assert "很开心" in prompt
    assert "很想念" in prompt


def test_render_mood_prompt_empty_returns_empty_string():
    m = {"happy": 0, "miss": 0, "jealous": 0, "annoyed": 0, "excited": 0, "bored": 0, "description": ""}
    prompt = mood.render_mood_prompt(m)
    assert prompt == ""


# ---------- DB storage ----------

def test_db_mood_upsert_and_get(tmp_data_dir):
    cid = db.create_character(
        user_id="alice", name="Sora", spec="v2",
        card={"name": "Sora"}, avatar_path=None,
    )[0]
    assert db.get_mood(user_id="alice", character_id=cid) is None

    mood_data = {"happy": 70, "miss": 40, "jealous": 0, "annoyed": 5, "excited": 30, "bored": 10, "description": "你有点开心"}
    db.upsert_mood(user_id="alice", character_id=cid, mood=mood_data)

    result = db.get_mood(user_id="alice", character_id=cid)
    assert result is not None
    assert result["mood"]["happy"] == 70
    assert result["mood"]["description"] == "你有点开心"
    assert "updated_at" in result


def test_db_mood_upsert_overwrites(tmp_data_dir):
    cid = db.create_character(
        user_id="alice", name="Sora", spec="v2",
        card={"name": "Sora"}, avatar_path=None,
    )[0]
    db.upsert_mood(user_id="alice", character_id=cid, mood={"happy": 50, "description": "first"})
    db.upsert_mood(user_id="alice", character_id=cid, mood={"happy": 80, "description": "second"})
    result = db.get_mood(user_id="alice", character_id=cid)
    assert result["mood"]["happy"] == 80
    assert result["mood"]["description"] == "second"

