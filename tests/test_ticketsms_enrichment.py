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
    # ogni TIPO di biglietto col suo NOME reale (non più solo il minimo per settore):
    # il bot deve saper rispondere su "early entry"/"early bird" citati sulla pagina.
    assert "Early Bird Donna: €10.00" in ps
    assert "Early Bird VIP: €45.00" in ps
    assert "Last Release VIP: €60.00" in ps
    # ordinati per prezzo crescente
    assert ps.index("€10.00") < ps.index("€45.00") < ps.index("€60.00")


def test_parse_ticket_names_with_sector_and_presale():
    data = {"body": [{"list": [
        {"componentType": "ticket", "typeTicketDescription": "Early Entry Ticket",
         "price": {"amount": "430", "formatted": "€4.30"},
         "presale": {"amount": "70", "formatted": "€0.70"},
         "sector": {"name": "Posto Unico"}, "stato": "active"},
        {"componentType": "ticket", "typeTicketDescription": "Vecchio Tier",
         "price": {"amount": "1000", "formatted": "€10.00"},
         "sector": {"name": "Posto Unico"}, "stato": "inactive"},  # non attivo → escluso
    ]}]}
    ps = _parse_ticketsms_event(data)["prices_str"]
    # nome + settore (quando il settore non è già nel nome) + commissione prevendita
    assert "Early Entry Ticket (Posto Unico): €4.30 + €0.70 prevendita" in ps
    assert "Vecchio Tier" not in ps


def test_parse_empty_is_safe():
    out = _parse_ticketsms_event({})
    assert out == {"about": "", "prices_str": "", "canceled": False}
    out2 = _parse_ticketsms_event({"body": [{"list": [{"componentType": "ticket", "price": {"amount": "x"}}]}]})
    assert out2["prices_str"] == ""  # amount non numerico → ignorato
