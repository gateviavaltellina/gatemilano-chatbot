"""Orari della singola serata letti da Sanity (fonte di verità, editabili in CMS).
Caso reale: opening party 8 luglio 18:30–20:30, orario diverso dalle serate club
22:00–04:00. Il bot deve poter leggere l'orario dell'evento dal contesto senza deploy."""
from sync.sanity_sync import _hhmm, _extract_hours, _build_document, _sardinia_default_hours


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


def test_only_end_or_only_start_no_line():
    # un solo estremo NON deve generare una riga: sovrascriverebbe il default della
    # venue (22:00–04:00) perdendo l'altro estremo. Meglio nessuna riga → vale il default.
    assert _extract_hours({"endTime": "04:00"}) == ""
    assert _extract_hours({"startTime": "22:00"}) == ""


def test_date_start_only_never_emits_line():
    # BUG (fixed): ogni evento ha un orario d'inizio nel `date` (Milano SEMPRE). Da solo
    # non deve mai produrre una riga "Orari:" a un estremo — corromperebbe tutti gli
    # eventi Milano (23:00–05:00 fisso) e le serate club Sardegna.
    assert _extract_hours({"date": "2026-09-04T23:00:00+02:00"}) == ""   # Milano
    assert _extract_hours({"date": "2026-07-08T20:00:00Z"}) == ""        # 22:00 Rome, start-only


def test_sardinia_sentinel_date_plus_closing_no_one_sided():
    # BUG (fixed): la data Sardegna standard "T22:00:00Z" = 00:00 Rome → _hhmm scarta
    # (mezzanotte). Con un closingTime isolato NON deve uscire "fino alle 04:00" (un
    # solo estremo che cancella l'apertura). Serve una fine ESPLICITA + un inizio.
    assert _extract_hours({"date": "2026-07-08T22:00:00Z", "closingTime": "04:00"}) == ""


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


def test_sardinia_document_falls_back_to_computed_hours():
    # senza orari espliciti da Sanity, un evento Sardegna ha comunque la finestra
    # standard fissa (22:00–03:00), mai una scheda senza orari.
    ev = {"_id": "x", "title": "Flaco G", "date": "2026-07-09"}
    doc, _ = _build_document(ev, "Gate Sardinia")
    assert "Orari: 22:00 - 03:00" in doc


def test_sardinia_default_hours_fixed():
    # orario fisso 22:00–03:00, tutte le sere (nessuna variazione per giorno)
    for d in ("2026-07-09", "2026-07-10", "2026-07-11", "2026-07-12", "2026-07-13"):
        assert _sardinia_default_hours(d) == "22:00 - 03:00"


def test_sardinia_event_gets_computed_hours_line():
    doc, _ = _build_document({"_id": "flaco", "title": "Flaco G", "date": "2026-07-09"}, "Gate Sardinia")
    assert "Orari: 22:00 - 03:00" in doc


def test_explicit_hours_override_beats_computed_default():
    # se lo staff mette orari espliciti in Sanity, vincono sul default calcolato
    doc, _ = _build_document(
        {"_id": "op", "title": "Opening", "date": "2026-07-09", "openingHours": "18:30 - 20:30"},
        "Gate Sardinia")
    assert "Orari: 18:30 - 20:30" in doc
    assert "02:30" not in doc


def test_milano_event_no_computed_hours():
    # Milano NON usa lo schema orari Sardegna: nessuna riga calcolata
    doc, _ = _build_document(
        {"_id": "lm", "title": "LILYA", "date": "2026-09-04T23:00:00+02:00", "venue": "Main Room"},
        "Gate Milano")
    assert "Orari:" not in doc


def test_compact_list_carries_hours_line():
    # BUG (fixed): una domanda sugli orari senza data esplicita ("fino a che ora siete
    # aperti?") non risolve query_dates → viene iniettata solo la lista compatta. Se il
    # compact scarta la riga "Orari:", il bot userebbe il default 22:00–04:00. La lista
    # compatta DEVE riportare l'orario specifico della serata.
    import datetime
    import rag.date_utils as du
    from rag import event_store as es

    fixed = datetime.datetime(2026, 7, 8, 19, 0, tzinfo=du._ROME)
    orig = du.business_now
    du.business_now = lambda now=None: fixed
    try:
        es._store.clear()
        ev = {"_id": "opening-2026-07-08", "title": "Opening Party",
              "date": "2026-07-08", "startTime": "18:30", "endTime": "20:30"}
        doc, meta = _build_document(ev, "Gate Sardinia")
        es.upsert_event("gate_sardinia", meta["sanity_id"], doc, meta)
        compact = es.get_upcoming_events_compact("gate_sardinia", days=14)
        assert "18:30 - 20:30" in compact
    finally:
        du.business_now = orig
        es._store.clear()


# --- Finestra oraria speciale 22-26 luglio 2026 (chiusura 03:30) ---

def test_special_week_hours_2230_0330():
    from sync.sanity_sync import _sardinia_default_hours
    # dentro la finestra: data secca e ISO con rollover (domenica sera = servizio 26)
    assert _sardinia_default_hours("2026-07-22") == "22:00 - 03:30"
    assert _sardinia_default_hours("2026-07-26T22:00:00Z") == "22:00 - 03:30"
    # fuori dalla finestra: torna da solo l'orario standard
    assert _sardinia_default_hours("2026-07-21") == "22:00 - 03:00"
    assert _sardinia_default_hours("2026-07-27") == "22:00 - 03:00"


def test_special_week_in_document_and_explicit_override_wins():
    doc, _ = _build_document({"_id": "sw", "title": "X", "date": "2026-07-24"}, "Gate Sardinia")
    assert "Orari: 22:00 - 03:30" in doc
    # un orario esplicito da Sanity vince comunque sulla finestra speciale
    doc2, _ = _build_document(
        {"_id": "sw2", "title": "Op", "date": "2026-07-24", "openingHours": "18:30 - 20:30"},
        "Gate Sardinia")
    assert "Orari: 18:30 - 20:30" in doc2
