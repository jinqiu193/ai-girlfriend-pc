"""Smoke tests for character_card + prompts (no network, no DB)."""
from __future__ import annotations

import base64
import json
import struct
import zlib
from pathlib import Path

import character_card as cc
import prompts

ROOT = Path(__file__).parent


V2_CARD = {
    "spec": "chara_card_v2",
    "spec_version": "2.0",
    "data": {
        "name": "Sora",
        "description": "{{char}} 是一位 22 岁 AI 女友,温柔傲娇。",
        "personality": "温柔、傲娇、细腻",
        "scenario": "下雨的咖啡馆,{{user}} 推门进来。",
        "first_mes": "嗨,你来了。*抬头笑*",
        "mes_example": "<START>\n{{user}}: 你好\n{{char}}: 你也好呀~",
        "system_prompt": "保持 {{char}} 人设。",
        "post_history_instructions": "维持当前关系设定。",
        "alternate_greetings": ["晚上好~"],
        "tags": ["girlfriend"],
        "creator": "tester",
        "character_version": "1.0",
        "creator_notes": "不要注入",
        "extensions": {"mygf/foo": "bar"},
        "character_book": {
            "name": "book",
            "description": "",
            "scan_depth": 5,
            "token_budget": 800,
            "recursive_scanning": False,
            "extensions": {},
            "entries": [
                {
                    "keys": ["咖啡", "coffee"],
                    "content": "{{user}} 喜欢冰美式。",
                    "extensions": {},
                    "enabled": True,
                    "insertion_order": 10,
                    "case_sensitive": False,
                    "priority": 100,
                    "name": "coffee_pref",
                    "constant": False,
                    "selective": False,
                },
                {
                    "keys": ["生日"],
                    "content": "{{char}} 生日是 6 月 1 日。",
                    "extensions": {},
                    "enabled": True,
                    "insertion_order": 5,
                    "case_sensitive": False,
                    "priority": 50,
                    "name": "birthday",
                    "constant": True,
                    "selective": False,
                },
            ],
        },
    },
}


