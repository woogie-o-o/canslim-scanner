"""
macro.py — 매크로(거시) 시장 지표 수집 모듈

스캐너 상단 신호등 띠에 표시할 핵심 지표를 모은다:
    VIX, S&P500, KOSPI, 원/달러(USD/KRW), 한국은행 기준금리

설계 원칙:
  - 지표별 독립 try/except → 일부 실패해도 나머지는 정상 표시
  - 범위 검증 → 이상치는 노출하지 않고 null('—') 처리
  - 인메모리 TTL 캐시 + stale-while-error (네트워크 실패 시 직전 값 유지)
  - /api/scan 과 완전히 분리 — 이 모듈의 어떤 예외도 스캔을 깨뜨리지 않는다
"""
from __future__ import annotations

import logging
import re
import threading
import time
import urllib.request
from datetime import datetime, timezone, timedelta

_LOG = logging.getLogger("macro")

# ── 캐시 ──────────────────────────────────────────────────────────────
_CACHE_LOCK = threading.Lock()
_CACHE: dict | None = None          # 마지막으로 성공 조립한 payload
_CACHE_TS: float = 0.0              # 캐시 적재 시각(epoch sec)

_KST = timezone(timedelta(hours=9))

# 거래시간(KST 09~16시, 평일)엔 15분, 그 외 60분
_TTL_TRADING_SEC = 15 * 60
_TTL_OFF_SEC = 60 * 60

# ── 범위 검증 한계 (이 밖이면 이상치로 보고 버림) ──
_RANGES = {
    "vix":      (0.0, 150.0),
    "sp500":    (1.0, 100000.0),
    "kospi":    (1.0, 100000.0),
    "usdkrw":   (100.0, 5000.0),
    "kr_rate":  (-5.0, 30.0),
}


def _ttl_now() -> int:
    now = datetime.now(_KST)
    trading = now.weekday() < 5 and 9 <= now.hour < 16
    return _TTL_TRADING_SEC if trading else _TTL_OFF_SEC


def _valid(key: str, val):
    """범위 검증 통과 시 float 반환, 아니면 None."""
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    lo, hi = _RANGES.get(key, (float("-inf"), float("inf")))
    if not (lo <= f <= hi):
        _LOG.warning("macro: %s out of range (%s)", key, f)
        return None
    return f


# ── yfinance 배치 수집 ────────────────────────────────────────────────
_YF_MAP = {
    "^VIX":  "vix",
    "^GSPC": "sp500",
    "^KS11": "kospi",
    "KRW=X": "usdkrw",
}


def _fetch_yf() -> dict:
    """yfinance 1회 배치 호출 → {key: {value, change_pct}}. 실패 키는 생략."""
    out: dict = {}
    try:
        import yfinance as yf
        symbols = list(_YF_MAP.keys())
        df = yf.download(
            symbols, period="5d", interval="1d",
            progress=False, group_by="ticker", threads=True,
        )
    except Exception as e:
        _LOG.warning("macro: yfinance batch failed: %s", e)
        return out

    for sym, key in _YF_MAP.items():
        try:
            sub = df[sym] if sym in df.columns.get_level_values(0) else None
            if sub is None:
                continue
            closes = sub["Close"].dropna()
            if len(closes) == 0:
                continue
            last = float(closes.iloc[-1])
            prev = float(closes.iloc[-2]) if len(closes) >= 2 else last
            v = _valid(key, last)
            if v is None:
                continue
            chg = ((last - prev) / prev * 100.0) if prev else 0.0
            out[key] = {"value": round(v, 2), "change_pct": round(chg, 2)}
        except Exception as e:
            _LOG.warning("macro: yf parse %s failed: %s", sym, e)
    return out


# ── 네이버 검색 카드 — 한국은행 기준금리 스크래핑 ────────────────────
# 네이버 통합검색 "한국은행 기준금리" → 중앙은행 기준금리 표 카드.
# 표 구조: [발표일] [발표] [이전발표] — 최근 발표월은 미발표('- -')일 수 있어
# 태그 제거 후 '기준금리 표' 이후 첫 실수 퍼센트(2.50%)를 최신 기준금리로 본다.
_NAVER_RATE_URL = (
    "https://search.naver.com/search.naver?query="
    "%ED%95%9C%EA%B5%AD%EC%9D%80%ED%96%89%20%EA%B8%B0%EC%A4%80%EA%B8%88%EB%A6%AC"
)
_TAG_RE = re.compile(r"<[^>]+>")
_RATE_PCT_RE = re.compile(r"(\d{1,2}\.\d{1,2})\s*%")


