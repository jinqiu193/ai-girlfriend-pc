"""Multi-AI group chat endpoints.

A group contains multiple AI characters. When the user sends a message,
each character replies in turn according to its own personality, seeing
the full group conversation history (including other characters' replies).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import character_card as cc
import db
from auth import current_user
from llm import call_once, sse_pack, stream_chat

log = logging.getLogger("ai-girlfriend.group")

router = APIRouter(prefix="/api")


class CreateGroupBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    character_ids: list[str] = Field(..., min_length=2)


@router.post("/groups")
def create_group(body: CreateGroupBody, user_id: str = Depends(current_user)):
    for cid in body.character_ids:
        if not db.get_character(cid, user_id):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"角色 {cid} 不存在")
    return db.create_group(user_id=user_id, name=body.name.strip(), character_ids=body.character_ids)


@router.get("/groups")
def list_groups(user_id: str = Depends(current_user)):
    return {"groups": db.list_groups(user_id=user_id)}


@router.get("/groups/{group_id}")
def get_group(group_id: str, user_id: str = Depends(current_user)):
    g = db.get_group(group_id, user_id)
    if not g:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return g


@router.delete("/groups/{group_id}")
def delete_group(group_id: str, user_id: str = Depends(current_user)):
    ok = db.delete_group(group_id, user_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"deleted": True}


@router.get("/groups/{group_id}/messages")
def get_group_messages(group_id: str, user_id: str = Depends(current_user)):
    if not db.get_group(group_id, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"messages": db.list_group_messages(group_id=group_id, user_id=user_id)}


def _build_group_system_prompt(card: dict[str, Any], group_name: str, member_names: list[str]) -> str:
    char_name = card.get("nickname") or card.get("name") or "角色"
    personality = card.get("personality", "")
    speech_style = card.get("speech_style", "")
    basic_info = card.get("basic_info", "")

    parts = [
        f"你是{char_name}，正在一个叫「{group_name}」的群聊里。",
        f"群里还有：{', '.join(member_names)}，以及用户。",
        "你在群聊里像真人一样自然发言，根据你的性格和说话风格回复。",
    ]
    if basic_info:
        parts.append(f"你的基本信息：{basic_info[:200]}")
    if personality:
        parts.append(f"你的性格：{personality[:200]}")
    if speech_style:
        parts.append(f"你的说话风格：{speech_style[:200]}")
    parts.append("- 回复简短自然，像微信群聊，一般10-40字")
    parts.append("- 可以回应其他人的发言，也可以表达自己的看法")
    parts.append("- 不要每次都回复很长，群聊里大家说话都简短")
    parts.append("- 用 *...* 描写小动作和表情")
    return "\n".join(parts)


def _build_group_messages(
    history: list[dict[str, Any]],
    system_prompt: str,
    current_user_msg: str,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for m in history[-20:]:
        if m["sender_type"] == "user":
            messages.append({"role": "user", "content": m["content"]})
        else:
            char_name = m.get("char_name") or "角色"
            messages.append({"role": "assistant", "content": f"[{char_name}]: {m['content']}"})
    messages.append({"role": "user", "content": current_user_msg})
    return messages


@router.get("/groups/{group_id}/send")
async def send_group_message(
    group_id: str,
    message: str,
    request: Request,
    user_id: str = Depends(current_user),
):
    group = db.get_group(group_id, user_id)
    if not group:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    db.add_group_message(
        group_id=group_id, user_id=user_id,
        sender_type="user", content=message,
    )

    history = db.list_group_messages(group_id=group_id, user_id=user_id, limit=30)
    members = group["members"]
    member_names = [m["name"] for m in members]

    async def event_stream():
        for member in members:
            char_id = member["character_id"]
            char = db.get_character(char_id, user_id)
            if not char:
                continue
            card = json.loads(char["card_json"])
            char_name = card.get("nickname") or card.get("name") or char["name"]

            system_prompt = _build_group_system_prompt(card, group["name"], member_names)
            fresh_history = db.list_group_messages(group_id=group_id, user_id=user_id, limit=30)
            messages = _build_group_messages(fresh_history, system_prompt, message)

            yield sse_pack("char_start", {"character_id": char_id, "name": char_name})

            acc = ""
            try:
                async for delta in stream_chat(messages, max_tokens=256):
                    acc += delta
                    yield sse_pack("token", {"character_id": char_id, "t": delta})
            except Exception as exc:
                log.warning("group chat reply failed for %s: %s", char_name, exc)
                acc = "..."
                yield sse_pack("token", {"character_id": char_id, "t": acc})

            acc = acc.strip()
            if acc:
                db.add_group_message(
                    group_id=group_id, user_id=user_id,
                    sender_type="character", character_id=char_id,
                    content=acc,
                )
            yield sse_pack("char_done", {"character_id": char_id, "name": char_name, "content": acc})

        yield sse_pack("done", {})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )