"""engine/execution_validator.py
5분봉 기반 Execution Quality Score (E) 계산기.

E_long  : 롱 진입 품질 (6항목)
E_short : 인버스 진입 품질 (6항목)

롱과 인버스는 완전 별도 브랜치로 계산한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Bar5m:
    """5분봉 단일 bar."""
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float


# ---------------------------------------------------------------------------
# E_long 계산
# ---------------------------------------------------------------------------

def compute_e_long(
    bars: List[Bar5m],
    vwap: float,
    config: Optional[dict] = None,
) -> float:
    """롱 방향 Execution Quality Score.

    최소 3개 bar 필요 (현재봉 + 이전 2봉).

    6항목:
    1. close > pullback cluster high (이전 2봉 high 중 최대)
    2. CLV >= threshold
    3. trigger range expansion (현재 range > 이전 range)
    4. trigger volume expansion (현재 volume > 이전 volume)
    5. VWAP reclaim (close >= VWAP)
    6. OFI proxy positive (close > (high + low) / 2 근사)

    Returns
    -------
    float [0, 1]
    """
    cfg = config or {}
    clv_threshold = cfg.get("clv_threshold", 0.6)

    if len(bars) < 3:
        return 0.0

    curr = bars[-1]
    prev = bars[-2]
    prev2 = bars[-3]

    score = 0.0
    count = 6

    # 1. close > pullback cluster high
    cluster_high = max(prev.high, prev2.high)
    if curr.close > cluster_high:
        score += 1.0

    # 2. CLV (Close Location Value)
    bar_range = curr.high - curr.low
    if bar_range > 0:
        clv = (curr.close - curr.low) / bar_range
        if clv >= clv_threshold:
            score += 1.0

    # 3. Range expansion
    curr_range = curr.high - curr.low
    prev_range = prev.high - prev.low
    if prev_range > 0 and curr_range > prev_range:
        score += 1.0

    # 4. Volume expansion
    if prev.volume > 0 and curr.volume > prev.volume:
        score += 1.0

    # 5. VWAP reclaim
    if vwap > 0 and curr.close >= vwap:
        score += 1.0

    # 6. OFI proxy (close > midpoint)
    midpoint = (curr.high + curr.low) / 2
    if curr.close > midpoint:
        score += 1.0

    return score / count


# ---------------------------------------------------------------------------
# E_short 계산
# ---------------------------------------------------------------------------

def compute_e_short(
    bars: List[Bar5m],
    vwap: float,
    config: Optional[dict] = None,
) -> float:
    """인버스 방향 Execution Quality Score.

    최소 3개 bar 필요.

    6항목:
    1. rebound failure (prev high > prev2 high AND curr close < prev close)
    2. weak close (CLV < threshold)
    3. upper wick dominance (upper wick > body)
    4. failed reclaim (close < VWAP after low touched near VWAP)
    5. next bar low break (curr low < prev low)
    6. volume fade on rebound (curr volume < prev volume when prev was up)

    Returns
    -------
    float [0, 1]
    """
    cfg = config or {}
    weak_clv_threshold = cfg.get("weak_clv_threshold", 0.3)

    if len(bars) < 3:
        return 0.0

    curr = bars[-1]
    prev = bars[-2]
    prev2 = bars[-3]

    score = 0.0
    count = 6

    # 1. Rebound failure
    if prev.high > prev2.high and curr.close < prev.close:
        score += 1.0

    # 2. Weak close (CLV < threshold)
    bar_range = curr.high - curr.low
    if bar_range > 0:
        clv = (curr.close - curr.low) / bar_range
        if clv < weak_clv_threshold:
            score += 1.0

    # 3. Upper wick dominance
    body = abs(curr.close - curr.open)
    upper_wick = curr.high - max(curr.close, curr.open)
    if upper_wick > body:
        score += 1.0

    # 4. Failed reclaim (close < VWAP)
    if vwap > 0 and curr.close < vwap:
        score += 1.0

    # 5. Low break (curr low < prev low)
    if curr.low < prev.low:
        score += 1.0

    # 6. Volume fade on rebound
    # prev가 상승봉이었는데 현재 volume이 줄었으면 반등 약화
    if prev.close > prev.open and curr.volume < prev.volume:
        score += 1.0

    return score / count


# ---------------------------------------------------------------------------
# 편의 함수
# ---------------------------------------------------------------------------

def bars_from_lists(
    ts_list: List[str],
    opens: List[float],
    highs: List[float],
    lows: List[float],
    closes: List[float],
    volumes: List[float],
    start_idx: int,
    count: int,
) -> List[Bar5m]:
    """리스트 인덱스에서 Bar5m 리스트 추출."""
    result = []
    for i in range(start_idx, min(start_idx + count, len(ts_list))):
        result.append(Bar5m(
            ts=ts_list[i],
            open=opens[i] if opens[i] is not None else 0.0,
            high=highs[i] if highs[i] is not None else 0.0,
            low=lows[i] if lows[i] is not None else 0.0,
            close=closes[i] if closes[i] is not None else 0.0,
            volume=volumes[i] if volumes[i] is not None else 0.0,
        ))
    return result
