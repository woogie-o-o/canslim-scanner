# -*- coding: utf-8 -*-
"""heat_signal.py — 과열/바닥 신호 계산기.

RSI(14) + BB%B + Stochastic%K + MFI(14) → 가중합 점수 (-100 ~ +100)
  +60 이상 → 과열 주의   (hot / red)
  +35 이상 → 강한 모멘텀 (warm / orange)
  -35 이하 → 약한 모멘텀 (cool / light-blue)
  -60 이하 → 과매도 구간 (cold / blue)
  그 외    → 중립        (neutral / gray)
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def compute_heat_signal(hist: pd.DataFrame, market: str = "US") -> dict:
    """OHLCV DataFrame → 과열/바닥 신호 dict.

    Returns:
        score:   float, -100 ~ +100
        label:   str (과열 주의 / 강한 모멘텀 / 중립 / 약한 모멘텀 / 과매도 구간)
        color:   "hot" | "warm" | "neutral" | "cool" | "cold"
        rsi:     0~100
        bb_b:    0~100  (BB%B)
        stoch_k: 0~100  (Stochastic %K)
        mfi:     0~100  (Money Flow Index)
    """
    try:
        close  = hist["Close"]
        high   = hist["High"]
        low    = hist["Low"]
        volume = hist["Volume"]

        # ── RSI(14) ───────────────────────────────────────────────────
        delta = close.diff()
        up  = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        dn  = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        rsi = float((100 - 100 / (1 + up / dn.replace(0, np.nan))).iloc[-1])
        if not np.isfinite(rsi):
            rsi = 50.0

        # ── BB%B ──────────────────────────────────────────────────────
        bb_n  = 15 if market == "KR" else 20
        bb_m  = close.rolling(bb_n).mean()
        bb_s  = close.rolling(bb_n).std(ddof=0)
        bb_u  = bb_m + 2 * bb_s
        bb_l  = bb_m - 2 * bb_s
        raw_b = float(((close - bb_l) / (bb_u - bb_l + 1e-9)).iloc[-1])
        bb_b  = float(np.clip(raw_b, 0.0, 1.0)) * 100   # 0~100

        # ── Stochastic %K ─────────────────────────────────────────────
        st_n    = 5 if market == "KR" else 14
        lo_n    = low.rolling(st_n).min()
        hi_n    = high.rolling(st_n).max()
        raw_k   = float(((close - lo_n) / (hi_n - lo_n + 1e-9)).iloc[-1])
        stoch_k = float(np.clip(raw_k, 0.0, 1.0)) * 100

        # ── MFI(14) ───────────────────────────────────────────────────
        tp    = (high + low + close) / 3
        mf    = tp * volume
        pos_m = mf.where(tp > tp.shift(1), 0.0).rolling(14).sum()
        neg_m = mf.where(tp <= tp.shift(1), 0.0).rolling(14).sum()
        mfi   = float((100 - 100 / (1 + pos_m / neg_m.replace(0, np.nan))).iloc[-1])
        if not np.isfinite(mfi):
            mfi = 50.0

        # ── 가중합 점수 (각 지표를 -100~+100으로 환산) ───────────────
        rsi_s   = (rsi     - 50) * 2   # 50→0, 70→+40, 30→-40
        bb_s    = (bb_b    - 50) * 2   # 50→0, 100→+100, 0→-100
        stoch_s = (stoch_k - 50) * 2
        mfi_s   = (mfi     - 50) * 2

        score = rsi_s * 0.30 + bb_s * 0.25 + stoch_s * 0.25 + mfi_s * 0.20
        score = round(float(np.clip(score, -100.0, 100.0)), 1)

        if score >= 60:
            label, color = "과열 주의", "hot"
        elif score >= 35:
            label, color = "강한 모멘텀", "warm"
        elif score <= -60:
            label, color = "과매도 구간", "cold"
        elif score <= -35:
            label, color = "약한 모멘텀", "cool"
        else:
            label, color = "중립", "neutral"

        return {
            "score":   score,
            "label":   label,
            "color":   color,
            "rsi":     round(rsi, 1),
            "bb_b":    round(bb_b, 1),
            "stoch_k": round(stoch_k, 1),
            "mfi":     round(mfi, 1),
        }
    except Exception as e:
        return {"score": 0.0, "label": "계산 불가", "color": "neutral", "error": str(e)}
