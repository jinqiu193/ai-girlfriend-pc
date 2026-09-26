"""Lightweight SQLite access for business tables (users, characters, messages).

Uses stdlib sqlite3 synchronously — these queries are tiny and we call them
from FastAPI's async routes via ``asyncio.to_thread`` where blocking matters.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from config import get_settings


SCHEMA_FILE = Path(__file__).parent / "schemas.sql"

# Schema DDL + migrations are idempotent but expensive: ``executescript``
# re-parses ~340 lines of DDL and ``_migrate`` issues several
# ``PRAGMA table_info`` per call. A single chat request makes 15-25 DB
# calls, so running this on every ``connect()`` dominated request time.
# We now run it once per database file (tracked by path so tests using
# temp DBs still get initialised).
_schema_lock = threading.Lock()
_schema_ready_paths: set[str] = set()


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _ensure_schema(conn: sqlite3.Connection, path: str) -> None:
    """Run schema DDL + migrations exactly once per database file."""
    if path in _schema_ready_paths:
        return
    with _schema_lock:
        if path in _schema_ready_paths:
            return
        with open(SCHEMA_FILE, "r", encoding="utf-8") as fh:
            conn.executescript(fh.read())
        _migrate(conn)
        _schema_ready_paths.add(path)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(settings.db_path)
    try:
        _ensure_schema(conn, str(settings.db_path))
        yield conn
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent column additions for databases created before the column
    existed. SQLite has no ADD COLUMN IF NOT EXISTS, so guard via PRAGMA."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if "excluded_from_context" not in cols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN "
            "excluded_from_context INTEGER NOT NULL DEFAULT 0"
        )
    if "debug_ctx" not in cols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN debug_ctx TEXT"
        )
    char_cols = {row[1] for row in conn.execute("PRAGMA table_info(characters)")}
    if char_cols and "active_scene_id" not in char_cols:
        conn.execute(
            "ALTER TABLE characters ADD COLUMN active_scene_id TEXT NOT NULL DEFAULT ''"
        )
    if char_cols and "card_type" not in char_cols:
        conn.execute(
            "ALTER TABLE characters ADD COLUMN card_type TEXT NOT NULL DEFAULT 'character'"
        )
    if char_cols and "cover_url" not in char_cols:
        conn.execute(
            "ALTER TABLE characters ADD COLUMN cover_url TEXT"
        )
    if char_cols and "story_status" not in char_cols:
        conn.execute(
            "ALTER TABLE characters ADD COLUMN story_status TEXT NOT NULL DEFAULT 'ongoing'"
        )
    spatial_cols = {row[1] for row in conn.execute("PRAGMA table_info(spatial_state)")}
    if spatial_cols and "description" not in spatial_cols:
        conn.execute(
            "ALTER TABLE spatial_state ADD COLUMN description TEXT NOT NULL DEFAULT ''"
        )
    if spatial_cols and "outfit" not in spatial_cols:
        conn.execute(
            "ALTER TABLE spatial_state ADD COLUMN outfit TEXT NOT NULL DEFAULT ''"
        )
    moment_cols = {row[1] for row in conn.execute("PRAGMA table_info(character_moments)")}
    if moment_cols and "author_type" not in moment_cols:
        conn.execute(
            "ALTER TABLE character_moments ADD COLUMN author_type TEXT NOT NULL DEFAULT 'character'"
        )


# ---------- characters ----------

def create_character(
    *, user_id: str, name: str, spec: str, card: dict[str, Any], avatar_path: str | None,
    card_type: str = "character",
) -> tuple[str, str]:
    """Upsert: if a character with the same (user_id, name) exists, replace
    its card_json + spec + avatar_path and reuse the existing id so the
    message history stays attached. Otherwise insert a fresh row.

    Returns ``(char_id, action)`` where ``action`` is ``"updated"`` or
    ``F"inserted"``.
    """
    payload = json.dumps(card, ensure_ascii=False)
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM characters WHERE user_id = ? AND name = ?",
            (user_id, name),
        ).fetchone()
        if existing is not None:
            char_id = existing["id"]
            conn.execute(
                "UPDATE characters SET spec = ?, card_json = ?, avatar_path = ?, card_type = ? "
                "WHERE id = ? AND user_id = ?",
                (spec, payload, avatar_path, card_type, char_id, user_id),
            )
            return char_id, "updated"
        char_id = uuid4().hex
        conn.execute(
            "INSERT INTO characters (id, user_id, name, spec, card_type, card_json, avatar_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (char_id, user_id, name, spec, card_type, payload, avatar_path),
        )
    return char_id, "inserted"


def list_characters(user_id: str, card_type: str | None = None) -> list[dict[str, Any]]:
    with connect() as con:
        if card_type:
            rows = con.execute(
                "SELECT id, name, spec, card_type, avatar_path, cover_url, story_status, created_at FROM characters "
                "WHERE user_id = ? AND card_type = ? ORDER BY created_at",
                (user_id, card_type),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT id, name, spec, card_type, avatar_path, cover_url, story_status, created_at FROM characters "
                "WHERE user_id = ? ORDER BY created_at",
                (user_id,),
            ).fetchall()
    return [dict(r) for r in rows]


def count_characters(user_id: str) -> int:
    with connect() as con:
        row = con.execute(
            "SELECT COUNT(*) AS n FROM characters WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return int(row["n"]) if row else 0


def storage_info() -> dict[str, Any]:
    """Return diagnostic info about the on-disk storage: paths, sizes,
    row counts. The user can hit /api/storage to verify persistence
    themselves — the response never includes message content."""
    with connect() as con:
        total_chars = con.execute("SELECT COUNT(*) AS n FROM characters").fetchone()["n"]
        total_msgs = con.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
    return {
        "db_path": str(_db_path()),
        "db_exists": _db_path().exists(),
        "db_size_bytes": _db_path().stat().st_size if _db_path().exists() else 0,
        "uploads_path": str(_uploads_path()),
        "total_characters": int(total_chars),
        "total_messages": int(total_msgs),
    }


def _db_path() -> Path:
    from config import get_settings
    return get_settings().db_path


def _uploads_path() -> Path:
    from config import get_settings
    return get_settings().data_dir / "uploads"


def get_character(char_id: str, user_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM characters WHERE id = ? AND user_id = ?",
            (char_id, user_id),
        ).fetchone()
    if row is None:
        return None
    out = dict(row)
    # Decoded once at the source so callers always see a Python object,
    # not a JSON string. Existing routes that called ``json.loads`` on
    # this field continue to work — the decoded value is already a dict.
    if isinstance(out.get("card_json"), str) and out["card_json"]:
        try:
            out["card"] = json.loads(out["card_json"])
        except (json.JSONDecodeError, ValueError):
            out["card"] = {}
    return out


def delete_character(char_id: str, user_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM characters WHERE id = ? AND user_id = ?",
            (char_id, user_id),
        )
    return cur.rowcount > 0


# ---------- image pool (AI self-photos per character) ----------

def add_pool_image(
    *,
    user_id: str,
    character_id: str,
    url: str,
    thumb_url: str,
    caption: str = "",
    embedding: bytes | None = None,
    source: str = "manual",
) -> str:
    pool_id = uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO character_image_pool "
            "(id, user_id, character_id, url, thumb_url, caption, caption_embedding, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pool_id, user_id, character_id, url, thumb_url, caption, embedding, source),
        )
    return pool_id


def list_pool_images(user_id: str, character_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, url, thumb_url, caption, source, created_at "
            "FROM character_image_pool "
            "WHERE user_id = ? AND character_id = ? "
            "ORDER BY created_at",
            (user_id, character_id),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "url": r["url"],
            "thumb_url": r["thumb_url"],
            "caption": r["caption"] or "",
            "source": r["source"] or "manual",
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def get_pool_image(user_id: str, character_id: str, pool_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM character_image_pool "
            "WHERE id = ? AND user_id = ? AND character_id = ?",
            (pool_id, user_id, character_id),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "url": row["url"],
        "thumb_url": row["thumb_url"],
        "caption": row["caption"] or "",
        "source": row["source"] or "manual",
        "created_at": row["created_at"],
    }


def edit_pool_caption(
    user_id: str,
    character_id: str,
    pool_id: str,
    *,
    caption: str,
    embedding: bytes | None,
) -> bool:
    """Update only the caption (+ optionally the embedding) for a pool row.
    Used by the manual-edit endpoint so the user can rewrite what the
    vision model produced without re-running vision."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE character_image_pool "
            "SET caption = ?, caption_embedding = ? "
            "WHERE id = ? AND user_id = ? AND character_id = ?",
            (caption, embedding, pool_id, user_id, character_id),
        )
    return cur.rowcount > 0


