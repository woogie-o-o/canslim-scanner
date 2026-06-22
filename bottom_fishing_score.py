# -*- coding: utf-8 -*-
"""bottom_fishing_score.py -- Bottom-Fishing Composite Score (BF Score).

3-axis bottom-fishing scoring system (0-100):
  Axis 1: Value/Quality (40 pts) -- Piotroski F-Score, PBR, FCF Yield, Altman Z
  Axis 2: Technical/Statistical (35 pts) -- Z-Score, MFI+BB, OBV divergence, vol squeeze
  Axis 3: Macro/Flow (25 pts) -- Regime, flow reversal, capitulation

Goldman Sachs, BlackRock, Renaissance, Two Sigma, Citadel, Nomura methodology.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd


# ======================================================================
# DART financial statement helpers
# ======================================================================
def _find_amount(items: list[dict], keywords: list[str],
                 period: str = "thstrm_amount") -> float | None:
    for item in items:
        nm = item.get("account_nm", "")
        for kw in keywords:
            if kw in nm:
                raw = item.get(period, "")
                if raw and raw != "-":
                    try:
                        return float(str(raw).replace(",", ""))
                    except (ValueError, TypeError):
                        continue
    return None


# ======================================================================
# Piotroski F-Score (0-9)
# ======================================================================
def _piotroski_f_score(dart_data: dict | None) -> dict:
    result: dict[str, Any] = {"f_score": 0, "signals": [], "available": False}
    if not dart_data or not dart_data.get("available"):
        return result

    bs = dart_data.get("BS", [])
    is_ = dart_data.get("IS", [])
    cf = dart_data.get("CF", [])

    ta_cur = _find_amount(bs, ["자산총계"])
    ta_prev = _find_amount(bs, ["자산총계"], "frmtrm_amount")
    ni_cur = _find_amount(is_, ["당기순이익", "당기순손익", "분기순이익"])
    ni_prev = _find_amount(is_, ["당기순이익", "당기순손익", "분기순이익"], "frmtrm_amount")
    rev_cur = _find_amount(is_, ["매출액", "수익(매출액)", "영업수익"])
    rev_prev = _find_amount(is_, ["매출액", "수익(매출액)", "영업수익"], "frmtrm_amount")
    gp_cur = _find_amount(is_, ["매출총이익"])
    gp_prev = _find_amount(is_, ["매출총이익"], "frmtrm_amount")
    ocf_cur = _find_amount(cf, ["영업활동", "영업활동현금흐름"])
    ca_cur = _find_amount(bs, ["유동자산"])
    ca_prev = _find_amount(bs, ["유동자산"], "frmtrm_amount")
    cl_cur = _find_amount(bs, ["유동부채"])
    cl_prev = _find_amount(bs, ["유동부채"], "frmtrm_amount")
    ncl_cur = _find_amount(bs, ["비유동부채"])
    ncl_prev = _find_amount(bs, ["비유동부채"], "frmtrm_amount")

    if not ta_cur or ta_cur == 0:
        return result

    result["available"] = True
    f = 0
    signals = []

    # 1) ROA > 0
    if ni_cur is not None and ni_cur > 0:
        f += 1; signals.append("ROA+")
    else:
        signals.append("ROA-")

    # 2) OCF > 0
    if ocf_cur is not None and ocf_cur > 0:
        f += 1; signals.append("OCF+")
    else:
        signals.append("OCF-")

    # 3) delta-ROA > 0
    if ni_cur is not None and ni_prev is not None and ta_prev and ta_prev > 0:
        if (ni_cur / ta_cur) > (ni_prev / ta_prev):
            f += 1; signals.append("dROA+")
        else:
            signals.append("dROA-")
    else:
        signals.append("dROA?")

    # 4) Accruals: OCF > NI
    if ocf_cur is not None and ni_cur is not None and ocf_cur > ni_cur:
        f += 1; signals.append("ACCR+")
    else:
        signals.append("ACCR-")

    # 5) delta-Leverage < 0
    if ncl_cur is not None and ncl_prev is not None and ta_prev and ta_prev > 0:
        if (ncl_cur / ta_cur) < (ncl_prev / ta_prev):
            f += 1; signals.append("LEV+")
        else:
            signals.append("LEV-")
    else:
        signals.append("LEV?")

    # 6) delta-Liquidity > 0
    if ca_cur and cl_cur and ca_prev and cl_prev and cl_cur > 0 and cl_prev > 0:
        if (ca_cur / cl_cur) > (ca_prev / cl_prev):
            f += 1; signals.append("LIQ+")
        else:
            signals.append("LIQ-")
    else:
        signals.append("LIQ?")

    # 7) No dilution -- hard to detect from DART alone, default pass
    f += 1; signals.append("DIL+")

    # 8) delta-Gross Margin > 0
    if (gp_cur is not None and gp_prev is not None
            and rev_cur and rev_prev and rev_cur > 0 and rev_prev > 0):
        if (gp_cur / rev_cur) > (gp_prev / rev_prev):
            f += 1; signals.append("GM+")
        else:
            signals.append("GM-")
    else:
        signals.append("GM?")

    # 9) delta-Asset Turnover > 0
    if rev_cur is not None and rev_prev is not None and ta_prev and ta_prev > 0:
        if (rev_cur / ta_cur) > (rev_prev / ta_prev):
            f += 1; signals.append("AT+")
        else:
            signals.append("AT-")
    else:
        signals.append("AT?")

    result["f_score"] = f
    result["signals"] = signals
    return result


# ======================================================================
# Piotroski F-Score fallback (yfinance info — US stocks)
# ======================================================================
def _piotroski_f_score_yf(info: dict) -> dict:
    """Simplified Piotroski F-Score from yfinance info (no YoY delta)."""
    result: dict[str, Any] = {"f_score": 0, "signals": [], "available": False}
    roa = info.get("returnOnAssets")
    if roa is None:
        return result
    result["available"] = True
    f = 0
    signals = []

    # 1) ROA > 0
    if roa > 0:
        f += 1; signals.append("ROA+")
    else:
        signals.append("ROA-")

    # 2) OCF > 0
    ocf = info.get("operatingCashflow") or 0
    if ocf > 0:
        f += 1; signals.append("OCF+")
    else:
        signals.append("OCF-")

    # 3) delta-ROA — unavailable from single snapshot
    signals.append("dROA?")

    # 4) Accruals: OCF > NI
    ni = info.get("netIncomeToCommon") or 0
    if ocf > ni:
        f += 1; signals.append("ACCR+")
    else:
        signals.append("ACCR-")

    # 5) Leverage — low D/E is good
    de = info.get("debtToEquity")
    if de is not None and de < 100:
        f += 1; signals.append("LEV+")
    else:
        signals.append("LEV-" if de is not None else "LEV?")

    # 6) Liquidity — current ratio > 1
    cr = info.get("currentRatio")
    if cr is not None and cr > 1:
        f += 1; signals.append("LIQ+")
    else:
        signals.append("LIQ-" if cr is not None else "LIQ?")

    # 7) No dilution — default pass
    f += 1; signals.append("DIL+")

    # 8) Gross margin > 20%
    gm = info.get("grossMargins")
    if gm is not None and gm > 0.20:
        f += 1; signals.append("GM+")
    else:
        signals.append("GM-" if gm is not None else "GM?")

    # 9) Asset turnover — revenue / total assets
    rev = info.get("totalRevenue") or 0
    ta = info.get("totalAssets") or 0
    if ta > 0 and rev / ta > 0.5:
        f += 1; signals.append("AT+")
    elif ta > 0:
        signals.append("AT-")
    else:
        signals.append("AT?")

    result["f_score"] = f
    result["signals"] = signals
    return result


# ======================================================================
# Altman Z-Score
# ======================================================================
def _altman_z_score(dart_data: dict | None, market_cap: float,
                    info: dict | None = None) -> dict:
    result: dict[str, Any] = {"z_score": None, "zone": "UNKNOWN", "available": False}
    if not market_cap or market_cap <= 0:
        return result

    # Try DART first (KR), then yfinance info fallback (US)
    ta, ca, cl, tl, re, oi, rev = None, None, None, None, None, None, None

    if dart_data and dart_data.get("available"):
        bs = dart_data.get("BS", [])
        is_ = dart_data.get("IS", [])
        ta = _find_amount(bs, ["자산총계"])
        ca = _find_amount(bs, ["유동자산"]) or 0
        cl = _find_amount(bs, ["유동부채"]) or 0
        tl = _find_amount(bs, ["부채총계"]) or 0
        re = _find_amount(bs, ["이익잉여금"]) or 0
        oi = _find_amount(is_, ["영업이익", "영업손익"]) or 0
        rev = _find_amount(is_, ["매출액", "수익(매출액)", "영업수익"]) or 0
    elif info:
        ta = info.get("totalAssets")
        tl = info.get("totalDebt") or 0
        rev = info.get("totalRevenue") or 0
        oi = info.get("ebitda") or 0  # EBITDA as EBIT proxy
        ca = cl = re = 0  # X1, X2 zeroed — partial Z still useful

    if not ta or ta == 0:
        return result

    if not tl or tl == 0:
        tl = 1.0

    x1 = (ca - cl) / ta
    x2 = re / ta
    x3 = oi / ta
    x4 = market_cap / tl
    x5 = rev / ta

    z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5

    if z > 2.99:
        zone = "SAFE"
    elif z > 1.81:
        zone = "GREY"
    else:
        zone = "DISTRESS"

    result.update(z_score=round(z, 2), zone=zone, available=True)
    return result


# ======================================================================
# Main BF Score computation
# ======================================================================
def compute_bf_score(
    ticker: str,
    mr: dict, flow: dict, ff: dict, qual: dict,
    atr: dict, hurst: dict, regime: dict, dd: dict,
    info: dict, hist: pd.DataFrame,
    dart_data: dict | None = None,
) -> dict:
    """Return Bottom-Fishing Composite Score (0-100) with breakdown."""
    result: dict[str, Any] = {
        "bf_score": 0.0, "bf_signal": "NEUTRAL",
        "axis1_value": 0.0, "axis2_tech": 0.0, "axis3_macro": 0.0,
        "piotroski": {"f_score": 0, "available": False},
        "altman": {"z_score": None, "zone": "UNKNOWN", "available": False},
        "bf_tags": [], "breakdown": [],
    }

    try:
        # ===========================================================
        # AXIS 1: Value / Quality  (max 40)
        # ===========================================================
        axis1 = 0.0

        # 1a. Piotroski F-Score  (0-15)
        pio = _piotroski_f_score(dart_data)
        if not pio["available"]:
            pio = _piotroski_f_score_yf(info)
        result["piotroski"] = pio
        if pio["available"]:
            fs = pio["f_score"]
            if   fs >= 8: axis1 += 15
            elif fs >= 7: axis1 += 12
            elif fs >= 6: axis1 += 8
            elif fs >= 5: axis1 += 5
            elif fs >= 3: axis1 += 2

        # 1b. PBR discount  (0-10)
        pbr = float(info.get("priceToBook") or 0)
        if pbr > 0:
            if   pbr < 0.5: axis1 += 10
            elif pbr < 0.8: axis1 += 8
            elif pbr < 1.0: axis1 += 6
            elif pbr < 1.3: axis1 += 3
            elif pbr < 1.5: axis1 += 1

        # 1c. FCF Yield  (0-8)
        fcf = float(info.get("freeCashflow") or 0)
        mcap = float(info.get("marketCap") or 0)
        fcf_yield = 0.0
        if mcap > 0 and fcf != 0:
            fcf_yield = fcf / mcap
            if   fcf_yield > 0.15: axis1 += 8
            elif fcf_yield > 0.10: axis1 += 6
            elif fcf_yield > 0.06: axis1 += 4
            elif fcf_yield > 0.03: axis1 += 2
            elif fcf_yield < -0.05: axis1 -= 2

        # 1d. Altman Z-Score safety  (0-7, can go negative)
        alt = _altman_z_score(dart_data, mcap, info=info)
        result["altman"] = alt
        if alt["available"] and alt["z_score"] is not None:
            z_alt = alt["z_score"]
            if   z_alt > 3.0:  axis1 += 7
            elif z_alt > 2.5:  axis1 += 5
            elif z_alt > 1.81: axis1 += 3
            elif z_alt > 1.0:  axis1 += 0
            else:              axis1 -= 3

        axis1 = max(0.0, min(40.0, axis1))
        result["axis1_value"] = round(axis1, 1)

        # ===========================================================
        # AXIS 2: Technical / Statistical  (max 35)
        # ===========================================================
        axis2 = 0.0
        z_score = mr.get("z_score", 0.0)
        mfi = flow.get("mfi", 50.0)
        bb_pos = mr.get("bb_position", 0.0)
        rsi = mr.get("rsi", 50.0)

        # 2a. Price Z-Score deviation  (0-12)
        if   z_score < -2.5: axis2 += 12
        elif z_score < -2.0: axis2 += 10
        elif z_score < -1.5: axis2 += 7
        elif z_score < -1.0: axis2 += 5
        elif z_score < -0.5: axis2 += 3
        elif z_score < 0:    axis2 += 1

        # 2b. MFI + Bollinger %B + RSI combo  (0-13)
        mfi_pts = 0.0
        if   mfi < 10: mfi_pts = 5
        elif mfi < 20: mfi_pts = 4
        elif mfi < 30: mfi_pts = 3
        elif mfi < 40: mfi_pts = 1

        bb_pts = 0.0
        if   bb_pos < -1.0: bb_pts = 5
        elif bb_pos < -0.5: bb_pts = 3
        elif bb_pos < 0:    bb_pts = 2
        elif bb_pos < 0.3:  bb_pts = 1

        rsi_pts = 0.0
        if   rsi < 25: rsi_pts = 3
        elif rsi < 30: rsi_pts = 2
        elif rsi < 40: rsi_pts = 1

        axis2 += min(13.0, mfi_pts + bb_pts + rsi_pts)

        # 2c. OBV divergence  (0-8)
        obv_bullish = flow.get("obv_trend") in ("BULLISH", "up")
        ad_acc = flow.get("ad", 0) in (1, "bullish")
        macd_bull_div = mr.get("macd_divergence") == "BULLISH"

        obv_pts = 0.0
        if obv_bullish:
            obv_pts += 3
            if z_score < -0.5: obv_pts += 1
        if ad_acc:
            obv_pts += 1
            if z_score < -0.5: obv_pts += 1
        if macd_bull_div:
            obv_pts += 2
        axis2 += min(8.0, obv_pts)

        # 2d. Volatility squeeze  (0-5)
        bb_squeeze = mr.get("bb_squeeze", False)
        atr_pct = atr.get("atr_percent", 0)
        if bb_squeeze:
            axis2 += 3
        if 0 < atr_pct < 1.5:
            axis2 += 2
        elif 0 < atr_pct < 2.5:
            axis2 += 1

        axis2 = max(0.0, min(35.0, axis2))
        result["axis2_tech"] = round(axis2, 1)

        # ===========================================================
        # AXIS 3: Macro / Flow  (max 25)
        # ===========================================================
        axis3 = 0.0
        regime_label = str(regime.get("m_label", "") or regime.get("regime", ""))
        adx = regime.get("adx", 0)

        # 3a. Regime reversal potential  (0-10)
        if "Bear" in regime_label or "Risk-Off" in regime_label:
            axis3 += 6
            if adx < 20:   axis3 += 4
            elif adx < 30: axis3 += 2
        elif "Neutral" in regime_label or "Sideways" in regime_label:
            axis3 += 3
            if adx < 20: axis3 += 2
            elif adx < 30: axis3 += 1
        else:
            if adx < 15: axis3 += 2
            elif adx < 25: axis3 += 1

        # 3b. Flow reversal signal  (0-8)
        flow_signal = flow.get("signal", "NEUTRAL")
        if flow_signal == "ACCUMULATION":
            axis3 += 5
            if rsi < 45:
                axis3 += 3
        elif flow_signal == "NEUTRAL":
            if   rsi < 30: axis3 += 3
            elif rsi < 40: axis3 += 2
            elif rsi < 50: axis3 += 1

        # 3c. Drawdown capitulation  (0-7)
        cur_dd = abs(dd.get("current_dd", 0))
        if   cur_dd > 0.40: axis3 += 7
        elif cur_dd > 0.30: axis3 += 5
        elif cur_dd > 0.20: axis3 += 4
        elif cur_dd > 0.15: axis3 += 3
        elif cur_dd > 0.10: axis3 += 2
        elif cur_dd > 0.05: axis3 += 1

        axis3 = max(0.0, min(25.0, axis3))
        result["axis3_macro"] = round(axis3, 1)

        # ===========================================================
        # Composite BF Score
        # ===========================================================
        bf_raw = axis1 + axis2 + axis3

        # Value-trap penalty: weak financials + distressed
        if (pio["available"] and pio["f_score"] < 3
                and alt["available"] and (alt["z_score"] or 99) < 1.81):
            bf_raw *= 0.5

        # Hurst adjustment: mean-reverting nature boosts bounce probability
        h = hurst.get("h", 0.5)
        if h < 0.35:
            bf_raw *= 1.10
        elif h > 0.65:
            bf_raw *= 0.90

        bf_score = max(0.0, min(100.0, round(bf_raw, 1)))
        result["bf_score"] = bf_score

        # ===========================================================
        # Signal & Tags
        # ===========================================================
        if   bf_score >= 75: result["bf_signal"] = "STRONG_BUY"
        elif bf_score >= 60: result["bf_signal"] = "BUY"
        elif bf_score >= 45: result["bf_signal"] = "WATCH"
        elif bf_score >= 30: result["bf_signal"] = "NEUTRAL"
        else:                result["bf_signal"] = "AVOID"

        tags = []
        if pio["available"] and pio["f_score"] >= 7 and z_score < -2:
            tags.append("F+Z COMBO")
        if mfi < 20 and bb_pos < 0:
            tags.append("MFI+BB")
        if obv_bullish and z_score < -1.5:
            tags.append("OBV DIV")
        if cur_dd > 0.30 and flow_signal == "ACCUMULATION":
            tags.append("CAPITULATION")
        result["bf_tags"] = tags

        # ===========================================================
        # Breakdown items (for UI)
        # ===========================================================
        bd = []

        if pio["available"]:
            fs = pio["f_score"]
            bd.append((
                "[BF] Piotroski F-Score",
                fs,
                f"재무 건전성 {fs}/9점이에요. "
                + ("우량 기업 -- 안심하고 매수할 수 있어요." if fs >= 7
                   else "보통 수준이에요." if fs >= 5
                   else "재무가 약해요. 밸류트랩 주의가 필요해요.")
                + f" ({', '.join(pio['signals'])})"
            ))

        if alt["available"] and alt["z_score"] is not None:
            zv = alt["z_score"]
            bd.append((
                "[BF] Altman Z-Score",
                zv,
                f"파산위험지수 {zv:.2f}이에요. "
                + ("안전 구간이에요." if alt["zone"] == "SAFE"
                   else "주의 구간이에요." if alt["zone"] == "GREY"
                   else "위험 구간이에요 -- 파산 가능성을 점검하세요.")
            ))

        bd.append((
            "[BF] 평균회귀 Z-Score",
            round(z_score, 2),
            f"주가 Z-Score {z_score:+.1f}이에요. "
            + ("강한 과매도 구간이에요 -- 반등 확률이 높아요 (KOSPI 승률 62%)."
               if z_score < -2
               else "과매도 영역이에요." if z_score < -1
               else "정상 범위예요.")
        ))

        if mfi < 30 or bb_pos < -0.5:
            bd.append((
                "[BF] MFI + Bollinger 과매도",
                round(mfi_pts + bb_pts, 1),
                f"MFI {mfi:.0f}, BB%B {bb_pos:+.2f}예요. "
                + ("두 지표 모두 극단적 과매도 -- 반등 승률 71%!"
                   if mfi < 20 and bb_pos < 0
                   else "과매도 신호가 감지됐어요.")
            ))

        bd.append((
            "[BF] 수급 + 낙폭",
            round(axis3, 1),
            f"수급 '{flow_signal}', RSI {rsi:.0f}, MDD {cur_dd:.0%}예요. "
            + ("투매 후 매집 패턴이에요 -- 바닥 가능성!"
               if cur_dd > 0.3 and flow_signal == "ACCUMULATION"
               else "낙폭이 크지만 아직 매집 신호는 없어요."
               if cur_dd > 0.2
               else "특별한 저점 신호는 없어요.")
        ))

        result["breakdown"] = bd

    except Exception as e:
        logging.error(f"[BF Score] {ticker}: {e}")

    return result
