"""四级优先级上下文预算系统。

借鉴 zenstory 的 ContextPriority + TokenBudget 设计，但做轻量化适配：
ai-girlfriend 没有"文件树"，上下文来自角色卡字段、故事记忆、检索片段等，
因此用 Fragment(content, priority) 代替 ContextItem，用 assemble_with_budget
代替 assembler + prioritizer + budget 三件套。

核心行为：
- 总 token 不超预算时，所有 fragment 按原始顺序拼接（与改造前完全一致）
- 超预算时，按优先级丢弃/截断低档 fragment，CRITICAL 永不整条丢弃
- CRITICAL 档内逐项保底 200 token，宁可每条截断也不让任何一条消失
- 预算分配：CRITICAL 30% / CONSTRAINT 35% / RELEVANT 25% / INSPIRATION 10%
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class Priority(IntEnum):
    CRITICAL = 0
    CONSTRAINT = 1
    RELEVANT = 2
    INSPIRATION = 3


@dataclass
class Fragment:
    content: str
    priority: Priority
    is_static: bool = False


_ALLOC: dict[Priority, float] = {
    Priority.CRITICAL: 0.30,
    Priority.CONSTRAINT: 0.35,
    Priority.RELEVANT: 0.25,
    Priority.INSPIRATION: 0.10,
}

_MIN_CRITICAL_ITEM_TOKENS = 200
_LOWER_TIER_FLOOR_RATIO = 0.5
_TRUNC_SUFFIX = "…（已截断）"


def _estimate_tokens(text: str) -> int:
    """CJK 感知的 token 估算：中文 ~2 字符/token，拉丁文 ~4 字符/token。"""
    if not text:
        return 0
    cjk = 0
    for c in text:
        if "\u4e00" <= c <= "\u9fff" or "\u3000" <= c <= "\u303f":
            cjk += 1
    other = len(text) - cjk
    return max(1, cjk // 2 + other // 4)


def _truncate(text: str, max_tokens: int) -> str:
    """把文本截断到 max_tokens 以内，追加截断后缀。"""
    if max_tokens <= 0 or not text:
        return ""
    if _estimate_tokens(text) <= max_tokens:
        return text

    suffix_cost = _estimate_tokens(_TRUNC_SUFFIX)
    body_budget = max_tokens - suffix_cost
    if body_budget <= 0:
        cpt = 2.0
        return text[: max(1, int(max_tokens * cpt))]

    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    cpt = 2.0 if cjk > len(text) * 0.3 else 4.0
    body = text[: max(1, int(body_budget * cpt))]

    guard = 0
    while body and _estimate_tokens(body) > body_budget and guard < 20:
        body = body[: max(1, int(len(body) * 0.85))]
        guard += 1

    return body.rstrip() + _TRUNC_SUFFIX


def _allocate_tiers(
    max_tokens: int,
    demand: dict[Priority, int],
) -> dict[Priority, int]:
    """按各档真实需求分配预算，保证总和不超过 max_tokens。

    CRITICAL 先拿名义份额与"下位档预留后剩余"的较大值；
    若 CRITICAL 需求仍超出，允许向下位档借用但留 50% 地板。
    """
    critical = Priority.CRITICAL
    lower = [p for p in Priority if p != critical]

    reserved_for_lower = sum(
        min(int(max_tokens * _ALLOC[p]), demand.get(p, 0)) for p in lower
    )
    critical_budget = max(
        int(max_tokens * _ALLOC[critical]),
        max_tokens - reserved_for_lower,
    )

    critical_demand = demand.get(critical, 0)
    if critical_demand > critical_budget:
        soft_floor = sum(
            int(min(int(max_tokens * _ALLOC[p]), demand.get(p, 0)) * _LOWER_TIER_FLOOR_RATIO)
            for p in lower
        )
        critical_budget = max(
            critical_budget,
            min(critical_demand, max_tokens - soft_floor),
        )

    critical_alloc = min(critical_budget, critical_demand)
    leftover = max(0, max_tokens - critical_alloc)
    lower_share_sum = sum(_ALLOC[p] for p in lower) or 1.0

    budgets: dict[Priority, int] = {critical: critical_budget}
    for p in lower:
        share = _ALLOC[p] / lower_share_sum
        budgets[p] = min(int(max_tokens * _ALLOC[p]), int(leftover * share))

    return budgets


def assemble_with_budget(
    fragments: list[Fragment],
    max_tokens: int,
) -> str:
    """按优先级预算拼接 fragment 列表。

    总量不超预算时按原始顺序全量拼接（行为与改造前一致）；
    超预算时按优先级丢弃/截断，CRITICAL 永不整条丢弃。
    """
    if not fragments:
        return ""

    total = sum(_estimate_tokens(f.content) for f in fragments)
    if total <= max_tokens:
        return "\n\n".join(f.content for f in fragments)

    groups: dict[Priority, list[int]] = {p: [] for p in Priority}
    for i, f in enumerate(fragments):
        groups[f.priority].append(i)

    # Within each priority group, static fragments come first (preferred retention).
    # Dynamic fragments are more likely to be truncated/dropped when budget is tight
    # because they change every turn anyway.
    for p in groups:
        groups[p].sort(key=lambda idx: (0 if fragments[idx].is_static else 1, idx))

    demand = {
        p: sum(_estimate_tokens(fragments[i].content) for i in groups[p])
        for p in Priority
    }
    budgets = _allocate_tiers(max_tokens, demand)

    selected: set[int] = set()
    truncated: dict[int, int] = {}
    for priority in Priority:
        group = groups[priority]
        budget = budgets[priority]
        used = 0

        for idx_in_group, frag_idx in enumerate(group):
            frag = fragments[frag_idx]
            tokens = _estimate_tokens(frag.content)

            effective_budget = budget
            if priority == Priority.CRITICAL:
                pending = len(group) - idx_in_group - 1
                reserve = min(
                    pending * _MIN_CRITICAL_ITEM_TOKENS,
                    max(0, budget - used - _MIN_CRITICAL_ITEM_TOKENS),
                )
                effective_budget = budget - reserve

            if used + tokens <= effective_budget:
                selected.add(frag_idx)
                used += tokens
            elif priority == Priority.CRITICAL:
                remaining = effective_budget - used
                if remaining > 50:
                    selected.add(frag_idx)
                    truncated[frag_idx] = remaining
                    used += remaining

    result_parts: list[str] = []
    for i, f in enumerate(fragments):
        if i not in selected:
            continue
        if i in truncated:
            result_parts.append(_truncate(f.content, truncated[i]))
        else:
            result_parts.append(f.content)

    return "\n\n".join(result_parts)