"""Regressioni per il go-live Gate Sardinia: prompt per-venue + drinklist venue-aware.

Garantiscono che (a) il prompt Sardegna non contenga più dati Milano hardcoded,
(b) il prompt Milano resti col suo contenuto, (c) la drinklist sia selezionata per venue.
"""
import os

from ai.claude_client import build_system_blocks
from whatsapp.webhook import _DRINKLISTS


def _static(venue: str) -> str:
    return build_system_blocks(venue, "RAG", "DT")[0]["text"]


# --- Prompt Sardegna: niente contatti/fatti Milano ---

def test_sardinia_prompt_has_no_milano_contacts():
    s = _static("gate_sardinia")
    for milano_token in (
        "gatemilano", "Antonio", "389 640 6077", "329 169 6882",
        "marketing@", "Main Room", "Club Room", "Carroponte", "Valtellina",
    ):
        assert milano_token not in s, f"Sardegna non deve contenere '{milano_token}'"


def test_sardinia_prompt_has_no_milano_hours():
    s = _static("gate_sardinia")
    assert "23:00" not in s and "05:00" not in s
    assert "22:00 – 04:00" in s


def test_sardinia_prompt_uses_ticketsms_not_xceed_dice_as_platform():
    s = _static("gate_sardinia")
    assert "ticketsms.it" in s
    # Xceed/Dice possono comparire solo nella regola che li VIETA, mai come piattaforma.
    assert "xceed.me" not in s
    assert "dice.fm" not in s


def test_sardinia_prompt_has_vip_zones_and_booking_channel():
    s = _static("gate_sardinia")
    assert "Terrace" in s
    assert "vip@gatesardinia.it" in s
    assert "+39 391 487 6443" in s


def test_sardinia_prompt_directs_tables_to_online_booking():
    s = _static("gate_sardinia")
    # i tavoli ora si prenotano/pagano online: il prompt deve citare il link del sito
    assert "gatesardinia.it/tavoli" in s
    # e NON deve più dire che la prenotazione online non è attiva
    assert "non è ancora attiva" not in s


# --- Drinklist: decisione di invio del PDF ---

def test_drinklist_explicit_request_always_sends():
    from whatsapp.webhook import _should_send_drinklist
    # richiesta esplicita → invia anche se già inviato in precedenza
    assert _should_send_drinklist("gate_sardinia", "mi giri il listino bottiglie?", "", already_sent=True)
    assert _should_send_drinklist("gate_sardinia", "mi mandi la drinklist?", "", already_sent=True)


def test_drinklist_implicit_trigger_sends_once():
    from whatsapp.webhook import _should_send_drinklist
    # parlando di tavoli: invio proattivo solo la prima volta
    assert _should_send_drinklist("gate_sardinia", "vorrei un tavolo", "", already_sent=False)
    assert not _should_send_drinklist("gate_sardinia", "vorrei un tavolo", "", already_sent=True)


def test_drinklist_unrelated_message_no_send():
    from whatsapp.webhook import _should_send_drinklist
    assert not _should_send_drinklist("gate_sardinia", "a che ora aprite?", "alle 22", already_sent=False)


def test_drinklist_unknown_venue_no_send():
    from whatsapp.webhook import _should_send_drinklist
    assert not _should_send_drinklist("gate_unknown", "mandami il listino", "", already_sent=False)


# --- Prompt Milano: contenuto preservato ---

def test_milano_prompt_keeps_its_content():
    s = _static("gate_milano")
    for milano_token in ("Main Room", "Antonio", "23:00 – 05:00", "info@gatemilano.com"):
        assert milano_token in s, f"Milano deve ancora contenere '{milano_token}'"


# --- Drinklist venue-aware ---

def test_drinklist_mapping_is_venue_aware():
    milano_url, milano_name = _DRINKLISTS["gate_milano"]
    sard_url, sard_name = _DRINKLISTS["gate_sardinia"]
    assert "drinklist_perreo.pdf" in milano_url
    assert "drinklist_sardegna.pdf" in sard_url
    assert milano_url != sard_url
    assert milano_name != sard_name


def test_sardinia_drinklist_pdf_exists_in_static():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert os.path.exists(os.path.join(root, "static", "drinklist_sardegna.pdf"))
