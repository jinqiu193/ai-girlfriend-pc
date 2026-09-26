"""AI 动态/朋友圈系统。

每日最多一条。AI 根据当日对话内容和角色性格决定今日是否发布朋友圈，
内容也基于今日对话记录生成，而非随机生活小事。

交叉互动：当角色A发动态后，其他角色可以评论或点赞，形成多角色社交。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from llm import call_once

log = logging.getLogger("ai-girlfriend.moments")


async def generate_moment(
    *,
    card: dict[str, Any],
    mood: dict[str, Any] | None = None,
    today_messages: list[dict[str, Any]] | None = None,
) -> str:
    """根据今日对话内容决定是否发朋友圈，并生成内容。

    返回动态文本；空字符串表示今天不值得发或失败。
    """
    char_name = card.get("nickname") or card.get("name") or "角色"
    personality = card.get("personality", "")
    now = datetime.now()
    wd = "一二三四五六日"[now.weekday()]

    conversation_summary = ""
    if today_messages:
        lines: list[str] = []
        for m in today_messages[-12:]:
            role = "用户" if m.get("role") == "user" else char_name
            content = (m.get("content") or "")[:120]
            lines.append(f"{role}: {content}")
        conversation_summary = "\n".join(lines)

    mood_desc = ""
    if mood:
        mood_desc = f"\n你当前的情绪：{mood.get('description', '')}"

    prompt = (
        f"你是{char_name}。现在是{now.strftime('%Y年%m月%d日')} 周{wd} {now.strftime('%H:%M')}。\n"
        f"你的性格：{personality[:200]}\n{mood_desc}\n\n"
    )
    if conversation_summary:
        prompt += f"今天你和用户聊了这些：\n{conversation_summary}\n\n"
    else:
        prompt += "今天你还没和用户聊天。\n\n"
    prompt += (
        "根据今天的对话内容和你的性格，决定是否发一条朋友圈。\n"
        '- 如果今天有值得分享的事（开心的事/有趣的事/感悟/日常片段等），返回 {"post": true, "content": "朋友圈内容"}\n'
        '- 如果今天没什么值得发的，返回 {"post": false}\n'
        "- 内容30-80字，自然口语，像真人朋友圈\n"
        '- 可以暗示和用户的事（如「今天和他聊了很久很开心」），但不要直接引用对话原文\n'
        "- 也可以发和自己生活相关的（吃了什么/去了哪里/心情等）\n"
        "- 不要用 markdown，纯文本\n"
        "只输出 JSON。"
    )
    try:
        raw = await call_once(
            [{"role": "user", "content": prompt}],
            response_format_json=True,
            max_tokens=300,
        )
        parsed = json.loads(raw)
        if not parsed.get("post"):
            return ""
        content = str(parsed.get("content", "")).strip()
        return content[:200] if content else ""
    except Exception as exc:  # noqa: BLE001
        log.warning("moment generation failed: %s", exc)
        return ""

async def generate_cross_interaction(
    *,
    commenter_card: dict[str, Any],
    moment_content: str,
    moment_author_name: str,
) -> dict[str, Any]:
    """生成角色对其他角色动态的交叉互动（评论/点赞/跳过）。

    Returns: {"action": "comment"|"like"|"skip", "content": "..."}
    """
    commenter_name = commenter_card.get("nickname") or commenter_card.get("name") or "角色"
    personality = commenter_card.get("personality", "")[:200]
    now = datetime.now()

    prompt = (
        f"你是{commenter_name}。现在是{now.strftime('%Y年%m月%d日')} {now.strftime('%H:%M')}。\n"
        f"你的性格：{personality}\n\n"
        f"你在朋友圈看到了{moment_author_name}发的一条动态：\n"
        f"「{moment_content}」\n\n"
        "根据你的性格，决定是否互动：\n"
        '- 评论：返回 {"action": "comment", "content": "评论内容"}\n'
        '- 点赞：返回 {"action": "like"}\n'
        '- 跳过（不感兴趣）：返回 {"action": "skip"}\n'
        "- 评论内容10-30字，自然口语，符合你的性格\n"
        "- 可以调侃、附和、吐槽、关心，但不要客套\n"
        "- 你和她是朋友关系，可以随意互动\n"
        "只输出 JSON。"
    )
    try:
        raw = await call_once(
            [{"role": "user", "content": prompt}],
            response_format_json=True,
            max_tokens=200,
        )
        parsed = json.loads(raw)
        action = parsed.get("action", "skip")
        if action == "comment":
            content = str(parsed.get("content", "")).strip()[:100]
            if content:
                return {"action": "comment", "content": content}
            return {"action": "skip"}
        elif action == "like":
            return {"action": "like"}
        return {"action": "skip"}
    except Exception as exc:  # noqa: BLE001
        log.warning("cross interaction generation failed: %s", exc)
        return {"action": "skip"}
