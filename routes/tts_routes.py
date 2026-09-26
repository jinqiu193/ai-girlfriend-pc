"""Text-to-speech via MiniMax t2a_v2.

Synthesizes an MP3 from the supplied text and returns the raw audio bytes.
The voice / model / key are read from the in-app settings so the user can
switch timbre from the settings page without a restart.
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from auth import current_user
from config import get_settings

log = logging.getLogger("ai-girlfriend.tts")
router = APIRouter(prefix="/api")


class TTSBody(BaseModel):
    text: str


@router.post("/tts")
async def synthesize(body: TTSBody, _: str = Depends(current_user)):
    s = get_settings()
    text = (body.text or "").strip()
    if not text:
        return Response(status_code=400, content='{"error":"empty text"}', media_type="application/json")

    url = f"{s.tts_base_url.rstrip('/')}/t2a_v2"
    payload = {
        "model": s.tts_model,
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": s.tts_voice_id,
            "speed": 1.0,
            "vol": 1.0,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {s.tts_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        log.warning("tts request failed: %s", exc)
        return Response(status_code=502, content='{"error":"tts unreachable"}', media_type="application/json")

    if resp.status_code != 200:
        log.warning("tts api error %s: %s", resp.status_code, resp.text[:200])
        return Response(
            status_code=502,
            content=f'{{"error":"tts api {resp.status_code}"}}',
            media_type="application/json",
        )

    data = resp.json()
    audio_hex = (data.get("data") or {}).get("audio", "")
    if not audio_hex:
        log.warning("tts returned no audio: %s", str(data)[:200])
        return Response(status_code=502, content='{"error":"no audio"}', media_type="application/json")

    audio_bytes = bytes.fromhex(audio_hex)
    return Response(content=audio_bytes, media_type="audio/mpeg")