"""FRED DGS10 (10년 국채금리) fetcher + 24h pickle 캐시."""
from __future__ import annotations

import os
import pickle
import time
import urllib.request
from typing import Optional

CACHE_PATH = os.path.join(os.path.dirname(__file__), "cache_v19", "rates_us.pkl")
CACHE_TTL_SEC = 24 * 3600
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"


def _parse_last_valid(csv_text: str) -> Optional[float]:
    last = None
    for line in csv_text.strip().splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        v = parts[1].strip()
        if v and v != ".":
            try:
                last = float(v)
            except ValueError:
                continue
    return last


def _fetch_remote() -> Optional[float]:
    try:
        with urllib.request.urlopen(FRED_URL, timeout=5) as resp:
            data = resp.read().decode("utf-8", errors="replace")
        return _parse_last_valid(data)
    except Exception:
        return None


def get_dgs10() -> Optional[float]:
    """캐시 fresh면 반환, 만료면 fetch 후 갱신. 모두 실패 시 last cached 또는 None."""
    cached = None
    try:
        if os.path.exists(CACHE_PATH):
            with open(CACHE_PATH, "rb") as f:
                cached = pickle.load(f)
    except Exception:
        cached = None

    if cached and (time.time() - cached.get("_ts", 0)) < CACHE_TTL_SEC:
        return cached.get("dgs10_pct")

    fresh = _fetch_remote()
    if fresh is None:
        return cached.get("dgs10_pct") if cached else None

    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "wb") as f:
            pickle.dump({"_ts": time.time(), "dgs10_pct": fresh}, f)
    except Exception:
        pass
    return fresh
