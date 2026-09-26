"""Chat history import endpoint.

POST /api/import_chat
    multipart form-data:
        file: the exported JSON file
        format: (optional) "auto" | "telegram" | "qq_chat_exporter" | "generic"

Returns a preview object::

    {
      "format_detected": "qq_chat_exporter",
      "segments": [
        {
          "chat_type": "private",
          "title": "...",
          "message_count": 1234,
          "candidate": { name, message_count, sample_messages, ... } | null
        }
      ]
    }

POST /api/import_chat/confirm
    JSON body: { segment_index: int, character_name: str? }
    Effects:
      1. derive a V2 character card from the candidate's messages
      2. write the conversation as recall_files into memU
      3. create a `characters` row, set active_character_id in session
    Returns the created character id + summary.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

import chat_import
import db
from auth import current_user
from chat_loaders import LOADERS, detect_format, iter_messages
from chat_loaders.base import ChatSegment
from memu.app import MemoryService

router = APIRouter(prefix="/api/import_chat")


@router.post("")
async def import_preview(
    file: UploadFile = File(...),
    format: str = Form("auto"),
    user_id: str = Depends(current_user),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="empty file")

    fmt = format if format != "auto" else detect_format(raw)
    if fmt is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="could not detect format; please specify one of: "
            + ", ".join(LOADERS.keys()),
        )
    if fmt not in LOADERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"unknown format '{fmt}'")

    try:
        segments = list(iter_messages(raw, format_key=fmt))
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"parse failed: {exc}") from exc

    out: list[dict[str, Any]] = []
    for seg in segments:
        cand = chat_import.pick_candidate(seg)
        out.append(
            {
                "chat_type": seg.chat_type,
                "title": seg.title,
                "message_count": len(seg.messages),
                "text_message_count": sum(1 for m in seg.messages if m.message_type == "text"),
                "first_timestamp": seg.messages[0].timestamp if seg.messages else "",
                "last_timestamp": seg.messages[-1].timestamp if seg.messages else "",
                "candidate": cand,
            }
        )

    return {"format_detected": fmt, "segments": out}


@router.post("/confirm")
async def import_confirm(
    request: Request,
    payload: dict[str, Any],
    user_id: str = Depends(current_user),
):
    segment_index: int = int(payload.get("segment_index", 0))
    override_name: str | None = payload.get("character_name")
    raw_b64: str | None = payload.get("raw_b64")  # base64 of original file
    fmt: str = payload.get("format", "auto")

    if not raw_b64:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="raw_b64 required")
    import base64

    raw = base64.b64decode(raw_b64)
    segments = list(iter_messages(raw, format_key=fmt if fmt != "auto" else None))
    if segment_index < 0 or segment_index >= len(segments):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid segment_index")

    seg: ChatSegment = segments[segment_index]
    candidate = chat_import.pick_candidate(seg)
    if candidate is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="this chat has no clear 'other side' (only private chats are supported in MVP)",
        )
    char_name = override_name or candidate["name"]

    # 1. derive character card via LLM
    try:
        card = await chat_import.derive_character_card(candidate)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"LLM character derivation failed: {exc}"
        ) from exc

    # 2. create the character row in our DB (overwrite if same name)
    existing_id = None
    for row in db.list_characters(user_id):
        if row["name"] == char_name:
            existing_id = row["id"]
            break
    if existing_id:
        db.delete_character(existing_id, user_id)
        cid, _action = db.create_character(
            user_id=user_id,
            name=char_name,
            spec="v2",
            card=card,
            avatar_path=None,
        )
    else:
        cid, _action = db.create_character(
            user_id=user_id,
            name=char_name,
            spec="v2",
            card=card,
            avatar_path=None,
        )

    request.session["active_character_id"] = cid

    # 3. commit conversation to memU
    memu: MemoryService = request.app.state.memu
    ingest = await chat_import.ingest_segment_to_memu(
        segment=seg,
        user_id=user_id,
        character_id=cid,
        character_name=char_name,
        memu=memu,
    )

    return {
        "character_id": cid,
        "character_name": char_name,
        "card_spec": card.get("spec", "chara_card_v2"),
        "memory_files_committed": ingest.memory_files_committed,
        "recall_segments_written": ingest.recall_segments_written,
    }