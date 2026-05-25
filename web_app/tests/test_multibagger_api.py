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


def test_rebuild_surfaces_abort_reason_when_base_empty(monkeypatch):
    """P2-4: base scan 캐시 비었을 때 abort_reason 을 meta 에 노출."""
    import multibagger_blueprint as bp
    monkeypatch.setattr(flask_app, "_scan_results_cache", {})
    cache: dict = {}
    monkeypatch.setattr(flask_app, "_multibagger_results_cache", cache)
    bp._rebuild_multibagger_us()
    assert cache["data"]["meta"]["abort_reason"] == "base_cache_empty"
    assert cache["data"]["meta"]["warming"] is True


def test_rebuild_atomic_update_keeps_cache_populated(monkeypatch):
    """P2-3: clear() 제거로 갱신 도중 빈 dict 윈도우 없음."""
    import multibagger_blueprint as bp
    import multibagger as mb
    cache = {"_ts": 1.0, "data": {"pass": [{"ticker": "OLD"}], "watch": [], "meta": {}}}
    monkeypatch.setattr(flask_app, "_multibagger_results_cache", cache)
    monkeypatch.setattr(flask_app, "_scan_results_cache",
                        {("US", "BALANCED", ""): {"data": [{"Ticker": "X", "market_cap": 1e9}]}})

    def fake_build(*a, **kw):
        # 빌드 직전 cache 가 비어있지 않음을 확인 (clear 가 제거되었는지)
        assert "data" in cache and cache["data"]["pass"]
        return {"pass": [{"ticker": "NEW"}], "watch": [],
                "meta": {"pass_n": 1, "watch_n": 0, "universe_n": 1, "candidates_n": 1}}
    monkeypatch.setattr(mb, "build_results", fake_build)
    monkeypatch.setattr("multibagger_rates.get_dgs10", lambda: 4.0)
    bp._rebuild_multibagger_us()
    assert cache["data"]["pass"][0]["ticker"] == "NEW"


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
