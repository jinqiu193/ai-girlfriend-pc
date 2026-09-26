"""Tests for QQChatExporter v2 format (chatInfo + content.text)."""
from __future__ import annotations

import json

from chat_loaders import detect_format, iter_messages


def _make_v2(messages=None):
    return {
        "metadata": {"name": "QQChatExporter", "version": "0.1.0"},
        "chatInfo": {
            "name": "李宥恩",
            "type": "private",
            "selfUid": "u_self",
            "selfUin": "123",
            "selfName": "沙白",
            "peerUid": "u_peer",
            "peerUin": "456",
        },
        "messages": messages or [],
    }


def _msg(seq, sender_uid, text, mtype="text", system=False, recalled=False):
    return {
        "seq": str(seq),
        "time": f"2026-08-11T14:2{seq}:00.000Z",
        "sender": {"uid": sender_uid, "name": sender_uid, "nickname": sender_uid},
        "type": mtype,
        "content": {"text": text},
        "system": system,
        "recalled": recalled,
    }


def test_v2_detect_format():
    raw = json.dumps(_make_v2(), ensure_ascii=False).encode("utf-8")
    assert detect_format(raw) == "qq_chat_exporter"


def test_v2_parse_basic():
    data = _make_v2([
        _msg(1, "u_peer", "[Markdown消息]你好呀"),
        _msg(2, "u_self", "你好"),
    ])
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    segs = list(iter_messages(raw))
    assert len(segs) == 1
    seg = segs[0]
    assert seg.title == "李宥恩"
    assert len(seg.messages) == 2


def test_v2_strips_markdown_prefix():
    data = _make_v2([_msg(1, "u_peer", "[Markdown消息]刚落地，还在适应")])
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    seg = list(iter_messages(raw))[0]
    assert seg.messages[0].content == "刚落地，还在适应"


def test_v2_role_mapping():
    data = _make_v2([
        _msg(1, "u_peer", "你好"),
        _msg(2, "u_self", "嗨"),
    ])
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    seg = list(iter_messages(raw))[0]
    assert seg.messages[0].role == "assistant"
    assert seg.messages[1].role == "user"


def test_v2_skips_system_messages():
    data = _make_v2([
        _msg(1, "u_peer", "你好"),
        _msg(2, "unknown", "系统提示", system=True),
        _msg(3, "u_self", "嗨"),
    ])
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    seg = list(iter_messages(raw))[0]
    assert len(seg.messages) == 2


def test_v2_skips_recalled_messages():
    data = _make_v2([
        _msg(1, "u_peer", "你好"),
        _msg(2, "u_peer", "撤回的消息", recalled=True),
    ])
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    seg = list(iter_messages(raw))[0]
    assert len(seg.messages) == 1


def test_v2_participants():
    data = _make_v2([_msg(1, "u_peer", "你好")])
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    seg = list(iter_messages(raw))[0]
    assert "沙白" in seg.participants
    assert "李宥恩" in seg.participants