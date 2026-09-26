"""LLM client + streaming generator.

Supports two protocols:
- ``anthropic``: Anthropic Messages API (DeepSeek, MiniMax gateway, etc.)
- ``openai``: OpenAI-compatible Chat Completions API (LM Studio, vLLM, etc.)

The protocol is selected via ``LLM_PROTOCOL`` in settings. All public
functions (``stream_chat``, ``call_once``, ``call_with_state_tool``)
dispatch to the appropriate backend automatically.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from config import get_settings


# ---------- client pools ----------

_anthropic_client: AsyncAnthropic | None = None
_anthropic_cfg: tuple[str, str] | None = None

_openai_client: AsyncOpenAI | None = None
_openai_cfg: tuple[str, str] | None = None


def _build_timeout() -> httpx.Timeout:
    """Per-request timeout. Connect covers TCP/TLS; read covers
    time-to-first-byte and the whole stream. Without this the SDK
    default (600s) lets a hung request stall the SSE stream."""
    s = get_settings()
    return httpx.Timeout(
        connect=s.llm_connect_timeout,
        read=s.llm_read_timeout,
        write=s.llm_read_timeout,
        pool=s.llm_connect_timeout,
    )


# ---------- concurrency governance ----------

_llm_semaphore: asyncio.Semaphore | None = None


def get_llm_semaphore() -> asyncio.Semaphore:
    """Global cap on concurrent LLM calls. Callers that fire background
    work (state update, moments, events, ...) should wrap their calls in
    ``async with get_llm_semaphore():`` so a burst of chats cannot
    stampede the upstream and trigger gateway timeouts."""
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(get_settings().max_concurrent_llm)
    return _llm_semaphore


def _get_anthropic_client() -> AsyncAnthropic:
    global _anthropic_client, _anthropic_cfg
    s = get_settings()
    cfg = (s.llm_base_url, s.llm_api_key)
    if _anthropic_client is None or _anthropic_cfg != cfg:
        _anthropic_client = AsyncAnthropic(
            api_key=s.llm_api_key,
            base_url=s.llm_base_url,
            timeout=_build_timeout(),
            max_retries=s.llm_max_retries,
        )
        _anthropic_cfg = cfg
    return _anthropic_client


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client, _openai_cfg
    s = get_settings()
    cfg = (s.llm_base_url, s.llm_api_key)
    if _openai_client is None or _openai_cfg != cfg:
        _openai_client = AsyncOpenAI(
            api_key=s.llm_api_key,
            base_url=s.llm_base_url,
            timeout=_build_timeout(),
            max_retries=s.llm_max_retries,
        )
        _openai_cfg = cfg
    return _openai_client


def _get_protocol() -> str:
    return get_settings().llm_protocol.lower()


def get_model() -> str:
    return get_settings().llm_model


# ---------- message shape translation (Anthropic) ----------

def split_system_and_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """OpenAI messages (with a leading system role) -> (system, rest)."""
    system_parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "system":
            content = m.get("content", "")
            if isinstance(content, list):
                content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
            if content:
                system_parts.append(content)
        else:
            rest.append(m)
    return "\n\n".join(system_parts), rest


def to_anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI-style messages (after system is removed) into the
    Anthropic Messages API format."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue

        content = m.get("content", "")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        blocks: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "text":
                blocks.append({"type": "text", "text": part.get("text", "")})
            elif ptype == "image_url":
                url = (part.get("image_url") or {}).get("url", "")
                blocks.append({
                    "type": "image",
                    "source": {"type": "url", "url": url},
                })
        if blocks:
            out.append({"role": role, "content": blocks})
    return out


# ---------- streaming ----------

async def stream_chat(
    messages: list[dict[str, Any]], *, max_tokens: int = 1024
) -> AsyncIterator[str]:
    """Yield text deltas from the assistant message."""
    if _get_protocol() == "openai":
        async for chunk in _stream_chat_openai(messages, max_tokens=max_tokens):
            yield chunk
    else:
        async for chunk in _stream_chat_anthropic(messages, max_tokens=max_tokens):
            yield chunk


async def _stream_chat_anthropic(
    messages: list[dict[str, Any]], *, max_tokens: int = 1024
) -> AsyncIterator[str]:
    system, rest = split_system_and_messages(messages)
    anthropic_messages = to_anthropic_messages(rest)

    client = _get_anthropic_client()
    kwargs: dict[str, Any] = {
        "model": get_model(),
        "messages": anthropic_messages,
        "max_tokens": max_tokens,
        "extra_body": {"temperature": 0.8},
    }
    if system:
        kwargs["system"] = system

    async with client.messages.stream(**kwargs) as stream:
        async for text in stream.text_stream:
            if text:
                yield text


async def _stream_chat_openai(
    messages: list[dict[str, Any]], *, max_tokens: int = 1024
) -> AsyncIterator[str]:
    client = _get_openai_client()
    stream = await client.chat.completions.create(
        model=get_model(),
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.8,
        stream=True,
        extra_body={"enable_thinking": False, "reasoning_effort": "none"},
    )
    has_content = False
    reasoning_buf: list[str] = []
    try:
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            rc = getattr(delta, "reasoning_content", None)
            if rc:
                reasoning_buf.append(rc)
            if delta.content:
                has_content = True
                yield delta.content
    except RuntimeError:
        pass
    if not has_content and reasoning_buf:
        yield "".join(reasoning_buf)


# ---------- non-streaming ----------

async def call_once(
    messages: list[dict[str, Any]],
    *,
    response_format_json: bool = False,
    max_tokens: int = 4096,
) -> str:
    """One-shot completion; returns the assistant message text."""
    if _get_protocol() == "openai":
        return await _call_once_openai(messages, response_format_json=response_format_json, max_tokens=max_tokens)
    return await _call_once_anthropic(messages, response_format_json=response_format_json, max_tokens=max_tokens)


async def _call_once_anthropic(
    messages: list[dict[str, Any]],
    *,
    response_format_json: bool = False,
    max_tokens: int = 4096,
) -> str:
    system, rest = split_system_and_messages(messages)
    anthropic_messages = to_anthropic_messages(rest)

    if response_format_json:
        json_instr = "You MUST reply with a single JSON object only, no prose, no markdown fences."
        if system:
            system = f"{system}\n\n{json_instr}"
        else:
            system = json_instr

    client = _get_anthropic_client()
    kwargs: dict[str, Any] = {
        "model": get_model(),
        "messages": anthropic_messages,
        "max_tokens": max_tokens,
        "extra_body": {"temperature": 0.7},
    }
    if system:
        kwargs["system"] = system

    resp = await client.messages.create(**kwargs)
    return "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    )


async def _call_once_openai(
    messages: list[dict[str, Any]],
    *,
    response_format_json: bool = False,
    max_tokens: int = 4096,
) -> str:
    client = _get_openai_client()
    kwargs: dict[str, Any] = {
        "model": get_model(),
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "extra_body": {"enable_thinking": False, "reasoning_effort": "none"},
    }
    if response_format_json:
        kwargs["response_format"] = {"type": "json_object"}

    resp = await client.chat.completions.create(**kwargs)
    msg = resp.choices[0].message
    text = msg.content or ""
    # qwen3.5 等模型可能把所有内容放在 reasoning_content 中
    if not text:
        text = getattr(msg, "reasoning_content", None) or ""
    return text


# ---------- tool-use variant (for structured state output) ----------

_STATE_TOOL_SCHEMA = {
    "name": "update_state",
    "description": "更新你当前的心情和时空状态。每次回复后必须调用此工具。如果本轮对话发生了重要的「第一次」事件，请在 milestone 中记录。",
    "input_schema": {
        "type": "object",
        "properties": {
            "mood": {
                "type": "object",
                "properties": {
                    "happy": {"type": "integer", "description": "开心程度 0-100"},
                    "miss": {"type": "integer", "description": "想念程度 0-100"},
                    "jealous": {"type": "integer", "description": "嫉妒程度 0-100"},
                    "annoyed": {"type": "integer", "description": "烦躁程度 0-100"},
                    "excited": {"type": "integer", "description": "兴奋程度 0-100"},
                    "bored": {"type": "integer", "description": "无聊程度 0-100"},
                    "libido": {"type": "integer", "description": "情欲程度 0-100"},
                    "description": {"type": "string", "description": "用\"你\"开头简短描述当前心情"},
                },
                "required": ["happy", "miss", "jealous", "annoyed", "excited", "bored", "libido", "description"],
            },
            "spatial": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "用第三人称描述双方当前的位置、姿势、动作"},
                    "outfit": {"type": "string", "description": "描述你当前的衣着"},
                },
                "required": ["description", "outfit"],
            },
            "milestone": {
                "type": "object",
                "description": "（可选）如果本轮对话发生了一个重要的\"第一次\"事件，在此记录。每种类型只能记录一次，重复类型会被系统自动忽略。",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "里程碑类型",
                        "enum": [
                            "first_goodnight",
                            "first_morning",
                            "first_confession",
                            "first_date",
                            "first_kiss",
                            "first_argument",
                            "first_apology",
                            "first_jealousy",
                            "first_gift",
                            "first_meeting",
                            "custom",
                        ],
                    },
                    "title": {"type": "string", "description": "里程碑标题，如\"第一次说晚安\""},
                    "description": {"type": "string", "description": "简短描述这个时刻发生了什么"},
                },
                "required": ["type", "title"],
            },
        },
        "required": ["mood", "spatial"],
    },
}

_STATE_TOOL_SCHEMA_OPENAI = {
    "type": "function",
    "function": {
        "name": "update_state",
        "description": "更新你当前的心情和时空状态。每次回复后必须调用此工具。如果本轮对话发生了重要的「第一次」事件，请在 milestone 中记录。",
        "parameters": _STATE_TOOL_SCHEMA["input_schema"],
    },
}


async def call_with_state_tool(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 2048,
) -> tuple[str, dict[str, Any] | None]:
    """One-shot completion with a tool call for state output.

    Returns ``(text, tool_input)`` where *text* is the assistant's
    conversational reply and *tool_input* is the parsed JSON from the
    ``update_state`` tool call (or ``None`` if the model didn't call it).
    """
    if _get_protocol() == "openai":
        return await _call_with_state_tool_openai(messages, max_tokens=max_tokens)
    return await _call_with_state_tool_anthropic(messages, max_tokens=max_tokens)


async def _call_with_state_tool_anthropic(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 2048,
) -> tuple[str, dict[str, Any] | None]:
    system, rest = split_system_and_messages(messages)
    anthropic_messages = to_anthropic_messages(rest)

    _TOOL_REMINDER = "[系统提醒：请务必调用 update_state 工具来更新你的心情和时空状态。]"
    if anthropic_messages:
        last = anthropic_messages[-1]
        if last.get("role") == "user":
            c = last.get("content")
            if isinstance(c, str):
                last["content"] = _TOOL_REMINDER + "\n" + c
            elif isinstance(c, list):
                last["content"] = [{"type": "text", "text": _TOOL_REMINDER}] + c

    client = _get_anthropic_client()
    kwargs: dict[str, Any] = {
        "model": get_model(),
        "messages": anthropic_messages,
        "max_tokens": max_tokens,
        "tools": [_STATE_TOOL_SCHEMA],
        "extra_body": {"temperature": 0.7},
    }
    if system:
        kwargs["system"] = system

    resp = await client.messages.create(**kwargs)

    text_parts: list[str] = []
    tool_input: dict[str, Any] | None = None
    for block in resp.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(block.text)
        elif btype == "tool_use" and getattr(block, "name", "") == "update_state":
            raw_input = getattr(block, "input", None)
            if isinstance(raw_input, dict):
                tool_input = raw_input
    return "".join(text_parts), tool_input


async def _call_with_state_tool_openai(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 2048,
) -> tuple[str, dict[str, Any] | None]:
    _TOOL_REMINDER = "[系统提醒：请务必调用 update_state 工具来更新你的心情和时空状态。]"
    msg_copy = list(messages)
    if msg_copy:
        last = msg_copy[-1]
        if last.get("role") == "user":
            last["content"] = _TOOL_REMINDER + "\n" + last.get("content", "")

    client = _get_openai_client()
    resp = await client.chat.completions.create(
        model=get_model(),
        messages=msg_copy,
        max_tokens=max_tokens,
        temperature=0.7,
        tools=[_STATE_TOOL_SCHEMA_OPENAI],
        extra_body={"enable_thinking": False, "reasoning_effort": "none"},
    )

    choice = resp.choices[0]
    text = choice.message.content or ""
    # qwen3.5 等模型可能把所有内容放在 reasoning_content 中
    if not text:
        text = getattr(choice.message, "reasoning_content", None) or ""
    tool_input: dict[str, Any] | None = None
    if choice.message.tool_calls:
        for tc in choice.message.tool_calls:
            if tc.function and tc.function.name == "update_state":
                try:
                    tool_input = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    pass
    return text, tool_input


# ---------- SSE pack helper ----------

def _json_default(obj: Any) -> Any:
    """JSON encoder fallback for memU model objects that leak into SSE data."""
    if hasattr(obj, "to_iso8601_string"):
        return obj.to_iso8601_string()
    iso = getattr(obj, "isoformat", None)
    if callable(iso):
        return iso()
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:  # noqa: BLE001
            pass
    if hasattr(obj, "dict") and callable(obj.dict):
        try:
            return obj.dict()
        except Exception:  # noqa: BLE001
            pass
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return str(obj)


def sse_pack(event: str, data: dict[str, Any] | None = None) -> bytes:
    payload = json.dumps(data or {}, ensure_ascii=False, default=_json_default)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")
