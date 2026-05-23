import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import history


def test_grade_from_score_cuts():
    assert history._grade_from_score(75) == "S"
    assert history._grade_from_score(74) == "A"
    assert history._grade_from_score(60) == "A"
    assert history._grade_from_score(59) == "B"
    assert history._grade_from_score(45) == "B"
    assert history._grade_from_score(44) == "C"


def test_grade_from_score_invalid():
    assert history._grade_from_score(None) is None
    assert history._grade_from_score("abc") is None
    assert history._grade_from_score("") is None


import json
import importlib
from datetime import date


def _reload_history_with_dir(tmp_path, monkeypatch):
    import history as h
    importlib.reload(h)
    monkeypatch.setattr(h, "_SNAP_DIR", str(tmp_path))
    return h


def test_save_snapshot_includes_entry(tmp_path, monkeypatch):
    h = _reload_history_with_dir(tmp_path, monkeypatch)
    results = [
        {"Ticker": "AAA", "TotalScore": 80, "EntryStatus": "STRONG"},
        {"Ticker": "BBB", "TotalScore": 50},
    ]
    h.save_snapshot(results, "US")
    p = os.path.join(str(tmp_path), f"scanner_US_{date.today().isoformat()}.json")
    with open(p, encoding="utf-8") as f:
        snap = json.load(f)
    assert snap["AAA"]["entry"] == "STRONG"
    assert snap["BBB"]["entry"] is None
    assert snap["AAA"]["score"] == 80.0


from datetime import timedelta


def _write_snap(tmp_path, market, day, payload):
    p = os.path.join(str(tmp_path), f"scanner_{market}_{day.isoformat()}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_load_timeline_normal(tmp_path, monkeypatch):
    h = _reload_history_with_dir(tmp_path, monkeypatch)
    today = date.today()
    _write_snap(tmp_path, "US", today, {"AAA": {"score": 80, "rank": 1, "entry": "STRONG"}})
    _write_snap(tmp_path, "US", today - timedelta(days=1),
                {"AAA": {"score": 50, "rank": 2, "entry": "AVOID"}})
    tl = h.load_timeline("AAA", "US")
    assert len(tl) == 14
    assert tl[-1] == {"date": today.isoformat(), "grade": "S", "entry": "STRONG"}
    assert tl[-2] == {"date": (today - timedelta(days=1)).isoformat(),
                      "grade": "B", "entry": "AVOID"}
    assert tl[0]["date"] < tl[-1]["date"]


def test_load_timeline_empty_day(tmp_path, monkeypatch):
    h = _reload_history_with_dir(tmp_path, monkeypatch)
    today = date.today()
    _write_snap(tmp_path, "US", today, {"AAA": {"score": 80, "rank": 1, "entry": "STRONG"}})
    tl = h.load_timeline("AAA", "US")
    assert tl[-2] == {"date": (today - timedelta(days=1)).isoformat(),
                      "grade": None, "entry": None}


def test_load_timeline_legacy_snapshot(tmp_path, monkeypatch):
    h = _reload_history_with_dir(tmp_path, monkeypatch)
    today = date.today()
    _write_snap(tmp_path, "US", today, {"AAA": {"score": 80, "rank": 1}})
    tl = h.load_timeline("AAA", "US")
    assert tl[-1] == {"date": today.isoformat(), "grade": "S", "entry": None}


def test_load_timeline_ticker_missing(tmp_path, monkeypatch):
    h = _reload_history_with_dir(tmp_path, monkeypatch)
    today = date.today()
    _write_snap(tmp_path, "US", today, {"BBB": {"score": 80, "rank": 1, "entry": "STRONG"}})
    tl = h.load_timeline("AAA", "US")
    assert all(item["grade"] is None and item["entry"] is None for item in tl)


def test_endpoint_ok(monkeypatch):
    import app as flask_app
    monkeypatch.setattr("history.load_timeline",
                        lambda t, m: [{"date": "2026-05-22", "grade": "S", "entry": "STRONG"}])
    client = flask_app.app.test_client()
    resp = client.get("/api/signal-history/AAPL?market=US")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ticker"] == "AAPL"
    assert data["market"] == "US"
    assert isinstance(data["timeline"], list)


def test_endpoint_missing_market(monkeypatch):
    import app as flask_app
    client = flask_app.app.test_client()
    assert client.get("/api/signal-history/AAPL").status_code == 400


def test_endpoint_bad_market(monkeypatch):
    import app as flask_app
    client = flask_app.app.test_client()
    assert client.get("/api/signal-history/AAPL?market=XX").status_code == 400
