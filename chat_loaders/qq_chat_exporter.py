"""QQChatExporter JSON parser.

Top-level shape (single conversation per file)::

    {
      "version": "...",
      "exported_at": "...",
      "chat_type": "friend" | "group",
      "self":   {"uin": "...", "nickname": "我"},
      "peer":   {"uin": "...", "nickname": "她", "remark": "..."},
      "messages": [
        {
          "seq": 12345,
          "time": "2024-05-12T21:33:05+08:00",
          "sender": {"uin": "...", "nickname": "..."},
          "type": 1 | 2 | 3 | ...   # numeric enum
          "text": "..."
        }
      ]
    }

The numeric ``type`` enum varies across exporter versions, but the common
mapping used here covers the most stable values:

    1  text
    2  image
    3  file
    4  audio / voice
    5  video
    6  face / sticker
    7  red packet
    8  system / json (stickers, app shares, etc.)
    9  location
    10 video call
"""
from __future__ import annotations

import json
from typing import Any

from .base import ChatSegment, NormalizedMessage


_TYPE_MAP: dict[int, str] = {
    1: "text",
    2: "image",
    3: "file",
    4: "voice",
    5: "video",
    6: "sticker",
    7: "redpacket",
    8: "system",
    9: "location",
    10: "call",
}


class QQChatExporterLoader:
    key = "qq_chat_exporter"

    def iter_segments(self, raw: bytes) -> list[ChatSegment]:
        for enc in ("utf-8", "utf-8-sig", "gbk"):
            try:
                obj = json.loads(raw.decode(enc))
                break
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        else:
            raise ValueError("QQ export file is neither UTF-8 nor GBK JSON")

        return [self._parse(obj)]

    def _parse(self, obj: dict[str, Any]) -> ChatSegment:
        # v2 format: chatInfo + messages (newer QQChatExporter)
        if "chatInfo" in obj:
            return self._parse_v2(obj)
        # v1 format: self + peer + messages (original)
        return self._parse_v1(obj)

    def _parse_v1(self, obj: dict[str, Any]) -> ChatSegment:
        chat_type_str = obj.get("chat_type", "friend")
        if chat_type_str == "group":
            ctype = "group"
            title = (obj.get("peer") or {}).get("nickname", "群聊")
        else:
            ctype = "private"
            title = (obj.get("peer") or {}).get("remark") or (obj.get("peer") or {}).get(
                "nickname", "私聊"
            )

        me = obj.get("self") or {}
        my_id = me.get("uin")
        peer = obj.get("peer") or {}

        seg = ChatSegment(
            chat_id=peer.get("uin") or title,
            chat_type=ctype,
            title=title,
            my_user_id=my_id,
            participants=[me.get("nickname"), peer.get("nickname")],
        )

        for m in obj.get("messages") or []:
            nm = self._parse_message(m, my_id, ctype == "group")
            if nm is not None:
                seg.messages.append(nm)
        return seg

    def _parse_v2(self, obj: dict[str, Any]) -> ChatSegment:
        info = obj.get("chatInfo") or {}
        title = info.get("name") or "私聊"
        ctype = "group" if info.get("type") == "group" else "private"
        my_id = info.get("selfUid") or info.get("selfUin")
        self_name = info.get("selfName") or "我"
        peer_name = info.get("name") or "对方"

        seg = ChatSegment(
            chat_id=info.get("peerUid") or info.get("peerUin") or title,
            chat_type=ctype,
            title=title,
            my_user_id=my_id,
            participants=[self_name, peer_name],
        )

        for m in obj.get("messages") or []:
            nm = self._parse_message_v2(m, my_id)
            if nm is not None:
                seg.messages.append(nm)
        return seg

    def _parse_message_v2(
        self, m: dict[str, Any], my_id: str | None
    ) -> NormalizedMessage | None:
        if m.get("system") or m.get("recalled"):
            return None

        mtype = m.get("type") or "text"
        content = m.get("content") or {}
        text = (content.get("text") or "").strip()

        if text.startswith("[Markdown消息]"):
            text = text[len("[Markdown消息]"):].strip()

        if mtype == "text" and not text:
            return None
        if not text:
            text = f"[{mtype}]"

        sender = m.get("sender") or {}
        sender_id = sender.get("uid") or sender.get("uin")
        is_self = bool(my_id) and sender_id == my_id
        sender_name = sender.get("name") or sender.get("nickname")

        role = "user" if is_self else "assistant"

        return NormalizedMessage(
            role=role,
            content=text,
            timestamp=str(m.get("time") or ""),
            sender_name=sender_name,
            message_type=mtype,
            extras={"seq": m.get("seq")},
        )

    def _parse_message(
        self, m: dict[str, Any], my_id: str | None, is_group: bool
    ) -> NormalizedMessage | None:
        # QQChatExporter uses both numeric enum and string variants.
        type_field = m.get("type")
        if isinstance(type_field, int):
            mtype = _TYPE_MAP.get(type_field, "other")
        elif isinstance(type_field, str):
            mtype = type_field
        else:
            mtype = "text"

        text = (m.get("text") or "").strip()
        if mtype == "text" and not text:
            # Empty text messages are not interesting.
            return None

        sender = m.get("sender") or {}
        sender_id = sender.get("uin")
        is_self = bool(my_id) and sender_id == my_id
        sender_name = sender.get("nickname") or sender.get("card")

        # In group chats, anyone who isn't "me" is treated as `assistant`
        # (the candidate character). In 1-on-1 chats, the peer is `assistant`.
        role = "user" if is_self else "assistant"

        # Inferred image-only / sticker-only messages may have empty text
        # — emit a short placeholder so they aren't silently dropped.
        if not text:
            text = f"[{mtype}]"

        return NormalizedMessage(
            role=role,
            content=text,
            timestamp=str(m.get("time") or ""),
            sender_name=sender_name,
            message_type=mtype,
            extras={"seq": m.get("seq")},
        )