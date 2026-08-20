"""Liste/promo Fourvenues (prevendita €5) nel documento evento.

Le liste si prendono sulla pagina Fourvenues della serata (non su TicketSMS): il
bot deve avere il link giusto e mostrare SOLO le liste rivolte al pubblico —
guest pass / guestlist / contest winner sono gestite dallo staff.

NB Boarding Pass: promo CHIUSA per decisione staff (20/8) — anche se la lista
risultasse ancora aperta su Fourvenues NON deve più comparire nel contesto
(caso reale: il bot la proponeva a un cliente dopo la chiusura).
"""
from sync.sanity_sync import _fv_lists_str, _fv_norm, _build_document


_FV_URL = "https://site.fourvenues.com/gate-sardinia/events/wade-31-07-20261-5UNE"


def _index():
    return {
        ("2026-07-31", _fv_norm("Wade")): {
            "url": _FV_URL, "name": "Wade",
            "lists": [{"slug": "boarding-pass", "name": "BOARDING PASS"},
                      {"slug": "pagamento-5-euro", "name": "Pagamento 5 Euro"}],
        },
        ("date-only", "2026-07-31"): [{
            "url": _FV_URL, "name": "Wade",
            "lists": [{"slug": "boarding-pass", "name": "BOARDING PASS"},
                      {"slug": "pagamento-5-euro", "name": "Pagamento 5 Euro"}],
        }],
        ("2026-08-22", _fv_norm("Perreo XL")): {
            "url": _FV_URL, "name": "Perreo XL",
            "lists": [{"slug": "boarding-pass", "name": "BOARDING PASS"}],
        },
    }


def test_lists_str_renders_public_lists_with_link():
    out = _fv_lists_str(_index(), "2026-07-31", "Wade")
    assert "Prevendita €5" in out and "entro le 23:00" in out
    assert _FV_URL in out


def test_boarding_pass_closed_never_rendered():
    # la promo è chiusa: anche se Fourvenues la lista ancora, NON va nel contesto
    out = _fv_lists_str(_index(), "2026-07-31", "Wade")
    assert "BOARDING" not in out
    # serata con SOLO il boarding pass in lista → nessun blocco liste
    assert _fv_lists_str(_index(), "2026-08-22", "Perreo XL") == ""


def test_lists_str_date_only_fallback_when_name_differs():
    # titolo Sanity diverso dal nome Fourvenues ma unica serata quel giorno → match
    out = _fv_lists_str(_index(), "2026-07-31", "Wade DJ Set")
    assert "Prevendita €5" in out


def test_lists_str_empty_without_index_or_match():
    assert _fv_lists_str({}, "2026-07-31", "Wade") == ""
    assert _fv_lists_str(_index(), "2026-08-15", "Altro") == ""


def test_document_includes_fv_block():
    doc, _ = _build_document(
        {"_id": "wade", "title": "Wade", "date": "2026-07-31"},
        "Gate Sardinia",
        {"about": "", "prices_str": "",
         "fv_lists": _fv_lists_str(_index(), "2026-07-31", "Wade")})
    assert "LISTE/PROMO" in doc
    assert "Prevendita €5" in doc
    assert _FV_URL in doc


def test_canceled_event_suppresses_fv_block():
    doc, _ = _build_document(
        {"_id": "wade", "title": "Wade", "date": "2026-07-31"},
        "Gate Sardinia",
        {"about": "", "prices_str": "", "canceled": True,
         "fv_lists": _fv_lists_str(_index(), "2026-07-31", "Wade")})
    assert "EVENTO ANNULLATO" in doc
    assert "Prevendita €5" not in doc  # niente promo su una serata annullata
