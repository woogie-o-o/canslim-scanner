from __future__ import annotations

import re
import time
import logging

try:
    from curl_cffi import requests as cffi_requests
except Exception:  # pragma: no cover - optional anti-bot client
    import requests as cffi_requests

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 4 * 3600

try:
    _session = cffi_requests.Session(impersonate="chrome120")
except TypeError:
    _session = cffi_requests.Session()

_KR_PATTERN = re.compile(r"^\d{6}(\.KS|\.KQ)?$", re.IGNORECASE)

# DevTools에서 발굴한 실제 엔드포인트로 교체 필요
_TK_BASE_URL = "https://api.tradingkey.com/v1"

_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.tradingkey.com/",
    # DevTools에서 발견한 인증 헤더 추가
}


def is_kr_ticker(ticker: str) -> bool:
    return bool(_KR_PATTERN.match(ticker.strip()))


def _fetch_raw(ticker: str) -> dict:
    """TradingKey API 실제 호출. DevTools에서 발굴한 엔드포인트 사용."""
    url = f"{_TK_BASE_URL}/stock/{ticker}/overview"
    resp = _session.get(url, headers=_DEFAULT_HEADERS, timeout=3)
    resp.raise_for_status()
    raw = resp.json()
    return _parse_response(raw)


def _parse_response(raw: dict) -> dict:
    """TradingKey API 응답을 표준 스키마로 변환."""
    return {
        "score": _parse_score(raw),
        "institutional": _parse_institutional(raw),
        "analyst": _parse_analyst(raw),
        "valuation": _parse_valuation(raw),
        "fundamentals": _parse_fundamentals(raw),
        "risk_technical": _parse_risk_technical(raw),
        "performance": _parse_performance(raw),
    }


def _parse_score(raw: dict) -> dict:
    s = raw.get("score", raw.get("scoreInfo", {}))
    return {
        "overall": int(s.get("overall", 0)),
        "valuation": int(s.get("valuation", 0)),
        "growth": int(s.get("growth", 0)),
        "profitability": int(s.get("profitability", 0)),
        "momentum": int(s.get("momentum", 0)),
        "risk": int(s.get("risk", 0)),
        "industry_rank": int(s.get("industry_rank", 0)),
        "industry_total": int(s.get("industry_total", 0)),
        "overall_rank": int(s.get("overall_rank", 0)),
        "overall_total": int(s.get("overall_total", 0)),
        "sector_percentile": float(s.get("sector_percentile", 0)),
    }


def _parse_institutional(raw: dict) -> dict:
    i = raw.get("institutional", raw.get("institutionInfo", {}))
    return {
        "confidence_score": float(i.get("confidence_score", 0)),
        "holding_pct": float(i.get("holding_pct", 0)),
        "holding_qoq": float(i.get("holding_qoq", 0)),
        "top_holder": str(i.get("top_holder", "")),
        "top_holder_pct": float(i.get("top_holder_pct", 0)),
        "top_holder_chg": float(i.get("top_holder_chg", 0)),
    }


def _parse_analyst(raw: dict) -> dict:
    a = raw.get("analyst", raw.get("analystInfo", {}))
    return {
        "consensus": str(a.get("consensus", "Hold")),
        "target_price": float(a.get("target_price", 0)),
        "upside_pct": float(a.get("upside_pct", 0)),
        "analyst_count": int(a.get("analyst_count", 0)),
        "buy_count": int(a.get("buy_count", 0)),
        "hold_count": int(a.get("hold_count", 0)),
        "sell_count": int(a.get("sell_count", 0)),
    }


def _parse_valuation(raw: dict) -> dict:
    v = raw.get("valuation", raw.get("valuationInfo", {}))
    return {
        "pe_ttm": float(v.get("pe_ttm", 0)),
        "pe_dynamic": float(v.get("pe_dynamic", 0)),
        "pe_static": float(v.get("pe_static", 0)),
        "pb": float(v.get("pb", 0)),
        "eps_ttm": float(v.get("eps_ttm", 0)),
        "market_cap": float(v.get("market_cap", 0)),
    }


def _parse_fundamentals(raw: dict) -> dict:
    f = raw.get("fundamentals", raw.get("fundamentalInfo", {}))
    return {
        "roe": float(f.get("roe", 0)),
        "roa": float(f.get("roa", 0)),
        "gross_margin": float(f.get("gross_margin", 0)),
        "net_profit": float(f.get("net_profit", 0)),
        "dividend_yield": float(f.get("dividend_yield", 0)),
        "payout_ratio": float(f.get("payout_ratio", 0)),
    }


def _parse_risk_technical(raw: dict) -> dict:
    r = raw.get("risk_technical", raw.get("riskInfo", {}))
    return {
        "beta": float(r.get("beta", 1.0)),
        "risk_rate": float(r.get("risk_rate", 0)),
        "reward_risk": float(r.get("reward_risk", 0)),
        "support": float(r.get("support", 0)),
        "resistance": float(r.get("resistance", 0)),
        "volume_ratio": float(r.get("volume_ratio", 1.0)),
        "amplitude": float(r.get("amplitude", 0)),
        "turnover_ratio": float(r.get("turnover_ratio", 0)),
    }


def _parse_performance(raw: dict) -> dict:
    p = raw.get("performance", raw.get("performanceInfo", {}))
    return {
        "1d": float(p.get("1d", 0)),
        "5d": float(p.get("5d", 0)),
        "1m": float(p.get("1m", 0)),
        "6m": float(p.get("6m", 0)),
        "ytd": float(p.get("ytd", 0)),
        "1y": float(p.get("1y", 0)),
    }


def get_tradingkey_data(ticker: str) -> dict | None:
    if is_kr_ticker(ticker):
        return None
    cached = _cache.get(ticker)
    if cached and time.time() - cached[1] < _CACHE_TTL:
        return cached[0]
    try:
        raw = _fetch_raw(ticker)
        data = {**raw, "_cached_at": time.time(), "_source": "tradingkey"}
        _cache[ticker] = (data, time.time())
        return data
    except Exception as e:
        logger.warning(f"TradingKey fetch failed for {ticker}: {e}")
        return None


def get_score(ticker: str) -> dict | None:
    if is_kr_ticker(ticker):
        return None
    data = get_tradingkey_data(ticker)
    if data is None:
        return None
    return data.get("score")


def get_support_resistance(ticker: str) -> tuple[float, float] | None:
    if is_kr_ticker(ticker):
        return None
    data = get_tradingkey_data(ticker)
    if data is None:
        return None
    rt = data.get("risk_technical", {})
    s = rt.get("support")
    r = rt.get("resistance")
    if s and r:
        return (float(s), float(r))
    return None
