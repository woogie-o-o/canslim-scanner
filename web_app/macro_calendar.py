"""Generate recurring macro events and merge explicit date overrides."""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, timedelta

_DIR = os.path.dirname(os.path.abspath(__file__))
_JSON_PATH = os.path.join(_DIR, "macro_events.json")
_CACHE_TTL = 86400
_cache: list[dict] | None = None
_cache_ts = 0.0


def _first_friday(year: int, month: int) -> date:
    first_day_dow = date(year, month, 1).weekday()
    return date(year, month, 1 + (4 - first_day_dow) % 7)


def _cpi_estimate(year: int, month: int) -> date:
    for day in range(10, 16):
        candidate = date(year, month, day)
        if candidate.weekday() in (1, 2, 3):
            return candidate
    return date(year, month, 12)


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _generate_nfp(start: date, months: int = 14) -> list[dict]:
    events = []
    year, month = start.year, start.month
    for _ in range(months):
        previous_month = month - 1 if month > 1 else 12
        events.append({
            "date": _first_friday(year, month).isoformat(),
            "name": f"고용보고서 ({previous_month}월)",
            "kind": "nfp",
            "region": "US",
        })
        year, month = _next_month(year, month)
    return events


def _generate_cpi(start: date, months: int = 14) -> list[dict]:
    events = []
    year, month = start.year, start.month
    for _ in range(months):
        previous_month = month - 1 if month > 1 else 12
        events.append({
            "date": _cpi_estimate(year, month).isoformat(),
            "name": f"CPI ({previous_month}월)",
            "kind": "cpi",
            "region": "US",
        })
        year, month = _next_month(year, month)
    return events


def _load_json_events() -> list[dict]:
    try:
        with open(_JSON_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data.get("events", []) if isinstance(data, dict) else []
    except Exception as exc:
        logging.warning("macro_events.json load failed: %s", exc)
        return []


def _event_key(event: dict) -> str:
    return f"{event.get('kind', '')}_{str(event.get('date', ''))[:7]}"


def get_macro_events() -> list[dict]:
    global _cache, _cache_ts
    now = time.time()
    if _cache is not None and now - _cache_ts < _CACHE_TTL:
        return list(_cache)

    today = date.today()
    generated = _generate_nfp(today) + _generate_cpi(today)
    explicit = _load_json_events()
    overrides = {
        _event_key(event): event
        for event in explicit
        if event.get("kind") in ("nfp", "cpi")
    }
    fixed = [
        event for event in explicit
        if event.get("kind") not in ("nfp", "cpi")
    ]
    merged = [overrides.get(_event_key(event), event) for event in generated]
    merged.extend(fixed)
    cutoff = (today - timedelta(days=7)).isoformat()
    merged = sorted(
        (event for event in merged if str(event.get("date", "")) >= cutoff),
        key=lambda event: str(event.get("date", "")),
    )
    _cache = merged
    _cache_ts = now
    return list(merged)
