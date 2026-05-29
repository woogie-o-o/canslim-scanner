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
import urllib.parse
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
    "us_rate":  (-5.0, 30.0),
    "us10y":    (0.0, 30.0),
    "dxy":      (50.0, 200.0),
    "gold":     (100.0, 10000.0),
    "wti":      (0.0, 500.0),
    "btc":      (1.0, 10_000_000.0),
    "nasdaq":   (1.0, 100000.0),
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
    "^VIX":    "vix",
    "^GSPC":   "sp500",
    "^IXIC":   "nasdaq",
    "^KS11":   "kospi",
    "KRW=X":   "usdkrw",
    "^TNX":    "us10y",     # 미국 10년물 국채 금리(%)
    "DX-Y.NYB": "dxy",       # 달러인덱스
    "GC=F":    "gold",       # 금 선물(USD/oz)
    "CL=F":    "wti",        # WTI 원유(USD/bbl)
    "BTC-USD": "btc",        # 비트코인(USD)
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


# ── 네이버 검색 카드 — 한국/미국 기준금리 스크래핑 ────────────────────
# 네이버 통합검색 "한국은행 기준금리" / "미국 기준금리" → 중앙은행 기준금리 표 카드.
# 태그 제거 후 '기준금리 표' 이후 첫 실수 퍼센트를 최신 기준금리로 본다.
# 미국 Fed funds rate는 범위(예: 4.00~4.25%)로 표기되며, 상단(upper bound)을 선호.
_TAG_RE = re.compile(r"<[^>]+>")
_RATE_PCT_RE = re.compile(r"(\d{1,2}\.\d{1,2})\s*%")
_RATE_RANGE_RE = re.compile(r"(\d{1,2}\.\d{1,2})\s*[~∼\-–]\s*(\d{1,2}\.\d{1,2})\s*%")


def _fetch_naver_rate(key: str, query: str) -> float | None:
    """네이버 검색 카드에서 기준금리(%) 스크래핑. 실패 시 None."""
    try:
        url = ("https://search.naver.com/search.naver?query="
               + urllib.parse.quote(query))
        req = urllib.request.Request(
            url,
            headers={"User-Agent":
                     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) scanner-macro"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            htm = resp.read().decode("utf-8", "ignore")
        anchor = htm.find("기준금리 표")
        if anchor == -1:
            anchor = htm.find("기준금리")
        if anchor == -1:
            return None
        seg = _TAG_RE.sub(" ", htm[anchor:anchor + 1200])
        # 범위(예: 4.00~4.25%) 우선 — 상단 사용
        rng = _RATE_RANGE_RE.search(seg)
        if rng:
            return _valid(key, rng.group(2))
        m = _RATE_PCT_RE.search(seg)
        if m:
            return _valid(key, m.group(1))
    except Exception as e:
        _LOG.warning("macro: %s scrape failed: %s", key, e)
    return None


def _fetch_kr_rate() -> float | None:
    return _fetch_naver_rate("kr_rate", "한국은행 기준금리")


def _fetch_us_rate() -> float | None:
    return _fetch_naver_rate("us_rate", "미국 기준금리")


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


_ALL_KEYS = (
    "vix", "sp500", "nasdaq", "kospi", "usdkrw",
    "kr_rate", "us_rate", "us10y", "dxy",
    "gold", "wti", "btc",
)


def _build() -> dict:
    """지표를 새로 수집해 payload 조립. 부분 실패 허용."""
    yf_data = _fetch_yf()
    kr_rate = _fetch_kr_rate()
    us_rate = _fetch_us_rate()

    def cell(key):
        d = yf_data.get(key)
        if not d:
            return None
        return d

    vix = (cell("vix") or {}).get("value")
    usdkrw = (cell("usdkrw") or {}).get("value")

    indicators = {
        "vix":     cell("vix"),
        "sp500":   cell("sp500"),
        "nasdaq":  cell("nasdaq"),
        "kospi":   cell("kospi"),
        "usdkrw":  cell("usdkrw"),
        "us10y":   cell("us10y"),
        "dxy":     cell("dxy"),
        "gold":    cell("gold"),
        "wti":     cell("wti"),
        "btc":     cell("btc"),
        "kr_rate": ({"value": kr_rate, "change_pct": None} if kr_rate is not None else None),
        "us_rate": ({"value": us_rate, "change_pct": None} if us_rate is not None else None),
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
            "indicators": {k: None for k in _ALL_KEYS},
            "ts": datetime.now(_KST).isoformat(timespec="seconds"),
            "stale": True,
        }
