"""Auto-rinnovo token Instagram: store con persistenza, override su rotazione
manuale, e il job di refresh che aggiorna lo store / avvisa solo al primo fail."""
import json

import pytest

from instagram import token_store, token_refresh, client as ig_client


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    token_store._tokens.clear()
    token_store._origin.clear()
    monkeypatch.setattr("config.settings.persist_dir", str(tmp_path))
    monkeypatch.setattr("config.settings.ig_gatemilano_token", "env-milano")
    monkeypatch.setattr("config.settings.ig_gatesardinia_token", "env-sardinia")


# --- token_store ---

def test_load_falls_back_to_env_when_no_file():
    token_store.load()
    assert token_store.get("gate_milano") == "env-milano"
    assert token_store.get("gate_sardinia") == "env-sardinia"


def test_set_persists_and_reloads():
    token_store.load()
    token_store.set_token("gate_sardinia", "refreshed-1")
    assert token_store.get("gate_sardinia") == "refreshed-1"
    # simula un riavvio: nuovo processo, stessa env → deve ricaricare il rinnovato
    token_store._tokens.clear(); token_store._origin.clear()
    token_store.load()
    assert token_store.get("gate_sardinia") == "refreshed-1"
    assert token_store.get("gate_milano") == "env-milano"


def test_manual_env_rotation_overrides_persisted(monkeypatch):
    token_store.load()
    token_store.set_token("gate_sardinia", "refreshed-old")
    # lo staff mette a mano un NUOVO token nell'env (rotazione) e riavvia
    monkeypatch.setattr("config.settings.ig_gatesardinia_token", "env-nuovo-manuale")
    token_store._tokens.clear(); token_store._origin.clear()
    token_store.load()
    # vince il nuovo env, non il vecchio persistito
    assert token_store.get("gate_sardinia") == "env-nuovo-manuale"


def test_empty_env_keeps_persisted_token(monkeypatch):
    # env temporaneamente vuota (glitch secret) NON deve buttare via il token buono
    token_store.load()
    token_store.set_token("gate_sardinia", "refreshed-buono")
    monkeypatch.setattr("config.settings.ig_gatesardinia_token", "")
    token_store._tokens.clear(); token_store._origin.clear()
    token_store.load()
    assert token_store.get("gate_sardinia") == "refreshed-buono"


def test_non_dict_json_does_not_crash_load(tmp_path, monkeypatch):
    # un file JSON valido ma non-oggetto (es. un numero) non deve far crashare load()
    (tmp_path / "ig_tokens.json").write_text("123", encoding="utf-8")
    token_store._tokens.clear(); token_store._origin.clear()
    token_store.load()  # non solleva
    assert token_store.get("gate_milano") == "env-milano"


def test_get_without_load_uses_env():
    # se lo store non è stato caricato, get() ricade sull'env (nessun crash)
    assert token_store.get("gate_milano") == "env-milano"


def test_client_reads_token_from_store():
    token_store.load()
    token_store.set_token("gate_sardinia", "refreshed-xyz")
    # _SARDINIA_IDS include l'id business 17841452139166980
    assert ig_client._token_for_account("17841452139166980") == "refreshed-xyz"


# --- token_refresh ---

async def test_refresh_all_updates_store(monkeypatch):
    token_store.load()

    async def _fake_refresh(token):
        return f"NEW::{token}"
    monkeypatch.setattr(token_refresh, "_refresh_one", _fake_refresh)

    res = await token_refresh.refresh_all()
    assert res == {"gate_milano": True, "gate_sardinia": True}
    assert token_store.get("gate_milano") == "NEW::env-milano"
    assert token_store.get("gate_sardinia") == "NEW::env-sardinia"


async def test_refresh_failure_does_not_overwrite_store(monkeypatch):
    token_store.load()

    async def _fail(token):
        return None
    monkeypatch.setattr(token_refresh, "_refresh_one", _fail)

    res = await token_refresh.refresh_all()
    assert res == {"gate_milano": False, "gate_sardinia": False}
    # lo store NON viene sovrascritto con None su fallimento: resta il token valido
    assert token_store.get("gate_milano") == "env-milano"
    assert token_store.get("gate_sardinia") == "env-sardinia"


async def test_refresh_recovers_after_failure(monkeypatch):
    token_store.load()
    seq = {"n": 0}

    async def _flaky(token):
        seq["n"] += 1
        return None if seq["n"] <= 2 else f"NEW::{token}"
    monkeypatch.setattr(token_refresh, "_refresh_one", _flaky)

    await token_refresh.refresh_all()   # entrambe falliscono
    await token_refresh.refresh_all()   # entrambe ok → store aggiornato
    assert token_store.get("gate_milano").startswith("NEW::")
