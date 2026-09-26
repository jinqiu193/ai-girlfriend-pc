"""High-level chat-history importer.

Given one or more ``ChatSegment`` objects, this module:

1. Picks the best candidate as the "character" (the side that should be
   replicated as the AI persona).
2. Uses the configured LLM to derive a SillyTavern-style character card
   from a slice of their messages.
3. Slices the conversation into groups and writes each group as one
   ``recall_file`` into memU.

We deliberately keep each step independent so callers can preview / skip
any of them.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import db
from chat_loaders.base import ChatSegment, NormalizedMessage
from llm_json import parse_json_lenient
from config import get_settings
from llm import call_once
from memu.app import MemoryService


# ---------- candidate selection ----------

def pick_candidate(seg: ChatSegment) -> dict[str, Any] | None:
    """For a private chat, return a summary dict for the "other side"
    (the AI we want to replicate). Returns None if there's no clear peer
    (e.g. group chats we haven't filtered out upstream).

    The returned dict has at minimum::

        {"name": <str>, "message_count": <int>, "sample_messages": [<str>...]}
    """
    if seg.chat_type != "private":
        return None  # MVP: only private chats

    user_msgs = [m for m in seg.messages if m.role == "user"]
    peer_msgs = [m for m in seg.messages if m.role == "assistant"]
    if not peer_msgs:
        return None

    # Pick the most frequent sender_name among `assistant` messages as the
    # peer's display name (usually there is only one in 1-on-1).
    counts: dict[str, int] = {}
    for m in peer_msgs:
        name = m.sender_name or "(unknown)"
        counts[name] = counts.get(name, 0) + 1
    name = max(counts, key=counts.get) if counts else "(unknown)"

    sample = [m.content for m in peer_msgs if m.content.strip()][:30]

    return {
        "name": name,
        "message_count": len(peer_msgs),
        "user_message_count": len(user_msgs),
        "sample_messages": sample,
        "first_timestamp": seg.messages[0].timestamp if seg.messages else "",
        "last_timestamp": seg.messages[-1].timestamp if seg.messages else "",
    }


# ---------- character card derivation ----------

_CARD_PROMPT = """你是一位角色分析师。下面是一段真实私聊记录里"对方"说的话(已经过滤掉表情包、图片等非文本内容,共 {n} 条样本)。

请根据这些对话推断对方的性格、说话习惯、关系亲密度,然后生成一份 **SillyTavern V2 JSON 角色卡**(只输出 JSON,不要任何解释或 markdown 包裹)。

要求:
- `spec`: "chara_card_v2"
- `spec_version`: "2.0"
- `data.name`: 直接用上面给出的对方名字 "{name}"
- `data.description`: 用第三人称描述对方的背景、性格、与用户的关系(100-200 字)
- `data.personality`: 性格关键词(用逗号分隔,5-10 个)
- `data.scenario`: 你们当前的关系场景(50 字以内)
- `data.first_mes`: 模拟对方开口说的第一句话,贴合人设,50 字以内,带括号动作描写
- `data.mes_example`: 2-3 轮示例对话,用 `<START>` 开头,`{{user}}:` 和 `{{char}}:` 分行
- `data.system_prompt`: 保持角色人设的指令,包含 `{{original}}` 占位
- `data.post_history_instructions`: 简短,提醒模型记住对方的人设
- `data.creator_notes`: 空字符串
- `data.tags`: 3-5 个标签
- `data.creator`: "chat-importer"
- `data.character_version`: "1.0.0"
- `data.extensions`: 留空对象 {{}}
- `data.alternate_greetings`: 1-2 条备选开场白

样本对话:
{samples}

直接输出 JSON。"""


async def derive_character_card(candidate: dict[str, Any]) -> dict[str, Any]:
    """Call the configured LLM to produce a V2 character card."""
    sample_text = "\n".join(f"- {m}" for m in candidate["sample_messages"])
    prompt = _CARD_PROMPT.format(
        name=candidate["name"],
        n=len(candidate["sample_messages"]),
        samples=sample_text,
    )

    raw = await call_once(
        messages=[{"role": "user", "content": prompt}],
        response_format_json=True,
        max_tokens=4096,
    )
    # Tolerant parse: handles ```json fences, prose-wrapped JSON, and
    # line-delimited output. Replaces a fragile regex+json.loads pair
    # that broke when the model emitted nested objects — the old
    # ``r"\{.*\}"`` would stop at the first inner ``}`` and corrupt
    # the parse.
    card = parse_json_lenient(raw, expect="object")
    if not isinstance(card, dict):
        raise ValueError(f"LLM did not return JSON: {raw[:200]!r}")
    card.setdefault("spec", "chara_card_v2")
    card.setdefault("spec_version", "2.0")
    return card


# ---------- memory ingestion ----------

@dataclass
class IngestResult:
    character_id: str
    memory_files_committed: int
    recall_segments_written: int


async def ingest_segment_to_memu(
    *,
    segment: ChatSegment,
    user_id: str,
    character_id: str | tuple[str, str],
    character_name: str,
    memu: MemoryService,
    slice_size: int = 30,
) -> IngestResult:
    """Slice the conversation and commit each slice as one recall_file.

    Each slice becomes a single ``recall_file`` with ``track="memory"``;
    its ``content`` is a deterministic dialogue transcript (so future
    progressive_retrieve can find relevant snippets by topic overlap).

    ``character_id`` accepts either a plain id string or the
    ``(char_id, action)`` tuple returned by :func:`db.create_character` —
    tests and call sites have been passing both shapes historically.
    """
    # Tolerate the ``(char_id, action)`` shape so existing call sites and
    # tests don't have to be reworked.
    if isinstance(character_id, tuple):
        character_id = character_id[0]
    msgs = [m for m in segment.messages if m.message_type == "text" and m.content.strip()]
    if not msgs:
        return IngestResult(character_id=character_id, memory_files_committed=0, recall_segments_written=0)

    slices = [msgs[i : i + slice_size] for i in range(0, len(msgs), slice_size)]
    recall_files = []
    for idx, chunk in enumerate(slices):
        lines = []
        for m in chunk:
            tag = "user" if m.role == "user" else character_name
            ts = m.timestamp or ""
            line = f"[{ts}] {tag}: {m.content}".strip()
            lines.append(line)
        recall_files.append(
            {
                "name": f"imported_{character_name}_{idx:03d}",
                "track": "memory",
                "description": (
                    f"{character_name} 和 {user_id} 的聊天记录 第 {idx + 1}/{len(slices)} 段 "
                    f"({chunk[0].timestamp[:10]} ~ {chunk[-1].timestamp[:10]})"
                ),
                "content": "\n".join(lines),
            }
        )

    await memu.commit_results(
        recall_files=recall_files,
        user={"user_id": user_id, "character_id": character_id},
    )

    db.record_memory_commit(
        user_id=user_id,
        character_id=character_id,
        memu_name=f"imported_{character_name}_bulk",
        topic=f"imported {len(msgs)} messages in {len(slices)} slices",
    )

    return IngestResult(
        character_id=character_id,
        memory_files_committed=len(recall_files),
        recall_segments_written=sum(len(c) for c in slices),
    )