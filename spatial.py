"""时空状态渲染：把持久化的时空描述/衣着渲染成 system prompt 片段。

与 scene.py 的「现实时间场景」互补：

- scene.py 回答「现在几点，按作息 AI 应该在哪」——纯时间推断；
- 本模块回答「根据刚才聊的内容，双方现在实际在哪、在做什么」——对话推断。

时空描述由主对话的 update_state tool call（llm.py）生成并持久化到
spatial_state 表，这里只负责读取和渲染。
"""
from __future__ import annotations

from typing import Any


def render_spatial_prompt(state: dict[str, Any] | None) -> str:
    """把时空描述和衣着渲染成 system prompt 片段。"""
    if not state:
        return ""
    parts: list[str] = []
    desc = state.get("description", "")
    if desc:
        parts.append(f"## 当前时空（由对话推断）\n{desc}")
    outfit = state.get("outfit", "")
    if outfit:
        parts.append(f"## 你的衣着\n{outfit}")
    return "\n\n".join(parts)

def render_story_scene_prompt(state: dict[str, Any] | None) -> str:
    """把故事场景状态渲染成 system prompt 片段（强约束锚点）。

    与 ``render_spatial_prompt`` 复用同一张 ``spatial_state`` 表，但语义
    面向故事卡：故事是旁白 + 多个 NPC 的叙事，需要锚定「地点 + 在场角色
    + 正在发生的事」，尤其要防止已离场角色在后续轮次被无故拉回同一场景。
    """
    if not state:
        return ""
    desc = (state.get("description") or "").strip()
    if not desc:
        return ""
    return (
        "## 当前场景\n"
        f"{desc}\n"
        "- 已离场角色不得无故重新出现；场景变化时需明确交代过渡。\n"
        "- 场景与上一轮相同时不要重复描写环境氛围。"
    )
