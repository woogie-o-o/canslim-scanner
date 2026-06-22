"""Classify a stock's current entry setup from an existing OHLCV frame."""
from __future__ import annotations

import numpy as np
import pandas as pd

SCENARIOS = {
    "CLIMAX": ("클라이맥스 매도", "진입 금지 - 단기 반전 대기", "EXTREME"),
    "EXHAUSTION": ("추세 소진", "관망 - 거래량 회복 확인", "HIGH"),
    "DOWNTREND": ("하락 추세", "관망 - 추세 전환 확인", "HIGH"),
    "PULLBACK_RISKY": ("위험한 눌림", "지지 확인 전 진입 보류", "HIGH"),
    "OVERBOUGHT": ("과매수 과열", "MA20 회귀 후 재판단", "HIGH"),
    "BASE": ("베이스 구축 중", "거래량 동반 돌파 대기", "LOW"),
    "RECOVERY": ("반등 초기", "소량 분할 후 추세 확인", "MED"),
    "NEUTRAL": ("중립", "방향 확인 후 진입", "MED"),
    "BREAKOUT": ("돌파 진행 중", "돌파 직후 소량 진입 가능", "MED"),
    "TREND_STRONG": ("강한 추세 중", "MA20 지지 눌림목 우선", "MED"),
    "PULLBACK_HEALTHY": ("건전한 눌림", "거래량 감소가 끝나는 지점 주목", "LOW"),
}


def compute_technicals(hist: pd.DataFrame) -> dict:
    frame = hist[["Close", "High", "Low", "Volume"]].copy()
    frame = frame.apply(pd.to_numeric, errors="coerce").dropna(subset=["Close"])
    if len(frame) < 20:
        raise ValueError("entry judge requires at least 20 rows")

    close = frame["Close"].astype(float)
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    volume = frame["Volume"].astype(float).fillna(0)
    returns = close.pct_change()
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    true_range = np.maximum(
        high - low,
        np.maximum((high - close.shift(1)).abs(), (low - close.shift(1)).abs()),
    )
    rvol = returns.rolling(20).std() * np.sqrt(252)
    dd60 = close / close.rolling(60, min_periods=20).max() - 1
    vol20 = volume.rolling(20).mean()
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2])

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)

    streak_up = streak_down = 0
    for value in returns.iloc[-1:-8:-1]:
        if np.isfinite(value) and value > 0:
            streak_up += 1
        else:
            break
    for value in returns.iloc[-1:-8:-1]:
        if np.isfinite(value) and value < 0:
            streak_down += 1
        else:
            break

    ma20_value = float(ma20.iloc[-1])
    ma60_value = float(ma60.iloc[-1]) if np.isfinite(ma60.iloc[-1]) else np.nan
    return {
        "close": last,
        "chg1d": last / prev - 1,
        "chg5d": last / float(close.iloc[-6]) - 1 if len(close) >= 6 else np.nan,
        "ma5": float(ma5.iloc[-1]),
        "ma20": ma20_value,
        "ma60": ma60_value,
        "ma20_dev": last / ma20_value - 1,
        "ma60_dev": last / ma60_value - 1 if np.isfinite(ma60_value) else np.nan,
        "atr": float(true_range.rolling(14).mean().iloc[-1]),
        "rvol": float(rvol.iloc[-1]),
        "dd60": float(dd60.iloc[-1]),
        "vol_ratio": float(volume.iloc[-1] / vol20.iloc[-1]) if vol20.iloc[-1] else 0,
        "vol_trend": float(volume.iloc[-5:].mean() / vol20.iloc[-1] - 1) if vol20.iloc[-1] else 0,
        "rsi": float(rsi.iloc[-1]) if np.isfinite(rsi.iloc[-1]) else 50.0,
        "streak_up": streak_up,
        "streak_down": streak_down,
    }


def classify(tc: dict) -> tuple[str, list[str]]:
    d60 = tc["ma60_dev"]
    d20 = tc["ma20_dev"]
    chg5 = tc["chg5d"] if np.isfinite(tc["chg5d"]) else 0.0
    volume_ratio = tc["vol_ratio"]
    volume_trend = tc["vol_trend"]
    rsi = tc["rsi"]
    ma5, ma20, ma60 = tc["ma5"], tc["ma20"], tc["ma60"]
    bull = ma5 > ma20 > ma60 if np.isfinite(ma60) else ma5 > ma20
    bear = ma5 < ma20 < ma60 if np.isfinite(ma60) else ma5 < ma20
    extreme = (np.isfinite(d60) and d60 > 0.45) or chg5 > 0.20
    moderate = not extreme and ((np.isfinite(d60) and d60 > 0.15) or chg5 > 0.08)
    divergence = chg5 > 0.01 and volume_trend < -0.20

    if extreme and volume_ratio > 3 and tc["streak_up"] >= 4:
        return "CLIMAX", [f"5일 {chg5:+.1%} 급등", f"거래량 {volume_ratio:.1f}배 폭발"]
    if extreme and divergence:
        return "EXHAUSTION", [f"MA60 대비 {d60:+.1%}", "가격 상승 중 거래량 이탈"]
    if extreme:
        return "OVERBOUGHT", [f"5일 {chg5:+.1%}", "단기 평균 대비 과도한 이격"]
    if tc["dd60"] > -0.03 and volume_ratio > 2 and bull:
        return "BREAKOUT", ["60일 신고가권", f"거래량 {volume_ratio:.1f}배", "이평선 정배열"]
    if bull and volume_trend > -0.20 and moderate:
        return "TREND_STRONG", ["이평선 정배열", f"거래량 추세 {volume_trend:+.0%}"]
    if chg5 < -0.03 and volume_trend < -0.15 and not bear:
        return "PULLBACK_HEALTHY", [f"5일 {chg5:+.1%} 조정", "거래량 감소로 매도 압력 제한"]
    if chg5 < -0.03 and volume_trend > 0.15:
        return "PULLBACK_RISKY", [f"5일 {chg5:+.1%} 하락", "거래량이 실린 하락"]
    if bear and chg5 < 0:
        return "DOWNTREND", ["이평선 역배열", f"5일 {chg5:+.1%}"]
    if tc["rvol"] < 0.30 and abs(d20) < 0.05:
        return "BASE", [f"실현변동성 {tc['rvol']:.0%}", "MA20 근처 에너지 축적"]
    if not bull and chg5 > 0.03 and (not np.isfinite(d60) or d60 < 0.05):
        return "RECOVERY", ["하락 추세 중 단기 반등", "추세 전환 확인 필요"]
    return "NEUTRAL", [f"RSI {rsi:.0f}", f"MA20 대비 {d20:+.1%}", f"5일 {chg5:+.1%}"]


def apply_regime(scenario: str, regime_state: str | None) -> tuple[str, bool]:
    if regime_state not in ("BEAR", "high_vol_downtrend"):
        return scenario, False
    downgraded = {
        "BREAKOUT": "OVERBOUGHT",
        "TREND_STRONG": "OVERBOUGHT",
        "PULLBACK_HEALTHY": "NEUTRAL",
    }.get(scenario)
    return (downgraded, True) if downgraded else (scenario, False)


def build_judgment(hist: pd.DataFrame, regime_state: str | None = None) -> dict:
    technicals = compute_technicals(hist)
    scenario, reasons = classify(technicals)
    scenario, downgraded = apply_regime(scenario, regime_state)
    label, timing, risk = SCENARIOS[scenario]
    return {
        "scenario": scenario,
        "label": label,
        "timing": timing,
        "risk": risk,
        "reasons": reasons,
        "regime_downgraded": downgraded,
    }
