"""Live end-to-end: simulate a real LLM failure against the actual running
SQLite DB (the one the uvicorn process on :8765 reads) and prove that
the fallback turn does not enter history or memU.

Skips the test if no uvicorn instance is reachable.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path


def main() -> int:
    import os
    import db as _db
    import routes.chat_routes as chat_routes

    # Same DB the live server uses.
    db_path = Path(os.environ.get("DB_PATH", "data/app.sqlite3"))
    if not db_path.exists():
        print(f"SKIP: live DB not at {db_path}")
        return 0

    # Fresh user/character just for this verification.
    cid = _db.create_character(
        user_id="verify_fallback",
        name="VerifyBot",
        spec="v2",
        card={"name": "VerifyBot", "description": "verification target"},
        avatar_path=None,
    )[0]

    # Wipe any prior rows from a previous live run (each run gets a new
    # character id, but be defensive about leftover state).
    with _db.connect() as _conn:
        _conn.execute(
            "DELETE FROM messages WHERE user_id = ? AND character_id = ?",
            ("verify_fallback", cid),
        )

    # Snapshot history size before.
    before = _db.recent_messages(
        user_id="verify_fallback", character_id=cid, limit=1000,
    )
    print(f"[before] history rows = {len(before)}")

    # Force the LLM to fail immediately with NO tokens produced.
    async def _broken_stream(_messages):
        raise RuntimeError("simulated upstream timeout")
        yield ""  # make this an async generator function

    chat_routes.stream_chat = _broken_stream
    async def _no_pick(**_kwargs):
        return None
    chat_routes._maybe_pick_pool_image = _no_pick
    chat_routes.embedding_enabled = lambda: False

    class _DummyRequest:
        def __init__(self):
            class _State:
                pass
            state = _State()
            state.memu = _NullMemu()
            self.app = type("_App", (), {"state": state})()

    class _NullMemu:
        async def progressive_retrieve(self, query, *, where):
            return {"segments": [], "files": []}
        async def commit_results(self, *, recall_files, user):
            return None

    req = _DummyRequest()
    resp = asyncio.run(chat_routes.send_message(
        request=req, character_id=cid,
        message="VERIFY_FALLBACK_MARKER",
        image_paths=None, user_id="verify_fallback",
    ))

    async def _consume():
        chunks = []
        async for c in resp.body_iterator:
            chunks.append(c if isinstance(c, bytes) else c.encode())
        return b"".join(chunks)

    body = asyncio.run(_consume())
    text = body.decode("utf-8", errors="replace")
    print("[sse response]")
    for line in text.splitlines():
        if line.strip():
            print(f"  {line}")

    after = _db.recent_messages(
        user_id="verify_fallback", character_id=cid, limit=1000,
    )
    print(f"[after]  history rows = {len(after)}")

    new_rows = after[len(before):]
    print(f"[after]  NEW rows: {[r['content'] for r in new_rows]}")

    has_fallback = any(r["content"] == "哈哈哈" for r in after)
    has_marker = any("VERIFY_FALLBACK_MARKER" in (r["content"] or "") for r in after)
    has_skipped = "skipped" in text and "llm_fallback" in text
    delta_rows = len(after) - len(before)

    # Context view: failed turn must NOT feed back into the LLM history.
    ctx_rows = _db.recent_messages_for_context(
        user_id="verify_fallback", character_id=cid, limit=100
    )
    ctx_fallback = any(r["content"] == "哈哈哈" for r in ctx_rows)
    ctx_marker = any("VERIFY_FALLBACK_MARKER" in (r["content"] or "") for r in ctx_rows)

    print()
    print(f"  new rows this turn:  {delta_rows} (expect 2 — persisted but flagged)")
    print(f"  '哈哈哈' in log:     {has_fallback} (expect True — visible in record)")
    print(f"  marker in log:       {has_marker} (expect True — visible in record)")
    print(f"  '哈哈哈' in context: {ctx_fallback} (expect False — dropped from LLM history)")
    print(f"  marker in context:   {ctx_marker} (expect False — dropped from LLM history)")
    print(f"  'skipped' SSE event: {has_skipped} (expect True)")

    # The failed turn is persisted (visible) but excluded from the LLM context.
    ok = (
        has_fallback and has_marker and (delta_rows == 2)
        and (not ctx_fallback) and (not ctx_marker) and has_skipped
    )
    print()
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())