"""Risoluzione evento per nome/artista oltre la finestra 'prossimi giorni'."""
from rag import event_store
from rag.event_store import find_event_dates_by_name, _today_start_utc


def _seed(venue, name, days_ahead):
    ts = _today_start_utc() + days_ahead * 86400
    event_store.upsert_event(
        venue,
        f"ev-{name}-{days_ahead}",
        document=f"EVENTO: {name}\nData: x",
        metadata={"type": "event", "event_name": name, "date_ts": ts},
    )


def setup_function(_):
    event_store._store.clear()


def test_finds_artist_beyond_window():
    _seed("gate_sardinia", "Rondodasosa", 42)
    _seed("gate_sardinia", "Perreo XL", 13)
    ts = _today_start_utc() + 42 * 86400
    import datetime as _dt
    expected = _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).strftime("%Y-%m-%d")
    assert find_event_dates_by_name("gate_sardinia", "ci sarà Rondodasosa?") == [expected]


def test_no_match_returns_empty():
    _seed("gate_sardinia", "Rondodasosa", 42)
    # nessun token significativo combacia col titolo
    assert find_event_dates_by_name("gate_sardinia", "ciao vorrei prenotare un tavolo") == []


def test_generic_tokens_do_not_false_match():
    _seed("gate_sardinia", "Summer Festival Gate Sardinia", 30)
    # "festival"/"gate"/"sardinia" sono stopword → nessun match spurio
    assert find_event_dates_by_name("gate_sardinia", "che festival avete a gate sardinia?") == []


def test_respects_limit_and_order():
    _seed("gate_sardinia", "Villabanks", 50)
    _seed("gate_sardinia", "Villabanks", 20)
    out = find_event_dates_by_name("gate_sardinia", "quando suona Villabanks?", limit=2)
    assert len(out) == 2
    assert out[0] < out[1]  # ordine cronologico


def test_ignores_events_past_horizon():
    _seed("gate_sardinia", "Faraway", 200)  # oltre `days`
    assert find_event_dates_by_name("gate_sardinia", "biglietti per Faraway", days=80) == []
