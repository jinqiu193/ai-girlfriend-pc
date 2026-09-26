"""Helpers for the AI self-photo pool.

Three responsibilities:
1. Generate a one-line Chinese caption for an uploaded image using the
   configured vision-capable LLM (Anthropic-style; ``llm.call_once``).
2. Embed that caption with the local bge-m3 model and return raw bytes.
3. End-to-end: given a freshly-uploaded image, produce (caption,
   embedding_bytes). Failures are non-fatal — callers log and continue
   with empty caption / None embedding.

The chat flow also imports ``embed_query`` for retrieval.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("ai-girlfriend.image_pool")


# Keep the prompt short; vision calls are billed per token.
_CAPTION_PROMPT = (
    "用一句不超过 30 个汉字描述这张照片中人物的动作和行为。"
    "重点描述人物正在做什么、姿态、动作细节,而非场景环境或外貌长相。"
    "直接输出描述,不要加 '照片中'、'这张图' 之类前缀。"
)


# Thumbnail bytes tend to be < 50 KB even for 256x256 JPEGs, so base64
# stays well under the 5 MB Anthropic limit per image. PIL doesn't have
# to be imported here — we just sniff the magic byte for the MIME type.
_MAGIC_MIME: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # RIFF....WEBP
    (b"BM", "image/bmp"),
]


def _read_local_image_as_data_url(url_or_path: str) -> str | None:
    """Resolve a `/uploads/...` path or absolute path to a
    ``data:image/...;base64,...`` string. Returns ``None`` if the file
    is missing or the format is unrecognised. Already-data URLs and
    http(s) URLs are returned as-is."""
    if url_or_path.startswith("data:"):
        return url_or_path
    if url_or_path.startswith(("http://", "https://")):
        return url_or_path
    settings_path: Path | None = None
    if url_or_path.startswith("/uploads/"):
        from config import get_settings
        # /uploads/{user_id}/{char_id}/... → {data_dir}/uploads/{user_id}/...
        settings_path = get_settings().data_dir / "uploads" / url_or_path[len("/uploads/"):]
    elif url_or_path:
        settings_path = Path(url_or_path)
    if settings_path is None or not settings_path.is_file():
        return None
    raw = settings_path.read_bytes()
    mime = next((m for sig, m in _MAGIC_MIME if raw.startswith(sig)), None)
    if mime is None:
        return None
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def generate_caption(thumb_url: str) -> str:
    """Blocking wrapper around ``generate_caption_async``.

    Returns empty string on any failure (callers must treat empty as
    'caption pending/failed' — never crash the upload path).
    """
    import asyncio
    try:
        return asyncio.run(generate_caption_async(thumb_url))
    except Exception as exc:  # noqa: BLE001
        log.warning("generate_caption failed for %s: %s", thumb_url, exc)
        return ""


async def generate_caption_async(thumb_url: str) -> str:
    """Ask the configured vision LLM for a one-line Chinese caption.

    Returns:
        "" — caller's responsibility to retry (used by preset seed which
             retries on its own schedule).
        "[无法描述]" — vision call definitively failed or timed out;
             callers MUST NOT retry this. Persist as-is so the UI can
             show the failure label instead of "正在生成场景描述…"
             forever.
    """
    import asyncio
    try:
        from llm import call_once  # local import keeps startup light
        # Anthropic SDK rejects /uploads/... paths — convert to a
        # base64 data URL on the way out.
        inline = _read_local_image_as_data_url(thumb_url) or thumb_url
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _CAPTION_PROMPT},
                    {"type": "image_url", "image_url": {"url": inline}},
                ],
            }
        ]
        # Hard timeout: vision calls shouldn't block the upload response
        # or hang a background backfill forever. Sourced from settings so
        # it can be tuned without editing source.
        from config import get_settings
        text = await asyncio.wait_for(
            call_once(msgs, max_tokens=2048),
            timeout=get_settings().caption_timeout,
        )
        text = text.strip()
        text = text.strip("`").strip()
        if text.lower().startswith("caption"):
            text = text.split(":", 1)[-1].strip()
        text = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        return text[:80]
    except asyncio.TimeoutError:
        log.warning("generate_caption_async timed out for %s", thumb_url)
        return "[无法描述]"
    except Exception as exc:  # noqa: BLE001
        log.warning("generate_caption_async failed for %s: %s", thumb_url, exc)
        return "[无法描述]"


def embed_text(text: str) -> bytes | None:
    """Run the local bge-m3 encoder and return raw float32 bytes. None on failure."""
    if not text:
        return None
    try:
        from local_embed_server import _load_model
        from config import get_settings

        s = get_settings()
        model = _load_model(s.embed_model_name)
        vec = model.encode(
            [text], normalize_embeddings=True, show_progress_bar=False
        ).astype("float32")[0]
        return vec.tobytes()
    except Exception as exc:  # noqa: BLE001
        log.warning("embed_text failed: %s", exc)
        return None


def embed_query(text: str):
    """Same as ``embed_text`` but returns a numpy array (or None).
    Used by the chat picker for similarity search."""
    raw = embed_text(text)
    if raw is None:
        return None
    import numpy as np
    return np.frombuffer(raw, dtype=np.float32)


def caption_and_embed(thumb_url: str) -> tuple[str, bytes | None]:
    """Convenience: produce both at once, sharing the connection pool where possible."""
    caption = generate_caption(thumb_url)
    emb = embed_text(caption) if caption else None
    return caption, emb


# ---------- on-demand generation (MiniMax image-01) ----------

def generate_image(
    prompt: str, *, user_id: str, character_id: str
) -> tuple[str, str] | None:
    """Call MiniMax image-01 to generate one photo and save it locally.

    Blocking (use via ``asyncio.to_thread``). Returns
    ``(public_url, thumb_url)`` — both under /uploads/ — or ``None`` on
    any failure (missing key, content-filter rejection, download error).
    """
    from uuid import uuid4

    import httpx

    from config import get_settings

    s = get_settings()
    if not s.image_gen_api_key or s.image_gen_api_key in ("sk-replace-me",):
        log.info("image_gen: no api key configured, skip generation")
        return None

    endpoint = f"{s.image_gen_base_url.rstrip('/')}/v1/image_generation"
    payload = {
        "model": s.image_gen_model,
        "prompt": prompt,
        "aspect_ratio": "3:4",
        "response_format": "url",
        "n": 1,
    }
    try:
        with httpx.Client(timeout=s.image_gen_request_timeout) as client:
            r = client.post(
                endpoint,
                json=payload,
                headers={
                    "Authorization": f"Bearer {s.image_gen_api_key}",
                    "Content-Type": "application/json",
                },
            )
            data = r.json()
        base_resp = data.get("base_resp") or {}
        if base_resp.get("status_code", -1) not in (0, None):
            log.warning("image_gen rejected: %s %s", base_resp.get("status_code"), base_resp.get("status_msg"))
            return None
        image_urls = (data.get("data") or {}).get("image_urls") or []
        if not image_urls:
            log.warning("image_gen: no image_urls in response")
            return None

        with httpx.Client(timeout=s.image_gen_download_timeout, follow_redirects=True) as client:
            img = client.get(image_urls[0])
        if img.status_code != 200 or not img.content:
            log.warning("image_gen download failed: HTTP %s", img.status_code)
            return None
        content = img.content
    except Exception as exc:  # noqa: BLE001
        log.warning("image_gen failed: %s", exc)
        return None

    return _save_generated_bytes(content, user_id=user_id, character_id=character_id)


def _save_generated_bytes(
    content: bytes, *, user_id: str, character_id: str
) -> tuple[str, str] | None:
    """Sniff format, write under uploads/<user>/<char>/, make a thumbnail."""
    from uuid import uuid4

    from PIL import Image

    from config import get_settings

    if content[:8] == b"\x89PNG\r\n\x1a\n":
        ext = "png"
    elif content[:3] == b"\xff\xd8\xff":
        ext = "jpg"
    else:
        log.warning("image_gen: unsupported format (not PNG/JPEG)")
        return None

    s = get_settings()
    sub = Path(user_id) / (character_id or "_misc")
    target_dir = s.data_dir / "uploads" / sub
    target_dir.mkdir(parents=True, exist_ok=True)
    name = f"gen_{uuid4().hex}.{ext}"
    dest = target_dir / name
    dest.write_bytes(content)
    public_url = f"/uploads/{sub.as_posix()}/{name}"

    thumb_url = public_url
    try:
        with Image.open(dest) as im:
            im.thumbnail((256, 256))
            thumb_dir = target_dir / "_thumbs"
            thumb_dir.mkdir(exist_ok=True)
            fmt = "JPEG" if ext == "jpg" else "PNG"
            im.save(thumb_dir / name, format=fmt)
            thumb_url = f"/uploads/{sub.as_posix()}/_thumbs/{name}"
    except Exception:  # noqa: BLE001
        pass
    return public_url, thumb_url