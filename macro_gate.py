# -*- coding: utf-8 -*-
"""거시 레짐 게이트 — VIX 기반 Risk-On/Neutral/Risk-Off 판정."""
from __future__ import annotations
import datetime as dt
import logging
from typing import Dict, Any


log = logging.getLogger(__name__)


_VIX_CACHE: Dict[str, Any] = {"v": None, "ts": 0.0}
_VIX_TTL_SEC = 1800  # 30분


def _fetch_vix() -> float | None:
    import time
    now = time.time()
    c = _VIX_CACHE
    if c["v"] is not None and (now - c["ts"]) < _VIX_TTL_SEC:
        return c["v"]
    try:
        import yfinance as yf
        h = yf.Ticker("^VIX").history(period="5d")
        if len(h) == 0:
            return None
        v = float(h["Close"].iloc[-1])
        c["v"], c["ts"] = v, now
        return v
    except Exception as e:
        log.warning("VIX fetch failed: %s", e)
        return None


_VKOSPI_CACHE: Dict[str, Any] = {"v": None, "ts": 0.0}
_VKOSPI_TTL_SEC = 1800  # 30분 (일중 변동값)
_INVESTING_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _fetch_vkospi() -> float | None:
    """코스피200 변동성지수(VKOSPI) 현재값 — investing.com 스크래핑 + 30분 캐시.

    yfinance/네이버/KRX 모두 VKOSPI 미지원(2026-06 검증)이라 investing.com을 사용한다.
    파싱: ① HTML 속성 instrument-price-last (메인 현재가) ② __NEXT_DATA__ JSON last+high.
    """
    import time
    now = time.time()
    c = _VKOSPI_CACHE
    if c["v"] is not None and (now - c["ts"]) < _VKOSPI_TTL_SEC:
        return c["v"]
    html = _fetch_investing_html("https://kr.investing.com/indices/kospi-volatility")
    if not html:
        return None
    try:
        import re
        val = None
        m = re.search(r'instrument-price-last"[^>]*>\s*([0-9][0-9.,]*)', html)
        if m:
            val = float(m.group(1).replace(",", ""))
        else:
            # __NEXT_DATA__ 메인 블록: "last":X,...,"high" 인접 패턴으로 특정
            m2 = re.search(r'"last"\s*:\s*([0-9]+\.[0-9]+)\s*,\s*"high"', html)
            if m2:
                val = float(m2.group(1))
        if val and val > 0:
            c["v"], c["ts"] = val, now
            return val
        log.warning("VKOSPI parse failed (no value matched)")
    except Exception as e:
        log.warning("VKOSPI parse failed: %s", e)
    return None


def _fetch_investing_html(url: str) -> str | None:
    """investing.com HTML 취득. requests는 TLS fingerprint로 403 차단되므로
    curl_cffi(브라우저 TLS 임퍼소네이트) → subprocess curl 순으로 폴백한다.
    """
    # 1순위: curl_cffi (Chrome TLS 임퍼소네이트)
    try:
        from curl_cffi import requests as _cr
        r = _cr.get(url, impersonate="chrome", timeout=15)
        if r.status_code == 200 and r.text:
            return r.text
        log.warning("VKOSPI curl_cffi status=%s", r.status_code)
    except ImportError:
        pass
    except Exception as e:
        log.warning("VKOSPI curl_cffi failed: %s", e)
    # 2순위: subprocess curl
    try:
        import subprocess
        out = subprocess.run(
            ["curl", "-s", "-m", "15", "-A", _INVESTING_UA, url],
            capture_output=True, timeout=20,
        )
        if out.returncode == 0 and out.stdout:
            return out.stdout.decode("utf-8", "replace")
        log.warning("VKOSPI curl rc=%s", out.returncode)
    except Exception as e:
        log.warning("VKOSPI curl failed: %s", e)
    return None


def get_vol_index(market: str) -> Dict[str, Any]:
    """시장별 변동성 지수 → {source, level} 또는 {source:None}.

    KR → VKOSPI, US → VIX. 연율 변동성 수준값(level)을 반환한다.
    실패 시 {"source": None, "level": None} (호출측이 종목 RV/ATR로 폴백).
    """
    mkt = (market or "US").upper()
    if mkt == "KR":
        v = _fetch_vkospi()
        return {"source": "VKOSPI", "level": v} if v else {"source": None, "level": None}
    v = _fetch_vix()
    return {"source": "VIX", "level": v} if v else {"source": None, "level": None}


def get_regime() -> Dict[str, Any]:
    """
    Returns:
        {regime, vix, reason, ts}
        regime ∈ {'Risk-On','Neutral','Risk-Off','Unknown'}
    임계값:
        VIX < 20  → Risk-On  (저변동성, 우호적 환경)
        20~30     → Neutral  (평상 변동성, 선별 구간)
        ≥ 30      → Risk-Off (고변동성, 관망 구간)
    """
    vix = _fetch_vix()
    ts  = dt.datetime.now().isoformat(timespec="seconds")
    if vix is None:
        return {"regime": "Unknown", "vix": None,
                "reason": "VIX 조회 실패 — 데이터 소스 점검 필요", "ts": ts}
    if vix < 20:
        regime = "Risk-On"
        reason = f"VIX {vix:.1f} — 변동성 낮음, 우호적 환경"
    elif vix < 30:
        regime = "Neutral"
        reason = f"VIX {vix:.1f} — 평상 변동성, 종목 선별 구간"
    else:
        regime = "Risk-Off"
        reason = f"VIX {vix:.1f} — 고변동성, 관망 구간"
    return {"regime": regime, "vix": round(vix, 2), "reason": reason, "ts": ts}


def build_banner_text(state: Dict[str, Any]) -> str:
    icon = {"Risk-On": "🟢", "Neutral": "🟡", "Risk-Off": "🔴", "Unknown": "⚪"}\
           .get(state.get("regime", "Unknown"), "⚪")
    return f"{icon} 시장 레짐: {state.get('regime')}  ·  {state.get('reason','')}"


def build_banner_style(state: Dict[str, Any]) -> dict:
    bg = {"Risk-On": "#E8F5E9", "Neutral": "#FFF8E1",
          "Risk-Off": "#FFEBEE", "Unknown": "#F5F5F5"}\
         .get(state.get("regime", "Unknown"), "#F5F5F5")
    fg = "#191919"
    return {"bg": bg, "fg": fg, "font": ("Segoe UI", 9, "bold"),
            "padx": 10, "pady": 4}


if __name__ == "__main__":
    s = get_regime()
    print(build_banner_text(s))
    assert s["regime"] in {"Risk-On", "Neutral", "Risk-Off", "Unknown"}
    assert "vix" in s
    print("[OK] macro_gate:", s)
