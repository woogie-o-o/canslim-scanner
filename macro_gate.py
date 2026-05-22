# -*- coding: utf-8 -*-
"""거시 레짐 게이트 — VIX 기반 Risk-On/Neutral/Risk-Off 판정."""
from __future__ import annotations
import datetime as dt
import logging
from typing import Dict, Any


log = logging.getLogger(__name__)


def _fetch_vix() -> float | None:
    try:
        import yfinance as yf
        h = yf.Ticker("^VIX").history(period="5d")
        if len(h) == 0:
            return None
        return float(h["Close"].iloc[-1])
    except Exception as e:
        log.warning("VIX fetch failed: %s", e)
        return None


def get_regime() -> Dict[str, Any]:
    """
    Returns:
        {regime, vix, reason, ts}
        regime ∈ {'Risk-On','Neutral','Risk-Off','Unknown'}
    임계값:
        VIX < 20  → Risk-On  (저변동성, 진입 우호)
        20~30     → Neutral  (평상 변동성, 선별 진입)
        ≥ 30      → Risk-Off (고변동성, 진입 보류)
    """
    vix = _fetch_vix()
    ts  = dt.datetime.now().isoformat(timespec="seconds")
    if vix is None:
        return {"regime": "Unknown", "vix": None,
                "reason": "VIX 조회 실패 — 데이터 소스 점검 필요", "ts": ts}
    if vix < 20:
        regime = "Risk-On"
        reason = f"VIX {vix:.1f} — 변동성 낮음, 신규 진입 우호적"
    elif vix < 30:
        regime = "Neutral"
        reason = f"VIX {vix:.1f} — 평상 변동성, 종목 선별 진입"
    else:
        regime = "Risk-Off"
        reason = f"VIX {vix:.1f} — 고변동성, 신규 진입 보류 권장"
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
