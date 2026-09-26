"""Dynamic prompt rule matching engine.

Each rule has ``match_conditions`` (JSON) and ``content``. The system
builds a match context from the current conversation state (time, emotion,
scene, relationship, intimacy, user message) and injects all matched
rules' content into the system prompt.

Condition fields (all optional, AND logic — every specified condition
must be satisfied for the rule to match):

- ``time_start`` / ``time_end``: ``"HH:MM"`` time range. Supports cross-
  midnight (e.g. ``"22:00"`` → ``"06:00"``). Empty = no time restriction.
- ``emotions``: list of user emotion keys (``sad``/``happy``/``angry``/
  ``anxious``). Empty = any emotion.
- ``scenes``: list of scene keys. Empty = any scene.
- ``relationships``: list of relationship type strings. Empty = any.
- ``intimacy_min`` / ``intimacy_max``: 0-100 range. null = unbounded.
- ``keywords``: list of substrings; user message must contain at least
  one. Empty = no keyword requirement.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def _parse_hhmm(s: str | None) -> int | None:
    """''HH:MM' → minutes since midnight. None/empty → None."""
    if not s or not s.strip():
        return None
    parts = s.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return None


def _time_matches(conditions: dict[str, Any], now_min: int) -> bool:
    start = _parse_hhmm(conditions.get("time_start"))
    end = _parse_hhmm(conditions.get("time_end"))
    if start is None and end is None:
        return True
    if start is not None and end is not None:
        if start <= end:
            return start <= now_min < end
        # 跨午夜：22:00-06:00
        return now_min >= start or now_min < end
    if start is not None:
        return now_min >= start
    return now_min < (end or 0)


def matches_conditions(conditions: dict[str, Any], ctx: dict[str, Any]) -> bool:
    """Return True if *all* specified conditions in ``conditions`` are
    satisfied by the current match context ``ctx``.

    ``ctx`` keys: ``now`` (datetime), ``emotion`` (str|None),
    ``scene_key`` (str|None), ``relationship`` (str|None),
    ``intimacy`` (int|None), ``user_msg`` (str).
    """
    now: datetime = ctx.get("now") or datetime.now()
    if not _time_matches(conditions, now.hour * 60 + now.minute):
        return False

    emotions = conditions.get("emotions") or []
    if emotions:
        if ctx.get("emotion") not in emotions:
            return False

    scenes = conditions.get("scenes") or []
    if scenes:
        if ctx.get("scene_key") not in scenes:
            return False

    relationships = conditions.get("relationships") or []
    if relationships:
        if ctx.get("relationship") not in relationships:
            return False

    intimacy = ctx.get("intimacy")
    if intimacy is not None:
        lo = conditions.get("intimacy_min")
        hi = conditions.get("intimacy_max")
        if lo is not None and intimacy < lo:
            return False
        if hi is not None and intimacy > hi:
            return False

    keywords = conditions.get("keywords") or []
    if keywords:
        msg = (ctx.get("user_msg") or "").lower()
        if not any(k.lower() in msg for k in keywords):
            return False

    return True


def match_and_render(
    rules: list[dict[str, Any]], ctx: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    """Match enabled rules against ``ctx``, return (matched_rules, prompt_text).

    Rules are sorted by ``priority`` (ascending). The prompt text is the
    concatenation of all matched rules' content, separated by blank lines.
    """
    matched: list[dict[str, Any]] = []
    for r in rules:
        if not r.get("enabled"):
            continue
        try:
            conditions = json.loads(r.get("match_conditions") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(conditions, dict):
            continue
        if matches_conditions(conditions, ctx):
            matched.append(r)
    matched.sort(key=lambda r: r.get("priority", 100))
    parts = [r["content"] for r in matched if r.get("content", "").strip()]
    return matched, "\n\n".join(parts)


def build_match_context(
    *, now: datetime | None = None, emotion: str | None = None,
    scene_key: str | None = None, relationship: str | None = None,
    intimacy: int | None = None, user_msg: str = "",
) -> dict[str, Any]:
    """Convenience builder for the match context dict."""
    return {
        "now": now or datetime.now(),
        "emotion": emotion,
        "scene_key": scene_key,
        "relationship": relationship,
        "intimacy": intimacy,
        "user_msg": user_msg,
    }