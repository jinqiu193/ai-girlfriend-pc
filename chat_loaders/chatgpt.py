"""ChatGPT export JSON loader.

Accepts ``conversations.json`` produced by ChatGPT's "Export data" feature.
Structure::

    [
      {
        "title": "...",
        "create_time": 1700000000.0,   # unix seconds (float)
        "update_time": 1700000999.0,
        "mapping": {
          "<node_id>": {
            "id": "...",
            "message": {
              "author": {"role": "user" | "assistant" | "system"},
              "content": {"content_type": "text", "parts": ["..."]}
            } | null,
            "parent": "<parent_id>" | null,
            "children": ["..."]
          },
          ...
        }
      },
      ...
    ]

We rebuild the linear message order by walking each conversation's mapping
tree from root via ``children``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .base import ChatSegment, NormalizedMessage


class ChatGPTLoader:
    key = "chatgpt"

    def iter_segments(self, raw: bytes) -> list[ChatSegment]:
        import json

        for enc in ("utf-8", "utf-8-sig"):
            try:
                obj = json.loads(raw.decode(enc))
                break
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        else:
            raise ValueError("ChatGPT JSON is not valid UTF-8")

        if not isinstance(obj, list):
            raise ValueError("ChatGPT export root must be a JSON array")

        segments: list[ChatSegment] = []
        for idx, conv in enumerate(obj):
            if not isinstance(conv, dict):
                continue
            seg = self._parse_conv(conv, idx)
            if seg.messages:
                segments.append(seg)
        return segments

    def _parse_conv(self, conv: dict[str, Any], idx: int) -> ChatSegment:
        title = conv.get("title") or f"ChatGPT 对话 #{idx + 1}"
        mapping = conv.get("mapping") or {}
        create_time = conv.get("create_time")

        # find roots (nodes with no parent)
        roots = [
            node_id
            for node_id, node in mapping.items()
            if isinstance(node, dict) and not node.get("parent")
        ]

        # walk the first root tree depth-first
        linear: list[dict[str, Any]] = []
        if roots:
            self._walk(mapping, roots[0], linear)

        msgs: list[NormalizedMessage] = []
        for node in linear:
            message = node.get("message") or {}
            author = message.get("author") or {}
            role_raw = (author.get("role") or "").lower()
            if role_raw not in {"user", "assistant", "system"}:
                continue
            content_obj = message.get("content") or {}
            parts = content_obj.get("parts") or []
            text = "".join(str(p) for p in parts if isinstance(p, str)).strip()
            if not text:
                continue
            ts = message.get("create_time") or create_time
            ts_str = self._fmt_ts(ts)
            msgs.append(
                NormalizedMessage(
                    role=role_raw,
                    content=text,
                    timestamp=ts_str,
                    sender_name=author.get("name") or role_raw,
                    message_type="text",
                )
            )

        seg = ChatSegment(
            chat_id=title,
            chat_type="private",
            title=title,
            my_user_id=None,
        )
        seg.messages = msgs
        return seg

    def _walk(
        self,
        mapping: dict[str, Any],
        node_id: str,
        out: list[dict[str, Any]],
    ) -> None:
        node = mapping.get(node_id)
        if not isinstance(node, dict):
            return
        out.append(node)
        for child_id in node.get("children") or []:
            self._walk(mapping, child_id, out)

    @staticmethod
    def _fmt_ts(ts: Any) -> str:
        if ts is None:
            return ""
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
        except (ValueError, TypeError, OSError):
            return str(ts)