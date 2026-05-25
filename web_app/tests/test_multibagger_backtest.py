import os, sys
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import multibagger_backtest as mbk


def test_compute_multiple_basic():
    closes = pd.Series([10.0] + [100.0] * 250)
    assert mbk._compute_multiple(closes) == 10.0


def test_compute_multiple_too_few_rows():
    closes = pd.Series([100.0] * 50)
    assert mbk._compute_multiple(closes) is None


def test_extract_baggers_filters_by_threshold():
    by_symbol = {
        "TENX": pd.DataFrame({"Close": [10.0] + [100.0]*250}),
        "FLAT": pd.DataFrame({"Close": [50.0]*251}),
        "DELISTED": pd.DataFrame({"Close": [10.0]*100}),
    }
    result = mbk._extract_baggers(by_symbol, multiple=10.0)
    tickers = {b["ticker"] for b in result}
    assert "TENX" in tickers
    assert "FLAT" not in tickers
    assert "DELISTED" not in tickers


def test_extract_baggers_populates_snapshot_when_fn_given():
    by_symbol = {"TENX": pd.DataFrame({"Close": [10.0] + [100.0]*250})}
    calls = []

    def fake_snapshot(sym, as_of):
        calls.append((sym, as_of))
        return {"market_cap": 5e8, "ebitda": 1e7, "fcf": 5e6, "sector": "Technology"}

    result = mbk._extract_baggers(by_symbol, multiple=10.0,
                                  start="2021-01-01", snapshot_fn=fake_snapshot)
    assert calls == [("TENX", "2021-01-01")]
    assert result[0]["snapshot_at_start"]["sector"] == "Technology"


def test_extract_baggers_snapshot_failure_sets_none():
    by_symbol = {"TENX": pd.DataFrame({"Close": [10.0] + [100.0]*250})}

    def boom(sym, as_of):
        raise RuntimeError("yfinance down")

    result = mbk._extract_baggers(by_symbol, multiple=10.0,
                                  start="2021-01-01", snapshot_fn=boom)
    assert result[0]["snapshot_at_start"] is None


def test_extract_baggers_no_snapshot_fn_omits_key():
    by_symbol = {"TENX": pd.DataFrame({"Close": [10.0] + [100.0]*250})}
    result = mbk._extract_baggers(by_symbol, multiple=10.0)
    assert "snapshot_at_start" not in result[0]
