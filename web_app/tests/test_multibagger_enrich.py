import os, sys
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import multibagger_enrich as me
import multibagger as mb


class FakeTicker:
    def __init__(self, info=None, hist=None, income=None, balance=None, cash=None):
        self.info = info or {}
        self.fast_info = {}
        self._hist = hist
        self._income = income
        self._balance = balance
        self._cash = cash

    def history(self, period=None, **_kw):
        return self._hist if self._hist is not None else pd.DataFrame()

    @property
    def income_stmt(self):
        return self._income if self._income is not None else pd.DataFrame()

    @property
    def balance_sheet(self):
        return self._balance if self._balance is not None else pd.DataFrame()

    @property
    def cashflow(self):
        return self._cash if self._cash is not None else pd.DataFrame()

    def get_insider_transactions(self):
        return pd.DataFrame()


def test_extract_yoy_from_income():
    df = pd.DataFrame(
        {"2024-12-31": [200, 100], "2023-12-31": [150, 80]},
        index=["TotalRevenue", "EBITDA"],
    )
    rev_yoy = me._yoy(df, "TotalRevenue")
    ebitda_yoy = me._yoy(df, "EBITDA")
    assert abs(rev_yoy - (200/150 - 1)) < 1e-6
    assert abs(ebitda_yoy - (100/80 - 1)) < 1e-6


def test_extract_yoy_handles_missing_row():
    df = pd.DataFrame({"2024-12-31": [200], "2023-12-31": [150]}, index=["TotalRevenue"])
    assert me._yoy(df, "EBITDA") is None


def test_extract_52w_high_distance():
    hist = pd.DataFrame({"Close": [100, 110, 120, 90, 100]})
    distance, ret_1m = me._price_signals(hist)
    assert abs(distance - (100/120 - 1)) < 1e-6


def test_enrich_one_returns_fundamentals(monkeypatch):
    info = {
        "marketCap": 1e9, "freeCashflow": 5e7, "ebitda": 1e8,
        "priceToBook": 2.0, "sector": "Healthcare",
    }
    income = pd.DataFrame(
        {"2024-12-31": [1000, 200, 50], "2023-12-31": [800, 150, 40]},
        index=["TotalRevenue", "EBITDA", "InterestExpense"],
    )
    balance = pd.DataFrame(
        {"2024-12-31": [2000, 300], "2023-12-31": [1800, 280]},
        index=["TotalAssets", "TotalDebt"],
    )
    cash = pd.DataFrame(
        {"2024-12-31": [50, -100, -20]}, index=["FreeCashFlow", "CapitalExpenditure", "RepurchaseOfCapitalStock"]
    )
    hist = pd.DataFrame({"Close": list(range(80, 130))})
    fake = FakeTicker(info=info, hist=hist, income=income, balance=balance, cash=cash)

    monkeypatch.setattr(me, "_get_ticker", lambda sym: fake)
    f = me.enrich_one("FOO", dgs10_pct=4.5)
    assert isinstance(f, mb.Fundamentals)
    assert f.market_cap == 1e9
    assert f.ebitda == 1e8
    assert f.dgs10_pct == 4.5
    assert f.revenue_yoy is not None and f.revenue_yoy > 0


def test_enrich_one_exception_returns_none(monkeypatch):
    def boom(_):
        raise RuntimeError("yf down")
    monkeypatch.setattr(me, "_get_ticker", boom)
    assert me.enrich_one("FOO", dgs10_pct=None) is None
