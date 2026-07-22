"""Unit test (offline) del parser di enrichment TicketSMS per gli eventi Sardegna."""
import json

from sync.sanity_sync import (
    _extract_ticketsms_slug,
    _quill_to_text,
    _parse_ticketsms_event,
)


def test_extract_slug():
    url = "https://www.ticketsms.it/event/Perreo-Xl-Budoni-Gate-Sardinia-04-07-2026"
    assert _extract_ticketsms_slug(url) == "Perreo-Xl-Budoni-Gate-Sardinia-04-07-2026"
    assert _extract_ticketsms_slug("https://xceed.me/whatever") == ""
    assert _extract_ticketsms_slug("") == ""


def test_quill_to_text():
    raw = json.dumps({"ops": [{"attributes": {"bold": True}, "insert": "Titolo"}, {"insert": "\ncorpo"}]})
    assert _quill_to_text(raw) == "Titolo\ncorpo"
    assert _quill_to_text("") == ""
    assert _quill_to_text("non-json") == "non-json"


_SAMPLE = {
    "body": [
        {
            "ticketsPriceMin": "a partire da €11.50",
            "list": [
                {"componentType": "eventDetails",
                 "description": json.dumps({"ops": [{"insert": "Sabato 4 luglio Perreo XL"}]})},
                {"componentType": "ticket", "typeTicketDescription": "Early Bird Donna",
                 "price": {"amount": "1000", "formatted": "€10.00"}, "sector": {"name": None}},
                {"componentType": "ticket", "typeTicketDescription": "Early Bird VIP",
                 "price": {"amount": "4500", "formatted": "€45.00"}, "sector": {"name": "VIP"}},
                {"componentType": "ticket", "typeTicketDescription": "Last Release VIP",
                 "price": {"amount": "6000", "formatted": "€60.00"}, "sector": {"name": "VIP"}},
            ],
        }
    ]
}


def test_parse_about_and_prices():
    out = _parse_ticketsms_event(_SAMPLE)
    assert out["about"] == "Sabato 4 luglio Perreo XL"
    ps = out["prices_str"]
    assert "a partire da €11.50" in ps
    # settore senza nome → "Generale", prezzo minimo
    assert "Generale: a partire da €10.00" in ps
    # per il settore VIP deve prendere il MINIMO (€45), non €60
    assert "VIP: a partire da €45.00" in ps
    assert "€60.00" not in ps


def test_parse_empty_is_safe():
    out = _parse_ticketsms_event({})
    assert out == {"about": "", "prices_str": "", "canceled": False}
    out2 = _parse_ticketsms_event({"body": [{"list": [{"componentType": "ticket", "price": {"amount": "x"}}]}]})
    assert out2["prices_str"] == ""  # amount non numerico → ignorato
