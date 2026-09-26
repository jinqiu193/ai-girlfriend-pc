"""Image upload helpers: byte-level MIME sniff + save + thumbnail."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from PIL import Image

from config import get_settings


# (signature_bytes, extension)
_MAGIC: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"RIFF", "webp"),  # RIFF....WEBP
    (b"BM", "bmp"),
]

# AVIF/HEIC: ftyp box at offset 4, brand at offset 8
_FTYP_BRANDS: dict[bytes, str] = {
    b"mif1": "avif", b"avif": "avif", b"avis": "avif",
    b"heic": "heic", b"heix": "heic", b"hevc": "heic",
}


def _sniff_ext(head: bytes) -> str | None:
    ext = next((e for sig, e in _MAGIC if head.startswith(sig)), None)
    if ext is not None:
        return ext
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return _FTYP_BRANDS.get(head[8:12])
    return None


async def save_image(
    upload: UploadFile, *, user_id: str, character_id: str | None = None
) -> tuple[str, str]:
    """Validate, persist, and thumbnail an uploaded image.

    Returns ``(public_url, thumb_url)`` — both relative to /uploads/.
    """
    settings = get_settings()
    head = await upload.read(16)
    await upload.seek(0)

    ext = _sniff_ext(head)
    if ext is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="unsupported image type")

    sub = Path(user_id) / (character_id or "_misc")
    target_dir = settings.data_dir / "uploads" / sub
    target_dir.mkdir(parents=True, exist_ok=True)

    name = f"{uuid.uuid4().hex}.{ext}"
    dest = target_dir / name
    size_limit = settings.max_upload_mb * 1024 * 1024
    written = 0
    with dest.open("wb") as fh:
        while chunk := await upload.read(64 * 1024):
            written += len(chunk)
            if written > size_limit:
                fh.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"image exceeds {settings.max_upload_mb} MB",
                )
            fh.write(chunk)

    public_url = f"/uploads/{sub.as_posix()}/{name}"

    # Thumbnail (best-effort)
    thumb_url = public_url
    try:
        with Image.open(dest) as im:
            im.thumbnail((256, 256))
            thumb_dir = target_dir / "_thumbs"
            thumb_dir.mkdir(exist_ok=True)
            thumb_path = thumb_dir / name
            ext_upper = ext.upper()
            if ext_upper in ("JPEG", "JPG"):
                ext_upper = "JPEG"
            im.save(thumb_path, format=ext_upper if ext_upper in ("PNG", "JPEG", "GIF", "BMP", "WEBP") else "PNG")
            thumb_url = f"/uploads/{sub.as_posix()}/_thumbs/{name}"
    except Exception:
        pass

    return public_url, thumb_url