def delete_pool_image(user_id: str, character_id: str, pool_id: str) -> dict[str, Any] | None:
    """Delete one row and return the deleted record (for caller to unlink
    the actual file). Returns None when the row doesn't exist or the
    caller doesn't own it."""
    with connect() as conn:
        row = conn.execute(
            "SELECT url, thumb_url FROM character_image_pool "
            "WHERE id = ? AND user_id = ? AND character_id = ?",
            (pool_id, user_id, character_id),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "DELETE FROM character_image_pool "
            "WHERE id = ? AND user_id = ? AND character_id = ?",
            (pool_id, user_id, character_id),
        )
    return {"url": row["url"], "thumb_url": row["thumb_url"]}


def delete_all_pool_images(user_id: str, character_id: str) -> list[dict[str, Any]]:
    """Delete every pool row of this character (manual upload, generated,
    preset — all sources). Returns the deleted records so the caller can
    unlink the actual files."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT url, thumb_url FROM character_image_pool "
            "WHERE user_id = ? AND character_id = ?",
            (user_id, character_id),
        ).fetchall()
        conn.execute(
            "DELETE FROM character_image_pool "
            "WHERE user_id = ? AND character_id = ?",
            (user_id, character_id),
        )
    return [{"url": r["url"], "thumb_url": r["thumb_url"]} for r in rows]


def update_pool_caption(
    user_id: str,
    character_id: str,
    pool_id: str,
    *,
    caption: str,
    embedding: bytes | None,
) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE character_image_pool "
            "SET caption = ?, caption_embedding = ? "
            "WHERE id = ? AND user_id = ? AND character_id = ?",
            (caption, embedding, pool_id, user_id, character_id),
        )
    return cur.rowcount > 0


def pick_pool_by_embedding(
    user_id: str,
    character_id: str,
    query_vec,
    *,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """Return up to ``top_k`` pool entries ranked by cosine similarity to
    ``query_vec`` (a 1-D numpy array or array-like of float32). Rows with
    no embedding (caption generation still in flight) are appended last
    in stable order so the vision model always has 3 candidates to
    consider when at least 3 entries exist.
    """
    import numpy as np

    with connect() as conn:
        rows = conn.execute(
            "SELECT id, url, thumb_url, caption, source, caption_embedding "
            "FROM character_image_pool "
            "WHERE user_id = ? AND character_id = ?",
            (user_id, character_id),
        ).fetchall()

    if not rows:
        return []

    query = np.asarray(query_vec, dtype=np.float32).reshape(-1)
    # If the query is all zeros (very rare), we still need a stable order.
    q_norm = float(np.linalg.norm(query))
    q_unit = query / q_norm if q_norm > 1e-9 else query

    scored: list[tuple[float, dict[str, Any]]] = []
    no_emb: list[dict[str, Any]] = []
    for r in rows:
        rec = {
            "id": r["id"],
            "url": r["url"],
            "thumb_url": r["thumb_url"],
            "caption": r["caption"] or "",
            "source": r["source"] or "manual",
            "score": 0.0,
        }
        blob = r["caption_embedding"]
        if not blob:
            no_emb.append(rec)
            continue
        v = np.frombuffer(blob, dtype=np.float32)
        # bge-m3 embeddings are pre-normalized; if not, fall back to cosine.
        v_norm = float(np.linalg.norm(v))
        if v_norm > 1e-9:
            sim = float(np.dot(q_unit, v / v_norm))
        else:
            sim = 0.0
        rec["score"] = sim
        scored.append((sim, rec))

    scored.sort(key=lambda x: x[0], reverse=True)
    out = [rec for _, rec in scored]
    if len(out) < top_k:
        out.extend(no_emb[: top_k - len(out)])
    return out[:top_k]


def count_pool_images(user_id: str, character_id: str) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM character_image_pool "
            "WHERE user_id = ? AND character_id = ?",
            (user_id, character_id),
        ).fetchone()
    return int(row["n"]) if row else 0


def update_character(
    *,
    char_id: str,
    user_id: str,
    card: dict[str, Any] | None = None,
    name: str | None = None,
    avatar_path: str | None = None,
) -> bool:
    """Partial update by id. Only the kwargs that are not None are touched.

    ``avatar_path`` uses ``COALESCE`` so a None here preserves the existing
    column value (pass an explicit empty string later if we ever want to clear
    it; not exposed by the current UI).
    """
    sets: list[str] = []
    params: list[Any] = []
    if card is not None:
        sets.append("card_json = ?")
        params.append(json.dumps(card, ensure_ascii=False))
        if name is not None:
            sets.append("name = ?")
            params.append(name)
    if avatar_path is not None:
        sets.append("avatar_path = COALESCE(?, avatar_path)")
        params.append(avatar_path)
    if not sets:
        return False
    params.extend([char_id, user_id])
    with connect() as conn:
        cur = conn.execute(
            f"UPDATE characters SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
            params,
        )
    return cur.rowcount > 0


# ---------- messages ----------

def append_message(
    *,
    user_id: str,
    character_id: str,
    role: str,
    content: str,
    image_paths: list[str] | None = None,
    excluded_from_context: bool = False,
    debug_ctx: str | None = None,
) -> int:
    paths = ",".join(image_paths) if image_paths else None
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO messages (user_id, character_id, role, content, image_paths, excluded_from_context, debug_ctx) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, character_id, role, content, paths, 1 if excluded_from_context else 0, debug_ctx),
        )
    return int(cur.lastrowid)


def recent_messages(
    *, user_id: str, character_id: str, limit: int, offset: int = 0
) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, role, content, image_paths, excluded_from_context, debug_ctx, created_at FROM messages "
            "WHERE user_id = ? AND character_id = ? "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (user_id, character_id, limit, offset),
        ).fetchall()
    out = [dict(r) for r in rows]
    out.reverse()  # chronological order
    return out


def count_messages(*, user_id: str, character_id: str) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE user_id = ? AND character_id = ?",
            (user_id, character_id),
        ).fetchone()
    return int(row[0])


def last_message_for_character(*, user_id: str, character_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT role, content, created_at FROM messages "
            "WHERE user_id = ? AND character_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (user_id, character_id),
        ).fetchone()
    return dict(row) if row else None


def delete_message(*, message_id: int, user_id: str, character_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM messages WHERE id = ? AND user_id = ? AND character_id = ?",
            (message_id, user_id, character_id),
        )
    return cur.rowcount > 0


def delete_messages(*, message_ids: list[int], user_id: str, character_id: str) -> int:
    if not message_ids:
        return 0
    placeholders = ",".join("?" for _ in message_ids)
    with connect() as conn:
        cur = conn.execute(
            f"DELETE FROM messages WHERE id IN ({placeholders}) AND user_id = ? AND character_id = ?",
            (*message_ids, user_id, character_id),
        )
    return cur.rowcount


def recent_messages_for_context(
    *, user_id: str, character_id: str, limit: int
) -> list[dict[str, Any]]:
    """History fed to the LLM: drops turns flagged ``excluded_from_context``
    (failed/fallback turns kept in the visible log but kept out of the
    model's context to avoid re-triggering the same failure)."""
    rows = recent_messages(user_id=user_id, character_id=character_id, limit=limit)
    return [r for r in rows if not r.get("excluded_from_context")]


def exclude_all_messages(*, user_id: str, character_id: str) -> int:
    """Mark every message of this character as ``excluded_from_context=1``.

    Used by the "clear chat" button: rows stay in the DB (visible on
    refresh with .skipped styling) but are kept out of the LLM context
    window so new character settings take full effect.
    """
    with connect() as conn:
        cur = conn.execute(
            "UPDATE messages SET excluded_from_context = 1 "
            "WHERE user_id = ? AND character_id = ?",
            (user_id, character_id),
        )
    return cur.rowcount


_LLM_FALLBACK_REPLIES: frozenset[str] = frozenset({"哈哈哈"})


def _is_fallback_reply(content: str | None) -> bool:
    """True if ``content`` is a synthetic LLM fallback (not real model output)."""
    if not content:
        return False
    return content.strip() in _LLM_FALLBACK_REPLIES


def recent_messages_excluding_fallback(
    *, user_id: str, character_id: str, limit: int
) -> list[dict[str, Any]]:
    """Like ``recent_messages`` but drops assistant rows whose content
    matches the synthetic LLM fallback — defensive cleanup for history
    written before the whole-turn skip was added in /api/chat/send.
    User-side rows still surface; only the synthetic AI replies are hidden.
    """
    rows = recent_messages(user_id=user_id, character_id=character_id, limit=limit)
    return [r for r in rows if r["role"] != "assistant" or not _is_fallback_reply(r["content"])]


def character_stats(*, user_id: str, character_id: str) -> dict[str, Any]:
    """Aggregate conversation stats for one character — full history, no window cap."""
    with connect() as conn:
        row = conn.execute(
            "SELECT "
            "  COUNT(*) AS total, "
            "  SUM(CASE WHEN role='user'      THEN 1 ELSE 0 END) AS user_count, "
            "  SUM(CASE WHEN role='assistant' THEN 1 ELSE 0 END) AS assistant_count, "
            "  SUM(LENGTH(content)) AS total_chars, "
            "  MIN(created_at) AS first_ts, "
            "  MAX(created_at) AS last_ts "
            "FROM messages WHERE user_id = ? AND character_id = ?",
            (user_id, character_id),
        ).fetchone()
    d = dict(row) if row else {}
    return {
        "total":           int(d.get("total") or 0),
        "user_count":      int(d.get("user_count") or 0),
        "assistant_count": int(d.get("assistant_count") or 0),
        "total_chars":     int(d.get("total_chars") or 0),
        "first_ts":        d.get("first_ts"),
        "last_ts":         d.get("last_ts"),
    }


def list_message_image_paths(message_id: int) -> list[str]:
    with connect() as conn:
        row = conn.execute(
            "SELECT image_paths FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
    if not row or not row["image_paths"]:
        return []
    return row["image_paths"].split(",")


# ---------- memory_files ----------

def record_memory_commit(
    *, user_id: str, character_id: str, memu_name: str, topic: str | None
) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO memory_files (user_id, character_id, memu_name, topic) "
            "VALUES (?, ?, ?, ?)",
            (user_id, character_id, memu_name, topic),
        )


# ---------- topics ----------
#
# A topic groups consecutive turns of the same subject into a single
# memU recall_file. While the topic is open, every new turn appends one
# "user: ..." / "assistant: ..." line to ``accumulated``; on close we
# hand the whole accumulated blob to ``memu.commit_results`` as one file.
# This keeps per-topic context dense and the recall_file description
# reflects the whole topic rather than the first 40 chars of one turn.

def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _new_topic_id() -> str:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return f"topic_{ts}_{uuid4().hex[:6]}"


def get_open_topic(*, user_id: str, character_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM topics "
            "WHERE user_id = ? AND character_id = ? AND closed_at IS NULL "
            "ORDER BY last_active_at DESC LIMIT 1",
            (user_id, character_id),
        ).fetchone()
    return dict(row) if row else None


def create_topic(
    *,
    user_id: str,
    character_id: str,
    summary: str,
    initial_lines: str = "",
) -> str:
    topic_id = _new_topic_id()
    now = _now_iso()
    with connect() as conn:
        conn.execute(
            "INSERT INTO topics (id, user_id, character_id, summary, "
            "started_at, last_active_at, accumulated, message_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (topic_id, user_id, character_id, summary, now, now, initial_lines),
        )
    return topic_id


def append_topic_lines(*, topic_id: str, lines: list[str]) -> None:
    """Append ``lines`` to ``accumulated`` and bump last_active_at + message_count.

    Each line in ``lines`` is one user-or-assistant turn rendered as
    ``"user: ..."`` or ``"assistant: ..."``; non-empty lines only.
    """
    cleaned = [ln for ln in (l.strip() for l in lines) if ln]
    if not cleaned:
        return
    now = _now_iso()
    with connect() as conn:
        conn.execute(
            "UPDATE topics SET accumulated = accumulated || ? || char(10), "
            "last_active_at = ?, message_count = message_count + ? "
            "WHERE id = ?",
            ("\n".join(cleaned), now, len(cleaned), topic_id),
        )


def close_topic_with_memu_name(*, topic_id: str, memu_name: str) -> None:
    now = _now_iso()
    with connect() as conn:
        conn.execute(
            "UPDATE topics SET closed_at = ?, memu_name = ? WHERE id = ?",
            (now, memu_name, topic_id),
        )


def touch_topic_activity(*, topic_id: str) -> None:
    """Bump last_active_at without appending content — used for the proactive-greet path."""
    with connect() as conn:
        conn.execute(
            "UPDATE topics SET last_active_at = ? WHERE id = ?",
            (_now_iso(), topic_id),
        )


def iso_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


# ---------- mood ----------

def get_mood(*, user_id: str, character_id: str) -> dict[str, Any] | None:
    """Return ``{"mood": {...}, "updated_at": "..."}`` or None."""
    with connect() as conn:
        row = conn.execute(
            "SELECT mood_json, updated_at FROM character_mood "
            "WHERE user_id = ? AND character_id = ?",
            (user_id, character_id),
        ).fetchone()
    if row is None:
        return None
    try:
        return {"mood": json.loads(row["mood_json"]), "updated_at": row["updated_at"]}
    except (json.JSONDecodeError, ValueError):
        return None


def upsert_mood(*, user_id: str, character_id: str, mood: dict[str, Any]) -> None:
    payload = json.dumps(mood, ensure_ascii=False)
    with connect() as conn:
        conn.execute(
            "INSERT INTO character_mood (user_id, character_id, mood_json, updated_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id, character_id) DO UPDATE SET "
            "mood_json = excluded.mood_json, updated_at = CURRENT_TIMESTAMP",
            (user_id, character_id, payload),
        )


# ---------- spatial state (对话时空分析) ----------

def get_spatial_state(*, user_id: str, character_id: str) -> dict[str, Any] | None:
    """Return ``{"description", "outfit", "updated_at"}`` or None."""
    with connect() as conn:
        row = conn.execute(
            "SELECT description, outfit, updated_at FROM spatial_state "
            "WHERE user_id = ? AND character_id = ?",
            (user_id, character_id),
        ).fetchone()
    if row is None:
        return None
    keys = row.keys()
    return {
        "description": row["description"] if "description" in keys else "",
        "outfit": row["outfit"] if "outfit" in keys else "",
        "updated_at": row["updated_at"],
    }


def upsert_spatial_state(
    *,
    user_id: str,
    character_id: str,
    description: str,
    outfit: str = "",
) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO spatial_state (user_id, character_id, description, outfit, updated_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id, character_id) DO UPDATE SET "
            "description = excluded.description, outfit = excluded.outfit, "
            "updated_at = CURRENT_TIMESTAMP",
            (user_id, character_id, description, outfit),
        )


