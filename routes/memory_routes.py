"""Long-term memory retrieval — used by the Contacts detail panel.

This is a thin REST wrapper around ``memu.progressive_retrieve`` so that views
outside the chat SSE pipeline (e.g. the character detail modal) can fetch the
top memory segments for browsing. Failure is intentionally non-fatal — the
frontend treats empty segments as "no recall yet" instead of an error.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request, status

from auth import current_user
from config import get_settings
from memu.app import MemoryService

router = APIRouter(prefix="/api/memory")
log = logging.getLogger("ai-girlfriend.memory")


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _scope(user_id: str, character_id: str) -> dict[str, str]:
    return {"user_id": user_id, "character_id": character_id}


def _get_memory_service(request: Request) -> MemoryService:
    return request.app.state.memu


def _file_to_dict(r: Any) -> dict[str, Any]:
    return {
        "id":          r.id,
        "name":        getattr(r, "name", ""),
        "description": getattr(r, "description", ""),
        "content":     getattr(r, "content", ""),
        "track":       getattr(r, "track", "memory"),
        "created_at":  _iso(getattr(r, "created_at", None)),
        "updated_at":  _iso(getattr(r, "updated_at", None)),
        "score":       1.0,
    }


async def _recompute_embedding(text: str) -> list[float] | None:
    """Re-embed a single string and return the vector, or None on failure.

    Uses the local bge-m3 model in-process when enabled; otherwise falls back
    to the configured remote embedding endpoint via httpx. Failure is non-fatal
    — the caller will persist the new content without a fresh vector, which
    only downgrades recall precision for that one record until the next commit.
    """
    settings = get_settings()
    if not text:
        return None
    if settings.embed_local_enabled:
        try:
            import local_embed_server as les
            model = les._load_model(settings.embed_model_name)  # noqa: SLF001
            vec = model.encode([text], normalize_embeddings=True, show_progress_bar=False).tolist()[0]
            return [float(x) for x in vec]
        except Exception as exc:  # noqa: BLE001
            log.warning("local re-embed failed: %s", exc)
            return None
    # Remote fallback — reuse memU's own embedding client by hitting the same
    # base_url it would call. We don't have a direct handle on the client here,
    # so we go via HTTP.
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as cli:
            r = await cli.post(
                f"{settings.embedding_base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {settings.embedding_api_key}"},
                json={"model": settings.embedding_model, "input": text},
            )
            r.raise_for_status()
            return [float(x) for x in r.json()["data"][0]["embedding"]]
    except Exception as exc:  # noqa: BLE001
        log.warning("remote re-embed failed: %s", exc)
        return None


@router.get("/recall")
async def recall(
    request: Request,
    character_id: str = Query(...),
    query: str = Query("(browse)"),
    user_id: str = Depends(current_user),
):
    memu = _get_memory_service(request)
    try:
        result = await asyncio.wait_for(
            memu.progressive_retrieve(
                query,
                where=_scope(user_id, character_id),
            ),
            timeout=5.0,
        )
    except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
        # Detail view is auxiliary; never escalate memU hiccups to 5xx.
        return {"segments": [], "files": [], "warning": str(exc)}
    return {
        "segments": result.get("segments", [])[:5],
        "files": result.get("files", [])[:3],
    }


@router.get("/files")
async def list_files(
    request: Request,
    character_id: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
    user_id: str = Depends(current_user),
):
    """Browse-mode listing for the Contacts detail panel.

    The chat SSE pipeline uses ``/recall`` which is similarity-ranked — useful
    for in-conversation recall but bad for the "show me everything she
    remembers" view, where the user expects to see the most recent memories
    first regardless of vector similarity. We read directly from the storage
    layer instead so ordering is by ``created_at`` desc.
    """
    memu = _get_memory_service(request)
    scope = _scope(user_id, character_id)
    try:
        repo = memu.database.recall_file_repo
        rows_map = repo.list_recall_files(where=scope)
        rows = list(rows_map.values()) if isinstance(rows_map, dict) else list(rows_map)
        rows = [r for r in rows if getattr(r, "track", "memory") == "memory"]
        rows.sort(key=lambda r: getattr(r, "created_at", "") or "", reverse=True)
        rows = rows[:limit]
        return {"files": [_file_to_dict(r) for r in rows]}
    except Exception as exc:  # noqa: BLE001
        return {"files": [], "warning": str(exc)}


@router.patch("/files/{file_id}")
async def update_file(
    request: Request,
    file_id: str = Path(...),
    payload: dict[str, Any] = Body(...),
    user_id: str = Depends(current_user),
):
    """Edit description and/or content of a memory file.

    `character_id` must be supplied in the body so we can scope-check that the
    caller owns this record before mutating it.
    """
    memu = _get_memory_service(request)
    character_id = payload.get("character_id")
    description = payload.get("description")
    content = payload.get("content")
    if not character_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "character_id required")
    if description is None and content is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "description or content required")
    if description is not None and not isinstance(description, str):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "description must be string")
    if content is not None and not isinstance(content, str):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "content must be string")
    if description is not None and not description.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "description cannot be empty")
    if content is not None and not content.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "content cannot be empty")

    scope = _scope(user_id, character_id)
    repo = memu.database.recall_file_repo
    # Scope check: confirm the row belongs to this (user, character) before mutating.
    rows_map = repo.list_recall_files(where=scope)
    rows = list(rows_map.values()) if isinstance(rows_map, dict) else list(rows_map)
    target = next((r for r in rows if r.id == file_id), None)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "memory not found in this scope")

    kwargs: dict[str, Any] = {}
    if description is not None:
        kwargs["description"] = description.strip()
    if content is not None:
        kwargs["content"] = content.strip()
        # content drives retrieval semantics → re-embed so the next recall hit
        # actually surfaces the new wording.
        new_vec = await _recompute_embedding(content.strip())
        if new_vec is not None:
            kwargs["embedding"] = new_vec
    try:
        updated = repo.update_recall_file(recall_file_id=file_id, **kwargs)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "memory disappeared during update")

    return {
        "updated": True,
        "id": updated.id,
        "description": updated.description,
        "content": updated.content,
        "embedding_updated": "embedding" in kwargs,
    }


@router.delete("/files/{file_id}")
async def delete_file(
    request: Request,
    file_id: str = Path(...),
    character_id: str = Query(...),
    user_id: str = Depends(current_user),
):
    """Hard-delete a memory file and all its segments.

    postgres: relies on FK ON DELETE CASCADE for segments.
    sqlite / inmemory: must explicitly wipe segments first or they remain
    as orphans (no FK constraint on either).
    """
    memu = _get_memory_service(request)
    scope = _scope(user_id, character_id)

    file_repo = memu.database.recall_file_repo
    seg_repo = memu.database.recall_file_segment_repo

    rows_map = file_repo.list_recall_files(where=scope)
    rows = list(rows_map.values()) if isinstance(rows_map, dict) else list(rows_map)
    target = next((r for r in rows if r.id == file_id), None)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "memory not found in this scope")

    # Always wipe segments explicitly — postgres' cascade makes this a no-op
    # for the DB side, but the inmemory segment store has no FK concept so we
    # need to clear it there regardless of backend.
    try:
        seg_repo.delete_segments_for_file(file_id)
    except Exception as exc:  # noqa: BLE001
        # If this fails (e.g. cascade-only backend that doesn't expose
        # delete_segments_for_file), don't abort — fall through to the file
        # delete and let the DB cascade handle it.
        log.warning("delete_segments_for_file failed (likely cascade-only backend): %s", exc)

    deleted = file_repo.clear_recall_files(where={**scope, "id": file_id})
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "memory disappeared during delete")

    return {"deleted": True, "id": file_id}