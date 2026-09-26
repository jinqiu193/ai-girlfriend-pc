"""Telegram Desktop JSON export parser.

Reference: https://telegram.org/blog/export-and-backup
Top-level shape::

    {
      "personal_info": {"user_id": "..."},
      "chats": {
        "list": [
          {
            "name": "...",
            "type": "personal_chat" | "private_group" | "public_supergroup" | "channel",
            "id": <int>,
            "messages": [
              {
                "id": <int>,
                "type": "message" | "service",
                "date": "2024-05-12T13:33:05Z",
                "date_unixtime": "1715559185",
                "from": "Alice",
                "from_id": "user1234567890",
                "text": <str OR list-of-entities>,   # see below
                "text_entities": <list-of-entities>, # optional, newer format
                "media_type": "photo" | "sticker" | ...,
                "reply_to_message_id": <int> | null,
                "forwarded_from": "..." (optional)
              }
            ]
          }
        ]
      }
    }

We treat each chat as one ChatSegment. Private chats are kept; groups
emit a `chat_type="group"` segment so the caller can decide whether to
import them.
"""
from __future__ import annotations

import json
from typing import Any

from .base import ChatSegment, NormalizedMessage


class TelegramDesktopLoader:
    key = "telegram"

    def iter_segments(self, raw: bytes) -> list[ChatSegment]:
        obj = json.loads(raw.decode("utf-8-sig"))
        my_id = (obj.get("personal_info") or {}).get("user_id")

        out: list[ChatSegment] = []
        for chat in (obj.get("chats") or {}).get("list") or []:
            seg = self._parse_chat(chat, my_id)
            if seg is not None:
                out.append(seg)
        return out

    def _parse_chat(self, chat: dict[str, Any], my_id: str | None) -> ChatSegment | None:
        ctype = chat.get("type", "unknown")
        if ctype == "channel":
            # Channels broadcast; nothing useful for a personal AI character.
            return None

        seg = ChatSegment(
            chat_id=str(chat.get("id", chat.get("name", "unknown"))),
            chat_type="private" if ctype == "personal_chat" else "group",
            title=chat.get("name", ""),
            my_user_id=my_id,
        )

        for m in chat.get("messages") or []:
            nm = self._parse_message(m, my_id)
            if nm is not None:
                seg.messages.append(nm)

        # Participants = distinct senders we observed
        seen = set()
        for m in seg.messages:
            if m.sender_name and m.sender_name not in seen:
                seen.add(m.sender_name)
                seg.participants.append(m.sender_name)

        return seg

    def _parse_message(self, m: dict[str, Any], my_id: str | None) -> NormalizedMessage | None:
        if m.get("type") == "service":
            return NormalizedMessage(
                role="system",
                content=str(m.get("action", "(service message)")),
                timestamp=_iso(m),
                sender_name=m.get("actor"),
                message_type="system",
                extras={"action": m.get("action")},
            )

        text = _text_to_string(m.get("text"), m.get("text_entities"))
        if not text.strip():
            return None  # skip empty / deleted-only messages

        from_id = m.get("from_id")
        is_self = bool(my_id) and from_id == my_id

        media_type = m.get("media_type")
        mtype = _media_to_message_type(media_type)

        return NormalizedMessage(
            role="user" if is_self else "assistant",
            content=text,
            timestamp=_iso(m),
            sender_name=m.get("from"),
            message_type=mtype,
            extras={
                "msg_id": m.get("id"),
                "reply_to": m.get("reply_to_message_id"),
                "forwarded_from": m.get("forwarded_from"),
            },
        )


def _iso(m: dict[str, Any]) -> str:
    """Return ISO 8601 timestamp, preferring ``date`` field then unixtime."""
    date = m.get("date")
    if date:
        return date
    ut = m.get("date_unixtime")
    if ut:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(int(ut), tz=timezone.utc).isoformat()
    return ""


def _text_to_string(text: Any, entities: list[dict[str, Any]] | None) -> str:
    """Telegram text can be either a plain string (newer format) or a
    list of {type, text} entities (older format). Normalise to plain
    text, preserving newlines and emoji."""

    if text is None:
        return ""
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        parts: list[str] = []
        for ent in text:
            if isinstance(ent, dict):
                parts.append(ent.get("text", ""))
            else:
                parts.append(str(ent))
        return "".join(parts)

    # Newer format with separate `text_entities`: just use the raw string
    return str(text)


def _media_to_message_type(media_type: str | None) -> str:
    return {
        None: "text",
        "photo": "image",
        "sticker": "sticker",
        "video": "video",
        "voice": "voice",
        "animation": "sticker",
        "video_note": "video",
        "document": "file",
        "audio": "file",
    }.get(media_type, "other")