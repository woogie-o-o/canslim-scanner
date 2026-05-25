import os, sys, json, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app as flask_app


def test_multibagger_page_renders():
    client = flask_app.app.test_client()
    resp = client.get("/multibagger")
    assert resp.status_code == 200
    assert b"multibagger" in resp.data.lower() or b"\xeb\xa9\x80\xed\x8b\xb0" in resp.data  # "멀티" UTF-8


def test_api_multibagger_returns_warming_when_no_cache(monkeypatch):
    monkeypatch.setattr(flask_app, "_multibagger_results_cache", {})
    client = flask_app.app.test_client()
    resp = client.get("/api/multibagger")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["meta"].get("warming") is True or resp.headers.get("X-Warming-In-Progress") == "true"


def test_api_thresholds():
    client = flask_app.app.test_client()
    resp = client.get("/api/multibagger/thresholds")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "F1_MCAP_MIN" in body


def test_api_ticker_returns_404_when_unknown(monkeypatch):
    monkeypatch.setattr(flask_app, "_multibagger_results_cache", {"data": {"pass": [], "watch": []}, "_ts": time.time()})
    client = flask_app.app.test_client()
    resp = client.get("/api/multibagger/ticker/UNKNOWNSYM")
    assert resp.status_code == 404


def test_api_diff_missing_pkl_returns_empty(monkeypatch, tmp_path):
    fake_path = str(tmp_path / "no_such.json")
    monkeypatch.setattr(flask_app, "_MULTIBAGGER_BAGGERS_PATH", fake_path)
    client = flask_app.app.test_client()
    resp = client.get("/api/multibagger/diff")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["baggers"] == []
    assert body["stats"]["available"] is False


def test_api_diff_loads_and_classifies(monkeypatch, tmp_path):
    fake_path = str(tmp_path / "baggers_us.json")
    with open(fake_path, "w", encoding="utf-8") as fh:
        json.dump({
            "_ts": time.time(),
            "baggers": [
                {"ticker": "TENX", "start_close": 10, "end_close": 100, "multiple": 10.0,
                 "snapshot_at_start": {
                     "market_cap": 1e9, "ebitda": 1e8, "fcf": 5e7,
                     "roic": 0.15, "fcf_yield": 0.08, "pb": 2.0,
                     "revenue_yoy": 0.10, "ebitda_yoy": 0.15, "assets_yoy": 0.08,
                     "icr": 5.0, "debt_ebitda": 2.0,
                     "from_52w_high": -0.20, "return_1m": 0.10,
                 }},
            ],
        }, fh)
    monkeypatch.setattr(flask_app, "_MULTIBAGGER_BAGGERS_PATH", fake_path)
    client = flask_app.app.test_client()
    resp = client.get("/api/multibagger/diff")
    body = resp.get_json()
    assert body["stats"]["pass_n"] + body["stats"]["watch_n"] + body["stats"]["miss_n"] == 1
