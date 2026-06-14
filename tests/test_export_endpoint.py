from fastapi.testclient import TestClient
import main


def test_export_endpoint(monkeypatch):
    client = TestClient(main.app)
    # token non configurato → 404 (disabilitato)
    monkeypatch.setattr("config.settings.eval_export_token", "")
    assert client.get("/eval/correction-cases").status_code == 404
    # token configurato, chiave errata → 403
    monkeypatch.setattr("config.settings.eval_export_token", "secret")
    assert client.get("/eval/correction-cases", params={"key": "wrong"}).status_code == 403
    # chiave giusta → 200 con i casi approvati
    monkeypatch.setattr("rag.corrections.get_approved_cases", lambda: [{"id": "corr-x"}])
    r = client.get("/eval/correction-cases", params={"key": "secret"})
    assert r.status_code == 200
    assert r.json() == {"cases": [{"id": "corr-x"}]}
