"""Tests for the tolerant LLM JSON parser.

Covers the three fallback strategies documented in
``companion-extraction.md`` (§3): fence stripping, outermost-bracket
slicing, line-delimited fallback. Also covers the ``expect`` parameter
that drives ai-girlfriend's mixed array / object call sites.
"""
from __future__ import annotations

from llm_json import parse_json_lenient


def test_strips_markdown_fence_array():
    raw = "```json\n[{\"a\": 1}, {\"a\": 2}]\n```"
    assert parse_json_lenient(raw) == [{"a": 1}, {"a": 2}]


def test_strips_fence_without_json_tag():
    raw = "```\n[1, 2, 3]\n```"
    assert parse_json_lenient(raw) == [1, 2, 3]


def test_slices_array_out_of_prose():
    raw = "Here's the list you asked for:\n\n[1, 2, 3]\n\nHope that helps."
    assert parse_json_lenient(raw) == [1, 2, 3]


def test_slices_object_out_of_prose():
    raw = "Sure thing!\n{\"name\": \"Alice\", \"age\": 30}\nDone."
    assert parse_json_lenient(raw, expect="object") == {"name": "Alice", "age": 30}


def test_handles_nested_object_directly():
    """The old regex-based parser (``re.search(r"\\{.*\\}", raw, re.DOTALL)``)
    used in chat_import.derive_character_card would stop at the first
    inner ``}`` and corrupt nested objects. The new parser must handle
    them in the very first strategy, no slicing needed."""
    raw = '{"data": {"nested": {"deep": true}}, "ok": 1}'
    out = parse_json_lenient(raw, expect="object")
    assert out == {"data": {"nested": {"deep": True}}, "ok": 1}


def test_line_delimited_fallback():
    """When the model emits one JSON object per line (a known Anthropic
    behaviour for batched prompts), recover as much as we can."""
    raw = '{"a": 1}\n{"a": 2}\n{"a": 3}\n'
    out = parse_json_lenient(raw, expect="array")
    assert out == [{"a": 1}, {"a": 2}, {"a": 3}]


def test_line_delimited_object_returns_first_dict():
    """When ``expect="object"`` and the model emitted a single object
    on its own line, return it."""
    raw = 'garbage line\n{"only": "object"}\nmore garbage'
    assert parse_json_lenient(raw, expect="object") == {"only": "object"}


def test_garbage_returns_none():
    assert parse_json_lenient("hello world, no json here") is None


def test_empty_string_returns_none():
    assert parse_json_lenient("") is None


def test_expect_object_with_single_object_array_unwraps():
    """If a shared prompt template mistakenly emits ``[obj]`` when the
    caller asked for an object, unwrap it once — common when the
    prompt is reused across callers."""
    raw = '[{"name": "Bob"}]'
    out = parse_json_lenient(raw, expect="object")
    assert out == {"name": "Bob"}


def test_expect_array_wraps_lone_object():
    """The mirror case: model emitted a single object, caller wanted an
    array. Wrap in a one-element list."""
    raw = '{"name": "Bob"}'
    out = parse_json_lenient(raw, expect="array")
    assert out == [{"name": "Bob"}]


def test_does_not_raise_on_trailing_comma_in_fenced_block():
    """Some older LLMs emit trailing commas inside fences. These are
    not valid JSON, so the parse must fail cleanly and move on to the
    line-delimited fallback rather than raise."""
    raw = "```json\n[{\"a\": 1,}, {\"a\": 2,}]\n```"
    # Whole-string parse fails (invalid trailing comma), but the line-
    # delimited fallback can still recover one object per line.
    out = parse_json_lenient(raw)
    # Either we got both objects (unlikely with the bracket slice) or
    # at least one — what matters is no exception escapes.
    assert out is None or isinstance(out, list)