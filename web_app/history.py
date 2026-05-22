"""스캔 결과 히스토리 스냅샷 — 어제 대비 점수/순위 변동 산출.

스냅샷 파일: web_app/snapshots/scanner_{MARKET}_{YYYY-MM-DD}.json
포맷: {"TICKER": {"score": float, "rank": int}, ...}

오늘 스냅샷이 이미 있으면 덮어쓰지 않고 가장 가까운 과거 스냅샷을 비교 기준으로 사용.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta

_BASE = os.path.dirname(os.path.abspath(__file__))
_SNAP_DIR = os.path.join(_BASE, "snapshots")
os.makedirs(_SNAP_DIR, exist_ok=True)

_MAX_LOOKBACK_DAYS = 14


def _snap_path(market: str, day: date) -> str:
    return os.path.join(_SNAP_DIR, f"scanner_{market}_{day.isoformat()}.json")


def _load(market: str, day: date) -> dict | None:
    p = _snap_path(market, day)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.warning("snapshot load failed (%s): %s", p, e)
        return None


def _find_baseline(market: str, today: date) -> tuple[dict | None, date | None]:
    """오늘을 제외한 가장 가까운 과거 스냅샷을 찾아 반환."""
    for back in range(1, _MAX_LOOKBACK_DAYS + 1):
        d = today - timedelta(days=back)
        snap = _load(market, d)
        if snap:
            return snap, d
    return None, None


def annotate_deltas(results: list[dict], market: str) -> list[dict]:
    """results 각 항목에 ScoreDelta / RankDelta / DeltaDays 필드 추가.

    - 기준 스냅샷 없음 → 모두 None
    - 결과는 그대로 반환 (in-place 수정)
    """
    if not results:
        return results
    today = date.today()
    baseline, base_day = _find_baseline(market, today)
    days = (today - base_day).days if base_day else 0
    # 오늘 순위 산출 (TotalScore 내림차순 가정 — 이미 정렬됨)
    sorted_by_score = sorted(
        results, key=lambda x: x.get("TotalScore", 0), reverse=True
    )
    today_rank = {r.get("Ticker"): i + 1 for i, r in enumerate(sorted_by_score)}

    for r in results:
        tkr = r.get("Ticker")
        cur_score = r.get("TotalScore")
        cur_rank = today_rank.get(tkr)
        if baseline and tkr in baseline:
            prev = baseline[tkr]
            prev_score = prev.get("score")
            prev_rank = prev.get("rank")
            r["ScoreDelta"] = (
                round(cur_score - prev_score, 1)
                if cur_score is not None and prev_score is not None
                else None
            )
            # 양수 = 순위 상승(숫자 감소), 음수 = 순위 하락
            r["RankDelta"] = (
                prev_rank - cur_rank
                if cur_rank is not None and prev_rank is not None
                else None
            )
            r["DeltaDays"] = days
            r["IsNew"] = False
        else:
            r["ScoreDelta"] = None
            r["RankDelta"] = None
            r["DeltaDays"] = days
            r["IsNew"] = baseline is not None  # 기준 있는데 누락된 종목 = 신규
    return results


def save_snapshot(results: list[dict], market: str) -> None:
    """오늘 결과를 스냅샷으로 저장. 이미 있으면 덮어씀(같은 날 재스캔)."""
    if not results:
        return
    today = date.today()
    sorted_by_score = sorted(
        results, key=lambda x: x.get("TotalScore", 0), reverse=True
    )
    snap = {
        r["Ticker"]: {
            "score": round(float(r.get("TotalScore", 0) or 0), 1),
            "rank": i + 1,
        }
        for i, r in enumerate(sorted_by_score)
        if r.get("Ticker")
    }
    p = _snap_path(market, today)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, separators=(",", ":"))
        logging.info("snapshot saved: %s (%d tickers)", p, len(snap))
    except Exception as e:
        logging.warning("snapshot save failed (%s): %s", p, e)
    # 오래된 스냅샷 청소 (보관 30일)
    _prune_old(market, today, keep_days=30)


def _prune_old(market: str, today: date, *, keep_days: int = 30) -> None:
    try:
        cutoff = today - timedelta(days=keep_days)
        prefix = f"scanner_{market}_"
        for name in os.listdir(_SNAP_DIR):
            if not (name.startswith(prefix) and name.endswith(".json")):
                continue
            try:
                day_str = name[len(prefix):-5]
                d = datetime.strptime(day_str, "%Y-%m-%d").date()
                if d < cutoff:
                    os.remove(os.path.join(_SNAP_DIR, name))
            except Exception:
                continue
    except Exception:
        pass
