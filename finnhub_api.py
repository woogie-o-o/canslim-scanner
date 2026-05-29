# -*- coding: utf-8 -*-
"""finnhub_api.py — Finnhub 데이터 조회 (한줄평 연동용).

무료 tier에서 사용 가능한 엔드포인트 (실측 확인 완료):
  - 내부자 거래 (stock_insider_transactions)
  - 애널리스트 추천 변화 (recommendation_trends)
  - 실적 서프라이즈 (company_earnings)
  - 경쟁사 (company_peers)
  - 뉴스 (company_news)
  - 실시간 시세 (quote)
  - 기본 재무 (company_basic_financials)

유료 tier 전용 (403): price_target, upgrade_downgrade, news_sentiment, earnings_calendar(symbol 필터)
"""
from __future__ import annotations

import logging
import os
import time
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_cache: Dict[str, tuple[Any, float]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 3600  # 1시간

# 라이브러리 누락을 1번만 경고
_lib_warned = False

# 동일 (fn_name, ticker) 경고 1회만 — 매 스캔마다 같은 403 로그 도배 방지
_warned_keys: set[tuple[str, str]] = set()


def _is_us_ticker(ticker: str) -> bool:
    """Finnhub 무료 tier 지원 티커 판별 (US 거래소만).

    비미국 규칙:
      - `.` 포함 (.HK/.L/.TO/.SZ/.SS/.TW/.KS/.KQ 등 거래소 suffix)
      - 전체 숫자 (한국 6자리 코드)
    """
    if not ticker:
        return False
    t = str(ticker).strip()
    if not t or "." in t or t.isdigit():
        return False
    return True


# 모듈 import 시점 sanity assert — 자료형/로직 회귀 즉시 감지
assert _is_us_ticker("NVDA") and not _is_us_ticker("650.HK") \
    and not _is_us_ticker("005930") and not _is_us_ticker(""), \
    "_is_us_ticker logic broken"


def _get_key() -> str:
    return (os.environ.get("FINNHUB_API_KEY") or "").strip()


def is_available() -> bool:
    if not _get_key():
        return False
    try:
        import finnhub  # noqa: F401
        return True
    except ImportError:
        global _lib_warned
        if not _lib_warned:
            log.error("finnhub-python 미설치: pip install finnhub-python")
            _lib_warned = True
        return False


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


def _safe(fn_name: str, ticker: str, fn):
    """API 호출을 안전하게 실행 — 실패 시 로그 1회 + None.

    동일 (fn_name, ticker) 조합은 프로세스당 1회만 경고 — 매 스캔마다 같은
    403 메시지가 수십 줄 쌓이는 로그 도배를 막는다.
    """
    try:
        return fn()
    except Exception as e:
        key = (fn_name, ticker)
        if key not in _warned_keys:
            _warned_keys.add(key)
            log.warning(f"Finnhub {fn_name}({ticker}) 실패: {type(e).__name__}: {e}")
        return None


def get_sentiment_data(ticker: str) -> Dict[str, Any]:
    """종목의 Finnhub 센티먼트 + 분석가 + 이벤트 데이터를 통합 조회.

    Returns dict — 자세한 키 목록은 코드 참조.
    """
    if not is_available() or not _is_us_ticker(ticker):
        return {"available": False}

    cache_key = f"fh:{ticker}"
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    result: Dict[str, Any] = {"available": True}
    fc = _client()
    today = datetime.now()
    today_s = today.strftime("%Y-%m-%d")
    three_months_ago = (today - timedelta(days=90)).strftime("%Y-%m-%d")
    seven_days_ago = (today - timedelta(days=7)).strftime("%Y-%m-%d")

    # 1) Insider transactions (최근 3개월)
    ins = _safe("insider_tx", ticker,
                lambda: fc.stock_insider_transactions(ticker, three_months_ago, today_s))
    if ins:
        txs = ins.get("data") or []
        net_shares = 0
        for tx in txs:
            shares = tx.get("share") or 0
            code = (tx.get("transactionCode") or "").upper()
            if code == "P":
                net_shares += shares
            elif code == "S":
                net_shares -= shares
        result["insider_net_shares"] = net_shares
        result["insider_tx_count"] = len(txs)
    else:
        result["insider_net_shares"] = 0
        result["insider_tx_count"] = 0

    # 2) Recommendation trends
    rec = _safe("rec_trends", ticker, lambda: fc.recommendation_trends(ticker))
    if rec and len(rec) >= 1:
        curr = rec[0]
        result["rec_strong_buy"] = curr.get("strongBuy", 0)
        result["rec_buy"] = curr.get("buy", 0)
        result["rec_hold"] = curr.get("hold", 0)
        result["rec_sell"] = curr.get("sell", 0) + curr.get("strongSell", 0)
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

    # 3) Earnings surprises (최근 4분기)
    earn = _safe("earnings", ticker, lambda: fc.company_earnings(ticker, limit=4))
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

    # 4) Quote (실시간 시세, 15분 지연)
    q = _safe("quote", ticker, lambda: fc.quote(ticker))
    if q and q.get("c"):
        result["current_price"] = q.get("c") or 0
        result["day_change"] = q.get("d") or 0
        result["day_change_pct"] = q.get("dp") or 0
        result["day_high"] = q.get("h") or 0
        result["day_low"] = q.get("l") or 0
        result["prev_close"] = q.get("pc") or 0
    else:
        result["current_price"] = 0
        result["day_change"] = 0
        result["day_change_pct"] = 0
        result["day_high"] = 0
        result["day_low"] = 0
        result["prev_close"] = 0

    # 5) 다음 실적발표일 추정 (company_earnings 최근 분기 + 90일)
    if earn and earn[0].get("period"):
        try:
            last_period = datetime.strptime(earn[0]["period"], "%Y-%m-%d")
            # 보통 분기말 후 30-45일 내 발표 → 약 120일 cadence
            est_next = last_period + timedelta(days=120)
            result["next_earnings_estimate"] = est_next.strftime("%Y-%m-%d")
            result["days_to_earnings_est"] = max(-1, (est_next - today).days)
        except Exception:
            result["next_earnings_estimate"] = ""
            result["days_to_earnings_est"] = -1
    else:
        result["next_earnings_estimate"] = ""
        result["days_to_earnings_est"] = -1

    # 6) Company news (최근 7일)
    news = _safe("company_news", ticker,
                 lambda: fc.company_news(ticker, _from=seven_days_ago, to=today_s))
    if news:
        result["news_count_7d"] = len(news)
        headlines = []
        for n in news[:5]:
            h = n.get("headline") or ""
            if h:
                headlines.append({
                    "title": h[:140],
                    "url": n.get("url") or "",
                    "source": n.get("source") or "",
                    "datetime": n.get("datetime") or 0,
                })
        result["news_headlines"] = headlines
    else:
        result["news_count_7d"] = 0
        result["news_headlines"] = []

    _store(cache_key, result)
    return result


def get_peers(ticker: str) -> List[str]:
    """동종업계 경쟁사 티커 리스트 (Finnhub GICS 기반, US 거래소만).

    ADR(BABA/BIDU/TSM 등)은 Finnhub가 원지(.HK/.SZ/.TW)를 반환하지만,
    무료 tier 후속 호출이 전부 403이므로 US 티커만 통과시킨다.
    """
    if not is_available() or not _is_us_ticker(ticker):
        return []
    cache_key = f"fh_peers:{ticker}"
    cached = _cached(cache_key)
    if cached is not None:
        return cached
    fc = _client()
    raw = _safe("peers", ticker, lambda: fc.company_peers(ticker)) or []
    # 자기 자신 + 비미국 제외, 상위 10개
    peers = [p for p in raw
             if p and p.upper() != ticker.upper() and _is_us_ticker(p)][:10]
    _store(cache_key, peers)
    return peers


def get_basic_financials(ticker: str) -> Dict[str, Any]:
    """Finnhub basic financials -> yfinance info 호환 dict.

    yfinance info가 빈 경우 US 종목 폴백으로 사용한다.
    무료 tier는 US 거래소만 지원 — .HK/.L/.TO/.SZ/.TW 등은 즉시 빈 dict.
    """
    if not is_available() or not _is_us_ticker(ticker):
        return {}

    cache_key = f"fh_fin:{ticker}"
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    fc = _client()
    m = _safe("basic_fin", ticker, lambda: fc.company_basic_financials(ticker, "all"))
    if not m:
        return {}
    metric = m.get("metric") or {}

    result: Dict[str, Any] = {}

    roe = metric.get("roeTTM")
    if roe is not None:
        result["returnOnEquity"] = roe / 100.0
    om = metric.get("operatingMarginTTM")
    if om is not None:
        result["operatingMargins"] = om / 100.0
    gm = metric.get("grossMarginTTM")
    if gm is not None:
        result["grossMargins"] = gm / 100.0
    rg = metric.get("revenueGrowthQuarterlyYoy")
    if rg is not None:
        result["revenueGrowth"] = rg / 100.0
    mc = metric.get("marketCapitalization")
    if mc is not None:
        result["marketCap"] = mc * 1_000_000
    cr = metric.get("currentRatioQuarterly")
    if cr is not None:
        result["currentRatio"] = cr
    de = metric.get("totalDebt/totalEquityQuarterly")
    if de is not None:
        result["debtToEquity"] = de * 100

    # 추가: P/E, P/B, 베타, 52주 고저, 배당
    pe = metric.get("peTTM") or metric.get("peNormalizedAnnual")
    if pe is not None:
        result["trailingPE"] = pe
    pb = metric.get("pbQuarterly") or metric.get("pbAnnual")
    if pb is not None:
        result["priceToBook"] = pb
    beta = metric.get("beta")
    if beta is not None:
        result["beta"] = beta
    hi52 = metric.get("52WeekHigh")
    if hi52 is not None:
        result["fiftyTwoWeekHigh"] = hi52
    lo52 = metric.get("52WeekLow")
    if lo52 is not None:
        result["fiftyTwoWeekLow"] = lo52
    div = metric.get("dividendYieldIndicatedAnnual")
    if div is not None:
        result["dividendYield"] = div / 100.0
    eps = metric.get("epsTTM")
    if eps is not None:
        result["trailingEps"] = eps

    _store(cache_key, result)
    return result
