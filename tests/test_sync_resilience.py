"""Resilienza del sync Sanity: un fetch fallito NON deve azzerare lo store.

Caso reale (mattina 2/7, ore 05:31): il bot rispondeva 'non ho la programmazione'
per Gate Sardinia pur essendoci eventi su Sanity. Tra le 22:00 e le 04:00 non ci
sono sync: se quello delle 04:00 fallisce (rete/API), il vecchio codice svuotava
lo store (delete_stale con lista vuota) e il bot restava cieco per ore.
"""
import pytest

from rag import event_store as es
import sync.sanity_sync as ss


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    es._store.clear()
    # niente rete per gli enrichment biglietteria
    async def _no_enrich(*a, **k):
        return {"about": "", "prices_str": ""}
    monkeypatch.setattr(ss, "_fetch_ticketsms_enrichment", _no_enrich)
    monkeypatch.setattr(ss, "_fetch_xceed_enrichment", _no_enrich)


def _seed(venue, eid, name):
    es.upsert_event(venue, eid, f"EVENTO: {name}", {
        "type": "event", "source": "sanity", "event_name": name, "date_ts": 1,
    })


async def test_fetch_failure_preserves_store(monkeypatch):
    _seed("gate_sardinia", "ev-perreo", "Perreo XL")
    _seed("gate_milano", "ev-mi", "Notorious")

    async def _boom(*a, **k):
        raise RuntimeError("rete giù")
    monkeypatch.setattr(ss, "_sanity_get", _boom)

    await ss.sync_all_venues()
    # gli eventi pre-esistenti NON vengono cancellati da un sync fallito
    assert es.count("gate_sardinia") == 1
    assert es.count("gate_milano") == 1


async def test_successful_sync_still_removes_stale(monkeypatch):
    _seed("gate_sardinia", "ev-vecchio", "Evento Passato")

    async def _fake_get(project_id, dataset, query, params=None, token="", perspective=""):
        if "siteSettings" in query:
            return {"result": None}
        if "blogPost" in query:
            return {"result": []}
        return {"result": [{"_id": "ev-nuovo", "title": "Nuovo", "date": "2026-07-04T20:00:00Z"}]}
    monkeypatch.setattr(ss, "_sanity_get", _fake_get)

    await ss.sync_all_venues()
    # il sync riuscito sostituisce gli eventi: lo stale sparisce, il nuovo c'è
    ids = [e["id"] for e in es._store["gate_sardinia"] if e["metadata"].get("type") == "event"]
    assert ids == ["ev-nuovo"]


async def test_draft_event_is_indexed_with_published_id(monkeypatch):
    # Con SANITY_API_TOKEN il sync legge anche le BOZZE (previewDrafts): un evento
    # creato in Studio ma mai pubblicato non deve "non esistere" per il bot.
    # L'id va normalizzato a quello pubblicato (link /tavoli e dedup post-publish).
    async def _fake_get(project_id, dataset, query, params=None, token="", perspective=""):
        if "siteSettings" in query:
            return {"result": None}
        if "blogPost" in query:
            return {"result": []}
        return {"result": [{
            "_id": "drafts.ev-perreo", "title": "Perreo XL", "date": "2026-07-04T22:00:00Z",
        }]}
    monkeypatch.setattr(ss, "_sanity_get", _fake_get)

    await ss.sync_all_venues()
    events = [e for e in es._store["gate_sardinia"] if e["metadata"].get("type") == "event"]
    assert len(events) == 1
    assert events[0]["id"] == "ev-perreo"  # niente prefisso drafts.
    assert events[0]["metadata"]["sanity_id"] == "ev-perreo"
    assert "in via di conferma" in events[0]["document"]


async def test_fetch_events_uses_draft_perspective_with_token(monkeypatch):
    captured = {}

    async def _spy(project_id, dataset, query, params=None, token="", perspective=""):
        captured.update({"token": token, "perspective": perspective})
        return {"result": []}
    monkeypatch.setattr(ss, "_sanity_get", _spy)

    monkeypatch.setattr("config.settings.sanity_api_token", "sk-token")
    await ss._fetch_events("p", "production")
    assert captured == {"token": "sk-token", "perspective": "previewDrafts"}

    monkeypatch.setattr("config.settings.sanity_api_token", "")
    await ss._fetch_events("p", "production")
    assert captured == {"token": "", "perspective": ""}


async def test_placeholder_title_event_is_indexed(monkeypatch):
    # Evento reale su Sanity ma con titolo placeholder "?????": prima veniva
    # FILTRATO dalla GROQ e il bot negava l'esistenza della serata. Ora entra
    # nello store con etichetta TBA e data corretta (giorno di servizio).
    async def _fake_get(project_id, dataset, query, params=None, token="", perspective=""):
        if "siteSettings" in query:
            return {"result": None}
        if "blogPost" in query:
            return {"result": []}
        return {"result": [{
            "_id": "ev-tba", "title": "?????", "date": "2026-07-04T22:00:00Z",
            "ticketUrl": "https://www.ticketsms.it/event/x",
        }]}
    monkeypatch.setattr(ss, "_sanity_get", _fake_get)

    await ss.sync_all_venues()
    events = [e for e in es._store["gate_sardinia"] if e["metadata"].get("type") == "event"]
    assert len(events) == 1
    doc = events[0]["document"]
    assert "?????" not in doc
    assert "line-up da annunciare" in doc
    assert "4 July 2026" in doc  # giorno di servizio, non il 5
    assert "ticketsms.it" in doc
