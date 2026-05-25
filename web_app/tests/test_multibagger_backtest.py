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