# ---------- story state (数值系统) ----------

# Stage condition expressions are authored in the story card config. We eval
# them with a strict character whitelist (identifiers, digits, comparison
# operators, boolean keywords, parens, whitespace) and an empty __builtins__,
# so only dimension names (injected as locals) can be referenced.
_STAGE_COND_RE = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_<>=!(). ]*$")


def _eval_stage_condition(condition: str, state: dict[str, Any]) -> bool:
    """Safely evaluate a stage condition like ``favorability<40 and corruption<30``.

    Only identifiers, numbers, comparison ops, ``and``/``or``/``not``, parens
    and whitespace are allowed. Dimension values are injected as locals.
    """
    cond = (condition or "").strip()
    if not cond or not _STAGE_COND_RE.match(cond):
        return False
    namespace = {k: v for k, v in state.items() if isinstance(v, (int, float))}
    try:
        return bool(eval(cond, {"__builtins__": {}}, namespace))  # noqa: S307
    except Exception:
        return False


def compute_story_stage(state: dict[str, Any], stages: list[dict[str, Any]]) -> int:
    """Return the matching stage id.

    Stages are checked in descending id order (highest first) so that
    threshold-progression configs where a high stage has a strict condition
    and a low stage has a broad fallback condition work intuitively — the
    high stage wins when its condition holds. If none match, the lowest-id
    stage is the fallback.
    """
    if not stages:
        return 1
    ordered = sorted(stages, key=lambda s: -int(s.get("id", 0)))
    for st in ordered:
        if _eval_stage_condition(st.get("condition", ""), state):
            return int(st.get("id", 1))
    return int(ordered[-1].get("id", 1))


