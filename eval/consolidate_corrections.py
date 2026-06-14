"""Consolida le correzioni staff approvate nella KB canonica (tool locale, dev-only).

Uso: python -m eval.consolidate_corrections <base_url> --token <TOKEN>
Edit SOLO additivi alla KB (mai riscrive testo esistente). Vedi
docs/superpowers/specs/2026-06-14-consolidate-corrections-to-kb-design.md.
"""
from __future__ import annotations

import logging
import argparse
import asyncio
import sys

import httpx

import config

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


_PLACEMENT_INSTRUCTIONS = """\
Integri una REGOLA staff nella knowledge base di un chatbot di un club/venue.
Ricevi la regola e l'elenco delle sezioni (heading) della KB.
Scegli la sezione più pertinente: riporta il testo ESATTO dell'heading (incluso "## ").
Se nessuna è adatta, lascia 'section' vuoto.
Scrivi 'line': una sola riga di guida, concisa, in stile KB, che cattura la regola
(senza markdown, senza trattino iniziale).
Registra con lo strumento propose_kb_placement.
"""

_PLACEMENT_TOOL = {
    "name": "propose_kb_placement",
    "description": "Propone dove integrare la regola nella KB.",
    "input_schema": {
        "type": "object",
        "properties": {
            "section": {"type": "string"},
            "line": {"type": "string"},
        },
        "required": ["section", "line"],
    },
}


def _headings(kb_text: str) -> list[str]:
    return [l for l in kb_text.split("\n") if l.startswith("## ")]


def _format_placement(kb_text: str, rule: str) -> str:
    hs = "\n".join(_headings(kb_text)) or "(nessuna sezione)"
    return f"REGOLA:\n{rule}\n\nSEZIONI DISPONIBILI:\n{hs}"


async def propose_placement(kb_text: str, rule: str, *, client, model: str) -> dict | None:
    """Chiede all'LLM dove inserire la regola. Ritorna {section, line} o None."""
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=300,
            temperature=0,
            system=_PLACEMENT_INSTRUCTIONS,
            tools=[_PLACEMENT_TOOL],
            tool_choice={"type": "tool", "name": "propose_kb_placement"},
            messages=[{"role": "user", "content": _format_placement(kb_text, rule)}],
        )
    except Exception:
        logger.exception("propose_placement: errore LLM")
        return None
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            data = block.input
            if data.get("line"):
                return {"section": data.get("section", "") or "", "line": data["line"]}
    return None


def _fetch(base_url: str, token: str) -> list[dict]:
    url = base_url.rstrip("/") + "/eval/corrections"
    r = httpx.get(url, params={"key": token}, timeout=20)
    r.raise_for_status()
    return r.json().get("corrections", [])


async def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("base_url")
    p.add_argument("--token", required=True)
    args = p.parse_args(argv)

    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=config.settings.anthropic_api_key)
    model = config.settings.model

    corrections = _fetch(args.base_url, args.token)
    if not corrections:
        print("Niente da consolidare.")
        return 0

    by_venue: dict[str, list[dict]] = {}
    for c in corrections:
        by_venue.setdefault(c["venue"], []).append(c)

    consolidated_ids: list[str] = []
    for venue, items in by_venue.items():
        kb_path = config.KNOWLEDGE_DIR / f"{venue}.md"
        if not kb_path.exists():
            print(f"⚠️ KB mancante per {venue}, salto")
            continue
        kb = kb_path.read_text(encoding="utf-8")
        changed = False
        for corr in items:
            if corr["rule"] in kb:
                continue  # già presente
            placement = await propose_placement(kb, corr["rule"], client=client, model=model)
            if not placement:
                print(f"⚠️ Salto {corr['id']}: placement non generato")
                continue
            new_kb = _apply_edit(kb, placement["section"], placement["line"])
            if new_kb != kb:
                kb = new_kb
                changed = True
                consolidated_ids.append(corr["id"])
        if changed:
            kb_path.write_text(kb, encoding="utf-8")

    if consolidated_ids:
        print("Consolidati:", ", ".join(consolidated_ids))
        print("Rivedi col `git diff`, poi `python -m eval.run`, poi commit+push.")
        print("Infine su Discord: " + " ".join(f"!rimuovi {i}" for i in consolidated_ids))
    else:
        print("Nessuna regola consolidata (già presenti o placement falliti).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
