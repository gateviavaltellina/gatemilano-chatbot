"""Liste/promo Fourvenues (Boarding Pass €0, prevendita €5) nel documento evento.

Le liste si prendono sulla pagina Fourvenues della serata (non su TicketSMS): il
bot deve avere il link giusto e mostrare SOLO le liste rivolte al pubblico —
guest pass / guestlist / contest winner sono gestite dallo staff.
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
            "lists": [{"slug": "boarding-pass", "name": "BOARDING PASS"}],
        }],
    }


def test_lists_str_renders_public_lists_with_link():
    out = _fv_lists_str(_index(), "2026-07-31", "Wade")
    assert "BOARDING PASS" in out and "€0" in out
    assert "Prevendita €5" in out and "entro le 23:00" in out
    assert _FV_URL in out


def test_lists_str_date_only_fallback_when_name_differs():
    # titolo Sanity diverso dal nome Fourvenues ma unica serata quel giorno → match
    out = _fv_lists_str(_index(), "2026-07-31", "Wade DJ Set")
    assert "BOARDING PASS" in out


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
    assert "BOARDING PASS" in doc
    assert _FV_URL in doc


def test_canceled_event_suppresses_fv_block():
    doc, _ = _build_document(
        {"_id": "wade", "title": "Wade", "date": "2026-07-31"},
        "Gate Sardinia",
        {"about": "", "prices_str": "", "canceled": True,
         "fv_lists": _fv_lists_str(_index(), "2026-07-31", "Wade")})
    assert "EVENTO ANNULLATO" in doc
    assert "BOARDING PASS" not in doc  # niente promo su una serata annullata
