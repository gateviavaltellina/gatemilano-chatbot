"""Audit regressions: a changed date/event/venue must select the right checkout."""
from datetime import datetime, timezone

import pytest

from rag import context_builder as cb, date_utils as du, event_store as es


def seed(name, day, venue="gate_milano", eid=None):
    eid = eid or f"{venue}-{name}-{day}"
    es.upsert_event(venue, eid, f"EVENTO: {name}\nData: {day}", {
        "type": "event", "event_name": name, "date": day,
        "date_ts": int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()),
        "sanity_id": eid, "ticket_url": "",
    })


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    es._store.clear()
    monkeypatch.setattr(du, "business_now", lambda now=None: datetime(2026, 11, 19, 17, tzinfo=du._ROME))

    async def milano(name, day):
        return f"TAVOLI MILANO: {name} {day}\nPrenota: https://milano.example/{day}"

    async def sardinia(eid):
        return f"TAVOLI SARDINIA: {eid}\nPRENOTA E PAGA ONLINE: https://sardinia.example/{eid}"

    monkeypatch.setattr(cb, "get_vip_tables_via_site", milano)
    monkeypatch.setattr(cb, "get_vip_tables_sardinia", sardinia)
    yield
    es._store.clear()


def history(*messages):
    return [{"role": "user", "content": m} for m in messages]


async def test_followup_after_date_change_uses_last_selection():
    seed("Perreo XL", "2026-11-21")
    seed("Perreo XL", "2026-11-28")
    ctx, dates = await cb.build_rag_context("gate_milano", "siamo in 5", history(
        "tavoli per Perreo XL del 21 novembre", "anzi facciamo il 28 novembre"))
    assert dates[0] == "2026-11-28"
    assert ctx.startswith("TAVOLI MILANO: Perreo XL 2026-11-28")


async def test_followup_after_artist_change_does_not_restore_old_date():
    seed("Perreo XL", "2026-11-21")
    seed("Kobosil", "2026-11-27")
    ctx, dates = await cb.build_rag_context("gate_milano", "siamo in 5", history(
        "tavoli Perreo XL del 21 novembre", "invece preferisco Kobosil"))
    assert dates[0] == "2026-11-27"
    assert ctx.startswith("TAVOLI MILANO: Kobosil 2026-11-27")


async def test_relative_selection_uses_date_confirmed_in_reply():
    seed("Perreo XL", "2026-11-21")
    seed("Kobosil", "2026-11-20")
    chat = history("tavoli Perreo XL del 21 novembre", "e domani invece?")
    chat.append({"role": "assistant", "content": "Kobosil del 20 novembre: ecco la mappa."})
    ctx, dates = await cb.build_rag_context("gate_milano", "siamo in 5", chat)
    assert dates[0] == "2026-11-20"
    assert ctx.startswith("TAVOLI MILANO: Kobosil 2026-11-20")


@pytest.mark.parametrize("message,expected", [("e domani?", "2026-11-20"), ("e per il 28?", "2026-11-28")])
async def test_new_relative_or_bare_date_beats_history(message, expected):
    seed("Perreo XL", "2026-11-21")
    seed("Kobosil", expected)
    _, dates = await cb.build_rag_context("gate_milano", message, history("tavoli Perreo XL del 21 novembre"))
    assert dates[0] == expected


async def test_same_night_change_uses_last_named_event_not_longest_old_name():
    seed("Soulstice Ryan Elliott Saoirse", "2026-11-21")
    seed("Raw", "2026-11-21")
    ctx, _ = await cb.build_rag_context("gate_milano", "siamo in 5", history(
        "tavoli per Soulstice Ryan Elliott Saoirse del 21 novembre", "preferisco Raw"))
    assert ctx.startswith("TAVOLI MILANO: Raw")


async def test_explicit_other_venue_wins_even_when_same_series_exists_locally():
    seed("Perreo XL", "2026-11-21")
    seed("Perreo XL", "2026-11-28", "gate_sardinia", "sa-perreo")
    ctx, dates = await cb.build_rag_context("gate_milano", "tavoli Perreo XL a Gate Sardinia")
    assert dates[0] == "2026-11-28"
    assert "TAVOLI SARDINIA: sa-perreo" in ctx
    assert "TAVOLI MILANO:" not in ctx


async def test_other_venue_tables_not_hidden_by_local_event_same_date():
    seed("Perreo XL", "2026-11-21")
    seed("Guè", "2026-11-21", "gate_sardinia", "sa-gue")
    ctx, _ = await cb.build_rag_context("gate_milano", "tavoli per Guè a Gate Sardinia del 21 novembre")
    assert "TAVOLI SARDINIA: sa-gue" in ctx
    assert "TAVOLI MILANO:" not in ctx


async def test_artist_only_in_other_venue_selects_its_tables():
    seed("Perreo XL", "2026-11-21")
    seed("Guè", "2026-11-21", "gate_sardinia", "sa-gue")
    ctx, _ = await cb.build_rag_context("gate_milano", "tavoli per Guè")
    assert "TAVOLI SARDINIA: sa-gue" in ctx
    assert "TAVOLI MILANO:" not in ctx


@pytest.mark.parametrize("message,expected", [
    ("tavoli per Halloween del 31/10/2027", "2027-10-31"),
    ("tavoli per natale il 24 dicembre 2026", "2026-12-24"),
])
def test_exact_date_beats_holiday_in_event_description(message, expected):
    assert du.extract_query_dates(message, explicit_only=True)[0] == expected
