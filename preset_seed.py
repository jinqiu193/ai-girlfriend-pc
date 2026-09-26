"""One-shot preset image pool seeder.

Looks at ``<repo-root>/img/<character-name>/`` for each character owned
by the user and, when the character's pool is empty, copies every
``.jpg/.jpeg/.png/.webp`` into ``uploads/{user_id}/{char_id}/pool/``
and inserts a row into ``character_image_pool``. Caption + embedding
generation is scheduled as a fire-and-forget background task so app
startup isn't blocked on the vision LLM.

Idempotent: re-running is safe — we skip characters whose pool already
has at least one entry.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

import db
from config import get_settings

log = logging.getLogger("ai-girlfriend.preset_seed")


_REPO_ROOT = Path(__file__).resolve().parent.parent  # ai-girlfriend/../
_IMG_DIR = _REPO_ROOT / "img"
_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


async def seed_preset_pool() -> None:
    settings = get_settings()

    # Iterate every (user_id, character) currently in the DB.
    seen: set[tuple[str, str]] = set()
    with db.connect() as con:
        rows = con.execute(
            "SELECT id, user_id, name FROM characters"
        ).fetchall()

    for row in rows:
        char_id = row["id"]
        user_id = row["user_id"]
        key = (user_id, char_id)
        if key in seen:
            continue
        seen.add(key)

        # Backfill captions for any existing rows missing them (handles
        # previous failed runs where seed wrote empty caption rows).
        await _backfill_existing(user_id, char_id)

        if db.count_pool_images(user_id, char_id) > 0:
            continue  # user-managed pool takes priority
        if not _IMG_DIR.exists():
            continue

        short = short_name(row["name"])
        candidate = _IMG_DIR / short
        if not candidate.is_dir():
            continue
        images = sorted(
            p for p in candidate.iterdir()
            if p.is_file() and p.suffix.lower() in _EXTS
        )
        if not images:
            continue

        sname = short_name(row["name"])
        candidate = _IMG_DIR / sname
        if not candidate.is_dir():
            continue
        images = sorted(
            p for p in candidate.iterdir()
            if p.is_file() and p.suffix.lower() in _EXTS
        )
        if not images:
            continue

        # Copy into uploads/{user_id}/{char_id}/pool/{uuid}.{ext} so the
        # browser can fetch them via /uploads/. Also produce a 256x256
        # thumbnail using PIL — vision calls and the UI grid both want
        # the smaller version.
        from upload import _MAGIC  # reuse byte-sniff table
        import uuid as _uuid
        from PIL import Image

        target_dir = settings.data_dir / "uploads" / user_id / char_id / "pool"
        target_dir.mkdir(parents=True, exist_ok=True)
        thumb_dir = target_dir / "_thumbs"
        thumb_dir.mkdir(exist_ok=True)

        seeded: list[tuple[str, str, str]] = []  # (pool_id, url, thumb_url)
        for src in images:
            ext = src.suffix.lower().lstrip(".") or "jpg"
            new_name = f"{_uuid.uuid4().hex}.{ext}"
            dest = target_dir / new_name
            shutil.copy2(src, dest)
            thumb_path = thumb_dir / new_name
            try:
                with Image.open(dest) as im:
                    im.thumbnail((256, 256))
                    im.save(thumb_path)
            except Exception:
                # If PIL fails, just symlink the original as thumb.
                shutil.copy2(dest, thumb_path)
            public_url = f"/uploads/{user_id}/{char_id}/pool/{new_name}"
            thumb_url = f"/uploads/{user_id}/{char_id}/pool/_thumbs/{new_name}"
            pool_id = db.add_pool_image(
                user_id=user_id,
                character_id=char_id,
                url=public_url,
                thumb_url=thumb_url,
                caption="",
                embedding=None,
                source="preset",
            )
            seeded.append((pool_id, public_url, thumb_url))

        log.info(
            "seeded %d preset images for character %s (%s) from %s",
            len(seeded),
            char_id,
            sname,
            candidate,
        )

        # Fire-and-forget caption + embedding generation. We do this per
        # image so a slow LLM doesn't delay app startup. Failures are
        # logged and left as empty caption / null embedding — the row
        # is still queryable, just ranked last in retrieval.
        asyncio.create_task(_backfill_captions(user_id, char_id, seeded))


async def _backfill_captions(
    user_id: str, char_id: str, items: list[tuple[str, str, str]]
) -> None:
    from image_pool import generate_caption_async, embed_text

    for pool_id, _url, thumb_url in items:
        try:
            caption = await generate_caption_async(thumb_url)
            if caption == "[无法描述]":
                # Vision call definitively failed — persist the failure
                # label, no embedding, and move on (no retry loop here).
                db.update_pool_caption(
                    user_id, char_id, pool_id,
                    caption="无法描述",
                    embedding=None,
                )
                continue
            emb = await asyncio.to_thread(embed_text, caption) if caption else None
            db.update_pool_caption(
                user_id, char_id, pool_id,
                caption=caption or "",
                embedding=emb,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("backfill caption failed for %s: %s", pool_id, exc)


async def _backfill_existing(user_id: str, char_id: str) -> None:
    """Find pool rows with empty caption and re-run the captioner.
    Non-blocking — fire-and-forget task."""
    with db.connect() as con:
        rows = con.execute(
            "SELECT id, thumb_url FROM character_image_pool "
            "WHERE user_id = ? AND character_id = ? "
            "AND (caption IS NULL OR caption = '')",
            (user_id, char_id),
        ).fetchall()
    if not rows:
        return
    items = [(r["id"], "", r["thumb_url"]) for r in rows]
    asyncio.create_task(_backfill_captions(user_id, char_id, items))


def short_name(card_name: str) -> str:
    for sep in ("（", "(", " "):
        if sep in card_name:
            return card_name.split(sep, 1)[0].strip()
    return card_name.strip()