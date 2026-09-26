"""Tests for chat history loaders + candidate selection + memU ingestion.

Does not require network access (no LLM call). Character card derivation
is mocked in a separate test in test_chat_import_e2e.py.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import chat_import
import db
from chat_loaders import LOADERS, detect_format, iter_messages
from chat_loaders.base import ChatSegment, NormalizedMessage


# ---- Telegram ----

TG_SAMPLE = {
    "personal_info": {"user_id": "user999"},
    "chats": {
        "list": [
            {
                "id": 1,
                "name": "Alice",
                "type": "personal_chat",
                "messages": [
                    {
                        "id": 1,
                        "type": "message",
                        "date": "2024-05-01T10:00:00Z",
                        "from": "me",
                        "from_id": "user999",
                        "text": [{"type": "plain", "text": "在吗?"}],
                    },
                    {
                        "id": 2,
                        "type": "message",
                        "date": "2024-05-01T10:00:10Z",
                        "from": "Alice",
                        "from_id": "user1",
                        "text": "在的~ 怎么啦?",
                    },
                    {
                        "id": 3,
                        "type": "message",
                        "date": "2024-05-01T10:00:20Z",
                        "from": "me",
                        "from_id": "user999",
                        "text": "想你了",
                    },
                    {
                        "id": 4,
                        "type": "service",
                        "date": "2024-05-01T10:00:25Z",
                        "action": "edit_message",
                        "actor": "Alice",
                    },
                ],
            }
        ]
    },
}


def test_telegram_detect_and_parse():
    raw = json.dumps(TG_SAMPLE).encode()
    assert detect_format(raw) == "telegram"
    segs = list(iter_messages(raw))
    assert len(segs) == 1
    seg = segs[0]
    assert seg.chat_type == "private"
    assert seg.title == "Alice"
    # service msg kept as system role
    assert any(m.role == "system" for m in seg.messages)
    # me = user role
    me = [m for m in seg.messages if m.role == "user"]
    assert me[0].content == "在吗?"
    # Alice = assistant role
    alice = [m for m in seg.messages if m.role == "assistant"]
    assert alice[0].content == "在的~ 怎么啦?"


# ---- QQChatExporter ----

QQ_SAMPLE = {
    "version": "1.0",
    "chat_type": "friend",
    "self": {"uin": "u_self", "nickname": "我"},
    "peer": {"uin": "u_her", "nickname": "小美", "remark": "宝贝"},
    "messages": [
        {
            "seq": 1,
            "time": "2024-05-01T10:00:00+08:00",
            "sender": {"uin": "u_self", "nickname": "我"},
            "type": 1,
            "text": "在吗?",
        },
        {
            "seq": 2,
            "time": "2024-05-01T10:00:10+08:00",
            "sender": {"uin": "u_her", "nickname": "小美"},
            "type": 1,
            "text": "在的~ 怎么啦?",
        },
        {
            "seq": 3,
            "time": "2024-05-01T10:00:15+08:00",
            "sender": {"uin": "u_her"},
            "type": 2,  # image
            "text": "",
        },
        {
            "seq": 4,
            "time": "2024-05-01T10:00:20+08:00",
            "sender": {"uin": "u_self"},
            "type": 1,
            "text": "想你了",
        },
    ],
}


def test_qq_detect_and_parse():
    raw = json.dumps(QQ_SAMPLE).encode()
    assert detect_format(raw) == "qq_chat_exporter"
    segs = list(iter_messages(raw))
    assert len(segs) == 1
    seg = segs[0]
    assert seg.chat_type == "private"
    assert seg.title == "宝贝"  # remark > nickname
    user_msgs = [m for m in seg.messages if m.role == "user"]
    peer_msgs = [m for m in seg.messages if m.role == "assistant"]
    assert len(user_msgs) == 2
    assert len(peer_msgs) == 2  # one text + one image placeholder


# ---- Generic ----

def test_generic_array():
    raw = json.dumps([
        {"role": "user", "content": "hi", "timestamp": "2024-05-01T10:00:00Z", "sender": "Alice"},
        {"role": "assistant", "content": "hello!", "timestamp": "2024-05-01T10:00:01Z", "sender": "Bob"},
    ]).encode()
    segs = list(iter_messages(raw))
    assert segs[0].messages[0].role == "user"
    assert segs[0].messages[1].role == "assistant"


def test_generic_object():
    raw = json.dumps({
        "title": "Test",
        "messages": [{"sender": "Alice", "content": "yo"}],
        "my_user_id": "u_self",
    }).encode()
    segs = list(iter_messages(raw))
    # Without explicit role, sender "Alice" is not "my_user_id" -> assistant
    assert segs[0].messages[0].role == "assistant"


def test_unknown_format():
    raw = json.dumps({"random": "shape"}).encode()
    assert detect_format(raw) is None


# ---- candidate selection ----

def test_pick_candidate_private():
    seg = ChatSegment(
        chat_id="x", chat_type="private", title="X", my_user_id="me",
    )
    seg.messages = [
        NormalizedMessage("user", "hi", "2024-05-01T10:00:00Z", sender_name="me"),
        NormalizedMessage("assistant", "yo", "2024-05-01T10:00:01Z", sender_name="Alice"),
        NormalizedMessage("assistant", "what's up", "2024-05-01T10:00:02Z", sender_name="Alice"),
        NormalizedMessage("assistant", "long time no see", "2024-05-01T10:00:03Z", sender_name="Bob"),
    ]
    cand = chat_import.pick_candidate(seg)
    assert cand is not None
    assert cand["name"] == "Alice"  # most-frequent peer
    assert cand["message_count"] == 3  # all assistant messages, not just Alice's
    assert "yo" in cand["sample_messages"]


def test_pick_candidate_group_returns_none():
    seg = ChatSegment(chat_id="g", chat_type="group", title="G", my_user_id="me")
    seg.messages = [NormalizedMessage("assistant", "x", "", sender_name="X")]
    assert chat_import.pick_candidate(seg) is None