"""DIFF — 5년 10배 종목 명단 배치 추출. CLI: python -m multibagger_backtest"""
from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import pandas as pd

# JSON 캐시(이전 .pkl 의 pickle.load RCE 위험 회피).
CACHE_PATH = os.path.join(os.path.dirname(__file__), "cache_v19", "baggers_us.json")
SECTORS_PATH = os.path.join(os.path.dirname(__file__), "cache_v19", "sectors_us.json")
MIN_TRADING_DAYS = 200


def _compute_multiple(closes: pd.Series) -> Optional[float]:
    if closes is None or len(closes) < MIN_TRADING_DAYS:
        return None
    start = float(closes.iloc[0])
    end = float(closes.iloc[-1])
    if start <= 0:
        return None
    return end / start


def _extract_baggers(by_symbol: dict, multiple: float,
                     start: Optional[str] = None,
                     snapshot_fn=None,
                     max_workers: int = 8) -> list:
    """multiple 이상 종목 추출 + snapshot_fn 주입 시 시작 시점 펀더멘털 적재.

    snapshot_fn: callable(symbol, as_of_date_str) -> dict | None.
    None 이면 snapshot_at_start 키 생략(레거시 동작).
    """
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

    if snapshot_fn is not None and start and out:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(snapshot_fn, b["ticker"], start): b for b in out}
            for fut in as_completed(futs):
                b = futs[fut]
                try:
                    snap = fut.result()
                except Exception as e:
                    logging.debug("snapshot fetch failed %s: %s", b["ticker"], e)
                    snap = None
                b["snapshot_at_start"] = snap
    return out


def _fetch_history(sym: str, start: str) -> Optional[pd.DataFrame]:
    try:
        import yfinance as yf
        return yf.Ticker(sym).history(start=start, timeout=15)
    except Exception as e:
        logging.warning("history fetch failed %s: %s", sym, e)
        return None


def _load_sectors_universe() -> list:
    """sectors_us.json → flat ticker list."""
    if not os.path.exists(SECTORS_PATH):
        return []
    try:
        with open(SECTORS_PATH, "r", encoding="utf-8") as f:
            sectors = json.load(f)
        if not isinstance(sectors, dict):
            return []
        return sorted({t for ts in sectors.values() for t in ts})
    except (OSError, json.JSONDecodeError) as e:
        logging.warning("sectors_us.json load failed: %s", e)
        return []


def build_bagger_list_us(start: str = "2021-01-01", multiple: float = 10.0,
                        universe: Optional[list] = None, max_workers: int = 8,
                        snapshot_fn=None) -> list:
    """5년 10x 종목 추출 + 시작 시점 스냅샷 적재.

    snapshot_fn 미지정 시 multibagger_enrich.snapshot_fundamentals_at 사용.
    """
    if universe is None:
        universe = _load_sectors_universe()

    if snapshot_fn is None:
        try:
            from multibagger_enrich import snapshot_fundamentals_at
            snapshot_fn = snapshot_fundamentals_at
        except Exception as e:
            logging.warning("snapshot_fn import failed; baggers will lack snapshot_at_start: %s", e)

    by_symbol = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_fetch_history, sym, start): sym for sym in universe}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                by_symbol[sym] = fut.result()
            except Exception as e:
                logging.debug("bagger future %s skipped: %s", sym, e)
                continue

    baggers = _extract_baggers(by_symbol, multiple, start=start, snapshot_fn=snapshot_fn,
                              max_workers=max_workers)
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"_ts": time.time(), "baggers": baggers}, f)
    except OSError as e:
        logging.warning("baggers json save failed: %s", e)
    logging.info("baggers extracted: %d", len(baggers))
    return baggers


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build_bagger_list_us()
