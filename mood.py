"""AI 情绪状态系统。

维护每个角色的情绪向量（开心/想念/吃醋/不耐烦/兴奋/无聊），
每轮对话后用轻量 LLM 调用更新，注入到下一轮的 system prompt。

情绪有惯性：旧情绪衰减 + 新情绪叠加。长时间无对话时，
"想念"和"无聊"自然上升。
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("ai-girlfriend.mood")

DEFAULT_MOOD: dict[str, Any] = {
    "happy": 55,
    "miss": 30,
    "jealous": 0,
    "annoyed": 0,
    "excited": 45,
    "bored": 5,
    "libido": 30,
    "description": "你心情不错，带着一点暧昧的期待，嘴角微微上扬。",
}

_IDLE_MISS_GAIN = 0.5
_IDLE_BORED_GAIN = 0.3
_IDLE_HAPPY_DECAY = 0.2
_IDLE_EXCITED_DECAY = 0.5

_MOOD_LABELS = {    "happy": "开心",
    "miss": "想念",
    "jealous": "吃醋",
    "annoyed": "生气",
    "excited": "兴奋",
    "bored": "无聊",
    "libido": "性欲",
}



def apply_idle_decay(mood: dict[str, Any], idle_minutes: int) -> dict[str, Any]:
    """长时间无对话时的情绪自然漂移。"""
    if idle_minutes <= 0:
        return dict(mood)
    m = dict(mood)
    m["miss"] = min(100, m.get("miss", 0) + idle_minutes * _IDLE_MISS_GAIN)
    m["bored"] = min(100, m.get("bored", 0) + idle_minutes * _IDLE_BORED_GAIN)
    m["happy"] = max(0, m.get("happy", 50) - idle_minutes * _IDLE_HAPPY_DECAY)
    m["excited"] = max(0, m.get("excited", 0) - idle_minutes * _IDLE_EXCITED_DECAY)
    return m



def render_mood_prompt(mood: dict[str, Any], state_config: dict[str, Any] | None = None) -> str:
    """把情绪状态渲染成 system prompt 片段。

    当 state_config 提供时，ai_access=none 的维度不渲染到 prompt。
    """
    desc = mood.get("description", "")
    parts: list[str] = []
    if desc:
        parts.append(desc)

    # Determine visible mood labels (respect ai_access from state_config).
    # _MOOD_LABELS provides the full dimension set; state_config can only
    # hide dimensions via ai_access=none, not add new ones.
    if state_config and state_config.get("mood_dimensions"):
        custom_dims = state_config["mood_dimensions"]
        visible_labels = {}
        for key, label in _MOOD_LABELS.items():
            dim_cfg = custom_dims.get(key, {})
            if dim_cfg.get("ai_access", "write") != "none":
                visible_labels[key] = label
    else:
        visible_labels = _MOOD_LABELS

    high: list[str] = []
    for key, label in visible_labels.items():
        v = mood.get(key, 0)
        if v >= 70:
            high.append(f"很{label}({v})")
        elif v >= 40:
            high.append(f"有点{label}({v})")
    if high:
        parts.append("当前情绪强度：" + "，".join(high))
    if not parts:
        return ""
    return "## 你当前的情绪状态\n" + "。".join(parts) + "。让回复自然反映你的情绪。"