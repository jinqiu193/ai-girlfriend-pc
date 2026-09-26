"""memU singleton: built once at app startup, shared across requests."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from memu.app import MemoryService
from memu.app.settings import DatabaseConfig, EmbeddingConfig, ProgressiveRetrieveConfig, UserConfig

from config import get_settings


class ChatScope(BaseModel):
    """Scope model for memU — extends the default with character_id.

    Both user_id and character_id are required at write time so each
    (user, character) pair has an isolated recall space. agent_id is left
    for future multi-session splitting.
    """

    user_id: str | None = None
    agent_id: str | None = None
    character_id: str | None = None


_PLACEHOLDER_KEYS = {"", "sk-replace-me", "sk-placeholder", "your-key-here"}


def _embedding_is_configured(settings) -> bool:
    """True only if the user has set a non-placeholder embedding key.

    Without a real key memU's OpenAI-compatible embedding client will hang on
    401s, and every chat turn waits for that timeout. Detect and let the chat
    route skip the call entirely.
    """
    if settings.embedding_api_key in _PLACEHOLDER_KEYS:
        return False
    if not settings.embedding_api_key.startswith(("sk-", "sk_")):
        # still allow it, but log a warning upstream
        return True
    return True


def build_memory_service() -> MemoryService:
    settings = get_settings()
    settings.memu_db_path.parent.mkdir(parents=True, exist_ok=True)
    dsn = f"sqlite:///{Path(settings.memu_db_path).as_posix()}"

    # When the local embed server is enabled in config, point memU at the same
    # FastAPI app we mount the embeddings endpoint on (no separate process).
    if settings.embed_local_enabled:
        base_url = f"http://127.0.0.1:{settings.app_port}/v1"
        api_key = "local-no-key"
        model = settings.embed_model_name
    else:
        base_url = settings.embedding_base_url
        api_key = settings.embedding_api_key
        model = settings.embedding_model

    return MemoryService(
        database_config=DatabaseConfig(
            metadata_store={"provider": "sqlite", "dsn": dsn},
            vector_index={"provider": "bruteforce"},
        ),
        user_config=UserConfig(model=ChatScope),
        progressive_retrieve_config=ProgressiveRetrieveConfig(
            # Only recall memories; skill (character card contents) is excluded
            file={"enabled": True, "top_k": 5, "tracks": ["memory"]},
            resource={"enabled": False},
        ),
        embedding_profiles={
            "default": EmbeddingConfig(
                provider="openai",
                base_url=base_url,
                api_key=api_key,
                embed_model=model,
                client_backend="httpx",  # bypass SDK strict validation
            )
        },
    )


def embedding_enabled() -> bool:
    """Public flag the chat route checks before calling memU.

    True when either a real embedding key is configured, or the local embed
    server is enabled (which will be running inside the same process).
    """
    settings = get_settings()
    if settings.embed_local_enabled:
        return True
    return _embedding_is_configured(settings)