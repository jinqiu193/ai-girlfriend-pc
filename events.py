"""事件记忆系统。

从对话中提取用户提到的重要事件（考试/面试/生日/旅行等），
在合适时机提醒 AI 主动追问。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from llm import call_once

log = logging.getLogger("ai-girlfriend.events")


async def extract_events(
    *,
    user_msg: str,
    ai_reply: str,
) -> list[dict[str, Any]]:
    """用 LLM 从对话中提取重要事件。返回 ``[{"text": ..., "date": ...}, ...]``。"""
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = (
        f"今天是 {today}。根据以下对话，提取用户提到的重要事件"
        "（考试/面试/生日/旅行/约会/看病/搬家/汇报等）。\n\n"
        f"用户说：{user_msg[:200]}\n"
        f"角色回复：{ai_reply[:200]}\n\n"
        '如果没有重要事件，返回 {"events": []}。\n'
        '如果有，返回 {"events": [{"text": "事件简述", "date": "YYYY-MM-DD或null"}]}。\n'
        "- text: 简短事件描述（10字以内）\n"
        "- date: 事件日期（如果能推断出来，ISO格式；推断不出就null）\n"
        "只输出 JSON。"
    )
    try:
        raw = await call_once(
            [{"role": "user", "content": prompt}],
            response_format_json=True,
            max_tokens=256,
        )
        parsed = json.loads(raw)
        events = parsed.get("events", [])
        result: list[dict[str, Any]] = []
        for e in events:
            text = str(e.get("text", "")).strip()
            if not text:
                continue
            d = e.get("date")
            if d:
                try:
                    datetime.strptime(str(d)[:10], "%Y-%m-%d")
                    d = str(d)[:10]
                except (ValueError, TypeError):
                    d = None
            else:
                d = None
            result.append({"text": text[:50], "date": d})
        return result
    except Exception as exc:  # noqa: BLE001
        log.warning("event extraction failed: %s", exc)
        return []


def render_events_prompt(events: list[dict[str, Any]]) -> str:
    """把到期事件渲染成 system prompt 片段，提醒 AI 主动追问。"""
    if not events:
        return ""
    today = datetime.now().strftime("%Y-%m-%d")
    lines: list[str] = []
    for e in events:
        text = e.get("event_text", "")
        ed = e.get("event_date", "")
        if ed and ed <= today:
            lines.append(f"- 用户之前提到过\"{text}\"（{ed}），你可以自然地关心一下结果怎么样了")
        else:
            lines.append(f"- 用户之前提到过\"{text}\"，你可以找机会问问")
    if not lines:
        return ""
    return "## 你记得用户提到的事\n" + "\n".join(lines) + "\n不要生硬地追问，自然地在对话中带出来。"