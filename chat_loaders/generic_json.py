"""Generic JSON chat loader.

Accepts either::

    {"messages": [{"role": "...", "content": "...", "sender": "...", "timestamp": "..."}]}

or a bare ``[{...}, {...}]`` array of the same shape.

`role` is mapped:
  - "user" / "human" / "self"   -> "user"
  - "assistant" / "ai" / "other" -> "assistant"
  - "system"                     -> "system"

If `role` is missing but `sender` matches the segment's `my_name`, it is
mapped to "user"; everything else goes to "assistant". The first non-system
sender that isn't "me" becomes the peer's display name.
"""
from __future__ import annotations

import json
from typing import Any

from .base import ChatSegment, NormalizedMessage


_ROLE_MAP: dict[str, str] = {
    "user": "user",
    "human": "user",
    "self": "user",
    "me": "user",
    "assistant": "assistant",
    "ai": "assistant",
    "bot": "assistant",
    "other": "assistant",
    "peer": "assistant",
    "them": "assistant",
    "system": "system",
    "service": "system",
}


class GenericJSONLoader:
    key = "generic"

    def iter_segments(self, raw: bytes) -> list[ChatSegment]:
        for enc in ("utf-8", "utf-8-sig", "gbk"):
            try:
                obj = json.loads(raw.decode(enc))
                break
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        else:
            raise ValueError("Generic JSON is neither UTF-8 nor GBK")

        if isinstance(obj, dict):
            messages = obj.get("messages") or []
            seg_title = obj.get("title") or obj.get("name") or "对话"
            my_user_id = obj.get("my_user_id") or obj.get("my_id")
            my_name = obj.get("my_name")
            chat_type = obj.get("chat_type", "private")
        elif isinstance(obj, list):
            messages = obj
            seg_title = "对话"
            my_user_id = None
            my_name = None
            chat_type = "private"
        else:
            raise ValueError("Generic JSON must be a dict with 'messages' or a list")

        seg = ChatSegment(
            chat_id=seg_title,
            chat_type=chat_type if chat_type in ("private", "group", "channel") else "private",
            title=seg_title,
            my_user_id=my_user_id,
        )

        for m in messages:
            nm = self._parse_message(m, my_user_id, my_name)
            if nm is not None:
                seg.messages.append(nm)

        seen = set()
        for m in seg.messages:
            if m.sender_name and m.sender_name not in seen:
                seen.add(m.sender_name)
                seg.participants.append(m.sender_name)

        return [seg]

    def _parse_message(
        self, m: dict[str, Any], my_id: str | None, my_name: str | None
    ) -> NormalizedMessage | None:
        content = (m.get("content") or m.get("text") or "").strip()
        if not content:
            return None

        raw_role = (m.get("role") or m.get("type") or "").lower()
        role = _ROLE_MAP.get(raw_role)

        sender = m.get("sender") or m.get("from") or m.get("name")
        sender_id = m.get("sender_id") or m.get("from_id")

        if role is None:
            # Infer from identity
            if (my_id and sender_id == my_id) or (my_name and sender == my_name):
                role = "user"
            elif sender:
                role = "assistant"
            else:
                role = "user"  # default

        return NormalizedMessage(
            role=role,
            content=content,
            timestamp=str(m.get("timestamp") or m.get("time") or ""),
            sender_name=sender,
            message_type=m.get("message_type", "text"),
        )