def _fetch_kr_rate() -> float | None:
    """네이버 검색 카드에서 한국은행 기준금리(%) 스크래핑. 실패 시 None."""
    try:
        req = urllib.request.Request(
            _NAVER_RATE_URL,
            headers={"User-Agent":
                     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) scanner-macro"},
        )
        with urllib.request.urlopen(req, timeout=7) as resp:
            htm = resp.read().decode("utf-8", "ignore")
        anchor = htm.find("기준금리 표")
        if anchor == -1:
            anchor = htm.find("기준금리")
        if anchor == -1:
            return None
        seg = _TAG_RE.sub(" ", htm[anchor:anchor + 1200])
        m = _RATE_PCT_RE.search(seg)
        if m:
            return _valid("kr_rate", m.group(1))
    except Exception as e:
        _LOG.warning("macro: kr_rate scrape failed: %s", e)
    return None


# ── 신호등 계산 ───────────────────────────────────────────────────────
def _signal(vix, usdkrw) -> dict:
    """VIX·원달러 기반 시장 신호등. 데이터 없으면 unknown."""
    if vix is None and usdkrw is None:
        return {"level": "unknown", "emoji": "⚪", "label": "정보없음"}
    # VIX 기준: ≥30 위험 / ≥22 주의 / <22 안정
    # USD/KRW: 1400대는 근래 평상 범위 — 1480↑ 위험, 1430↑ 주의
    danger  = (vix is not None and vix >= 30) or (usdkrw is not None and usdkrw > 1480)
    caution = (vix is not None and vix >= 22) or (usdkrw is not None and usdkrw > 1430)
    if danger:
        return {"level": "danger", "emoji": "🔴", "label": "위험"}
    if caution:
        return {"level": "caution", "emoji": "🟡", "label": "주의"}
    return {"level": "stable", "emoji": "🟢", "label": "안정"}


def _build() -> dict:
    """지표를 새로 수집해 payload 조립. 부분 실패 허용."""
    yf_data = _fetch_yf()
    kr_rate = _fetch_kr_rate()

    def cell(key):
        d = yf_data.get(key)
        if not d:
            return None
        return d

    vix = (cell("vix") or {}).get("value")
    usdkrw = (cell("usdkrw") or {}).get("value")

    indicators = {
        "vix":    cell("vix"),
        "sp500":  cell("sp500"),
        "kospi":  cell("kospi"),
        "usdkrw": cell("usdkrw"),
        "kr_rate": ({"value": kr_rate, "change_pct": None} if kr_rate is not None else None),
    }
    return {
        "signal": _signal(vix, usdkrw),
        "indicators": indicators,
        "ts": datetime.now(_KST).isoformat(timespec="seconds"),
        "stale": False,
    }


def get_macro(force: bool = False) -> dict:
    """
    매크로 지표 반환. TTL 캐시 + stale-while-error.

    절대 예외를 던지지 않는다 — 최악의 경우 빈 indicators + stale 표기.
    """
    global _CACHE, _CACHE_TS
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE
        fresh = cached is not None and (now - _CACHE_TS) < _ttl_now()
        if fresh and not force:
            return cached

    try:
        payload = _build()
        with _CACHE_LOCK:
            _CACHE = payload
            _CACHE_TS = time.time()
        return payload
    except Exception as e:
        _LOG.warning("macro: build failed, serving stale: %s", e)
        with _CACHE_LOCK:
            if _CACHE is not None:
                stale = dict(_CACHE)
                stale["stale"] = True
                return stale
        return {
            "signal": {"level": "unknown", "emoji": "⚪", "label": "정보없음"},
            "indicators": {k: None for k in
                           ("vix", "sp500", "kospi", "usdkrw", "kr_rate")},
            "ts": datetime.now(_KST).isoformat(timespec="seconds"),
            "stale": True,
        }
