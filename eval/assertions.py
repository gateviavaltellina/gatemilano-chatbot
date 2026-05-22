"""Controlli deterministici sulla risposta del bot (eseguiti prima del judge)."""
from __future__ import annotations
import re

from eval.schema import Assertions

# Marcatori markdown vietati su WhatsApp (il bot deve scrivere testo semplice)
_BOLD = re.compile(r"\*{1,3}[^*\n]+\*{1,3}")
_ITALIC = re.compile(r"(?<!\w)_{1,2}[^_\n]+_{1,2}(?!\w)")
_BULLET = re.compile(r"^\s*[-*]\s+", re.MULTILINE)


def detect_markdown(text: str) -> list[str]:
    """Ritorna l'elenco dei marcatori markdown trovati (vuoto se pulito)."""
    found = []
    if _BOLD.search(text):
        found.append("bold (*...*)")
    if _ITALIC.search(text):
        found.append("italic (_..._)")
    if _BULLET.search(text):
        found.append("bullet list")
    return found


def run_assertions(reply: str, assertions: Assertions) -> list[str]:
    """Ritorna l'elenco delle violazioni deterministiche (vuoto = pass)."""
    failures: list[str] = []
    low = reply.lower()
    for sub in assertions.forbidden_substrings:
        if sub.lower() in low:
            failures.append(f"contiene la stringa vietata: {sub!r}")
    if assertions.forbidden_markdown:
        for marker in detect_markdown(reply):
            failures.append(f"markdown vietato: {marker}")
    return failures
