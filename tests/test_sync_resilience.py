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


async def test_null_ticketurl_does_not_kill_venue_sync(monkeypatch):
    # BUG REALE che azzerava Gate Sardinia: le schede TBA hanno "ticketUrl": null
    # e .get("ticketUrl", "") ritorna None (il default vale solo a chiave assente)
    # → '"dice.fm" in None' → TypeError al PRIMO evento → 0 eventi per la venue.
    # Riproduce il payload vero: eventi date-only con ticketUrl null.
    async def _fake_get(project_id, dataset, query, params=None, token="", perspective=""):
        if "siteSettings" in query:
            return {"result": None}
        if "blogPost" in query:
            return {"result": []}
        return {"result": [
            {"_id": "tba-2026-07-03-x", "title": "Ale De Tuglie", "date": "2026-07-03", "ticketUrl": None},
            {"_id": "tba-2026-07-04-y", "title": "Perreo XL", "date": "2026-07-04", "ticketUrl": None},
        ]}
    monkeypatch.setattr(ss, "_sanity_get", _fake_get)

    await ss.sync_all_venues()
    assert es.count("gate_sardinia") == 2
    status = ss.get_last_sync_status()["gate_sardinia"]
    assert status["ok"] is True
    assert status["indexed"] == 2
    assert status["skipped_bad"] == 0


async def test_one_bad_event_does_not_block_the_others(monkeypatch):
    # Una singola scheda malformata si salta (e si conta in skipped_bad),
    # le altre si indicizzano comunque.
    async def _fake_get(project_id, dataset, query, params=None, token="", perspective=""):
        if "siteSettings" in query:
            return {"result": None}
        if "blogPost" in query:
            return {"result": []}
        return {"result": [
            {"_id": "ev-bad", "title": "Rotto", "date": "2026-07-03"},
            {"_id": "ev-ok", "title": "Buono", "date": "2026-07-04"},
        ]}
    monkeypatch.setattr(ss, "_sanity_get", _fake_get)

    real_build = ss._build_document

    def _fragile(event, label, xceed=None):
        if event.get("_id") == "ev-bad":
            raise TypeError("scheda malformata")
        return real_build(event, label, xceed)
    monkeypatch.setattr(ss, "_build_document", _fragile)

    await ss.sync_all_venues()
    ids = [e["id"] for e in es._store["gate_sardinia"] if e["metadata"].get("type") == "event"]
    assert ids == ["ev-ok"]
    status = ss.get_last_sync_status()["gate_sardinia"]
    assert status["skipped_bad"] == 1
    assert status["indexed"] == 1


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
    # le serate top secret devono creare ATTESA (annuncio in arrivo),
    # non un "non ho informazioni"
    assert "data CONFERMATA" in doc
    assert "annuncio" in doc
