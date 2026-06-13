"""Risoluzione di token di data relativi nelle fixture eval, così non scadono.

Le fixture esprimono l'INTENTO temporale invece di date assolute (che invecchiano):
  {{TODAY}}    -> data di oggi          (es. "14 giugno 2026")
  {{TODAY+N}}  -> oggi + N giorni       (evento imminente / futuro)
  {{TODAY-N}}  -> oggi - N giorni       (evento passato)

Le date sono formattate in italiano ("D mese YYYY") per coerenza con le fixture
e con come gli eventi sono descritti nel contesto RAG.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_MESI = [
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
]
_TOKEN_RE = re.compile(r"\{\{TODAY([+-]\d+)?\}\}")


def _format_it(d: date) -> str:
    return f"{d.day} {_MESI[d.month - 1]} {d.year}"


def today_rome() -> date:
    """Oggi nel fuso del bot (Europe/Rome), così i token combaciano con il
    DATA E ORA ATTUALE iniettato nel system prompt al momento della risposta."""
    return datetime.now(ZoneInfo("Europe/Rome")).date()


def resolve_tokens(text: str, today: date | None = None) -> str:
    """Sostituisce i token {{TODAY±N}} con date concrete. No-op se non ce ne sono."""
    if not text or "{{TODAY" not in text:
        return text
    base = today or today_rome()

    def _sub(m: "re.Match[str]") -> str:
        offset = int(m.group(1)) if m.group(1) else 0
        return _format_it(base + timedelta(days=offset))

    return _TOKEN_RE.sub(_sub, text)
