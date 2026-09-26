"""WeChat / 微信 chat history JSON loader.

Accepts a few common shapes produced by third-party WeChat exporters:

1. ``{"messages": [...]}`` where each message has::

       {"sender": str, "content": str, "time": "YYYY-MM-DD HH:MM:SS",
        "type": "text" | "image" | ...}

2. ``[{"from": str, "text": str, "timestamp": "..."}, ...]`` (plain array)

3. ``{"chat_records": [{"senderName": "...", "content": "...", ...}]}``
   (some Android exporters)
"""
from __future__ import annotations

from typing import Any

from .base import ChatSegment, NormalizedMessage


_TYPE_MAP = {
    "text": "text",
    "image": "image",
    "img": "image",
    "video": "video",
    "audio": "audio",
    "voice": "audio",
    "emoji": "sticker",
    "sticker": "sticker",
    "file": "file",
    "system": "system",
    "recall": "recalled",
}


def _map_msgtype(raw: Any) -> str:
    if raw is None:
        return "text"
    s = str(raw).strip().lower()
    return _TYPE_MAP.get(s, "other")


class WeChatLoader:
    key = "wechat"

    def iter_segments(self, raw: bytes) -> list[ChatSegment]:
        import json

        for enc in ("utf-8", "utf-8-sig", "gbk"):
            try:
                obj = json.loads(raw.decode(enc))
                break
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        else:
            raise ValueError("WeChat JSON is neither UTF-8 nor GBK")

        # normalise to a list of raw message dicts
        if isinstance(obj, dict):
            msgs = (
                obj.get("messages")
                or obj.get("chat_records")
                or obj.get("record")
                or obj.get("data")
                or []
            )
            title = (
                obj.get("chat_name")
                or obj.get("title")
                or obj.get("nickname")
                or obj.get("name")
                or "WeChat 对话"
            )
            my_user_id = obj.get("me_id") or obj.get("my_id")
            my_name = obj.get("me_name") or obj.get("my_name")
        elif isinstance(obj, list):
            msgs = obj
            title = "WeChat 对话"
            my_user_id = None
            my_name = None
        else:
            raise ValueError("WeChat JSON root must be a dict or list")

        seg = ChatSegment(
            chat_id=title,
            chat_type="private",
            title=title,
            my_user_id=my_user_id,
        )

        for m in msgs:
            if not isinstance(m, dict):
                continue
            nm = self._parse(m, my_user_id, my_name)
            if nm is not None:
                seg.messages.append(nm)

        seen: set[str] = set()
        for m in seg.messages:
            if m.sender_name and m.sender_name not in seen:
                seen.add(m.sender_name)
                seg.participants.append(m.sender_name)
        return [seg]

    def _parse(
        self, m: dict[str, Any], my_id: str | None, my_name: str | None
    ) -> NormalizedMessage | None:
        content = (
            m.get("content")
            or m.get("text")
            or m.get("msg")
            or m.get("message")
            or ""
        )
        content = str(content).strip()
        if not content:
            return None

        sender = (
            m.get("sender")
            or m.get("from")
            or m.get("senderName")
            or m.get("displayName")
            or m.get("name")
        )
        sender_id = (
            m.get("sender_id")
            or m.get("from_id")
            or m.get("wxid")
            or m.get("user_id")
        )
        ts = (
            m.get("time")
            or m.get("timestamp")
            or m.get("ts")
            or m.get("createTime")
        )

        # role: "user" if sender matches me, otherwise "assistant".
        # If me is not declared we still try to disambiguate using common
        # Chinese self-references ("我", "我本人", "me", "self").
        if sender_id and my_id and sender_id == my_id:
            role = "user"
        elif sender and my_name and sender == my_name:
            role = "user"
        elif sender and sender.lower() in {"me", "self", "我", "本人"}:
            role = "user"
        elif sender:
            role = "assistant"
        else:
            role = "user"  # no sender info → assume human (defensive default)

        return NormalizedMessage(
            role=role,
            content=content,
            timestamp=str(ts or ""),
            sender_name=sender,
            message_type=_map_msgtype(m.get("type") or m.get("msgType") or m.get("sub_type")),
        )

        return NormalizedMessage(
            role=role,
            content=content,
            timestamp=str(ts or ""),
            sender_name=sender,
            message_type=_map_msgtype(m.get("type") or m.get("msgType") or m.get("sub_type")),
        )