def init_story_state(*, user_id: str, character_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """Initialise story state from a config's dimension ``initial`` values.

    Returns the new state dict (also persisted). Idempotent: if a row already
    exists it is left untouched.
    """
    existing = get_story_state(user_id=user_id, character_id=character_id)
    if existing:
        return existing
    dims = config.get("dimensions") or []
    state = {d.get("id", ""): int(d.get("initial", 0)) for d in dims if d.get("id")}
    stages = config.get("stages") or []
    stage = compute_story_stage(state, stages)
    upsert_story_state(user_id=user_id, character_id=character_id, state=state, stage=stage)
    return {"state": state, "stage": stage}


def get_story_state(*, user_id: str, character_id: str) -> dict[str, Any] | None:
    """Return ``{"state": {...}, "stage": int, "updated_at": str}`` or None."""
    with connect() as conn:
        row = conn.execute(
            "SELECT state_json, stage, updated_at FROM story_state "
            "WHERE user_id = ? AND character_id = ?",
            (user_id, character_id),
        ).fetchone()
    if row is None:
        return None
    try:
        return {"state": json.loads(row["state_json"]), "stage": row["stage"], "updated_at": row["updated_at"]}
    except (json.JSONDecodeError, ValueError):
        return None


def upsert_story_state(*, user_id: str, character_id: str, state: dict[str, Any], stage: int) -> None:
    payload = json.dumps(state, ensure_ascii=False)
    with connect() as conn:
        conn.execute(
            "INSERT INTO story_state (user_id, character_id, state_json, stage, updated_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id, character_id) DO UPDATE SET "
            "state_json = excluded.state_json, stage = excluded.stage, "
            "updated_at = CURRENT_TIMESTAMP",
            (user_id, character_id, payload, stage),
        )


# ---------- custom scenes (角色自定义场景) ----------

def list_scenes(*, user_id: str, character_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, name, description, enabled, created_at FROM character_scenes "
            "WHERE user_id = ? AND character_id = ? ORDER BY created_at",
            (user_id, character_id),
        ).fetchall()
    return [dict(r) for r in rows]


def get_scene(*, user_id: str, character_id: str, scene_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, name, description, enabled, created_at FROM character_scenes "
            "WHERE id = ? AND user_id = ? AND character_id = ?",
            (scene_id, user_id, character_id),
        ).fetchone()
    return dict(row) if row else None


def add_scene(*, user_id: str, character_id: str, name: str, description: str) -> dict[str, Any]:
    scene_id = uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO character_scenes (id, user_id, character_id, name, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (scene_id, user_id, character_id, name, description),
        )
    return {"id": scene_id, "name": name, "description": description, "enabled": 1}


def update_scene(
    *, user_id: str, character_id: str, scene_id: str,
    name: str | None = None, description: str | None = None,
    enabled: bool | None = None,
) -> bool:
    sets, params = [], []
    if name is not None:
        sets.append("name = ?"); params.append(name)
    if description is not None:
        sets.append("description = ?"); params.append(description)
    if enabled is not None:
        sets.append("enabled = ?"); params.append(1 if enabled else 0)
    if not sets:
        return False
    params += [user_id, character_id, scene_id]
    with connect() as conn:
        cur = conn.execute(
            f"UPDATE character_scenes SET {', '.join(sets)} "
            "WHERE user_id = ? AND character_id = ? AND id = ?",
            params,
        )
    return cur.rowcount > 0


def delete_scene(*, user_id: str, character_id: str, scene_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM character_scenes "
            "WHERE user_id = ? AND character_id = ? AND id = ?",
            (user_id, character_id, scene_id),
        )
        # 若删的是当前激活场景，回落到自动模式
        conn.execute(
            "UPDATE characters SET active_scene_id = '' "
            "WHERE id = ? AND active_scene_id = ?",
            (character_id, scene_id),
        )
    return cur.rowcount > 0


def get_active_scene_id(*, user_id: str, character_id: str) -> str:
    with connect() as conn:
        row = conn.execute(
            "SELECT active_scene_id FROM characters WHERE id = ? AND user_id = ?",
            (character_id, user_id),
        ).fetchone()
    return (row["active_scene_id"] if row else "") or ""


def set_active_scene(*, user_id: str, character_id: str, scene_id: str) -> None:
    """scene_id 为空字符串 = 自动跟随时间推断的内置场景。"""
    with connect() as conn:
        conn.execute(
            "UPDATE characters SET active_scene_id = ? WHERE id = ? AND user_id = ?",
            (scene_id, character_id, user_id),
        )


def get_active_scene(*, user_id: str, character_id: str) -> dict[str, Any] | None:
    """返回当前激活的自定义场景（未启用或已删除 → None → 走自动模式）。"""
    scene_id = get_active_scene_id(user_id=user_id, character_id=character_id)
    if not scene_id:
        return None
    scene = get_scene(user_id=user_id, character_id=character_id, scene_id=scene_id)
    if scene is None or not scene.get("enabled"):
        return None
    return scene


# ---------- events ----------

def add_event(*, user_id: str, character_id: str, event_text: str, event_date: str | None = None) -> str:
    event_id = uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO character_events (id, user_id, character_id, event_text, event_date, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (event_id, user_id, character_id, event_text, event_date),
        )
    return event_id


def list_pending_events(*, user_id: str, character_id: str) -> list[dict[str, Any]]:
    """Return pending events that are due (event_date <= today or no date)."""
    today = datetime.now().strftime("%Y-%m-%d")
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, event_text, event_date FROM character_events "
            "WHERE user_id = ? AND character_id = ? AND status = 'pending' "
            "AND (event_date IS NULL OR event_date <= ?) "
            "ORDER BY created_at",
            (user_id, character_id, today),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_events_asked(*, event_ids: list[str]) -> None:
    if not event_ids:
        return
    now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    with connect() as conn:
        for eid in event_ids:
            conn.execute(
                "UPDATE character_events SET status = 'asked', asked_at = ? WHERE id = ?",
                (now, eid),
            )


def list_all_events(*, user_id: str, character_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, event_text, event_date, status, created_at, asked_at "
            "FROM character_events "
            "WHERE user_id = ? AND character_id = ? "
            "ORDER BY created_at DESC",
            (user_id, character_id),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- moments ----------

def add_moment(*, user_id: str, character_id: str, content: str, author_type: str = "character") -> str:
    moment_id = uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO character_moments (id, user_id, character_id, author_type, content) "
            "VALUES (?, ?, ?, ?, ?)",
            (moment_id, user_id, character_id, author_type, content),
        )
    return moment_id


def add_user_moment(*, user_id: str, content: str) -> str:
    return add_moment(user_id=user_id, character_id="", author_type="user", content=content)


def list_moments(*, user_id: str, character_id: str, limit: int = 20) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, content, created_at FROM character_moments "
            "WHERE user_id = ? AND character_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, character_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def list_all_moments(*, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Return moments across all characters (and user's own), with interactions."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT m.id, m.character_id, m.author_type, m.content, m.created_at, "
            "c.name AS char_name, c.avatar_path AS char_avatar "
            "FROM character_moments m "
            "LEFT JOIN characters c ON c.id = m.character_id AND c.user_id = m.user_id "
            "WHERE m.user_id = ? "
            "ORDER BY m.created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        moments = [dict(r) for r in rows]
        for m in moments:
            m["comments"] = _list_comments_raw(conn, m["id"])
            m["likes"] = _list_likes_raw(conn, m["id"])
    return moments


def last_moment_time(*, user_id: str, character_id: str) -> datetime | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT created_at FROM character_moments "
            "WHERE user_id = ? AND character_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id, character_id),
        ).fetchone()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row["created_at"])
    except (ValueError, TypeError):
        return None


