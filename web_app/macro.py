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
    "vix3m":    (0.0, 150.0),
    "skew":     (80.0, 200.0),
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
    "hyg":      (30.0, 200.0),
    "lqd":      (50.0, 200.0),
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
    "^VIX3M":  "vix3m",      # VIX 3개월 — 텀스트럭처 계산용
    "^SKEW":   "skew",       # CBOE SKEW — 꼬리 리스크
    "^GSPC":   "sp500",
    "^IXIC":   "nasdaq",
    "^KS11":   "kospi",
    "KRW=X":   "usdkrw",
    "^TNX":    "us10y",      # 미국 10년물 국채 금리(%)
    "DX-Y.NYB": "dxy",       # 달러인덱스
    "GC=F":    "gold",       # 금 선물(USD/oz)
    "CL=F":    "wti",        # WTI 원유(USD/bbl)
    "BTC-USD": "btc",        # 비트코인(USD)
    "HYG":     "hyg",        # 하이일드 채권 — 신용 스트레스
    "LQD":     "lqd",        # 투자등급 채권 — HY 스프레드 계산용
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


# ── 기준금리 스크래핑 ────────────────────────────────────────────────
# 한국 기준금리: 한국은행 공식 "한국은행 기준금리 추이" 표 우선.
# 미국 기준금리: 네이버 통합검색 "미국 기준금리" → 중앙은행 기준금리 표 카드.
# 태그 제거 후 '기준금리 표' 이후 첫 실수 퍼센트를 최신 기준금리로 본다.
# 미국 Fed funds rate는 범위(예: 4.00~4.25%)로 표기되며, 상단(upper bound)을 선호.
_TAG_RE = re.compile(r"<[^>]+>")
_RATE_PCT_RE = re.compile(r"(\d{1,2}\.\d{1,2})\s*%")
_RATE_RANGE_RE = re.compile(r"(\d{1,2}\.\d{1,2})\s*[~∼\-–]\s*(\d{1,2}\.\d{1,2})\s*%")
_BOK_BASE_RATE_URL = "https://www.bok.or.kr/portal/singl/baseRate/list.do?menuNo=200676"
_BOK_TABLE_RATE_RE = re.compile(
    r"<caption>\s*한국은행\s*기준금리\s*추이\s*</caption>.*?"
    r"<tbody>\s*<tr>\s*"
    r"<td[^>]*>\s*\d{4}\s*</td>\s*"
    r"<td[^>]*>.*?</td>\s*"
    r"<td[^>]*>\s*(\d{1,2}(?:\.\d{1,2})?)\s*</td>",
    re.S,
)
_BOK_CHART_RATE_RE = re.compile(
    r'\[\s*["\'](20\d{2}/\d{2}/\d{2})\s*["\']\s*,\s*(\d{1,2}(?:\.\d{1,2})?)\s*\]'
)


