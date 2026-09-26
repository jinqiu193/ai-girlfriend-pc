"""Claude.ai export JSON loader.

Accepts ``conversations.json`` from claude.ai's "Export conversations" feature.
Each conversation looks like::

    {
      "uuid": "...",
      "name": "...",
      "created_at": "2025-08-01T12:34:56.000Z",
      "updated_at": "...",
      "chat_messages": [
        {"sender": "human" | "assistant" | "system",
         "content": [{"type": "text", "text": "..."}, ...]},
        ...
      ]
    }
"""
from __future__ import annotations

from typing import Any

from .base import ChatSegment, NormalizedMessage


_SENDER_MAP = {
    "human": "user",
    "user": "user",
    "assistant": "assistant",
    "claude": "assistant",
    "system": "system",
}


class ClaudeLoader:
    key = "claude"

    def iter_segments(self, raw: bytes) -> list[ChatSegment]:
        import json

        for enc in ("utf-8", "utf-8-sig"):
            try:
                obj = json.loads(raw.decode(enc))
                break
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        else:
            raise ValueError("Claude JSON is not valid UTF-8")

        if not isinstance(obj, list):
            raise ValueError("Claude export root must be a JSON array")

        segments: list[ChatSegment] = []
        for idx, conv in enumerate(obj):
            if not isinstance(conv, dict):
                continue
            seg = self._parse_conv(conv, idx)
            if seg.messages:
                segments.append(seg)
        return segments

    def _parse_conv(self, conv: dict[str, Any], idx: int) -> ChatSegment:
        title = conv.get("name") or conv.get("title") or f"Claude 对话 #{idx + 1}"
        created_at = conv.get("created_at") or ""
        chat_messages = conv.get("chat_messages") or []

        seg = ChatSegment(
            chat_id=conv.get("uuid") or title,
            chat_type="private",
            title=title,
            my_user_id=None,
        )

        for m in chat_messages:
            if not isinstance(m, dict):
                continue
            sender = (m.get("sender") or "").lower()
            role = _SENDER_MAP.get(sender)
            if role is None:
                continue

            content_obj = m.get("content")
            text = self._extract_text(content_obj)
            if not text.strip():
                continue
            ts = m.get("created_at") or created_at
            seg.messages.append(
                NormalizedMessage(
                    role=role,
                    content=text.strip(),
                    timestamp=str(ts or ""),
                    sender_name=sender,
                    message_type="text",
                )
            )
        return seg

    @staticmethod
    def _extract_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    if isinstance(item, str):
                        parts.append(item)
                    continue
                t = item.get("type")
                if t in {"text", None}:
                    txt = item.get("text") or item.get("content") or ""
                    if isinstance(txt, str):
                        parts.append(txt)
            return "\n".join(parts)
        if isinstance(content, dict):
            return str(content.get("text") or content.get("content") or "")
        return ""