def messages_since(*, user_id: str, character_id: str, since: datetime) -> list[dict[str, Any]]:
    """Return messages since a given local datetime (for daily moment generation)."""
    # SQLite CURRENT_TIMESTAMP is UTC; convert local since to UTC for comparison.
    delta = datetime.now() - datetime.utcnow()
    since_str = (since - delta).strftime("%Y-%m-%d %H:%M:%S")
    with connect() as conn:
        rows = conn.execute(
            "SELECT role, content, created_at FROM messages "
            "WHERE user_id = ? AND character_id = ? AND created_at >= ? "
            "AND excluded_from_context = 0 "
            "ORDER BY id",
            (user_id, character_id, since_str),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- moment comments & likes ----------

def _list_comments_raw(conn: sqlite3.Connection, moment_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT mc.id, mc.commenter_type, mc.character_id, mc.content, mc.created_at, "
        "c.name AS char_name, c.avatar_path AS char_avatar "
        "FROM moment_comments mc "
        "LEFT JOIN characters c ON c.id = mc.character_id AND c.user_id = mc.user_id "
        "WHERE mc.moment_id = ? "
        "ORDER BY mc.created_at ASC",
        (moment_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _list_likes_raw(conn: sqlite3.Connection, moment_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT ml.id, ml.liker_type, ml.character_id, ml.created_at, "
        "c.name AS char_name, c.avatar_path AS char_avatar "
        "FROM moment_likes ml "
        "LEFT JOIN characters c ON c.id = ml.character_id AND c.user_id = ml.user_id "
        "WHERE ml.moment_id = ? "
        "ORDER BY ml.created_at ASC",
        (moment_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def add_comment(
    *,
    moment_id: str,
    user_id: str,
    commenter_type: str = "character",
    character_id: str = "",
    content: str,
) -> dict[str, Any] | None:
    comment_id = uuid4().hex
    with connect() as conn:
        try:
            conn.execute(
                "INSERT INTO moment_comments (id, moment_id, user_id, commenter_type, character_id, content) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (comment_id, moment_id, user_id, commenter_type, character_id, content),
            )
        except sqlite3.IntegrityError:
            return None
    with connect() as conn:
        row = conn.execute(
            "SELECT id, commenter_type, character_id, content, created_at FROM moment_comments "
            "WHERE id = ?",
            (comment_id,),
        ).fetchone()
    return dict(row) if row else None


def list_comments(*, moment_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        return _list_comments_raw(conn, moment_id)


def add_like(
    *,
    moment_id: str,
    user_id: str,
    liker_type: str = "character",
    character_id: str = "",
) -> dict[str, Any] | None:
    like_id = uuid4().hex
    with connect() as conn:
        try:
            conn.execute(
                "INSERT INTO moment_likes (id, moment_id, user_id, liker_type, character_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (like_id, moment_id, user_id, liker_type, character_id),
            )
        except sqlite3.IntegrityError:
            return None
    with connect() as conn:
        row = conn.execute(
            "SELECT id, liker_type, character_id, created_at FROM moment_likes "
            "WHERE id = ?",
            (like_id,),
        ).fetchone()
    return dict(row) if row else None


def list_likes(*, moment_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        return _list_likes_raw(conn, moment_id)


def has_liked(*, moment_id: str, character_id: str, liker_type: str = "character") -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM moment_likes "
            "WHERE moment_id = ? AND character_id = ? AND liker_type = ?",
            (moment_id, character_id, liker_type),
        ).fetchone()
    return row is not None


# ---------- news digests ----------

def add_news_digest(
    *,
    user_id: str,
    character_id: str,
    digest_date: str,
    summary: str,
    raw_titles: str = "",
) -> dict[str, Any] | None:
    digest_id = uuid4().hex
    with connect() as conn:
        try:
            conn.execute(
                "INSERT INTO news_digests (id, user_id, character_id, digest_date, summary, raw_titles) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (digest_id, user_id, character_id, digest_date, summary, raw_titles),
            )
        except sqlite3.IntegrityError:
            return None
    with connect() as conn:
        row = conn.execute(
            "SELECT id, digest_date, summary, raw_titles, created_at FROM news_digests WHERE id = ?",
            (digest_id,),
        ).fetchone()
    return dict(row) if row else None


def list_news_digests(*, user_id: str, character_id: str, limit: int = 30) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, digest_date, summary, raw_titles, created_at "
            "FROM news_digests "
            "WHERE user_id = ? AND character_id = ? "
            "ORDER BY digest_date DESC LIMIT ?",
            (user_id, character_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def has_news_digest(*, user_id: str, character_id: str, digest_date: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM news_digests "
            "WHERE user_id = ? AND character_id = ? AND digest_date = ?",
            (user_id, character_id, digest_date),
        ).fetchone()
    return row is not None


# ---------- group chat ----------

def create_group(*, user_id: str, name: str, character_ids: list[str]) -> dict[str, Any]:
    group_id = uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO chat_groups (id, user_id, name) VALUES (?, ?, ?)",
            (group_id, user_id, name),
        )
        for cid in character_ids:
            conn.execute(
                "INSERT INTO chat_group_members (group_id, character_id) VALUES (?, ?)",
                (group_id, cid),
            )
    return get_group(group_id, user_id)


def get_group(group_id: str, user_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, user_id, name, created_at FROM chat_groups "
            "WHERE id = ? AND user_id = ?",
            (group_id, user_id),
        ).fetchone()
        if not row:
            return None
        g = dict(row)
        members = conn.execute(
            "SELECT cgm.character_id, c.name, c.avatar_path "
            "FROM chat_group_members cgm "
            "JOIN characters c ON c.id = cgm.character_id AND c.user_id = ? "
            "WHERE cgm.group_id = ?",
            (user_id, group_id),
        ).fetchall()
        g["members"] = [dict(m) for m in members]
    return g


def list_groups(*, user_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT g.id, g.name, g.created_at, "
            "COUNT(cgm.character_id) AS member_count "
            "FROM chat_groups g "
            "LEFT JOIN chat_group_members cgm ON cgm.group_id = g.id "
            "WHERE g.user_id = ? "
            "GROUP BY g.id ORDER BY g.created_at DESC",
            (user_id,),
        ).fetchall()
        groups = [dict(r) for r in rows]
        for g in groups:
            members = conn.execute(
                "SELECT cgm.character_id, c.name, c.avatar_path "
                "FROM chat_group_members cgm "
                "JOIN characters c ON c.id = cgm.character_id AND c.user_id = ? "
                "WHERE cgm.group_id = ?",
                (user_id, g["id"]),
            ).fetchall()
            g["members"] = [dict(m) for m in members]
    return groups


def add_group_message(
    *,
    group_id: str,
    user_id: str,
    sender_type: str,
    character_id: str = "",
    content: str,
) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO group_messages (group_id, user_id, sender_type, character_id, content) "
            "VALUES (?, ?, ?, ?, ?)",
            (group_id, user_id, sender_type, character_id, content),
        )
        return cur.lastrowid


def list_group_messages(*, group_id: str, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT gm.id, gm.sender_type, gm.character_id, gm.content, gm.created_at, "
            "c.name AS char_name, c.avatar_path AS char_avatar "
            "FROM group_messages gm "
            "LEFT JOIN characters c ON c.id = gm.character_id AND c.user_id = ? "
            "WHERE gm.group_id = ? AND gm.user_id = ? "
            "ORDER BY gm.id DESC LIMIT ?",
            (user_id, group_id, user_id, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def delete_group(group_id: str, user_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM chat_groups WHERE id = ? AND user_id = ?",
            (group_id, user_id),
        )
        return cur.rowcount > 0


# ---------- relationship ----------

def get_relationship(*, user_id: str, character_id: str) -> str:
    """Return the user-set relationship type, or the default."""
    with connect() as conn:
        row = conn.execute(
            "SELECT relationship FROM character_relationship "
            "WHERE user_id = ? AND character_id = ?",
            (user_id, character_id),
        ).fetchone()
    return row["relationship"] if row else "陌生人"


def upsert_relationship(*, user_id: str, character_id: str, relationship: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO character_relationship (user_id, character_id, relationship, updated_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id, character_id) DO UPDATE SET "
            "relationship = excluded.relationship, updated_at = CURRENT_TIMESTAMP",
            (user_id, character_id, relationship),
        )


def add_evolution(
    *, user_id: str, character_id: str, change: str,
    old_personality: str | None, new_personality: str | None,
) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO character_evolution (user_id, character_id, change, old_personality, new_personality) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, character_id, change, old_personality, new_personality),
        )


def get_evolutions(*, user_id: str, character_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, change, old_personality, new_personality, created_at "
            "FROM character_evolution WHERE user_id = ? AND character_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, character_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def count_evolutions(*, user_id: str, character_id: str) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM character_evolution WHERE user_id = ? AND character_id = ?",
            (user_id, character_id),
        ).fetchone()
    return int(row[0])


def update_character_personality(*, user_id: str, character_id: str, personality: str) -> bool:
    """Update the personality field in the character's card JSON."""
    with connect() as conn:
        row = conn.execute(
            "SELECT card_json FROM characters WHERE id = ? AND user_id = ?",
            (character_id, user_id),
        ).fetchone()
        if not row:
            return False
        card = json.loads(row["card_json"])
        card["personality"] = personality
        conn.execute(
            "UPDATE characters SET card_json = ? WHERE id = ? AND user_id = ?",
            (json.dumps(card, ensure_ascii=False), character_id, user_id),
        )
    return True


# ---------- prompt rules (动态提示词库) ----------

def list_prompt_rules(*, user_id: str, character_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, name, enabled, priority, content, match_conditions, created_at, updated_at "
            "FROM prompt_rules WHERE user_id = ? AND character_id = ? ORDER BY priority, created_at",
            (user_id, character_id),
        ).fetchall()
    return [dict(r) for r in rows]


def add_prompt_rule(
    *, user_id: str, character_id: str, name: str, content: str,
    match_conditions: str, priority: int = 100, enabled: bool = True,
) -> dict[str, Any]:
    rule_id = uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO prompt_rules (id, user_id, character_id, name, enabled, priority, content, match_conditions) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rule_id, user_id, character_id, name, 1 if enabled else 0, priority, content, match_conditions),
        )
    return {"id": rule_id, "name": name, "enabled": 1 if enabled else 0,
            "priority": priority, "content": content, "match_conditions": match_conditions}


def update_prompt_rule(
    *, user_id: str, character_id: str, rule_id: str,
    name: str | None = None, content: str | None = None,
    match_conditions: str | None = None,
    priority: int | None = None, enabled: bool | None = None,
) -> bool:
    sets, params = [], []
    if name is not None:
        sets.append("name = ?"); params.append(name)
    if content is not None:
        sets.append("content = ?"); params.append(content)
    if match_conditions is not None:
        sets.append("match_conditions = ?"); params.append(match_conditions)
    if priority is not None:
        sets.append("priority = ?"); params.append(priority)
    if enabled is not None:
        sets.append("enabled = ?"); params.append(1 if enabled else 0)
    if not sets:
        return False
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params += [user_id, character_id, rule_id]
    with connect() as conn:
        cur = conn.execute(
            f"UPDATE prompt_rules SET {', '.join(sets)} "
            "WHERE user_id = ? AND character_id = ? AND id = ?",
            params,
        )
    return cur.rowcount > 0


def delete_prompt_rule(*, user_id: str, character_id: str, rule_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM prompt_rules WHERE user_id = ? AND character_id = ? AND id = ?",
            (user_id, character_id, rule_id),
        )
    return cur.rowcount > 0

# ---------- milestones ----------

_MILESTONE_TYPES = {
    "first_goodnight", "first_argument", "first_date", "first_kiss",
    "first_confession", "first_meeting", "first_gift", "first_apology",
    "first_jealousy", "first_morning", "custom",
}


def add_milestone(
    *,
    user_id: str,
    character_id: str,
    type: str,
    title: str,
    description: str = "",
) -> dict[str, Any] | None:
    """Insert a milestone. Returns the row if inserted, None if a milestone
    of the same type already exists for this (user, character) pair."""
    if type not in _MILESTONE_TYPES:
        type = "custom"
    mid = str(uuid4())
    with connect() as conn:
        try:
            conn.execute(
                "INSERT INTO character_milestones (id, user_id, character_id, type, title, description) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (mid, user_id, character_id, type, title, description),
            )
        except sqlite3.IntegrityError:
            return None
    return get_milestone(mid, user_id, character_id)


def list_milestones(*, user_id: str, character_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, type, title, description, created_at "
            "FROM character_milestones "
            "WHERE user_id = ? AND character_id = ? "
            "ORDER BY created_at ASC",
            (user_id, character_id),
        ).fetchall()
    return [dict(r) for r in rows]


def get_milestone(milestone_id: str, user_id: str, character_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, type, title, description, created_at "
            "FROM character_milestones "
            "WHERE id = ? AND user_id = ? AND character_id = ?",
            (milestone_id, user_id, character_id),
        ).fetchone()
    return dict(row) if row else None


def has_milestone_type(*, user_id: str, character_id: str, type: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM character_milestones "
            "WHERE user_id = ? AND character_id = ? AND type = ?",
            (user_id, character_id, type),
        ).fetchone()
    return row is not None

# ---------- story templates ----------

def list_story_templates() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, name, genre, description, world_setting, plot_summary, "
            "user_role, opening, story_rules, npcs FROM story_templates ORDER BY genre, name"
        ).fetchall()
    return [dict(r) for r in rows]


def get_story_template(template_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM story_templates WHERE id = ?",
            (template_id,),
        ).fetchone()
    return dict(row) if row else None


# ---------- story chapters ----------

def list_story_chapters(user_id: str, character_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, title, summary, chapter_order, is_completed, created_at, completed_at "
            "FROM story_chapters WHERE user_id = ? AND character_id = ? ORDER BY chapter_order",
            (user_id, character_id),
        ).fetchall()
    return [dict(r) for r in rows]


def add_story_chapter(*, user_id: str, character_id: str, title: str, summary: str = "",
                      chapter_order: int = 0) -> str:
    chapter_id = uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO story_chapters (id, user_id, character_id, title, summary, chapter_order) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chapter_id, user_id, character_id, title, summary, chapter_order),
        )
    return chapter_id


