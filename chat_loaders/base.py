"""Common types + auto-detection for chat history formats."""
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "assistant", "system"]


@dataclass
class NormalizedMessage:
    """One chat message after format normalisation.

    The role mapping follows "I'm the user, the other person is the assistant":
    this is the semantic that an AI-girlfriend app needs to replay the chat.
    """

    role: Role
    content: str
    timestamp: str            # ISO 8601, with timezone (UTC recommended)
    sender_name: str | None = None
    message_type: str = "text"  # text|image|video|sticker|file|system|recalled|other
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatSegment:
    """One conversational segment parsed out of an export file.

    A single export file can contain many conversations; the loader decides
    how to slice them. For private chats this is a 1:1 mapping.
    """

    chat_id: str
    chat_type: Literal["private", "group", "channel", "unknown"]
    title: str
    my_user_id: str | None
    participants: list[str] = field(default_factory=list)
    messages: list[NormalizedMessage] = field(default_factory=list)


def _load_json(path_or_bytes) -> Any:
    """Accept either a file path or raw bytes; tolerate utf-8 / gbk encoding."""
    if isinstance(path_or_bytes, (str, bytes)):
        if isinstance(path_or_bytes, bytes):
            raw = path_or_bytes
        else:
            with open(path_or_bytes, "rb") as fh:
                raw = fh.read()
    else:
        raw = path_or_bytes

    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError("chat file is neither valid UTF-8 nor GBK JSON")


def detect_format(raw: bytes) -> str | None:
    """Best-effort sniff of the export format. Returns one of the keys in
    ``LOADERS`` or ``None`` if unrecognised.

    The order of checks matters because some formats overlap (e.g. Telegram
    Desktop's wrapper contains a top-level ``chats`` key).
    """
    try:
        text = raw.decode("utf-8-sig", errors="replace")
        obj = json.loads(text)
    except Exception:
        return None

    # Telegram Desktop: top-level `chats.list[*].messages[*]`
    if isinstance(obj, dict) and isinstance(obj.get("chats"), dict):
        if isinstance(obj["chats"].get("list"), list):
            if any(
                isinstance(c, dict) and "messages" in c for c in obj["chats"]["list"]
            ):
                return "telegram"

    # QQChatExporter v2: top-level `chatInfo` + `messages`
    if isinstance(obj, dict) and "chatInfo" in obj and "messages" in obj:
        return "qq_chat_exporter"

    # QQChatExporter v1: top-level `self` + `peer` + `messages`
    if isinstance(obj, dict) and {"self", "peer", "messages"} <= obj.keys():
        return "qq_chat_exporter"

    # ChatGPT export: array of conversations with a `mapping` tree
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        first = obj[0]
        if "mapping" in first and "chat_messages" not in first:
            return "chatgpt"
        # Claude export: array of conversations with `chat_messages` list
        if "chat_messages" in first and isinstance(first["chat_messages"], list):
            return "claude"

    # WeChat: distinguish by shape — object with `chat_records` or messages
    # whose first item has `senderName`/`wxid` or `sender`+`type`
    if isinstance(obj, dict):
        if "chat_records" in obj and isinstance(obj["chat_records"], list):
            return "wechat"
        msgs = obj.get("messages")
        if isinstance(msgs, list) and msgs and isinstance(msgs[0], dict):
            sample = msgs[0]
            if any(k in sample for k in ("senderName", "wxid", "sender_id")):
                return "wechat"
            if "type" in sample and "sender" in sample and "content" in sample:
                return "wechat"
        if any(k in obj for k in ("wechat", "wxid", "chat_name")):
            return "wechat"

    # Generic: top-level `messages` is a list of {role|content|timestamp?}
    if isinstance(obj, dict) and isinstance(obj.get("messages"), list):
        msgs = obj["messages"]
        if msgs and isinstance(msgs[0], dict) and (
            "role" in msgs[0] or "sender" in msgs[0] or "from" in msgs[0]
        ):
            return "generic"

    # Single-segment fallback: top-level list of message dicts
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return "generic"

    return None


def iter_messages(path_or_bytes, *, format_key: str | None = None) -> Iterator[ChatSegment]:
    """Top-level dispatcher: detect (or accept) format, yield segments.

    Raises ``ValueError`` if the format cannot be determined.
    """
    if isinstance(path_or_bytes, str):
        with open(path_or_bytes, "rb") as fh:
            raw = fh.read()
    elif isinstance(path_or_bytes, bytes):
        raw = path_or_bytes
    else:
        raw = path_or_bytes

    fmt = format_key or detect_format(raw)
    if fmt is None:
        raise ValueError("could not detect chat format; please pick one explicitly")
    # Late import to avoid circular dependency at module load time.
    from . import LOADERS
    yield from LOADERS[fmt]().iter_segments(raw)