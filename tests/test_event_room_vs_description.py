"""Sala dell'evento: vale il campo "Sala:", non la descrizione della biglietteria.

Caso reale (WA 1/11): "Quando fate hard techno nella sala principale?". Samuel Moriero
il 6/11 è in Main Room sul sito, ma la descrizione Xceed diceva "PRTY chiama Samuel
Moriero in Club Room" e il bot rispondeva che in Main Room non c'era hard techno.
"""
from ai import claude_client as cc
from sync.sanity_sync import _build_document


def test_descrizione_marcata_come_promozionale():
    event = {"title": "PRTY & PLASMA: Samuel Moriero & Exproz", "date": "2026-11-06T22:00:00.000Z",
             "slug": {"current": "prty"}, "venue": "Main Room", "genres": ["Hardtechno"]}
    doc, _ = _build_document(event, "Gate Milano",
                             {"about": "PRTY chiama Samuel Moriero in Club Room. Hard techno."})
    assert "Sala: Main Room" in doc
    line = [l for l in doc.splitlines() if l.startswith("Descrizione")][0]
    assert "per sala, data e orari valgono i campi sopra" in line and "Club Room" in line


def test_regola_sala_nel_prompt():
    static = cc.build_system_blocks("gate_milano", "", "oggi")[0]["text"]
    assert "SALA DI UN EVENTO" in static and '"Hardtechno" = hard techno' in static


def test_regola_problemi_acquisto_gira_link_evento():
    static = cc.build_system_blocks("gate_milano", "", "oggi")[0]["text"]
    assert "PROBLEMI DURANTE L'ACQUISTO" in static and "aspettare qualche minuto" in static


def test_regole_prenotazione_tavoli_niente_email_niente_azioni_finte():
    """Caso reale IG 19/11: "scrivi a info@", "giro la richiesta, lo staff ti ricontatta"."""
    static = cc.build_system_blocks("gate_milano", "", "oggi")[0]["text"]
    assert "Per prenotare: info@gatemilano.com" not in static
    assert "NON PUOI fare azioni fuori dalla chat" in static
    assert "SE TI SEI SBAGLIATO PRIMA" in static
    assert "LINK PER PRENOTARE SUBITO" in static


def test_regola_evento_non_nel_contesto():
    static = cc.build_system_blocks("gate_milano", "", "oggi")[0]["text"]
    assert "EVENTO NON NEL CONTESTO" in static and "aggiornarlo più avanti" in static