def complete_story_chapter(user_id: str, character_id: str, chapter_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE story_chapters SET is_completed = 1, completed_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND user_id = ? AND character_id = ?",
            (chapter_id, user_id, character_id),
        )
    return cur.rowcount > 0


# ---------- story endings ----------

def get_story_ending(user_id: str, character_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, ending_type, description, created_at FROM story_endings "
            "WHERE user_id = ? AND character_id = ?",
            (user_id, character_id),
        ).fetchone()
    return dict(row) if row else None


def set_story_ending(*, user_id: str, character_id: str, ending_type: str,
                     description: str) -> str:
    ending_id = uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO story_endings (id, user_id, character_id, ending_type, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (ending_id, user_id, character_id, ending_type, description),
        )
        conn.execute(
            "UPDATE characters SET story_status = 'completed' WHERE id = ? AND user_id = ?",
            (character_id, user_id),
        )
    return ending_id


# ---------- story memory ----------

def list_story_memory(user_id: str, character_id: str, min_importance: int = 1) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, memory_type, content, importance, created_at FROM story_memory "
            "WHERE user_id = ? AND character_id = ? AND importance >= ? "
            "ORDER BY importance DESC, created_at",
            (user_id, character_id, min_importance),
        ).fetchall()
    return [dict(r) for r in rows]


