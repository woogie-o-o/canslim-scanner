"""FRED DGS10 (10년 국채금리) fetcher + 24h JSON 캐시."""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from typing import Optional

# JSON 캐시: pickle.load RCE 위험 회피. 옛 .pkl 경로는 무시(자동 삭제 X).
CACHE_PATH = os.path.join(os.path.dirname(__file__), "cache_v19", "rates_us.json")
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


_RETRY_BACKOFF_SEC = (1, 2, 4)  # 3회 재시도 backoff


def _fetch_remote() -> Optional[float]:
    """P1-9: 일시적 네트워크 오류에 3회 backoff 재시도."""
    last_err: Optional[Exception] = None
    for i, delay in enumerate((0,) + _RETRY_BACKOFF_SEC[:-1]):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(FRED_URL, timeout=5) as resp:
                data = resp.read().decode("utf-8", errors="replace")
            v = _parse_last_valid(data)
            if v is not None:
                return v
        except Exception as e:
            last_err = e
            logging.debug("DGS10 fetch attempt %d failed: %s", i + 1, e)
    if last_err is not None:
        logging.warning("DGS10 fetch exhausted retries: %s", last_err)
    return None


def _load_cached() -> Optional[dict]:
    """JSON 캐시 읽기. 손상 시 None."""
    if not os.path.exists(CACHE_PATH):
        return None
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError) as e:
        logging.debug("rates cache read failed: %s", e)
    return None


def get_dgs10() -> Optional[float]:
    """캐시 fresh면 반환, 만료면 fetch 후 갱신. 모두 실패 시 last cached 또는 None."""
    cached = _load_cached()

    if cached and (time.time() - cached.get("_ts", 0)) < CACHE_TTL_SEC:
        return cached.get("dgs10_pct")

    fresh = _fetch_remote()
    if fresh is None:
        return cached.get("dgs10_pct") if cached else None

    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"_ts": time.time(), "dgs10_pct": fresh}, f)
    except OSError as e:
        logging.debug("rates cache write failed: %s", e)
    return fresh
