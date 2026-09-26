"""亲密度/关系阶段系统。

根据对话轮次累积亲密度，注入到 prompt 影响互动深度。
不需要额外存储——直接从消息数计算。
"""
from __future__ import annotations


def compute_intimacy(user_message_count: int) -> int:
    """根据用户消息数计算亲密度（0-100）。"""
    return min(100, user_message_count * 3)


_LEVELS = [
    (0, "刚认识", ""),
    (11, "渐渐熟络", ""),
    (31, "熟悉", ""),
    (51, "亲密", ""),
    (71, "深爱", ""),
]


def render_intimacy_prompt(intimacy: int) -> str:
    """把亲密度渲染成 system prompt 片段。"""
    label = _LEVELS[0][1]
    for threshold, l, _ in _LEVELS:
        if intimacy >= threshold:
            label = l
    return f"## 你们的关系阶段\n你们现在{label}（亲密度: {intimacy}/100）。"


_INTIMACY_TO_RELATIONSHIP = [
    (0, "陌生人"),
    (11, "一面之缘"),
    (26, "朋友"),
    (41, "男女朋友"),
    (56, "热恋"),
    (71, "平淡期"),
    (86, "婚姻"),
]


def auto_relationship(intimacy: int) -> str:
    """根据亲密度自动映射到关系类型。"""
    result = _INTIMACY_TO_RELATIONSHIP[0][1]
    for threshold, rel in _INTIMACY_TO_RELATIONSHIP:
        if intimacy >= threshold:
            result = rel
    return result