"""Consolida le correzioni staff approvate nella KB canonica (tool locale, dev-only).

Uso: python -m eval.consolidate_corrections <base_url> --token <TOKEN>
Edit SOLO additivi alla KB (mai riscrive testo esistente). Vedi
docs/superpowers/specs/2026-06-14-consolidate-corrections-to-kb-design.md.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CONSOLIDATED_SECTION = "## Regole consolidate (da correzioni staff)"


def _apply_edit(kb_text: str, section: str, line: str) -> str:
    """Aggiunge '- line' sotto l'heading 'section' se esiste, altrimenti sotto una
    sezione dedicata in fondo. Idempotente: se 'line' è già nel testo, no-op.
    Non riscrive MAI testo esistente."""
    if line in kb_text:  # dedup
        return kb_text
    bullet = f"- {line}"
    heading = (section or "").strip()
    lines = kb_text.split("\n")
    if heading and heading in lines:
        i = lines.index(heading)
        lines.insert(i + 1, bullet)
        return "\n".join(lines)
    # fallback: sezione consolidata in fondo
    text = kb_text.rstrip()
    if _CONSOLIDATED_SECTION not in kb_text:
        text += f"\n\n{_CONSOLIDATED_SECTION}"
    return text + f"\n{bullet}\n"
