"""AI 自我成长 / 性格演化系统。

每轮对话后异步分析对话内容，提取性格变化点，
逐步更新角色的 personality 字段，让角色随互动自然成长。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from llm import call_once

log = logging.getLogger("ai-girlfriend.evolution")


async def analyze_evolution(
    *,
    current_personality: str,
    user_msg: str,
    ai_reply: str,
    char_name: str,
) -> dict[str, Any] | None:
    """分析对话，提取性格变化点。

    返回 {"change": "变化描述", "new_personality": "更新后的性格"} 或 None。
    失败时返回 None，永不阻塞主流程。
    """
    prompt = (
        "你是角色性格分析助手。根据以下对话，判断角色"
        f"（{char_name}）的性格是否发生了自然变化。\n\n"
        f"当前性格设定：{current_personality or '（空）'}\n\n"
        f"用户说：{user_msg[:200]}\n"
        f"角色回复：{ai_reply[:300]}\n\n"
        "分析这次对话是否让角色的性格产生了微妙变化（比如变得更活泼/更温柔/更独立/更黏人等）。\n"
        "大多数对话不会改变性格——只在有明显变化时才更新。\n\n"
        "请输出 JSON：\n"
        '{"changed": true/false, "change": "一句话描述变化（如：变得更爱撒娇了）", "new_personality": "更新后的完整性格描述"}\n\n'
        "如果性格没变化，输出 {\"changed\": false}。只输出 JSON。"
    )
    try:
        raw = await call_once(
            [{"role": "user", "content": prompt}],
            response_format_json=True,
            max_tokens=512,
        )
        parsed = json.loads(raw)
        if not parsed.get("changed"):
            return None
        return {
            "change": str(parsed.get("change", ""))[:200],
            "new_personality": str(parsed.get("new_personality", current_personality))[:2000],
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("evolution analysis failed: %s", exc)
        return None


def render_evolution_prompt(evolution_count: int) -> str:
    """把演化历史渲染成 system prompt 片段。"""
    if evolution_count <= 0:
        return ""
    return (
        "## 你的成长\n"
        f"你的性格已经过 {evolution_count} 次自然演化，"
        "在互动中逐渐成长变化。保持当前性格的连贯性，"
        "同时自然地展现你的成长。"
    )