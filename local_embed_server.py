"""Local OpenAI-compatible embedding server.

Runs alongside the FastAPI app (either as a subprocess on EMBED_PORT or mounted
via the parent process). Exposes ``POST /v1/embeddings`` matching the OpenAI
schema so memU's httpx backend can call it without modification.

Default model: ``BAAI/bge-small-zh-v1.5`` — lightweight Chinese embedding,
~0.1GB on disk, 512-dim vectors.

Run standalone::

    python -m local_embed_server --port 11435 --model BAAI/bge-small-zh-v1.5

Or, when imported, ``make_app()`` returns a FastAPI sub-app the parent can
mount under ``/_embed``.
"""
from __future__ import annotations

import argparse
import logging
import os
import threading
from pathlib import Path
from typing import Any

# Pin HuggingFace cache into the project before any HF imports, so we never
# accidentally write into the OS-supplied default (which on this machine is a
# Kingsoft-protected directory we cannot symlink into). HF_ENDPOINT may still
# be set in the environment for users who want the mirror; we don't touch it.
# We force offline mode so the model loader never reaches out — the bge-m3
# weights are already present in HF_HOME after the first download.
_HERE = Path(__file__).resolve().parent
_DEFAULT_HF_HOME = _HERE / ".cache" / "huggingface"
_DEFAULT_HF_HOME.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HF_HOME", str(_DEFAULT_HF_HOME))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger("local_embed")

_MODEL = None  # type: ignore[var-annotated]
_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
_LOAD_FAIL_UNTIL = 0.0  # monotonic timestamp: skip re-load attempts until then
_LOAD_RETRY_INTERVAL = 300.0  # seconds between failed load attempts

# Guards model loading so the startup warmup thread and the first request
# can't both load the model into memory. Also bounds concurrent
# inference — PyTorch encode is CPU/GPU heavy and concurrent calls thrash
# the device.
_LOAD_LOCK = threading.Lock()
_INFER_SEMAPHORE: threading.Semaphore | None = None


def _get_infer_semaphore() -> threading.Semaphore:
    global _INFER_SEMAPHORE
    if _INFER_SEMAPHORE is None:
        try:
            from config import get_settings
            limit = get_settings().embed_max_concurrent
        except Exception:  # noqa: BLE001
            limit = 2
        _INFER_SEMAPHORE = threading.Semaphore(max(1, limit))
    return _INFER_SEMAPHORE


def _encode_texts(model, texts: list[str]) -> list[list[float]]:
    """Run encode under the inference semaphore, returning plain lists."""
    with _get_infer_semaphore():
        return model.encode(
            texts,
            batch_size=8,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        ).tolist()


def _load_model(model_name: str):
    """Lazy-load the sentence-transformers model on first request.

    Double-checked locking ensures only one thread performs the (multi-
    second) load. On failure we back off for ``_LOAD_RETRY_INTERVAL``
    seconds instead of re-attempting on every request.
    """
    global _MODEL, _LOAD_FAIL_UNTIL
    if _MODEL is not None:
        return _MODEL
    import time

    with _LOAD_LOCK:
        if _MODEL is not None:
            return _MODEL
        if time.monotonic() < _LOAD_FAIL_UNTIL:
            raise RuntimeError(
                f"embedding model load failed recently; backing off "
                f"for {int(_LOAD_FAIL_UNTIL - time.monotonic())}s more"
            )
        from sentence_transformers import SentenceTransformer

        log.info("loading embedding model %s …", model_name)
        # device auto: cuda if available else cpu
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            _MODEL = SentenceTransformer(model_name, device=device)
        except Exception:
            _LOAD_FAIL_UNTIL = time.monotonic() + _LOAD_RETRY_INTERVAL
            raise
        log.info("model %s loaded on %s", model_name, device)
        return _MODEL


class EmbeddingRequest(BaseModel):
    input: str | list[str] = Field(...)
    model: str | None = None
    encoding_format: str | None = None  # ignored, always return float list


class EmbeddingData(BaseModel):
    object: str = "embedding"
    embedding: list[float]
    index: int


class EmbeddingResponse(BaseModel):
    object: str = "list"
    data: list[EmbeddingData]
    model: str
    usage: dict[str, int]


def make_app(model_name: str = _MODEL_NAME, *, under_v1: bool = False) -> FastAPI:
    """Build the embed FastAPI app.

    When ``under_v1=False`` (default for standalone mode), routes live at
    ``/models`` and ``/embeddings`` and the caller is expected to run it via
    uvicorn at ``http://host:port/v1`` (so the effective URL is
    ``http://host:port/v1/embeddings``).

    When ``under_v1=True`` (the mode used when the parent app *mounts* this
    sub-app at root), routes are ``/v1/models`` and ``/v1/embeddings`` directly.
    """
    app = FastAPI(title="local-embed", version="1.0")
    base = "/v1" if under_v1 else ""

    @app.get(f"{base}/models")
    def list_models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": model_name,
                    "object": "model",
                    "created": 0,
                    "owned_by": "local",
                }
            ],
        }

    @app.post(f"{base}/embeddings", response_model=EmbeddingResponse)
    def embed(req: EmbeddingRequest) -> EmbeddingResponse:
        texts = req.input if isinstance(req.input, list) else [req.input]
        if not texts:
            raise HTTPException(400, "input must be a non-empty string or list")
        try:
            model = _load_model(model_name)
        except Exception as exc:  # noqa: BLE001
            log.exception("failed to load embedding model")
            raise HTTPException(503, f"model unavailable: {exc}") from exc
        vectors = _encode_texts(model, texts)
        return EmbeddingResponse(
            data=[
                EmbeddingData(embedding=v, index=i) for i, v in enumerate(vectors)
            ],
            model=req.model or model_name,
            usage={"prompt_tokens": 0, "total_tokens": 0},
        )

    return app


def register_routes(parent_app: FastAPI, model_name: str = _MODEL_NAME) -> None:
    """Attach ``/v1/models`` and ``/v1/embeddings`` to an existing FastAPI app.

    Use this instead of ``app.mount()`` when you want the embeddings endpoint
    to live on the same Starlette routing tree (no mount prefix shenanigans).
    """
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/v1/models")
    def list_models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": model_name,
                    "object": "model",
                    "created": 0,
                    "owned_by": "local",
                }
            ],
        }

    @router.post("/v1/embeddings", response_model=EmbeddingResponse)
    def embed(req: EmbeddingRequest) -> EmbeddingResponse:
        texts = req.input if isinstance(req.input, list) else [req.input]
        if not texts:
            from fastapi import HTTPException

            raise HTTPException(400, "input must be a non-empty string or list")
        try:
            model = _load_model(model_name)
        except Exception as exc:  # noqa: BLE001
            log.exception("failed to load embedding model")
            from fastapi import HTTPException

            raise HTTPException(503, f"model unavailable: {exc}") from exc
        vectors = _encode_texts(model, texts)
        return EmbeddingResponse(
            data=[
                EmbeddingData(embedding=v, index=i) for i, v in enumerate(vectors)
            ],
            model=req.model or model_name,
            usage={"prompt_tokens": 0, "total_tokens": 0},
        )

    parent_app.include_router(router)


def main() -> None:
    parser = argparse.ArgumentParser(description="local OpenAI-compatible embedding server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11435)
    parser.add_argument("--model", default=_MODEL_NAME)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    import uvicorn

    app = make_app(args.model)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()