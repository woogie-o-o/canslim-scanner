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


def test_pick_period_cols_selects_latest_before_as_of():
    df = pd.DataFrame(
        {pd.Timestamp("2023-12-31"): [1], pd.Timestamp("2022-12-31"): [2],
         pd.Timestamp("2021-12-31"): [3]},
        index=["TotalRevenue"],
    )
    latest, prev = me._pick_period_cols(df, pd.Timestamp("2023-01-01"))
    assert latest == pd.Timestamp("2022-12-31")
    assert prev == pd.Timestamp("2021-12-31")


def test_pick_period_cols_no_data_before_as_of():
    df = pd.DataFrame({pd.Timestamp("2024-12-31"): [1]}, index=["TotalRevenue"])
    latest, prev = me._pick_period_cols(df, pd.Timestamp("2020-01-01"))
    assert latest is None and prev is None


def test_snapshot_fundamentals_at_picks_historical_report(monkeypatch):
    income = pd.DataFrame(
        {pd.Timestamp("2020-12-31"): [1000, 200, 50],
         pd.Timestamp("2019-12-31"): [800, 150, 40]},
        index=["TotalRevenue", "EBITDA", "InterestExpense"],
    )
    balance = pd.DataFrame(
        {pd.Timestamp("2020-12-31"): [2000, 300, 800],
         pd.Timestamp("2019-12-31"): [1800, 280, 700]},
        index=["TotalAssets", "TotalDebt", "StockholdersEquity"],
    )
    cash = pd.DataFrame(
        {pd.Timestamp("2020-12-31"): [50, -100],
         pd.Timestamp("2019-12-31"): [40, -90]},
        index=["FreeCashFlow", "CapitalExpenditure"],
    )
    dates = pd.date_range("2019-09-01", "2021-02-01", freq="B")
    hist = pd.DataFrame({"Close": [50.0] * len(dates)}, index=dates)
    info = {"sector": "Technology", "sharesOutstanding": 1e7, "priceToBook": 2.5}
    fake = FakeTicker(info=info, hist=hist, income=income, balance=balance, cash=cash)
    monkeypatch.setattr(me, "_get_ticker", lambda sym: fake)

    snap = me.snapshot_fundamentals_at("FOO", "2021-01-01")
    assert snap is not None
    assert snap["sector"] == "Technology"
    assert snap["revenue_yoy"] is not None
    assert abs(snap["revenue_yoy"] - (1000/800 - 1)) < 1e-6
    assert snap["market_cap"] == 1e7 * 50.0  # shares × price_at_start
    assert snap["dgs10_pct"] is None
    assert snap["insider_net_buy_3m"] is None
    # classify 입력 호환성 확인
    fund = mb.Fundamentals(**{k: snap[k] for k in mb.Fundamentals.__dataclass_fields__ if k in snap})
    cls = mb.classify(fund, mb.DEFAULTS)
    assert cls.layer in ("PASS", "WATCH", "MISS", "EXCLUDED")


def test_snapshot_fundamentals_at_returns_none_on_exception(monkeypatch):
    def boom(_):
        raise RuntimeError("yf down")
    monkeypatch.setattr(me, "_get_ticker", boom)
    assert me.snapshot_fundamentals_at("FOO", "2021-01-01") is None
