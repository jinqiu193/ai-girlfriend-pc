"""Tolerant JSON parser for LLM output.

LLMs occasionally wrap JSON in markdown fences, surround it with prose,
or emit one JSON value per line. This module centralises the recovery
strategies so every caller (``chat_import``, future profile/emotion/
promise extractors, etc.) gets the same fallbacks instead of each one
inventing its own regex.

Patterned after the extraction-layer fallback chain documented in
``companion-extraction.md`` (§3), adapted for ai-girlfriend's flat
module layout and stdlib-only dependencies.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger("ai-girlfriend.llm_json")


# Strips leading ```json / ``` fence and trailing ``` fence. We do not
# require ``json`` in the opening fence — some LLMs emit just ```.
_FENCE_RE = re.compile(r"^```(?:json|JSON)?\s*|\s*```\s*$", re.MULTILINE)


def _strip_fences(text: str) -> str:
    """Remove ``` ``` ``` markdown fences from the start and end.

    Acts only at line boundaries, so internal backticks survive.
    """
    return _FENCE_RE.sub("", text).strip()


def parse_json_lenient(text: str, *, expect: str = "auto") -> Any:
    """Parse JSON from LLM output, with up to three fallback strategies.

    Tries, in order:

    1. ``json.loads`` on the whole string after stripping ```` ``` ````
       markdown fences.
    2. Slice between the first ``[`` and last ``]`` (or ``{``/``}``),
       then ``json.loads`` on the slice. Useful when the model
       wraps JSON in preamble / postscript prose.
    3. Line-delimited: parse each non-empty line independently, return
       whichever line(s) parsed cleanly. Some LLMs default to "one
       JSON object per line" output for batched prompts.

    On total failure, returns ``None``. The caller is expected to check
    for ``None`` and follow its own fallback path; this function
    deliberately does not raise so a single malformed response can't
    take down a background task.

    :param text: Raw LLM response text.
    :param expect: ``"array"`` / ``"object"`` / ``"auto"``. When
        ``"auto"``, the array branch is attempted first because most
        ai-girlfriend extraction prompts emit arrays; an object is
        tried as a fallback. When ``"object"``, the object branch is
        attempted first.
    """
    if not text:
        return None
    cleaned = _strip_fences(text)

    if expect == "object":
        order = ("object", "array")
    else:
        order = ("array", "object")

    for kind in order:
        opener, closer = ("[", "]") if kind == "array" else ("{", "}")
        # Strategy 1 + 2: try the whole cleaned string first, then a
        # slice between the outermost opener/closer.
        candidates: list[str] = [cleaned]
        first = cleaned.find(opener)
        last = cleaned.rfind(closer)
        if first != -1 and last != -1 and last > first:
            candidates.append(cleaned[first : last + 1])

        for candidate in candidates:
            try:
                value = json.loads(candidate)
            except (ValueError, TypeError):
                continue
            # Normalise shape: caller asked for object, model emitted
            # a one-element array of objects → unwrap once. Common
            # when a shared prompt template is reused across callers.
            if expect == "object" and isinstance(value, list):
                if len(value) == 1 and isinstance(value[0], dict):
                    return value[0]
                # Wrong shape — keep trying other strategies.
                continue
            # Mirror case: caller wanted an array, model emitted a
            # single object → wrap in a one-element list.
            if expect == "array" and isinstance(value, dict):
                return [value]
            return value

    # Strategy 3: line-delimited fallback. Keep only the lines that
    # parse cleanly as JSON values and return whatever the caller
    # asked for (array → list of values; object → first value).
    parsed_lines: list[Any] = []
    for line in cleaned.splitlines():
        line = line.strip().rstrip(",")
        if not line:
            continue
        try:
            parsed_lines.append(json.loads(line))
        except (ValueError, TypeError):
            continue
    if parsed_lines:
        if expect == "object" and isinstance(parsed_lines[0], dict):
            return parsed_lines[0]
        if expect == "array":
            return parsed_lines

    log.warning("parse_json_lenient: failed to recover JSON from LLM output")
    return None