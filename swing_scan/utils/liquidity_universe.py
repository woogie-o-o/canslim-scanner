"""utils/liquidity_universe.py
유동성 기반 유니버스 선별 — KPI/Live 공통 로직 SSOT.

KPI 백테스트와 라이브 트레이더가 동일한 turnover 평균 → 상위 N
선별 로직을 사용하도록 하기 위한 단일 진입점.

`turnover_provider` 콜러블을 통해 데이터 소스를 추상화 — KPI는
일별 거래대금 맵을, 라이브는 일봉 CSV를 사용하지만 같은 함수로
같은 결과를 산출.

Codex US-01 (Fix #1) — Unified liquidity universe builder.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Callable, Dict, Iterable, List, Optional, Union

import polars as pl

logger = logging.getLogger(__name__)

DateLike = Union[str, date, datetime]
PRICE_FLOOR_KRW = 1000.0


def _to_date(value: DateLike) -> date:
    """문자열/datetime/date → date 정규화."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    raise TypeError(f"Unsupported date-like value: {value!r}")


def classify_price_floor(
    prices: Iterable[object],
    *,
    min_price: float = PRICE_FLOOR_KRW,
) -> Optional[str]:
    """Classify whether a price series violates the penny-stock floor.

    Returns:
      - ``below_floor`` when the latest price is below ``min_price`` and the
        series never traded at or above the floor.
      - ``broken_floor`` when the latest price is below ``min_price`` after the
        series had already traded at or above the floor.
      - ``None`` when the latest usable price is at or above the floor.
    """
    normalized: List[float] = []
    for value in prices:
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if price > 0:
            normalized.append(price)

    if not normalized:
        return None

    latest = normalized[-1]
    if latest >= min_price:
        return None

    if any(price >= min_price for price in normalized[:-1]):
        return "broken_floor"
    return "below_floor"


def filter_price_floor_frames(
    frames: Dict[str, pl.DataFrame],
    *,
    price_col: str = "close",
    min_price: float = PRICE_FLOOR_KRW,
) -> tuple[Dict[str, pl.DataFrame], Dict[str, str]]:
    """Filter ticker dataframes by the configured penny-stock floor."""
    kept: Dict[str, pl.DataFrame] = {}
    dropped: Dict[str, str] = {}

    for ticker, df in frames.items():
        if df is None or len(df) == 0 or price_col not in df.columns:
            kept[ticker] = df
            continue

        reason = classify_price_floor(df[price_col].to_list(), min_price=min_price)
        if reason:
            dropped[ticker] = reason
            continue
        kept[ticker] = df

    return kept, dropped


def build_liquidity_universe(
    tickers: List[str],
    turnover_provider: Callable[[str, date], float],
    asof_date: DateLike,
    lookback: int,
    n: int,
    *,
    min_avg_turnover: float = 0.0,
) -> List[str]:
    """유동성 기준 상위 N개 ticker 선별.

    `asof_date` 를 포함한 최근 `lookback` 거래일(달력일 기준) 의
    평균 turnover (close * volume, KRW) 가 큰 순서로 N 종목 반환.

    Args:
        tickers: 후보 종목 리스트.
        turnover_provider: (ticker, date) -> float 콜러블.
            데이터가 없으면 0.0 또는 None을 반환해야 함 (None은 0으로 처리).
        asof_date: 기준일 (포함). 문자열 "YYYY-MM-DD" 또는 date/datetime.
        lookback: 평균 계산용 룩백 일수 (asof 포함, 달력 기준).
            데이터보다 길면 사용 가능한 일자만 평균 — graceful.
        n: 반환할 ticker 수.
        min_avg_turnover: 평균 turnover 하한 (KRW). 미달 종목 제외.

    Returns:
        평균 turnover 내림차순 상위 N ticker 리스트.
        모두 0 이거나 데이터 없으면 빈 리스트.
    """
    if not tickers:
        return []
    if lookback <= 0:
        raise ValueError(f"lookback must be > 0, got {lookback}")
    if n <= 0:
        return []

    asof = _to_date(asof_date)
    # asof 포함 lookback 일치 (달력일 기준)
    candidate_dates = [asof - timedelta(days=i) for i in range(lookback)]

    scores: dict[str, float] = {}
    for ticker in tickers:
        vals: List[float] = []
        for d in candidate_dates:
            try:
                tv = turnover_provider(ticker, d)
            except Exception as exc:  # pragma: no cover — 방어용
                logger.debug("turnover_provider error %s @ %s: %s", ticker, d, exc)
                continue
            if tv is None:
                continue
            try:
                tv_f = float(tv)
            except (TypeError, ValueError):
                continue
            if tv_f > 0:
                vals.append(tv_f)
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        if avg >= min_avg_turnover:
            scores[ticker] = avg

    if not scores:
        return []

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [t for t, _ in ranked[:n]]