def _fetch_bok_base_rate() -> float | None:
    """한국은행 공식 기준금리 추이 페이지에서 최신 기준금리(%)를 읽는다."""
    try:
        req = urllib.request.Request(
            _BOK_BASE_RATE_URL,
            headers={"User-Agent": "Mozilla/5.0 scanner-macro"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            htm = resp.read().decode("utf-8", "ignore")
        m = _BOK_TABLE_RATE_RE.search(htm)
        if m:
            return _valid("kr_rate", m.group(1))

        pairs = _BOK_CHART_RATE_RE.findall(htm)
        if pairs:
            latest = max(pairs, key=lambda p: p[0])
            return _valid("kr_rate", latest[1])
    except Exception as e:
        _LOG.warning("macro: BOK base rate scrape failed: %s", e)
    return None


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
    return _fetch_bok_base_rate() or _fetch_naver_rate("kr_rate", "한국은행 기준금리")


def _fetch_us_rate() -> float | None:
    return _fetch_naver_rate("us_rate", "미국 기준금리")


# ── 신호등 상수 ───────────────────────────────────────────────────────
_SIG_DANGER  = {"level": "danger",  "emoji": "🔴", "label": "위험"}
_SIG_CAUTION = {"level": "caution", "emoji": "🟡", "label": "주의"}
_SIG_STABLE  = {"level": "stable",  "emoji": "🟢", "label": "안정"}
_SIG_UNKNOWN = {"level": "unknown", "emoji": "⚪", "label": "정보없음"}

# ── 임계값 (macro_gate.py와 동일한 VIX 기준) ─────────────────────────
_VIX_CAUTION   = 20
_VIX_DANGER    = 30
_KRW_CAUTION   = 1430
_KRW_DANGER    = 1480
_US10Y_STRESS  = 3.0   # 10Y 금리 일간 변화율 %
_DXY_STRESS    = 1.0   # DXY 일간 변화율 %


# ── 신호등 계산 ───────────────────────────────────────────────────────
def _signal(vix, usdkrw, us10y_chg=None, dxy_chg=None, vix_prev=None) -> dict:
    """VIX·원달러 + 금리·달러 변화율 기반 시장 신호등."""
    if vix is None and usdkrw is None:
        result = dict(_SIG_UNKNOWN)
        result["trend"] = "stable"
        return result

    # ── 주요 지표 스트레스 점수 (0=안정, 1=주의, 2=위험) ──
    vix_score = 0
    if vix is not None:
        if vix >= _VIX_DANGER: vix_score = 2
        elif vix >= _VIX_CAUTION: vix_score = 1

    krw_score = 0
    if usdkrw is not None:
        if usdkrw > _KRW_DANGER: krw_score = 2
        elif usdkrw > _KRW_CAUTION: krw_score = 1

    # ── 보조 지표 (기수집 데이터 활용) ──
    rate_stress = 1 if (us10y_chg is not None and us10y_chg > _US10Y_STRESS) else 0
    dxy_stress = 1 if (dxy_chg is not None and dxy_chg > _DXY_STRESS) else 0
    aux_count = rate_stress + dxy_stress

    # ── 종합 판정 ──
    primary = max(vix_score, krw_score)

    if primary >= 2:
        result = dict(_SIG_DANGER)
    elif primary >= 1:
        # 주요 주의 + 보조 2개 동시 스트레스 → 위험 격상
        if aux_count >= 2:
            result = dict(_SIG_DANGER)
        else:
            result = dict(_SIG_CAUTION)
    # 주요 안정이지만 보조 2개 동시 스트레스 → 주의
    elif aux_count >= 2:
        result = dict(_SIG_CAUTION)
    else:
        result = dict(_SIG_STABLE)

    # 전환 방향 감지
    trend = "stable"
    if vix is not None and vix_prev is not None and vix_prev > 0:
        vix_chg_pct = (vix - vix_prev) / vix_prev * 100
        if vix_chg_pct > 5:
            trend = "deteriorating"
        elif vix_chg_pct < -5:
            trend = "improving"
    result["trend"] = trend
    return result


_ALL_KEYS = (
    "vix", "vix3m", "skew", "sp500", "nasdaq", "kospi", "usdkrw",
    "kr_rate", "us_rate", "us10y", "dxy",
    "gold", "wti", "btc", "hyg", "lqd",
)


# ── 종가베팅 선행 지표 (Leading Indicators) ──────────────────────────
def _leading_signal(yf_data: dict) -> dict:
    """VIX 텀스트럭처 + SKEW + HY 스프레드 → 종가베팅 안전도 판정.

    Returns:
        {safety: "safe"/"caution"/"danger", reasons: [...],
         vix_term: float|None, skew: float|None, hy_spread_chg: float|None}
    """
    warnings: list[str] = []
    danger_count = 0

    vix_val = (yf_data.get("vix") or {}).get("value")
    vix3m_val = (yf_data.get("vix3m") or {}).get("value")
    skew_val = (yf_data.get("skew") or {}).get("value")
    hyg_data = yf_data.get("hyg") or {}
    lqd_data = yf_data.get("lqd") or {}
    hyg_chg = hyg_data.get("change_pct")
    lqd_chg = lqd_data.get("change_pct")

    # 1) VIX 텀스트럭처: VIX/VIX3M > 1.0 = 백워데이션 = 단기 공포 급증
    vix_term = None
    if vix_val and vix3m_val and vix3m_val > 0:
        vix_term = round(vix_val / vix3m_val, 3)
        if vix_term > 1.05:
            warnings.append(f"VIX 백워데이션 {vix_term:.2f} — 단기 공포 급증")
            danger_count += 2
        elif vix_term > 1.0:
            warnings.append(f"VIX 백워데이션 근접 {vix_term:.2f}")
            danger_count += 1

    # 2) SKEW: >150 꼬리 리스크 고조, >140 경계
    if skew_val is not None:
        if skew_val > 150:
            warnings.append(f"SKEW {skew_val:.0f} — 꼬리 리스크 고조")
            danger_count += 2
        elif skew_val > 140:
            warnings.append(f"SKEW {skew_val:.0f} — 꼬리 리스크 경계")
            danger_count += 1

    # 3) HY 스프레드 프록시: HYG가 LQD보다 크게 하락 = 신용 스트레스
    hy_spread_chg = None
    if hyg_chg is not None and lqd_chg is not None:
        hy_spread_chg = round(hyg_chg - lqd_chg, 2)
        if hy_spread_chg < -1.0:
            warnings.append(f"HY 스프레드 확대 {hy_spread_chg:+.1f}%p — 신용 스트레스")
            danger_count += 2
        elif hy_spread_chg < -0.3:
            warnings.append(f"HY 스프레드 소폭 확대 {hy_spread_chg:+.1f}%p")
            danger_count += 1

    if danger_count >= 3:
        safety = "danger"
    elif danger_count >= 1:
        safety = "caution"
    else:
        safety = "safe"

    return {
        "safety": safety,
        "reasons": warnings,
        "vix_term": vix_term,
        "skew": round(skew_val, 1) if skew_val is not None else None,
        "hy_spread_chg": hy_spread_chg,
    }


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

    vix_data = cell("vix")
    vix = (vix_data or {}).get("value")
    usdkrw = (cell("usdkrw") or {}).get("value")
    us10y_chg = (cell("us10y") or {}).get("change_pct")
    dxy_chg = (cell("dxy") or {}).get("change_pct")

    # VIX 전일 종가 (전환 방향 감지용)
    vix_prev = None
    if vix_data and vix_data.get("change_pct") is not None and vix is not None:
        chg = vix_data["change_pct"]
        if chg != 0:
            vix_prev = vix / (1 + chg / 100)

    indicators = {
        "vix":     cell("vix"),
        "vix3m":   cell("vix3m"),
        "skew":    cell("skew"),
        "sp500":   cell("sp500"),
        "nasdaq":  cell("nasdaq"),
        "kospi":   cell("kospi"),
        "usdkrw":  cell("usdkrw"),
        "us10y":   cell("us10y"),
        "dxy":     cell("dxy"),
        "gold":    cell("gold"),
        "wti":     cell("wti"),
        "btc":     cell("btc"),
        "hyg":     cell("hyg"),
        "lqd":     cell("lqd"),
        "kr_rate": ({"value": kr_rate, "change_pct": None} if kr_rate is not None else None),
        "us_rate": ({"value": us_rate, "change_pct": None} if us_rate is not None else None),
    }
    return {
        "signal": _signal(vix, usdkrw, us10y_chg=us10y_chg, dxy_chg=dxy_chg, vix_prev=vix_prev),
        "leading": _leading_signal(yf_data),
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
            "signal": _SIG_UNKNOWN,
            "indicators": {k: None for k in _ALL_KEYS},
            "ts": datetime.now(_KST).isoformat(timespec="seconds"),
            "stale": True,
        }