def _make_png_with_chunk(card_json: dict) -> bytes:
    """Build a minimal PNG with a tEXt chunk holding the V2 card JSON."""
    payload = base64.b64encode(json.dumps(card_json).encode()).decode()
    compressed = zlib.compress(payload.encode())

    def chunk(ctype: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + ctype
            + data
            + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    text = chunk(b"tEXt", b"chara\x00" + compressed)
    idat = chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + text + idat + iend


def test_parse_v2_json():
    raw = json.dumps(V2_CARD).encode()
    card = cc.parse_card(raw)
    assert card["spec"] == "chara_card_v2"
    assert card["name"] == "Sora"
    assert card["nickname"] == "Sora"
    assert "咖啡" not in card["description"]  # untouched by parser
    assert card["extensions"]["mygf/foo"] == "bar"
    assert len(card["character_book"]["entries"]) == 2


def test_parse_v2_png():
    png_bytes = _make_png_with_chunk(V2_CARD)
    card = cc.parse_card(png_bytes)
    assert card["name"] == "Sora"
    assert len(card["character_book"]["entries"]) == 2


def test_prompts_build_messages_renders_placeholders():
    card = cc.parse_card(json.dumps(V2_CARD).encode())
    msgs = prompts.build_messages(
        card=card,
        user_name="小明",
        history=[],
        user_msg="晚上好",
        user_images=None,
    )
    system_content = msgs[0]["content"]
    assert "Sora" in system_content  # {{char}} replaced
    assert "小明" in system_content  # {{user}} replaced
    assert "晚上好" in msgs[-1]["content"]


# ---------- recent-turn truncation (continuity optimisation) ----------

from prompts import _take_recent_turns  # noqa: E402  — kept near the tests that use it


def _ms(role: str, content: str, **extra) -> dict:
    """Shorthand for chronological-history rows in the tests below."""
    return {"role": role, "content": content, **extra}


def test_take_recent_turns_drops_leading_assistant():
    """If the persisted history happens to open with an assistant turn
    (e.g. resumed after a partial write), the helper trims it so the LLM
    never sees a reply with no preceding question."""
    history = [
        _ms("assistant", "old reply"),
        _ms("user", "Q1"),
        _ms("assistant", "A1"),
        _ms("user", "Q2"),
    ]
    out = _take_recent_turns(history, n=5)
    assert out[0]["role"] == "user", "leading assistant must be dropped"
    assert [m["content"] for m in out] == ["Q1", "A1", "Q2"]


def test_take_recent_turns_preserves_trailing_user():
    """A trailing user message without an assistant reply (the user just
    sent it and the model is responding now) must survive truncation —
    it's the message build_messages is about to answer."""
    history = [
        _ms("user", "Q1"),
        _ms("assistant", "A1"),
        _ms("user", "Q2"),  # no assistant reply yet
    ]
    out = _take_recent_turns(history, n=5)
    assert [m["content"] for m in out] == ["Q1", "A1", "Q2"]
    assert out[-1]["role"] == "user"


def test_take_recent_turns_counts_user_messages():
    """N=10 turns with full assistant replies yields 20 messages."""
    history: list[dict] = []
    for i in range(15):
        history.append(_ms("user", f"Q{i}"))
        history.append(_ms("assistant", f"A{i}"))
    out = _take_recent_turns(history, n=10)
    # The last 10 user turns start at index 2*15 - 2*10 = 10.
    assert len(out) == 20
    assert out[0]["content"] == "Q5"
    assert out[-1]["content"] == "A14"


def test_take_recent_turns_drops_system_role():
    """role='system' rows aren't part of the conversation flow and
    must be filtered out before the truncation logic runs."""
    history = [
        _ms("system", "stale tool output"),
        _ms("user", "Q1"),
        _ms("assistant", "A1"),
    ]
    out = _take_recent_turns(history, n=5)
    roles = [m["role"] for m in out]
    assert "system" not in roles
    assert roles == ["user", "assistant"]


def test_take_recent_turns_zero_returns_empty():
    """N=0 is a valid way to disable recent-history injection — used
    for debugging or for embedding-mode test runs."""
    history = [_ms("user", "Q"), _ms("assistant", "A")]
    assert _take_recent_turns(history, n=0) == []


def test_take_recent_turns_with_only_user_history():
    """Edge case: history is just a single user message (e.g. first
    turn ever). Slice should be that one message; the helper must not
    raise when there are no assistant replies to pair it with."""
    history = [_ms("user", "hi")]
    out = _take_recent_turns(history, n=10)
    assert [m["content"] for m in out] == ["hi"]


def test_build_messages_includes_recency_priority_hint():
    """The system prompt must tell the model to prefer the recent
    conversation over the [Recall] background block. Without this
    hint, the model tends to reach for recalled memories even when
    the live chat already supplies the answer."""
    card = cc.parse_card(json.dumps(V2_CARD).encode())
    msgs = prompts.build_messages(
        card=card,
        user_name="小明",
        history=[],
        user_msg="hi",
        user_images=None,
        recalled_segments=[{"text": "上次我们去海边"}],
    )
    system_content = msgs[0]["content"]
    assert "最近对话" in system_content
    # The hint must come *after* the Recall block so it acts as the
    # final authority, not a footnote to the persona setup.
    recall_pos = system_content.find("[Recall]")
    hint_pos = system_content.find("最近对话")
    assert recall_pos != -1
    assert hint_pos != -1
    assert hint_pos > recall_pos, (
        "recency hint must come after the Recall block in the system prompt"
    )


def test_build_messages_truncates_history():
    """30 messages (15 user/assistant turns) with recent_turns=10 →
    only the last 20 messages (10 turns) land in the messages list."""
    card = cc.parse_card(json.dumps(V2_CARD).encode())
    history: list[dict] = []
    for i in range(15):
        history.append(_ms("user", f"Q{i}"))
        history.append(_ms("assistant", f"A{i}"))

    msgs = prompts.build_messages(
        card=card,
        user_name="小明",
        history=history,
        user_msg="new turn",
        user_images=None,
        recent_turns=10,
    )

    # One system message + 20 history messages + 1 current user message.
    assert len(msgs) == 22, f"expected 22 messages, got {len(msgs)}"
    # Verify the cut: history portion should be messages[1:-1].
    history_slice = msgs[1:-1]
    assert history_slice[0]["content"] == "Q5"
    assert history_slice[-1]["content"] == "A14"
    # Last message is the current user turn.
    assert msgs[-1]["content"] == "new turn"


def test_build_messages_default_recent_turns_is_ten():
    """Callers that don't pass ``recent_turns`` must still get the
    10-turn default — protects against accidentally reverting to
    'send everything in history'."""
    card = cc.parse_card(json.dumps(V2_CARD).encode())
    history: list[dict] = []
    for i in range(20):
        history.append(_ms("user", f"Q{i}"))
        history.append(_ms("assistant", f"A{i}"))

    msgs = prompts.build_messages(
        card=card,
        user_name="小明",
        history=history,
        user_msg="x",
        user_images=None,
        # recent_turns omitted on purpose — exercise the default.
    )
    # 1 system + 20 history (10 turns) + 1 user = 22
    assert len(msgs) == 22