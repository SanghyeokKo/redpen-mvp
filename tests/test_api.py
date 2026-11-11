import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


def test_api(tmp_path, monkeypatch):
    monkeypatch.setenv("REDPEN_LEDGER", str(tmp_path / "ledger.jsonl"))
    import importlib

    import api
    importlib.reload(api)
    client = TestClient(api.app)
    evs = client.get("/events").json()
    assert {e["company"] for e in evs} == {"NVDA", "AMD", "MU"}
    r = client.post(f"/runs/{next(e['id'] for e in evs if e['company'] == 'MU')}").json()
    assert r["verdicts"]["S2"]["verdict"] == "수정" and r["verification"]["passed"]
    assert client.get("/ledger").json()["chain_ok"]
    assert client.post("/runs/unknown").status_code == 404
