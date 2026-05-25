"""DIFF — 5년 10배 종목 명단 배치 추출. CLI: python -m multibagger_backtest"""
from __future__ import annotations

import os
import pickle
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import pandas as pd

CACHE_PATH = os.path.join(os.path.dirname(__file__), "cache_v19", "baggers_us.pkl")
MIN_TRADING_DAYS = 200


def _compute_multiple(closes: pd.Series) -> Optional[float]:
    if closes is None or len(closes) < MIN_TRADING_DAYS:
        return None
    start = float(closes.iloc[0])
    end = float(closes.iloc[-1])
    if start <= 0:
        return None
    return end / start


def _extract_baggers(by_symbol: dict, multiple: float) -> list:
    out = []
    for sym, df in by_symbol.items():
        if df is None or df.empty or "Close" not in df.columns:
            continue
        mult = _compute_multiple(df["Close"])
        if mult is None or mult < multiple:
            continue
        out.append({
            "ticker": sym,
            "start_close": float(df["Close"].iloc[0]),
            "end_close": float(df["Close"].iloc[-1]),
            "multiple": round(mult, 2),
        })
    out.sort(key=lambda r: -r["multiple"])
    return out


def _fetch_history(sym: str, start: str) -> Optional[pd.DataFrame]:
    try:
        import yfinance as yf
        return yf.Ticker(sym).history(start=start, timeout=15)
    except Exception as e:
        logging.warning("history fetch failed %s: %s", sym, e)
        return None


def build_bagger_list_us(start: str = "2021-01-01", multiple: float = 10.0,
                        universe: Optional[list] = None, max_workers: int = 8) -> list:
    if universe is None:
        try:
            with open(os.path.join(os.path.dirname(__file__), "cache_v19", "sectors_us.pkl"), "rb") as f:
                sectors = pickle.load(f)
            universe = sorted({t for ts in sectors.values() for t in ts})
        except Exception:
            universe = []

    by_symbol = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_fetch_history, sym, start): sym for sym in universe}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                by_symbol[sym] = fut.result()
            except Exception:
                continue

    baggers = _extract_baggers(by_symbol, multiple)
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "wb") as f:
            import time
            pickle.dump({"_ts": time.time(), "baggers": baggers}, f)
    except Exception as e:
        logging.warning("baggers pkl save failed: %s", e)
    logging.info("baggers extracted: %d", len(baggers))
    return baggers


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build_bagger_list_us()
