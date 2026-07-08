"""Orari della singola serata letti da Sanity (fonte di verità, editabili in CMS).
Caso reale: opening party 8 luglio 18:30–20:30, orario diverso dalle serate club
22:00–04:00. Il bot deve poter leggere l'orario dell'evento dal contesto senza deploy."""
from sync.sanity_sync import _hhmm, _extract_hours, _build_document


def test_hhmm_from_string_variants():
    assert _hhmm("18:30") == "18:30"
    assert _hhmm("9:05") == "09:05"
    assert _hhmm("20:30:00") == "20:30"
    assert _hhmm("8.15") == "08:15"


def test_hhmm_from_iso_datetime_rome():
    # 18:30 UTC → 20:30 Rome (estate, +02:00)
    assert _hhmm("2026-07-08T18:30:00Z") == "20:30"
    # con offset esplicito
    assert _hhmm("2026-07-08T18:30:00+02:00") == "18:30"


def test_hhmm_rejects_midnight_and_garbage():
    # mezzanotte = data senza orario reale → vuoto (non "00:00")
    assert _hhmm("2026-07-08T00:00:00+02:00") == ""
    assert _hhmm("") == ""
    assert _hhmm(None) == ""
    assert _hhmm("banana") == ""
    assert _hhmm("99:99") == ""


def test_freeform_hours_used_verbatim():
    assert _extract_hours({"openingHours": "18:30 - 20:30"}) == "Orari: 18:30 - 20:30"
    assert _extract_hours({"hours": "aperitivo 19-21"}) == "Orari: aperitivo 19-21"


def test_start_end_from_explicit_fields():
    assert _extract_hours({"startTime": "18:30", "endTime": "20:30"}) == "Orari: 18:30 - 20:30"
    assert _extract_hours({"openingTime": "20:00", "closingTime": "02:00"}) == "Orari: 20:00 - 02:00"


def test_start_from_date_end_from_enddate():
    ev = {"date": "2026-07-08T18:30:00+02:00", "endDate": "2026-07-08T20:30:00+02:00"}
    assert _extract_hours(ev) == "Orari: 18:30 - 20:30"


def test_only_end_or_only_start():
    assert _extract_hours({"endTime": "04:00"}) == "Orari: fino alle 04:00"
    assert _extract_hours({"startTime": "22:00"}) == "Orari: apertura 22:00"


def test_no_hours_returns_empty():
    # data senza orario (Sardegna storica) e nessun campo orario → nessuna riga Orari
    assert _extract_hours({"date": "2026-07-09"}) == ""
    assert _extract_hours({}) == ""


def test_document_includes_hours_line():
    ev = {
        "_id": "opening-2026-07-08", "title": "Opening Party",
        "date": "2026-07-08", "startTime": "18:30", "endTime": "20:30",
    }
    doc, meta = _build_document(ev, "Gate Sardinia")
    assert "Orari: 18:30 - 20:30" in doc
    assert meta["event_name"] == "Opening Party"


def test_document_without_hours_has_no_orari_line():
    ev = {"_id": "x", "title": "Flaco G", "date": "2026-07-09"}
    doc, _ = _build_document(ev, "Gate Sardinia")
    assert "Orari:" not in doc
