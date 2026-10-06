"""Concerti ("Live Events"): un minore entra accompagnato dal genitore.

Caso reale (WhatsApp, 6/10, concerto Slomosa marcato 14+). Un genitore scrive "io
comunque entro con lei nel locale, non è sola" e il bot risponde: "la regola non
cambia... non c'è eccezione per genitori con bambini sotto quella soglia. Mi dispiace
ma non potrà entrare in sala con te." Sbagliato: sui concerti la deroga esiste
(conferma staff 6/10) e quella famiglia è stata respinta per niente.

La deroga vale SOLO sui concerti — le serate con "Live Events" fra i Generi — e mai
sulle serate club, dove la soglia resta rigida.
"""
import pathlib

import pytest

from ai.claude_client import build_system_blocks

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_KB_MILANO = (_ROOT / "rag" / "knowledge" / "gate_milano.md").read_text(encoding="utf-8")


def _prompt(venue: str = "gate_milano") -> str:
    return "\n".join(b["text"] for b in build_system_blocks(venue, "RAG", "DT"))


def test_deroga_concerti_presente_nel_prompt():
    p = _prompt()
    assert "Live Events" in p
    assert "genitore" in p.lower()


def test_deroga_e_limitata_ai_concerti():
    """Deve essere esplicito che sulle serate club la soglia non si tocca."""
    p = _prompt()
    blocco = p[p.index("DEROGA GENITORE"):]
    assert "NON vale per le serate club" in blocco
    assert "Perreo XL" in blocco


def test_kb_non_nega_piu_la_deroga():
    # La vecchia frase diceva che a Milano la deroga col genitore NON esiste: era
    # quella a far rispondere "non c'è eccezione" al genitore del concerto.
    assert "NON esiste a Milano** l'ingresso ai minori" not in _KB_MILANO
    assert "non esiste alcuna deroga per minorenni accompagnati" not in _prompt().lower()


def test_kb_cita_il_caso_del_bambino():
    # Lo staff ha indicato esplicitamente che anche un bambino piccolo entra col
    # genitore: senza l'esempio il bot tende a reintrodurre una soglia minima sua.
    assert "8 anni" in _KB_MILANO


def test_soglia_della_serata_resta_prioritaria():
    p = _prompt()
    assert "18+" in p  # la base resta
    assert "14+" in p or "16+" in p  # e le soglie per-serata sono previste


def test_alcol_resta_maggiorenni():
    # Entrare non significa consumare: la deroga non tocca il servizio alcolici.
    p = _prompt().lower()
    assert "alcol" in p and "18+" in p


def test_sardegna_non_tocca_la_regola_milano():
    # Gate Sardinia ha una sua policy sui minori: le due non devono confondersi.
    sard = _prompt("gate_sardinia")
    assert "Live Events" not in sard.split("INFORMAZIONI FISSE VENUE")[0]
