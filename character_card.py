"""SillyTavern V1 / V2 / V3 character card parser.

Supports three inputs:
- raw JSON bytes / str
- PNG bytes (with ``ccv3`` or ``chara`` tEXt chunk)
- a file path to either

Standardises into one flat dict so the rest of the app does not have to care
about the spec version. Unknown fields under ``data.extensions`` are preserved
verbatim (the spec forbids editors from discarding them).
"""
from __future__ import annotations

import base64
import json
import re
import struct
import zlib
from pathlib import Path
from typing import Any

# ---------- PNG chunk reading ----------

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _iter_png_chunks(data: bytes):
    if data[:8] != PNG_SIG:
        raise ValueError("Not a PNG file")
    pos = 8
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        ctype = data[pos + 4 : pos + 8].decode("ascii", "replace")
        cdata = data[pos + 8 : pos + 8 + length]
        pos += 8 + length + 4  # +4 for CRC
        yield ctype, cdata


def _read_tEXt_value(chunk_data: bytes) -> bytes:
    """tEXt chunk layout: keyword \\x00 text"""
    if b"\x00" in chunk_data:
        return chunk_data.split(b"\x00", 1)[1]
    return chunk_data


def _extract_card_json_from_png(png_bytes: bytes) -> bytes:
    """Find a ``ccv3`` (preferred) or ``chara`` tEXt chunk and return the
    raw JSON bytes inside. Tries zlib+base64 first, falls back to plain.

    PNG spec: character card data lives in a ``tEXt`` chunk whose
    keyword is either ``chara`` (V2) or ``ccv3`` (V3). The chunk's type
    itself is always ``tEXt``; the keyword is the first NUL-separated
    part of the chunk's data.
    """
    payload: bytes | None = None
    for ctype, cdata in _iter_png_chunks(png_bytes):
        if ctype != "tEXt":
            continue
        if b"\x00" not in cdata:
            continue
        keyword = cdata.split(b"\x00", 1)[0]
        if keyword in (b"ccv3", b"chara"):
            payload = _read_tEXt_value(cdata)
            break
    if payload is None:
        raise ValueError("PNG does not contain a chara/ccv3 chunk")

    try:
        decompressed = zlib.decompress(payload)
    except zlib.error:
        decompressed = payload

    # decompressed is now the base64 string of the JSON
    try:
        return base64.b64decode(decompressed)
    except (binascii_error := Exception):  # noqa: F841
        # very old format: not base64 after decompression
        return decompressed


# binascii.Error only available at runtime
try:
    import binascii  # noqa: F401
except ImportError:
    pass


# ---------- JSON normalisation ----------

def _normalise(raw: dict[str, Any]) -> dict[str, Any]:
    spec = raw.get("spec")
    data = raw.get("data") if spec in ("chara_card_v2", "chara_card_v3") else raw

    # Tiny safety: if data is missing entirely, return a minimal skeleton.
    if not isinstance(data, dict):
        data = {}

    extensions = data.get("extensions") or {}
    if not isinstance(extensions, dict):
        extensions = {"_invalid": extensions}

    return {
        "spec": spec or "chara_card_v1",
        "spec_version": str(raw.get("spec_version", "1.0")),
        "name": data.get("name", "Unnamed"),
        "nickname": data.get("nickname") or data.get("name", "Unnamed"),
        "description": data.get("description", ""),
        "personality": data.get("personality", ""),
        "scenario": data.get("scenario", ""),
        "first_mes": data.get("first_mes", ""),
        "mes_example": data.get("mes_example", ""),
        "system_prompt": data.get("system_prompt", ""),
        "post_history_instructions": data.get("post_history_instructions", ""),
        "alternate_greetings": list(data.get("alternate_greetings") or []),
        "tags": list(data.get("tags") or []),
        "creator": data.get("creator", ""),
        "character_version": data.get("character_version", ""),
        "creator_notes": data.get("creator_notes", ""),
        "extensions": extensions,
        "assets": list(data.get("assets") or []),
        "character_book": data.get("character_book"),
    }


# ---------- public API ----------

def parse_card(source: bytes | str | Path) -> dict[str, Any]:
    """Parse a card from bytes (raw PNG or JSON) or a file path."""
    if isinstance(source, (str, Path)):
        path = Path(source)
        raw = path.read_bytes()
    elif isinstance(source, str):
        raw = source.encode("utf-8")
    else:
        raw = source

    # sniff: PNG magic → png; otherwise try JSON
    if raw[:8] == PNG_SIG:
        json_bytes = _extract_card_json_from_png(raw)
    else:
        json_bytes = raw

    raw_obj = json.loads(json_bytes)
    return _normalise(raw_obj)


# ---------- helpers used elsewhere ----------

def pick_main_icon_assets(card: dict[str, Any]) -> list[dict[str, Any]]:
    """All assets of type 'icon' — main is conventionally ``name == 'main'``."""
    return [a for a in card.get("assets", []) if a.get("type") == "icon"]


def lorebook_entries(card: dict[str, Any]) -> list[dict[str, Any]]:
    book = card.get("character_book")
    if not isinstance(book, dict):
        return []
    return [e for e in (book.get("entries") or []) if isinstance(e, dict)]