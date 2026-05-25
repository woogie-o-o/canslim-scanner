import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import multibagger_rates as mr


def test_parse_csv_last_valid(monkeypatch):
    csv = "DATE,DGS10\n2026-05-22,4.32\n2026-05-23,.\n2026-05-24,4.35\n"
    assert mr._parse_last_valid(csv) == 4.35


def test_parse_csv_handles_all_missing():
    assert mr._parse_last_valid("DATE,DGS10\n2026-05-23,.\n") is None


def test_cache_hit(tmp_path, monkeypatch):
    cache_file = tmp_path / "rates_us.json"
    cache_file.write_text(json.dumps({"_ts": time.time(), "dgs10_pct": 4.2}), encoding="utf-8")
    monkeypatch.setattr(mr, "CACHE_PATH", str(cache_file))
    assert mr.get_dgs10() == 4.2


def test_cache_expired_triggers_fetch(tmp_path, monkeypatch):
    cache_file = tmp_path / "rates_us.json"
    cache_file.write_text(json.dumps({"_ts": time.time() - 48*3600, "dgs10_pct": 3.0}), encoding="utf-8")
    monkeypatch.setattr(mr, "CACHE_PATH", str(cache_file))
    called = {"n": 0}
    def fake_fetch():
        called["n"] += 1
        return 4.5
    monkeypatch.setattr(mr, "_fetch_remote", fake_fetch)
    assert mr.get_dgs10() == 4.5
    assert called["n"] == 1


def test_fetch_remote_retries_on_failure(monkeypatch):
    """P1-9: 일시적 실패에 backoff 재시도. 2회 실패 후 3회차 성공."""
    monkeypatch.setattr(mr.time, "sleep", lambda _s: None)  # backoff 단축
    attempts = {"n": 0}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"DATE,DGS10\n2026-05-24,4.21\n"

    def fake_urlopen(_url, timeout=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError("transient")
        return FakeResp()

    monkeypatch.setattr(mr.urllib.request, "urlopen", fake_urlopen)
    assert mr._fetch_remote() == 4.21
    assert attempts["n"] == 3


def test_fetch_remote_returns_none_after_all_retries(monkeypatch):
    monkeypatch.setattr(mr.time, "sleep", lambda _s: None)
    attempts = {"n": 0}

    def fake_urlopen(_url, timeout=None):
        attempts["n"] += 1
        raise OSError("permanent")

    monkeypatch.setattr(mr.urllib.request, "urlopen", fake_urlopen)
    assert mr._fetch_remote() is None
    assert attempts["n"] == 3
