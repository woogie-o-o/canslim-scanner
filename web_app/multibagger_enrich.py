"""yfinance 보강 — Ticker → Fundamentals 변환.

N+1 완화: enrich_one 호출 시 per-ticker hist 조회를 줄이려면
prefetch_history(symbols)로 yf.download 단일 호출 → hist_cache 전달.
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

import multibagger as mb


def _get_ticker(symbol: str):
    """test에서 monkeypatch로 대체."""
    import yfinance as yf
    return yf.Ticker(symbol)


def prefetch_history(symbols: list[str], period: str = "1y") -> dict[str, pd.DataFrame]:
    """다수 심볼 hist를 yf.download 단일 호출로 prefetch — N개 HTTP → 1개로 단축.

    실패 시 빈 dict 반환(호출측은 fallback 으로 t.history 사용).
    """
    if not symbols:
        return {}
    try:
        import yfinance as yf
        df = yf.download(
            tickers=" ".join(symbols), period=period,
            group_by="ticker", auto_adjust=True, progress=False, threads=True,
        )
        out: dict[str, pd.DataFrame] = {}
        # group_by="ticker" → columns 가 MultiIndex (sym, field)
        if isinstance(df.columns, pd.MultiIndex):
            for sym in symbols:
                if sym in df.columns.get_level_values(0):
                    sub = df[sym].dropna(how="all")
                    if not sub.empty:
                        out[sym] = sub
        elif len(symbols) == 1:
            out[symbols[0]] = df.dropna(how="all")
        return out
    except Exception as e:
        logging.warning("prefetch_history failed (%d symbols): %s", len(symbols), e)
        return {}


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


MIN_TRADING_DAYS_52W = 252  # 52주 기준 영업일


def _price_signals(hist: pd.DataFrame) -> tuple[Optional[float], Optional[float]]:
    """P1-3: 52w 라벨링이 정확하도록 252봉 미만이면 from_52w_high=None."""
    if hist is None or hist.empty or "Close" not in hist.columns:
        return None, None
    closes = hist["Close"].dropna()
    if len(closes) < 2:
        return None, None
    last = float(closes.iloc[-1])
    if len(closes) >= MIN_TRADING_DAYS_52W:
        window = closes.iloc[-MIN_TRADING_DAYS_52W:]
        high_52w = float(window.max())
        distance = last / high_52w - 1.0 if high_52w > 0 else None
    else:
        distance = None  # 신규 상장 — 52주 데이터 부족
    one_month_idx = max(0, len(closes) - 21)
    ret_1m = last / float(closes.iloc[one_month_idx]) - 1.0
    return distance, ret_1m


def _effective_tax_rate(info: dict, income: pd.DataFrame) -> float:
    """P1-2: 21% 하드코딩 제거. info.taxRate → income_stmt 의 TaxProvision/PretaxIncome → 21% fallback.

    합리적 범위 [0, 0.5] 로 클램프 (네거티브/이상치 가드).
    """
    rate = info.get("taxRate")
    if isinstance(rate, (int, float)) and 0 <= rate <= 0.5:
        return float(rate)
    tax = _latest_val(income, "TaxProvision") or _latest_val(income, "IncomeTaxExpense")
    pretax = _latest_val(income, "PretaxIncome") or _latest_val(income, "IncomeBeforeTax")
    if tax is not None and pretax and pretax > 0:
        eff = float(tax) / float(pretax)
        if 0 <= eff <= 0.5:
            return eff
    return 0.21  # US 연방 fallback


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
        tax_rate = _effective_tax_rate(info, income)
        return float(ebit) * (1 - tax_rate) / invested
    except Exception:
        return None


# P1-5: 트랜잭션 타입 명시적 분류 — 매수/매도가 아닌 이벤트는 무시(0).
_INSIDER_BUY_KEYWORDS = ("purchase", "acquisition", "buy")
_INSIDER_SELL_KEYWORDS = ("sale", "disposition", "sell")


def _insider_tx_sign(tx_str: str) -> float:
    """양수=매수, 음수=매도, 0=기타(Exercise/Conversion/Gift 등 무시)."""
    s = (tx_str or "").lower()
    if any(k in s for k in _INSIDER_SELL_KEYWORDS):
        return -1.0
    if any(k in s for k in _INSIDER_BUY_KEYWORDS):
        return 1.0
    return 0.0


def _insider_net_3m(t) -> Optional[float]:
    """3개월 내부자 net 매수 — Sale 음수, Buy/Acquisition 양수, 기타 무시.

    P1-4: 90일 윈도우 필터.
    P1-5: 명시적 트랜잭션 타입 화이트리스트.
    """
    try:
        df = t.get_insider_transactions()
        if df is None or df.empty:
            return 0.0
        if "Value" not in df.columns or "Transaction" not in df.columns:
            return 0.0
        date_col = next((c for c in ("Start Date", "Date", "Transaction Date") if c in df.columns), None)
        if date_col is not None:
            try:
                cutoff = pd.Timestamp.now(tz=None) - pd.Timedelta(days=90)
                dts = pd.to_datetime(df[date_col], errors="coerce")
                # tz-aware 비교 가드: cutoff 가 naive 면 dts 도 naive 로 정규화.
                if hasattr(dts, "dt") and dts.dt.tz is not None:
                    dts = dts.dt.tz_localize(None)
                df = df[dts >= cutoff]
            except Exception as e:
                logging.debug("insider date filter failed: %s", e)
        if df.empty:
            return 0.0
        sign = df["Transaction"].fillna("").astype(str).apply(_insider_tx_sign)
        return float((df["Value"].fillna(0) * sign).sum())
    except Exception as e:
        logging.debug("insider_net_3m failed: %s", e)
        return None


def _pick_period_cols(df: pd.DataFrame, as_of: pd.Timestamp) -> tuple[Optional[object], Optional[object]]:
    """fiscal report 컬럼 중 as_of 이전(<=) 최신/직전 한 쌍 반환. 없으면 (None, None)."""
    if df is None or df.empty:
        return None, None
    try:
        cols = []
        for c in df.columns:
            try:
                cols.append((pd.Timestamp(c), c))
            except Exception:
                continue
        cols = [(ts, c) for ts, c in cols if pd.notna(ts) and ts <= as_of]
        cols.sort(key=lambda x: x[0], reverse=True)
        if not cols:
            return None, None
        latest = cols[0][1]
        prev = cols[1][1] if len(cols) > 1 else None
        return latest, prev
    except Exception:
        return None, None


def _latest_at(df: pd.DataFrame, row: str, latest_col) -> Optional[float]:
    if df is None or df.empty or row not in df.index or latest_col is None:
        return None
    try:
        v = df.loc[row, latest_col]
        return float(v) if pd.notna(v) else None
    except Exception:
        return None


def _yoy_at(df: pd.DataFrame, row: str, latest_col, prev_col) -> Optional[float]:
    if latest_col is None or prev_col is None:
        return None
    if df is None or df.empty or row not in df.index:
        return None
    try:
        a = df.loc[row, latest_col]
        b = df.loc[row, prev_col]
        if pd.isna(a) or pd.isna(b) or float(b) == 0:
            return None
        return float(a) / float(b) - 1.0
    except Exception:
        return None


def _price_signals_at(hist: pd.DataFrame, as_of: pd.Timestamp) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """as_of 시점의 (price, from_52w_high, return_1m). hist 는 as_of 이전 1y+ 시계열."""
    if hist is None or hist.empty or "Close" not in hist.columns:
        return None, None, None
    try:
        idx = pd.to_datetime(hist.index)
        mask = idx <= as_of
        sub = hist.loc[mask].dropna(subset=["Close"])
        if len(sub) < 252:
            return None, None, None
        sub = sub.tail(252)
        closes = sub["Close"]
        last = float(closes.iloc[-1])
        high = float(closes.max())
        dist = last / high - 1.0 if high > 0 else None
        one_m = max(0, len(closes) - 21)
        ret_1m = last / float(closes.iloc[one_m]) - 1.0
        return last, dist, ret_1m
    except Exception:
        return None, None, None


def snapshot_fundamentals_at(symbol: str, as_of_date: str) -> Optional[dict]:
    """backtest 시작 시점의 펀더멘털 스냅샷. classify() 입력으로 직접 사용 가능한 dict.

    제약: yfinance 의 income/balance/cashflow 는 통상 4~5년치만 제공.
    as_of 이전 가장 최근 fiscal report 와 그 직전 보고서를 짝지어 YoY 계산.
    historical info(sector) 는 NOW 값을 사용 — sector 는 거의 안 변함.
    historical marketCap 은 sharesOutstanding × price_at_as_of 로 재구성.
    insider_net_buy_3m / buyback_yield_ttm / dgs10_pct 는 historical 재구성 불가 → None.
    """
    try:
        as_of = pd.Timestamp(as_of_date)
        t = _get_ticker(symbol)
        info = getattr(t, "info", {}) or {}
        income = t.income_stmt if hasattr(t, "income_stmt") else pd.DataFrame()
        balance = t.balance_sheet if hasattr(t, "balance_sheet") else pd.DataFrame()
        cash = t.cashflow if hasattr(t, "cashflow") else pd.DataFrame()
        hist = t.history(start=(as_of - pd.Timedelta(days=400)).strftime("%Y-%m-%d"),
                         end=(as_of + pd.Timedelta(days=5)).strftime("%Y-%m-%d"))

        inc_l, inc_p = _pick_period_cols(income, as_of)
        bal_l, _bal_p = _pick_period_cols(balance, as_of)
        cf_l, cf_p = _pick_period_cols(cash, as_of)

        ebitda = _latest_at(income, "EBITDA", inc_l)
        fcf = _latest_at(cash, "FreeCashFlow", cf_l)
        interest = _latest_at(income, "InterestExpense", inc_l)
        debt = _latest_at(balance, "TotalDebt", bal_l)
        ebit = _latest_at(income, "EBIT", inc_l) or _latest_at(income, "OperatingIncome", inc_l)
        equity = _latest_at(balance, "StockholdersEquity", bal_l)

        rev_yoy = _yoy_at(income, "TotalRevenue", inc_l, inc_p)
        ebitda_yoy = _yoy_at(income, "EBITDA", inc_l, inc_p)
        fcf_yoy = _yoy_at(cash, "FreeCashFlow", cf_l, cf_p)
        assets_yoy = _yoy_at(balance, "TotalAssets", bal_l, _bal_p)
        capex_yoy = _yoy_at(cash, "CapitalExpenditure", cf_l, cf_p)

        icr = (ebitda / abs(interest)) if (ebitda and interest) else None
        debt_ebitda = (debt / ebitda) if (debt and ebitda and ebitda > 0) else None
        roic = None
        if ebit and equity:
            invested = float(equity) + float(debt or 0.0)
            if invested > 0:
                tax_rate = info.get("taxRate") if isinstance(info.get("taxRate"), (int, float)) else 0.21
                roic = float(ebit) * (1 - float(tax_rate)) / invested

        price, dist, ret_1m = _price_signals_at(hist, as_of)
        shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
        mcap = (float(shares) * float(price)) if (shares and price) else None
        pb = info.get("priceToBook")  # historical 재구성 어려움 → NOW 값 사용
        fcf_yield = (fcf / mcap) if (fcf and mcap) else None

        return {
            "market_cap": mcap, "ebitda": ebitda, "fcf": fcf,
            "roic": roic, "fcf_yield": fcf_yield, "pb": pb,
            "revenue_yoy": rev_yoy, "ebitda_yoy": ebitda_yoy, "fcf_yoy": fcf_yoy,
            "assets_yoy": assets_yoy, "capex_yoy": capex_yoy,
            "icr": icr, "debt_ebitda": debt_ebitda,
            "from_52w_high": dist, "return_1m": ret_1m,
            "sector": info.get("sector"),
            "insider_net_buy_3m": None, "buyback_yield_ttm": None,
            "dgs10_pct": None,
            "roic_prev": None, "revenue_yoy_prev": None,
        }
    except Exception as e:
        logging.warning("snapshot_fundamentals_at failed for %s @ %s: %s", symbol, as_of_date, e)
        return None


def enrich_one(symbol: str, dgs10_pct: Optional[float],
               hist_cache: Optional[dict] = None) -> Optional[mb.Fundamentals]:
    """단일 심볼 보강.

    hist_cache: prefetch_history 결과 dict. 있으면 t.history 호출을 건너뛰어
    N+1 감소. 미제공 시 기존 동작(per-ticker history).
    """
    try:
        t = _get_ticker(symbol)
        info = getattr(t, "info", {}) or {}
        income = t.income_stmt if hasattr(t, "income_stmt") else pd.DataFrame()
        balance = t.balance_sheet if hasattr(t, "balance_sheet") else pd.DataFrame()
        cash = t.cashflow if hasattr(t, "cashflow") else pd.DataFrame()
        if hist_cache and symbol in hist_cache:
            hist = hist_cache[symbol]
        else:
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
