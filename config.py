"""Centralised settings loaded from environment / .env."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    admin_username: str = Field(default="admin", alias="ADMIN_USERNAME")
    admin_password: str = Field(default="changeme", alias="ADMIN_PASSWORD")

    session_secret: str = Field(default="dev-secret-change-me", alias="SESSION_SECRET")

    llm_base_url: str = Field(default="https://api.openai.com/v1", alias="LLM_BASE_URL")
    llm_api_key: str = Field(default="sk-replace-me", alias="LLM_API_KEY")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")
    llm_protocol: str = Field(default="anthropic", alias="LLM_PROTOCOL")

    embedding_base_url: str = Field(default="https://api.openai.com/v1", alias="EMBEDDING_BASE_URL")
    embedding_api_key: str = Field(default="sk-replace-me", alias="EMBEDDING_API_KEY")
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")

    # TTS (MiniMax text-to-audio). Used by the /api/tts endpoint to synthesize
    # speech for AI replies. voice_id selects the timbre; a curated list is
    # offered in the settings UI.
    tts_base_url: str = Field(default="https://api.minimaxi.com/v1", alias="TTS_BASE_URL")
    tts_api_key: str = Field(default="sk-replace-me", alias="TTS_API_KEY")
    tts_model: str = Field(default="speech-02-hd", alias="TTS_MODEL")
    tts_voice_id: str = Field(default="female-shaonv", alias="TTS_VOICE_ID")

    # Image generation (MiniMax image-01). Fallback when the chat image
    # picker finds no embedding match in the pool — generates a fresh
    # photo from a prompt built out of the character card + user request.
    image_gen_base_url: str = Field(default="https://api.minimaxi.com", alias="IMAGE_GEN_BASE_URL")
    image_gen_api_key: str = Field(default="sk-replace-me", alias="IMAGE_GEN_API_KEY")
    image_gen_model: str = Field(default="image-01", alias="IMAGE_GEN_MODEL")

    # Tavily search API for daily news digest.
    tavily_api_key: str = Field(default="tvly-replace-me", alias="TAVILY_API_KEY")

    # User profile for news personalisation.
    user_profession: str = Field(default="", alias="USER_PROFESSION")
    user_hobbies: str = Field(default="", alias="USER_HOBBIES")

    # AI 作息：睡眠时段不回复，起床后补回。
    sleep_enabled: bool = Field(default=True, alias="SLEEP_ENABLED")
    sleep_time: str = Field(default="00:30", alias="SLEEP_TIME")
    wake_time: str = Field(default="07:30", alias="WAKE_TIME")

    # 故事情景触发：用户消息与触发条件的 embedding 余弦相似度阈值，
    # 只有达到该阈值才触发对应情景文本（0~1，越高越严格）。
    story_trigger_similarity: float = Field(default=0.9, alias="STORY_TRIGGER_SIMILARITY")

    # Local embedding server (sentence-transformers bge-m3) launched in-process.
    # When enabled, the lifespan starts an internal HTTP server on
    # 127.0.0.1:embed_local_port, and the embedding_base_url/api_key/model above
    # are auto-pointed at it (unless EMBEDDING_BASE_URL is explicitly set to a
    # remote URL by the user).
    embed_local_enabled: bool = Field(default=True, alias="EMBED_LOCAL_ENABLED")
    embed_local_port: int = Field(default=11435, alias="EMBED_LOCAL_PORT")
    embed_model_name: str = Field(default="BAAI/bge-small-zh-v1.5", alias="EMBED_MODEL_NAME")

    data_dir: Path = Field(default=Path("./data"), alias="DATA_DIR")
    db_path: Path = Field(default=Path("./data/app.sqlite3"), alias="DB_PATH")
    memu_db_path: Path = Field(default=Path("./data/memu.db"), alias="MEMU_DB_PATH")

    app_host: str = Field(default="127.0.0.1", alias="APP_HOST")
    app_port: int = Field(default=8765, alias="APP_PORT")
    history_window: int = Field(default=20, alias="HISTORY_WINDOW")
    # Cap on how many *turns* of recent conversation are fed to the LLM
    # in build_messages. A turn = one user message plus its assistant
    # reply, so 5 turns ≈ 10 messages. Kept separate from
    # ``history_window`` (the SQL fetch limit) so we can fetch wider
    # than we send if we ever want a summariser pass.
    recent_turns: int = Field(default=5, alias="RECENT_TURNS")
    max_upload_mb: int = Field(default=8, alias="MAX_UPLOAD_MB")

    # ---------- Timeout & concurrency governance ----------
    # All previously-hardcoded timeouts live here so they can be tuned
    # without editing source. Values are seconds unless noted.
    #
    # LLM: connect timeout covers TCP/TLS handshake; read timeout covers
    # time-to-first-byte and total stream duration. The SDK default of
    # 600s is far too long — a hung request would stall the SSE stream.
    llm_connect_timeout: float = Field(default=15.0, alias="LLM_CONNECT_TIMEOUT")
    llm_read_timeout: float = Field(default=180.0, alias="LLM_READ_TIMEOUT")
    llm_max_retries: int = Field(default=1, alias="LLM_MAX_RETRIES")

    # Background LLM work (state update, moments, events, milestones, ...)
    # is capped globally so a burst of chats can't stampede the upstream.
    max_concurrent_llm: int = Field(default=4, alias="MAX_CONCURRENT_LLM")
    max_concurrent_background: int = Field(default=6, alias="MAX_CONCURRENT_BACKGROUND")

    # Vision captioning (self-photo pool).
    caption_timeout: float = Field(default=60.0, alias="CAPTION_TIMEOUT")

    # Local embedding inference concurrency (bge-m3 encode is CPU/GPU heavy).
    embed_max_concurrent: int = Field(default=2, alias="EMBED_MAX_CONCURRENT")

    # Chat pipeline stage timeouts.
    chat_reply_timeout: float = Field(default=120.0, alias="CHAT_REPLY_TIMEOUT")
    chat_state_timeout: float = Field(default=20.0, alias="CHAT_STATE_TIMEOUT")
    chat_retrieval_timeout: float = Field(default=8.0, alias="CHAT_RETRIEVAL_TIMEOUT")
    chat_pool_pick_timeout: float = Field(default=50.0, alias="CHAT_POOL_PICK_TIMEOUT")
    chat_choice_timeout: float = Field(default=30.0, alias="CHAT_CHOICE_TIMEOUT")

    # Image generation (MiniMax): internal request + download timeouts must
    # sum to LESS than chat_pool_pick_timeout, otherwise the outer
    # ``wait_for`` cancels the coroutine while the blocking thread keeps
    # running (threads can't be interrupted) and leaks resources.
    image_gen_request_timeout: float = Field(default=30.0, alias="IMAGE_GEN_REQUEST_TIMEOUT")
    image_gen_download_timeout: float = Field(default=12.0, alias="IMAGE_GEN_DOWNLOAD_TIMEOUT")

    # System prompt token budget for priority-based context assembly.
    # When the assembled system prompt exceeds this limit, lower-priority
    # fragments are dropped/truncated to fit. See context_budget.py.
    system_prompt_token_budget: int = Field(default=6000, alias="SYSTEM_PROMPT_TOKEN_BUDGET")

    # Rolling summary compression: when unsummarized turns exceed this
    # threshold, the oldest chunk is compressed via LLM into a single
    # summary row. A turn = one user + one assistant message, so 5 turns
    # = 10 messages. The summary is injected into the system prompt as a
    # CONSTRAINT-priority fragment, preserving early-conversation context
    # that would otherwise be lost beyond the recent_turns window.
    summary_chunk_turns: int = Field(default=5, alias="SUMMARY_CHUNK_TURNS")
    summary_max_tokens: int = Field(default=800, alias="SUMMARY_MAX_TOKENS")
    summary_enabled: bool = Field(default=True, alias="SUMMARY_ENABLED")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    (s.data_dir / "uploads").mkdir(parents=True, exist_ok=True)
    return s


def write_env(overrides: dict[str, str]) -> None:
    """Persist KEY=VALUE pairs into .env, preserving existing lines.

    Existing keys are replaced in place; new keys are appended. Blank or
    missing values are skipped so callers can pass only what changed."""
    overrides = {k: v for k, v in overrides.items() if v}
    if not overrides:
        return
    path = Path(".env")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in overrides:
            out.append(f"{key}={overrides.pop(key)}")
        else:
            out.append(line)
    for key, value in overrides.items():
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")