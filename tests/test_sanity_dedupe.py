"""Dedup dei doppioni Sanity — stessa serata, titoli equivalenti.

Casi reali (scansione 29/8): 18/9 'BISCOTTO: BLANKA & QUELZA' + 'BISCOTTO:
QUELZA & BLANKA'; 23/9 'LATE NIGHT MUSIC VIBES' + 'LATE NIGHT VIBES' → il bot
elencava due serate uguali nel calendario.
"""
from sync.sanity_sync import _dedupe_sanity_events


def _ev(eid, title, date, ticket=""):
    return {"_id": eid, "title": title, "date": date, "ticketUrl": ticket}


def test_same_tokens_different_order_collapse():
    evs = [_ev("a", "BISCOTTO: BLANKA & QUELZA", "2026-09-18"),
           _ev("b", "BISCOTTO: QUELZA & BLANKA", "2026-09-18", "https://xceed.me/x")]
    out = _dedupe_sanity_events(evs)
    assert len(out) == 1
    assert out[0]["_id"] == "b"          # tiene quella col ticketUrl


def test_subset_titles_collapse():
    evs = [_ev("a", "LATE NIGHT MUSIC VIBES", "2026-09-23", "https://xceed.me/x"),
           _ev("b", "LATE NIGHT VIBES", "2026-09-23")]
    out = _dedupe_sanity_events(evs)
    assert len(out) == 1
    assert out[0]["_id"] == "a"


def test_distinct_events_same_night_kept():
    # 4/9 reale: tre serate diverse la stessa notte — NON vanno toccate
    evs = [_ev("a", "ARKÔRE", "2026-09-04"),
           _ev("b", "LILYA MANDRE", "2026-09-04"),
           _ev("c", "THE CLOSING", "2026-09-04")]
    assert len(_dedupe_sanity_events(evs)) == 3


def test_same_title_different_dates_kept():
    evs = [_ev("a", "PERREO XL", "2026-09-12"),
           _ev("b", "PERREO XL", "2026-09-19")]
    assert len(_dedupe_sanity_events(evs)) == 2


def test_draft_loses_to_published():
    evs = [_ev("drafts.x", "KOBOSIL", "2026-09-25", "https://xceed.me/x"),
           _ev("x", "KOBOSIL", "2026-09-25", "https://xceed.me/x")]
    out = _dedupe_sanity_events(evs)
    assert len(out) == 1 and out[0]["_id"] == "x"


def test_tba_placeholders_never_deduped():
    evs = [_ev("a", "?????", "2026-11-20"), _ev("b", "?????", "2026-11-20")]
    assert len(_dedupe_sanity_events(evs)) == 2


# --- Dedup CROSS-FONTE (Sanity vs Xceed): has_matching_event per set di parole ---

def test_cross_source_dedupe_token_sets():
    # I doppioni reali NON venivano fermati dal confronto per prefisso: qui il
    # match deve scattare per set di parole (ordine diverso / parola in mezzo).
    import datetime
    from rag import event_store as es
    es._store.clear()
    ts = int(datetime.datetime(2026, 9, 18, tzinfo=datetime.timezone.utc).timestamp())
    es.upsert_event("gate_milano", "s1", "EVENTO: BISCOTTO: BLANKA & QUELZA", {
        "type": "event", "source": "sanity", "event_name": "BISCOTTO: BLANKA & QUELZA",
        "date_ts": ts, "venue": "gate_milano"})
    # ordine artisti invertito (payload Xceed) → stesso evento
    assert es.has_matching_event("gate_milano", ts, "BISCOTTO: QUELZA & BLANKA",
                                 exclude_source="xceed")
    # parola in mezzo ("MUSIC") → stesso evento
    ts2 = int(datetime.datetime(2026, 9, 23, tzinfo=datetime.timezone.utc).timestamp())
    es.upsert_event("gate_milano", "s2", "EVENTO: LATE NIGHT VIBES", {
        "type": "event", "source": "sanity", "event_name": "LATE NIGHT VIBES",
        "date_ts": ts2, "venue": "gate_milano"})
    assert es.has_matching_event("gate_milano", ts2, "LATE NIGHT MUSIC VIBES",
                                 exclude_source="xceed")
    # evento DIVERSO stessa sera → nessun falso match
    assert not es.has_matching_event("gate_milano", ts, "PERREO XL", exclude_source="xceed")
    es._store.clear()
