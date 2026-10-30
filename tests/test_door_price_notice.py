"""Prezzo alla porta non ancora deciso (Halloween 31/10, staff 28/10).

Caso reale WA 30/10: il cliente chiedeva se si può pagare al locale senza prevendita,
senza nominare Halloween; il bot dava i prezzi Perreo standard (€10/€15/€20).
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from ai import claude_client as cc

ROME = ZoneInfo("Europe/Rome")


def test_avviso_attivo_nei_giorni_prima_e_durante_la_serata():
    for when in (datetime(2026, 10, 30, 16, 0, tzinfo=ROME), datetime(2026, 11, 1, 3, 0, tzinfo=ROME)):
        notice = cc.door_price_notice(when)
        assert "HALLOWEEN" in notice and "NON è ancora stato definito" in notice


def test_avviso_spento_dopo_la_serata():
    assert cc.door_price_notice(datetime(2026, 11, 2, 12, 0, tzinfo=ROME)) == ""


def test_avviso_nel_blocco_dinamico_solo_milano(monkeypatch):
    monkeypatch.setattr(cc, "door_price_notice", lambda now=None: "AVVISO PORTA")
    milano = cc.build_system_blocks("gate_milano", "", "oggi")
    assert "AVVISO PORTA" in milano[1]["text"] and "AVVISO PORTA" not in milano[0]["text"]
    sardegna = cc.build_system_blocks("gate_sardinia", "", "oggi")
    assert "AVVISO PORTA" not in sardegna[1]["text"]
