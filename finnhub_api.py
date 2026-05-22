# -*- coding: utf-8 -*-
"""finnhub_api.py — Finnhub 데이터 조회 (한줄평 연동용).

무료 tier에서 사용 가능한 엔드포인트:
  - 내부자 거래 (insider transactions)
  - 애널리스트 추천 변화 (recommendation trends)
  - 실적 서프라이즈 (earnings surprises)
"""
from __future__ import annotations

import os
import time
import threading
from typing import Any, Dict, Optional

_cache: Dict[str, tuple[Any, float]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 3600  # 1시간


def _get_key() -> str:
    return (os.environ.get("FINNHUB_API_KEY") or "").strip()


def is_available() -> bool:
    return bool(_get_key())


def _client():
    import finnhub
    return finnhub.Client(api_key=_get_key())


def _cached(key: str):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and (time.time() - hit[1]) < _CACHE_TTL:
            return hit[0]
    return None


def _store(key: str, val: Any):
    with _cache_lock:
        _cache[key] = (val, time.time())


def get_sentiment_data(ticker: str) -> Dict[str, Any]:
    """종목의 Finnhub 센티먼트 데이터를 통합 조회.

    Returns:
        {
          "insider_net_shares": int,    # 최근 3개월 내부자 순매수 주수 (양수=매수, 음수=매도)
          "insider_tx_count": int,      # 내부자 거래 건수
          "rec_strong_buy": int,        # 이번달 strong buy 수
          "rec_buy": int,
          "rec_hold": int,
          "rec_sell": int,
          "rec_change": str,            # "upgrade" | "downgrade" | "stable" | ""
          "earnings_surprise_pct": float,  # 최근 분기 서프라이즈 %
          "earnings_beat_streak": int,  # 연속 서프라이즈 양수 분기 수
          "available": bool,
        }
    """
    if not is_available():
        return {"available": False}

    cache_key = f"fh:{ticker}"
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    result: Dict[str, Any] = {"available": True}
    fc = _client()

    # 1) Insider transactions (최근 3개월)
    try:
        from datetime import datetime, timedelta
        today = datetime.now().strftime("%Y-%m-%d")
        three_months_ago = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        ins = fc.stock_insider_transactions(ticker, three_months_ago, today)
        txs = ins.get("data") or []
        net_shares = 0
        for tx in txs:
            shares = tx.get("share") or 0
            # transactionCode: P=Purchase, S=Sale, A=Grant/Award
            code = (tx.get("transactionCode") or "").upper()
            if code == "P":
                net_shares += shares
            elif code == "S":
                net_shares -= shares
        result["insider_net_shares"] = net_shares
        result["insider_tx_count"] = len(txs)
    except Exception:
        result["insider_net_shares"] = 0
        result["insider_tx_count"] = 0

    # 2) Recommendation trends (최근 2개월 비교)
    try:
        rec = fc.recommendation_trends(ticker)
        if rec and len(rec) >= 1:
            curr = rec[0]
            result["rec_strong_buy"] = curr.get("strongBuy", 0)
            result["rec_buy"] = curr.get("buy", 0)
            result["rec_hold"] = curr.get("hold", 0)
            result["rec_sell"] = curr.get("sell", 0) + curr.get("strongSell", 0)
            # 변화 감지: 이번달 vs 지난달
            if len(rec) >= 2:
                prev = rec[1]
                curr_bull = curr.get("strongBuy", 0) + curr.get("buy", 0)
                prev_bull = prev.get("strongBuy", 0) + prev.get("buy", 0)
                curr_bear = curr.get("sell", 0) + curr.get("strongSell", 0)
                prev_bear = prev.get("sell", 0) + prev.get("strongSell", 0)
                if curr_bull > prev_bull and curr_bear <= prev_bear:
                    result["rec_change"] = "upgrade"
                elif curr_bear > prev_bear and curr_bull <= prev_bull:
                    result["rec_change"] = "downgrade"
                else:
                    result["rec_change"] = "stable"
            else:
                result["rec_change"] = ""
        else:
            result["rec_strong_buy"] = 0
            result["rec_buy"] = 0
            result["rec_hold"] = 0
            result["rec_sell"] = 0
            result["rec_change"] = ""
    except Exception:
        result["rec_strong_buy"] = 0
        result["rec_buy"] = 0
        result["rec_hold"] = 0
        result["rec_sell"] = 0
        result["rec_change"] = ""

    # 3) Earnings surprises (최근 4분기)
    try:
        earn = fc.company_earnings(ticker, limit=4)
        if earn:
            latest = earn[0]
            result["earnings_surprise_pct"] = latest.get("surprisePercent") or 0
            streak = 0
            for e in earn:
                if (e.get("surprisePercent") or 0) > 0:
                    streak += 1
                else:
                    break
            result["earnings_beat_streak"] = streak
        else:
            result["earnings_surprise_pct"] = 0
            result["earnings_beat_streak"] = 0
    except Exception:
        result["earnings_surprise_pct"] = 0
        result["earnings_beat_streak"] = 0

    _store(cache_key, result)
    return result
