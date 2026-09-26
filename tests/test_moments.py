"""Tests for the AI moments (朋友圈) system."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import db
import moments


@pytest.fixture
def tmp_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.sqlite3"))
    monkeypatch.setenv("MEMU_DB_PATH", str(tmp_path / "memu.db"))
    from config import get_settings
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


# ---------- DB storage ----------

def test_db_moment_add_and_list(tmp_data_dir):
    cid = db.create_character(
        user_id="alice", name="Sora", spec="v2",
        card={"name": "Sora"}, avatar_path=None,
    )[0]
    db.add_moment(user_id="alice", character_id=cid, content="今天去逛街了")
    db.add_moment(user_id="alice", character_id=cid, content="刚吃完晚饭")
    moms = db.list_moments(user_id="alice", character_id=cid)
    assert len(moms) == 2
    assert moms[0]["content"] == "刚吃完晚饭"


def test_db_last_moment_time_none(tmp_data_dir):
    cid = db.create_character(
        user_id="alice", name="Sora", spec="v2",
        card={"name": "Sora"}, avatar_path=None,
    )[0]
    assert db.last_moment_time(user_id="alice", character_id=cid) is None


def test_db_messages_since(tmp_data_dir):
    cid = db.create_character(
        user_id="alice", name="Sora", spec="v2",
        card={"name": "Sora"}, avatar_path=None,
    )[0]
    db.append_message(user_id="alice", character_id=cid, role="user", content="早安")
    db.append_message(user_id="alice", character_id=cid, role="assistant", content="早呀")
    since = datetime.now() - timedelta(hours=1)
    msgs = db.messages_since(user_id="alice", character_id=cid, since=since)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"


def test_db_messages_since_excludes_old(tmp_data_dir):
    cid = db.create_character(
        user_id="alice", name="Sora", spec="v2",
        card={"name": "Sora"}, avatar_path=None,
    )[0]
    db.append_message(user_id="alice", character_id=cid, role="user", content="旧消息")
    since = datetime.now() + timedelta(hours=1)
    msgs = db.messages_since(user_id="alice", character_id=cid, since=since)
    assert len(msgs) == 0


# ---------- generate_moment (with mocked LLM) ----------

@pytest.mark.asyncio
async def test_generate_moment_post_true(monkeypatch):
    fake_response = json.dumps({"post": True, "content": "今天和他聊了很久，好开心呀"})

    async def fake_call_once(messages, **kwargs):
        return fake_response

    monkeypatch.setattr(moments, "call_once", fake_call_once)
    result = await moments.generate_moment(
        card={"name": "三七", "personality": "温柔"},
        today_messages=[{"role": "user", "content": "今天聊了很多"}],
    )
    assert "开心" in result


@pytest.mark.asyncio
async def test_generate_moment_post_false(monkeypatch):
    fake_response = json.dumps({"post": False})

    async def fake_call_once(messages, **kwargs):
        return fake_response

    monkeypatch.setattr(moments, "call_once", fake_call_once)
    result = await moments.generate_moment(
        card={"name": "三七", "personality": "温柔"},
        today_messages=[{"role": "user", "content": "嗯"}],
    )
    assert result == ""


@pytest.mark.asyncio
async def test_generate_moment_failure_returns_empty(monkeypatch):
    async def fake_call_once(messages, **kwargs):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(moments, "call_once", fake_call_once)
    result = await moments.generate_moment(card={"name": "三七"})
    assert result == ""
