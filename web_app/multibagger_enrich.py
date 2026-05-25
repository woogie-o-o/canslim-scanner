"""yfinance 보강 — Ticker → Fundamentals 변환."""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

import multibagger as mb


def _get_ticker(symbol: str):
    """test에서 monkeypatch로 대체."""
    import yfinance as yf
    return yf.Ticker(symbol)


def _yoy(df: pd.DataFrame, row: str) -> Optional[float]:
    if df is None or df.empty or row not in df.index:
        return None
    try:
        series = df.loc[row].dropna()
        if len(series) < 2:
            return None
        cols = list(df.columns)
        latest_val = df.loc[row, cols[0]]
        prev_val = df.loc[row, cols[1]]
        if prev_val is None or pd.isna(prev_val) or prev_val == 0:
            return None
        return float(latest_val) / float(prev_val) - 1.0
    except Exception:
        return None


def _latest_val(df: pd.DataFrame, row: str) -> Optional[float]:
    if df is None or df.empty or row not in df.index:
        return None
    try:
        v = df.loc[row].dropna()
        return float(v.iloc[0]) if len(v) else None
    except Exception:
        return None


def _price_signals(hist: pd.DataFrame) -> tuple[Optional[float], Optional[float]]:
    if hist is None or hist.empty or "Close" not in hist.columns:
        return None, None
    closes = hist["Close"].dropna()
    if len(closes) < 2:
        return None, None
    last = float(closes.iloc[-1])
    high_52w = float(closes.max())
    distance = last / high_52w - 1.0 if high_52w > 0 else None
    one_month_idx = max(0, len(closes) - 21)
    ret_1m = last / float(closes.iloc[one_month_idx]) - 1.0 if len(closes) >= 2 else None
    return distance, ret_1m


def _roic(info: dict, income: pd.DataFrame, balance: pd.DataFrame) -> Optional[float]:
    try:
        ebit = _latest_val(income, "EBIT") or _latest_val(income, "OperatingIncome")
        debt = _latest_val(balance, "TotalDebt") or 0.0
        equity = _latest_val(balance, "StockholdersEquity") or _latest_val(balance, "TotalEquityGrossMinorityInterest")
        if not ebit or not equity:
            return None
        invested = float(equity) + float(debt)
        if invested <= 0:
            return None
        tax_rate = 0.21
        return float(ebit) * (1 - tax_rate) / invested
    except Exception:
        return None


def _insider_net_3m(t) -> Optional[float]:
    try:
        df = t.get_insider_transactions()
        if df is None or df.empty:
            return 0.0
        if "Value" in df.columns and "Transaction" in df.columns:
            return float(df["Value"].fillna(0).sum())
        return 0.0
    except Exception:
        return None


def enrich_one(symbol: str, dgs10_pct: Optional[float]) -> Optional[mb.Fundamentals]:
    try:
        t = _get_ticker(symbol)
        info = getattr(t, "info", {}) or {}
        income = t.income_stmt if hasattr(t, "income_stmt") else pd.DataFrame()
        balance = t.balance_sheet if hasattr(t, "balance_sheet") else pd.DataFrame()
        cash = t.cashflow if hasattr(t, "cashflow") else pd.DataFrame()
        hist = t.history(period="1y")

        mcap = info.get("marketCap")
        fcf = info.get("freeCashflow") or _latest_val(cash, "FreeCashFlow")
        ebitda = info.get("ebitda") or _latest_val(income, "EBITDA")
        pb = info.get("priceToBook")
        sector = info.get("sector")

        rev_yoy = _yoy(income, "TotalRevenue")
        ebitda_yoy = _yoy(income, "EBITDA")
        fcf_yoy = _yoy(cash, "FreeCashFlow")
        assets_yoy = _yoy(balance, "TotalAssets")
        capex_yoy = _yoy(cash, "CapitalExpenditure")

        interest = _latest_val(income, "InterestExpense")
        icr = (ebitda / abs(interest)) if (ebitda and interest) else None
        debt = _latest_val(balance, "TotalDebt")
        debt_ebitda = (debt / ebitda) if (debt and ebitda and ebitda > 0) else None

        fcf_yield = (fcf / mcap) if (fcf and mcap) else None
        roic = _roic(info, income, balance)
        distance, ret_1m = _price_signals(hist)

        buyback_raw = _latest_val(cash, "RepurchaseOfCapitalStock")
        buyback_yield = (abs(buyback_raw) / mcap) if (buyback_raw and mcap) else None

        return mb.Fundamentals(
            market_cap=mcap, ebitda=ebitda, fcf=fcf,
            roic=roic, fcf_yield=fcf_yield, pb=pb,
            revenue_yoy=rev_yoy, ebitda_yoy=ebitda_yoy, fcf_yoy=fcf_yoy,
            assets_yoy=assets_yoy, capex_yoy=capex_yoy,
            icr=icr, debt_ebitda=debt_ebitda,
            from_52w_high=distance, return_1m=ret_1m,
            sector=sector,
            insider_net_buy_3m=_insider_net_3m(t),
            buyback_yield_ttm=buyback_yield,
            dgs10_pct=dgs10_pct,
        )
    except Exception as e:
        logging.warning("multibagger enrich failed for %s: %s", symbol, e)
        return None
