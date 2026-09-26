"""Model endpoint / key settings, editable from the in-app settings page.

Values are persisted to .env (so they survive restarts) and applied to the
running process immediately — the LLM client is rebuilt on the next request.
Embedding values are also persisted; the local bge-m3 profile is the default
and ignores the remote embedding fields unless ``EMBED_LOCAL_ENABLED`` is
turned off, so embedding changes may need a restart to take full effect.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from auth import current_user
from config import get_settings, write_env

router = APIRouter(prefix="/api")

# field name -> .env key
_ENV_KEYS = {
    "llm_protocol": "LLM_PROTOCOL",
    "llm_base_url": "LLM_BASE_URL",
    "llm_api_key": "LLM_API_KEY",
    "llm_model": "LLM_MODEL",
    "embedding_base_url": "EMBEDDING_BASE_URL",
    "embedding_api_key": "EMBEDDING_API_KEY",
    "embedding_model": "EMBEDDING_MODEL",
    "tts_base_url": "TTS_BASE_URL",
    "tts_api_key": "TTS_API_KEY",
    "tts_model": "TTS_MODEL",
    "tts_voice_id": "TTS_VOICE_ID",
    "image_gen_base_url": "IMAGE_GEN_BASE_URL",
    "image_gen_api_key": "IMAGE_GEN_API_KEY",
    "image_gen_model": "IMAGE_GEN_MODEL",
    "sleep_enabled": "SLEEP_ENABLED",
    "sleep_time": "SLEEP_TIME",
    "wake_time": "WAKE_TIME",
}

_PLACEHOLDER_KEYS = {"", "sk-replace-me", "sk-placeholder", "your-key-here"}


def _mask_key(key: str) -> str:
    if not key or key in _PLACEHOLDER_KEYS:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:6]}…{key[-4:]}"


class SettingsBody(BaseModel):
    llm_protocol: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str | None = None
    tts_base_url: str | None = None
    tts_api_key: str | None = None
    tts_model: str | None = None
    tts_voice_id: str | None = None
    image_gen_base_url: str | None = None
    image_gen_api_key: str | None = None
    image_gen_model: str | None = None
    sleep_enabled: bool | None = None
    sleep_time: str | None = None
    wake_time: str | None = None


@router.get("/settings")
def get_settings_view(_: str = Depends(current_user)):
    s = get_settings()
    return {
        "llm_protocol": s.llm_protocol,
        "llm_base_url": s.llm_base_url,
        "llm_api_key_masked": _mask_key(s.llm_api_key),
        "llm_model": s.llm_model,
        "embedding_base_url": s.embedding_base_url,
        "embedding_api_key_masked": _mask_key(s.embedding_api_key),
        "embedding_model": s.embedding_model,
        "embed_local_enabled": s.embed_local_enabled,
        "embed_model_name": s.embed_model_name,
        "tts_base_url": s.tts_base_url,
        "tts_api_key_masked": _mask_key(s.tts_api_key),
        "tts_model": s.tts_model,
        "tts_voice_id": s.tts_voice_id,
        "image_gen_base_url": s.image_gen_base_url,
        "image_gen_api_key_masked": _mask_key(s.image_gen_api_key),
        "image_gen_model": s.image_gen_model,
        "sleep_enabled": s.sleep_enabled,
        "sleep_time": s.sleep_time,
        "wake_time": s.wake_time,
    }


@router.put("/settings")
def update_settings(body: SettingsBody, _: str = Depends(current_user)):
    s = get_settings()
    updates: dict[str, str] = {}

    # Fields are applied only when the client sent a non-empty value, so a
    # blank input keeps the existing config (keys are never echoed back).
    for field, env_key in _ENV_KEYS.items():
        value = getattr(body, field)
        if value is None:
            continue
        # bool fields (sleep_enabled) → store as "true"/"false"
        if isinstance(value, bool):
            text = "true" if value else "false"
        else:
            text = str(value).strip()
        if not text:
            continue
        setattr(s, field, text if not isinstance(value, bool) else value)
        updates[env_key] = text

    write_env(updates)
    return {"saved": bool(updates), "llm_hot_reloaded": bool(
        updates.get("LLM_BASE_URL") or updates.get("LLM_API_KEY")
    )}
