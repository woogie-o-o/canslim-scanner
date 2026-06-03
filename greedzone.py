# -*- coding: utf-8 -*-
"""GreedZone indicator — Pine Script (Zeiierman) → Python 재구현.

트레이딩뷰 GreedZone 지표를 서버측에서 직접 계산하여
yfinance OHLCV 데이터만으로 greed 구간 진입을 감지한다.

원본: GreedZone indicator (Zeiierman) - Contrarian Indicator
라이선스: CC BY-NC-SA 4.0
"""

import numpy as np
import pandas as pd


# ── 이동평균 함수 ──────────────────────────────────────────────────────

def _wma(series: pd.Series, period: int) -> pd.Series:
    """Weighted Moving Average (Pine Script ta.wma 동일)."""
    weights = np.arange(1, period + 1, dtype=float)
    return series.rolling(period).apply(
        lambda x: np.dot(x, weights) / weights.sum(), raw=True
    )


def _ma(series: pd.Series, period: int, matype: str = "WMA") -> pd.Series:
    if matype == "SMA":
        return series.rolling(period).mean()
    elif matype == "EMA":
        return series.ewm(span=period, adjust=False).mean()
    elif matype == "WMA":
        return _wma(series, period)
    elif matype == "HMA":
        half = max(int(period / 2), 1)
        sqrt_p = max(int(np.sqrt(period)), 1)
        return _wma(2 * _wma(series, half) - _wma(series, period), sqrt_p)
    elif matype == "RMA":
        return series.ewm(alpha=1 / period, adjust=False).mean()
    return _wma(series, period)


# ── GreedZone 계산 ─────────────────────────────────────────────────────

def calc_greedzone(
    hist: pd.DataFrame,
    low_period: int = 112,
    stdev_period: int = 50,
    matype: str = "WMA",
    _return_series: bool = False,
) -> "dict | pd.Series":
    """GreedZone 지표를 계산한다.

    Parameters
    ----------
    hist : DataFrame
        yfinance 형식 OHLCV (Open, High, Low, Close, Volume).
        최소 low_period + stdev_period + 20 봉 이상 필요.
    low_period : int
        최저가 계산 기간 (기본 112 — 일봉 기준 약 5개월).
    stdev_period : int
        표준편차 계산 기간 (기본 50).
    matype : str
        이동평균 종류 (SMA/EMA/WMA/HMA/RMA).

    Returns
    -------
    dict with keys:
        in_zone     : bool — 현재 Greed Zone 안에 있는지
        new_entry   : bool — 오늘 새로 진입했는지 (전일 False→오늘 True)
        days_in_zone: int  — 연속 Greed Zone 일수 (0이면 비해당)
        gz1         : float — GZ1 값 (음수일수록 저점 대비 상승폭 큼)
        gz1_limit   : float — GZ1 임계값
        gz2         : float — GZ2 값
        gz2_limit   : float — GZ2 임계값
    """
    min_bars = low_period + stdev_period + 20
    if len(hist) < min_bars:
        return {
            "in_zone": False, "new_entry": False, "days_in_zone": 0,
            "gz1": 0.0, "gz1_limit": 0.0, "gz2": 0.0, "gz2_limit": 0.0,
        }

    # source = OHLC4
    src = (hist["Open"] + hist["High"] + hist["Low"] + hist["Close"]) / 4.0

    # ── GZ1: 저점 대비 편차 ──
    lowest = src.rolling(low_period).min()
    gz1 = (lowest - src) / lowest
    avg1 = _ma(gz1, stdev_period, matype)
    std1 = gz1.rolling(stdev_period).std()
    gz1_limit = avg1 - std1

    # ── GZ2: 이동평균 상단 돌파 ──
    gz2 = _ma(src, low_period, matype)
    avg2 = _ma(gz2, stdev_period, matype)
    std2 = gz2.rolling(stdev_period).std()
    gz2_limit = avg2 + std2

    # ── Greed Zone 조건 ──
    zone = (gz1 < gz1_limit) & (gz2 > gz2_limit)

    if _return_series:
        return zone

    # 현재 상태
    in_zone = bool(zone.iloc[-1]) if not zone.empty else False
    prev_zone = bool(zone.iloc[-2]) if len(zone) >= 2 else False
    new_entry = in_zone and not prev_zone

    # 연속 일수
    days = 0
    if in_zone:
        for i in range(len(zone) - 1, -1, -1):
            if zone.iloc[i]:
                days += 1
            else:
                break

    return {
        "in_zone": in_zone,
        "new_entry": new_entry,
        "days_in_zone": days,
        "gz1": float(gz1.iloc[-1]) if not gz1.empty else 0.0,
        "gz1_limit": float(gz1_limit.iloc[-1]) if not gz1_limit.empty else 0.0,
        "gz2": float(gz2.iloc[-1]) if not gz2.empty else 0.0,
        "gz2_limit": float(gz2_limit.iloc[-1]) if not gz2_limit.empty else 0.0,
    }