def add_story_memory(*, user_id: str, character_id: str, memory_type: str,
                     content: str, importance: int = 5) -> str:
    mem_id = uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO story_memory (id, user_id, character_id, memory_type, content, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (mem_id, user_id, character_id, memory_type, content, importance),
        )
    return mem_id


def delete_story_memory(user_id: str, character_id: str, memory_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM story_memory WHERE id = ? AND user_id = ? AND character_id = ?",
            (memory_id, user_id, character_id),
        )
    return cur.rowcount > 0


# ---------- story triggers ----------

def list_story_triggers(user_id: str, character_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, condition_text, trigger_text, is_active, last_triggered_at, created_at "
            "FROM story_triggers WHERE user_id = ? AND character_id = ? "
            "ORDER BY created_at DESC",
            (user_id, character_id),
        ).fetchall()
    return [dict(r) for r in rows]


def add_story_trigger(*, user_id: str, character_id: str, condition_text: str,
                      trigger_text: str, is_active: int = 1) -> str:
    tid = uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO story_triggers (id, user_id, character_id, condition_text, trigger_text, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tid, user_id, character_id, condition_text, trigger_text, is_active),
        )
    return tid


def update_story_trigger(*, user_id: str, character_id: str, trigger_id: str,
                         condition_text: str | None = None, trigger_text: str | None = None,
                         is_active: int | None = None) -> bool:
    sets: list[str] = []
    vals: list[Any] = []
    if condition_text is not None:
        sets.append("condition_text = ?")
        vals.append(condition_text)
    if trigger_text is not None:
        sets.append("trigger_text = ?")
        vals.append(trigger_text)
    if is_active is not None:
        sets.append("is_active = ?")
        vals.append(is_active)
    if not sets:
        return False
    vals.extend([trigger_id, user_id, character_id])
    with connect() as conn:
        cur = conn.execute(
            f"UPDATE story_triggers SET {', '.join(sets)} WHERE id = ? AND user_id = ? AND character_id = ?",
            vals,
        )
    return cur.rowcount > 0


