"""Fallback LLM per il rilevamento venue (solo quando le keyword non bastano).

WhatsApp usa un numero condiviso tra Gate Milano e Gate Sardinia: la venue va dedotta
dal testo. Il VenueDetector a keyword copre i casi comuni; quando resta ambiguo, questo
mini-classificatore sfrutta la conoscenza geografica del modello (es. "San Teodoro è in
Sardegna") — senza API di mappe, senza chiavi nuove. Gira SOLO sui messaggi ambigui.
Su qualsiasi errore torna None (il chiamante applica il default), mai solleva.
"""
from __future__ import annotations

import logging

from config import settings

logger = logging.getLogger(__name__)

_SYSTEM = """Sei un classificatore per un locale con DUE sedi:
- Gate Milano — a Milano (Lombardia)
- Gate Sardinia — a Budoni, in Sardegna (zona Olbia, Nuoro, San Teodoro, Orosei, Costa Smeralda)

Dato il messaggio di un cliente, deduci a quale sede si riferisce, anche dai luoghi/città
citati o dal contesto (es. un paese sardo → Sardegna; un quartiere/città lombarda → Milano).
Rispondi con UNA sola parola, minuscola: "milano", "sardegna", oppure "sconosciuto" se non è
deducibile con ragionevole certezza."""


async def classify_venue(text: str) -> str | None:
    """'gate_milano' / 'gate_sardinia' / None (ambiguo o disabilitato/errore)."""
    text = (text or "").strip()
    if not text or not settings.venue_llm_fallback:
        return None
    try:
        from ai.claude_client import _client
        model = settings.venue_classifier_model or settings.model
        resp = await _client.messages.create(
            model=model,
            max_tokens=8,
            temperature=0,
            system=_SYSTEM,
            messages=[{"role": "user", "content": text[:500]}],
        )
        out = (resp.content[0].text or "").strip().lower()
    except Exception as e:
        logger.warning("classify_venue fallita (fallback su default): %s", e)
        return None
    if "milano" in out or "milan" in out:
        return "gate_milano"
    if "sard" in out:
        return "gate_sardinia"
    return None
