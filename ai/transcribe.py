"""Trascrizione dei vocali in chat (OpenAI Whisper API).

I clienti mandano vocali su WhatsApp/Instagram: invece del vecchio "scrivimi a
parole", il vocale viene scaricato, trascritto e passato al bot come normale
testo. Se OPENAI_API_KEY non è configurata, o la trascrizione fallisce, si
ricade sul fallback gentile di sempre: mai un errore in faccia al cliente.
"""
from __future__ import annotations

import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Whisper accetta file fino a ~25MB: margine di sicurezza (i vocali reali sono
# nell'ordine delle centinaia di KB).
_MAX_AUDIO_BYTES = 20_000_000


def transcription_enabled() -> bool:
    return bool(settings.openai_api_key)


async def fetch_media_bytes(url: str, headers: dict | None = None) -> tuple[bytes, str] | None:
    """Scarica un media: vocale IG (CDN pubblico) o media WhatsApp (serve
    l'header Authorization). Ritorna (bytes, content_type) o None. Non solleva."""
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=headers or {}) as client:
            r = await client.get(url)
        if r.status_code != 200:
            logger.warning("Download media fallito: HTTP %s per %s", r.status_code, str(url)[:80])
            return None
        if len(r.content) > _MAX_AUDIO_BYTES:
            return None
        ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
        return r.content, ctype
    except Exception as e:
        logger.warning("Download media fallito (%s): %s", str(url)[:80], e)
        return None


def _ext_for(mime: str) -> str:
    m = (mime or "").lower()
    if "mp4" in m or "m4a" in m or "aac" in m:
        return "m4a"
    if "mpeg" in m or "mp3" in m:
        return "mp3"
    if "wav" in m:
        return "wav"
    if "webm" in m:
        return "webm"
    return "ogg"  # default WhatsApp/IG: audio/ogg (opus)


async def transcribe_audio(data: bytes, mime: str = "audio/ogg") -> str | None:
    """Testo del vocale, o None (chiave non configurata / errore / vuoto).
    Whisper riconosce la lingua da solo (i clienti scrivono in più lingue).
    Non solleva mai."""
    if not settings.openai_api_key or not data or len(data) > _MAX_AUDIO_BYTES:
        return None
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                data={"model": "whisper-1"},
                files={"file": (f"voice.{_ext_for(mime)}", data, mime or "application/octet-stream")},
            )
        if r.status_code != 200:
            logger.error("Trascrizione fallita: HTTP %s — %s", r.status_code, r.text[:300])
            return None
        text = (r.json().get("text") or "").strip()
        return text or None
    except Exception as e:
        logger.error("Trascrizione fallita: %s", e)
        return None