def delete_story_trigger(user_id: str, character_id: str, trigger_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM story_triggers WHERE id = ? AND user_id = ? AND character_id = ?",
            (trigger_id, user_id, character_id),
        )
    return cur.rowcount > 0


def mark_trigger_fired(user_id: str, character_id: str, trigger_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE story_triggers SET last_triggered_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND user_id = ? AND character_id = ?",
            (trigger_id, user_id, character_id),
        )


# ---------- story cover / status ----------

def update_story_cover(user_id: str, character_id: str, cover_url: str) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE characters SET cover_url = ? WHERE id = ? AND user_id = ? AND card_type = 'story'",
            (cover_url, character_id, user_id),
        )
    return cur.rowcount > 0


def update_story_status(user_id: str, character_id: str, status: str) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE characters SET story_status = ? WHERE id = ? AND user_id = ? AND card_type = 'story'",
            (status, character_id, user_id),
        )
    return cur.rowcount > 0

# ---------- conversation summary (rolling compression) ----------

def get_conversation_summary(*, user_id: str, character_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT content, summarized_up_to, updated_at FROM conversation_summary "
            "WHERE user_id = ? AND character_id = ?",
            (user_id, character_id),
        ).fetchone()
    if row is None:
        return None
    return {"content": row["content"], "summarized_up_to": row["summarized_up_to"], "updated_at": row["updated_at"]}


def upsert_conversation_summary(*, user_id: str, character_id: str, content: str, summarized_up_to: int) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO conversation_summary (user_id, character_id, content, summarized_up_to, updated_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id, character_id) DO UPDATE SET "
            "content = excluded.content, summarized_up_to = excluded.summarized_up_to, "
            "updated_at = CURRENT_TIMESTAMP",
            (user_id, character_id, content, summarized_up_to),
        )


def delete_conversation_summary(*, user_id: str, character_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "DELETE FROM conversation_summary WHERE user_id = ? AND character_id = ?",
            (user_id, character_id),
        )


def count_unsummarized_messages(*, user_id: str, character_id: str, summarized_up_to: int) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM messages "
            "WHERE user_id = ? AND character_id = ? AND id > ? AND excluded_from_context = 0",
            (user_id, character_id, summarized_up_to),
        ).fetchone()
    return int(row[0])


def get_unsummarized_messages(*, user_id: str, character_id: str, summarized_up_to: int, limit: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, role, content FROM messages "
            "WHERE user_id = ? AND character_id = ? AND id > ? AND excluded_from_context = 0 "
            "ORDER BY id ASC LIMIT ?",
            (user_id, character_id, summarized_up_to, limit),
        ).fetchall()
    return [dict(r) for r in rows]