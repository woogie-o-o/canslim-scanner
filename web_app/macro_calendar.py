"""매크로 이벤트 자동 생성 모듈.

NFP(고용보고서)와 CPI는 알고리즘으로 생성하고,
FOMC/한은 등 비정기 이벤트는 macro_events.json에서 읽는다.
JSON에 같은 kind+월 조합이 있으면 자동 생성 결과를 덮어쓴다(교정 레이어).
"""
from __future__ import annotations

import calendar
import json
import logging
import os
from datetime import date, timedelta

_DIR = os.path.dirname(os.path.abspath(__file__))
_JSON_PATH = os.path.join(_DIR, "macro_events.json")

# 캐시 (24시간)
_cache: list[dict] | None = None
_cache_ts: float = 0.0
_CACHE_TTL = 86400  # 24h


def _first_friday(year: int, month: int) -> date:
    """해당 월의 첫째 금요일."""
    # 1일의 요일: 0=월 ... 4=금
    first_day_dow = date(year, month, 1).weekday()
    # 금요일(4)까지 남은 일수
    days_until_fri = (4 - first_day_dow) % 7
    return date(year, month, 1 + days_until_fri)


def _cpi_estimate(year: int, month: int) -> date:
    """CPI 발표 추정일: 해당 월 10~14일 사이 첫 번째 화~목요일.

    BLS는 보통 전월 CPI를 다음 달 10~14일 사이 화~목에 발표한다.
    정확한 날짜는 BLS가 연초에 공표하므로, JSON 오버라이드로 교정 가능.
    """
    for day in range(10, 16):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        # 화(1), 수(2), 목(3) — BLS는 주로 화~목 발표
        if d.weekday() in (1, 2, 3):
            return d
    # fallback: 12일 (평균적으로 가장 가까운 날)
    return date(year, month, 12)


def _generate_nfp(start: date, months: int = 14) -> list[dict]:
    """향후 N개월 NFP(고용보고서) 이벤트 생성."""
    events = []
    y, m = start.year, start.month
    for _ in range(months):
        d = _first_friday(y, m)
        # NFP는 전월 고용 데이터 → 라벨은 전월
        prev_m = m - 1 if m > 1 else 12
        events.append({
            "date": d.isoformat(),
            "name": f"고용보고서 ({prev_m}월)",
            "kind": "nfp",
            "region": "US",
            "_auto": True,
        })
        # 다음 달
        m += 1
        if m > 12:
            m = 1
            y += 1
    return events


def _generate_cpi(start: date, months: int = 14) -> list[dict]:
    """향후 N개월 CPI 이벤트 생성."""
    events = []
    y, m = start.year, start.month
    for _ in range(months):
        d = _cpi_estimate(y, m)
        prev_m = m - 1 if m > 1 else 12
        events.append({
            "date": d.isoformat(),
            "name": f"CPI ({prev_m}월)",
            "kind": "cpi",
            "region": "US",
            "_auto": True,
        })
        m += 1
        if m > 12:
            m = 1
            y += 1
    return events


def _load_json_overrides() -> list[dict]:
    """macro_events.json에서 이벤트 읽기 (FOMC, 한은, CPI/NFP 교정 포함)."""
    try:
        with open(_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("events", []) if isinstance(data, dict) else []
    except Exception as e:
        logging.warning("macro_events.json load failed: %s", e)
        return []


def _override_key(evt: dict) -> str:
    """이벤트의 kind + 월 조합으로 고유 키 생성 (교정 매칭용)."""
    ds = evt.get("date", "")[:7]  # "2026-06"
    return f"{evt.get('kind', '')}_{ds}"


def get_macro_events() -> list[dict]:
    """매크로 이벤트 목록 반환 (자동 생성 + JSON 교정 병합)."""
    import time
    global _cache, _cache_ts

    now = time.time()
    if _cache is not None and (now - _cache_ts) < _CACHE_TTL:
        return _cache

    today = date.today()

    # 1) 알고리즘 생성: NFP + CPI (이번 달부터 14개월)
    auto_events = _generate_nfp(today, 14) + _generate_cpi(today, 14)

    # 2) JSON 오버라이드 로드
    json_events = _load_json_overrides()

    # 3) 병합: JSON 이벤트가 같은 kind+월이면 자동 생성 결과를 덮어씀
    json_keys = {}
    non_auto_events = []
    for evt in json_events:
        key = _override_key(evt)
        kind = evt.get("kind", "")
        if kind in ("nfp", "cpi"):
            # NFP/CPI는 교정 레이어
            json_keys[key] = evt
        else:
            # FOMC, 한은 등은 그대로 추가
            non_auto_events.append(evt)

    merged = []
    for evt in auto_events:
        key = _override_key(evt)
        if key in json_keys:
            # JSON 교정이 있으면 그것을 사용
            merged.append(json_keys[key])
        else:
            # 자동 생성 결과 사용 (_auto 키 제거)
            clean = {k: v for k, v in evt.items() if k != "_auto"}
            merged.append(clean)

    # FOMC, 한은 등 비자동 이벤트 추가
    merged.extend(non_auto_events)

    # 과거 이벤트 제거 (7일 전까지는 유지 — D+N 표시용)
    cutoff = today - timedelta(days=7)
    merged = [e for e in merged if e.get("date", "") >= cutoff.isoformat()]

    _cache = merged
    _cache_ts = now
    return merged
