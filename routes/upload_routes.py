"""Standalone image upload endpoint (used when a user attaches an image
outside of the chat form, e.g. for an avatar preview)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile

from auth import current_user
from upload import save_image

router = APIRouter(prefix="/api")


@router.post("/upload")
async def upload_image(
    file: UploadFile = File(...),
    user_id: str = Depends(current_user),
):
    public_url, thumb_url = await save_image(upload=file, user_id=user_id)
    return {"url": public_url, "thumb_url": thumb_url}