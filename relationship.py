"""关系类型系统。

用户可以手动设置和 AI 的关系类型，影响 AI 的语气和行为。
关系类型不随对话自动变化，完全由用户手动设置。
"""
from __future__ import annotations

RELATIONSHIP_TYPES: dict[str, str] = {
    "陌生人": "你们互不认识。",
    "一面之缘": "你们见过一面，有些印象。",
    "朋友": "你们是朋友。",
    "工作关系": "你们是同事/工作伙伴。",
    "男女朋友": "你们在恋爱中。",
    "初恋": "这是你的初恋。",
    "热恋": "你们处于热恋期。",
    "平淡期": "你们的关系进入平淡期。",
    "分手": "你们已经分手了。",
    "复合": "你们复合了，重新在一起。",
    "婚姻": "你们已经结婚了。",
    "离婚": "你们离婚了。",
    "反目成仇": "你们反目成仇。",
}

DEFAULT_RELATIONSHIP = "陌生人"


def render_relationship_prompt(relationship: str) -> str:
    """把关系类型渲染成 system prompt 片段。"""
    if relationship not in RELATIONSHIP_TYPES:
        relationship = DEFAULT_RELATIONSHIP
    desc = RELATIONSHIP_TYPES[relationship]
    return f"## 你们的关系\n你们现在是「{relationship}」关系。{desc}"