"""Tests for the event memory system."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import db
import events


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

def test_render_events_prompt_with_due_event():
    evs = [{"id": "1", "event_text": "面试", "event_date": "2020-01-01"}]
    prompt = events.render_events_prompt(evs)
    assert "你记得用户提到的事" in prompt
    assert "面试" in prompt


def test_render_events_prompt_empty_returns_empty_string():
    assert events.render_events_prompt([]) == ""


# ---------- DB storage ----------

def test_db_event_add_and_list(tmp_data_dir):
    cid = db.create_character(
        user_id="alice", name="Sora", spec="v2",
        card={"name": "Sora"}, avatar_path=None,
    )[0]
    db.add_event(user_id="alice", character_id=cid, event_text="面试", event_date="2020-01-01")
    pending = db.list_pending_events(user_id="alice", character_id=cid)
    assert len(pending) == 1
    assert pending[0]["event_text"] == "面试"


def test_db_event_mark_asked(tmp_data_dir):
    cid = db.create_character(
        user_id="alice", name="Sora", spec="v2",
        card={"name": "Sora"}, avatar_path=None,
    )[0]
    eid = db.add_event(user_id="alice", character_id=cid, event_text="考试", event_date="2020-01-01")
    db.mark_events_asked(event_ids=[eid])
    pending = db.list_pending_events(user_id="alice", character_id=cid)
    assert len(pending) == 0


def test_db_list_all_events(tmp_data_dir):
    cid = db.create_character(
        user_id="alice", name="Sora", spec="v2",
        card={"name": "Sora"}, avatar_path=None,
    )[0]
    db.add_event(user_id="alice", character_id=cid, event_text="面试", event_date="2020-01-01")
    db.add_event(user_id="alice", character_id=cid, event_text="生日", event_date=None)
    all_events = db.list_all_events(user_id="alice", character_id=cid)
    assert len(all_events) == 2


def test_db_future_event_not_pending(tmp_data_dir):
    """Events with a future date should not be listed as pending."""
    from datetime import datetime, timedelta
    future = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    cid = db.create_character(
        user_id="alice", name="Sora", spec="v2",
        card={"name": "Sora"}, avatar_path=None,
    )[0]
    db.add_event(user_id="alice", character_id=cid, event_text="远期考试", event_date=future)
    pending = db.list_pending_events(user_id="alice", character_id=cid)
    assert len(pending) == 0


# ---------- extract_events (with mocked LLM) ----------

@pytest.mark.asyncio
async def test_extract_events_success(monkeypatch):
    fake_response = json.dumps({
        "events": [{"text": "面试", "date": "2026-09-01"}]
    })

    async def fake_call_once(messages, **kwargs):
        return fake_response

    monkeypatch.setattr(events, "call_once", fake_call_once)
    result = await events.extract_events(user_msg="明天我有面试", ai_reply="加油呀")
    assert len(result) == 1
    assert result[0]["text"] == "面试"


@pytest.mark.asyncio
async def test_extract_events_no_events(monkeypatch):
    async def fake_call_once(messages, **kwargs):
        return '{"events": []}'

    monkeypatch.setattr(events, "call_once", fake_call_once)
    result = await events.extract_events(user_msg="你好", ai_reply="你好呀")
    assert result == []


@pytest.mark.asyncio
async def test_extract_events_failure_returns_empty(monkeypatch):
    async def fake_call_once(messages, **kwargs):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(events, "call_once", fake_call_once)
    result = await events.extract_events(user_msg="hi", ai_reply="hello")
    assert result == []