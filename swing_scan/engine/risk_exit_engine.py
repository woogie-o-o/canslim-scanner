"""engine/risk_exit_engine.py
공통 Risk / Exit 엔진.

exit reason 분류:
  - full_stop     : SL 도달
  - failure_exit  : N봉 내 조건 미충족 (failure_check_fn)
  - time_stop     : 최대 hold bar 도달
  - eod_exit      : 장 마감 전 강제 청산

MFE / MAE 추적 포함.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class TradeResult:
    """단일 거래 결과."""
    strategy_name: str
    symbol: str
    signal_ts: str
    entry_ts: str
    exit_ts: str
    entry_price: float
    exit_price: float
    entry_reason: str
    exit_reason: str        # full_stop | failure_exit | time_stop | eod_exit
    hold_bars: int
    mfe: float              # maximum favorable excursion (%)
    mae: float              # maximum adverse excursion (%)
    gross_pnl: float        # 비용 전 수익률 (%)
    net_pnl: float          # 비용 후 수익률 (%)
    cost_scenario: str      # "base" | "stress"
    m_long: float = 0.0
    m_short: float = 0.0
    s_score: float = 0.0
    d_score: float = 0.0
    e_score: float = 0.0
    rank_score: float = 0.0
    sl_price: float = 0.0
    tp_price: float = 0.0
    side: str = "long"      # "long" | "inverse"
    entry_slippage_pct: float = 0.0  # (entry_price - signal_price) / signal_price


@dataclass
class BarData:
    """exit 시뮬레이션용 bar."""
    ts: str
    open: float
    high: float
    low: float
    close: float


def simulate_exit(
    entry_price: float,
    sl_price: float,
    tp_price: float,
    bars_after_entry: List[BarData],
    time_stop_bars: int,
    eod_time: str = "15:15",
    side: str = "long",
    failure_check_fn: Optional[Callable] = None,
    sl_slippage_pct: float = 0.0,
    profit_time_exit_bars: int = 0,
    profit_time_exit_min_pct: float = 0.0,
    early_exit_bars: int = 0,
    early_exit_range_pct: float = 1.5,
    hwm_trail_activate_pct: float = 0.0,
    hwm_trail_lock_ratio: float = 0.50,
) -> Tuple[float, str, int, float, float]:
    """Exit 시뮬레이션.

    Parameters
    ----------
    entry_price      : 진입 가격
    sl_price         : 손절 가격
    tp_price         : 목표 가격
    bars_after_entry : 진입 이후 bar 리스트 (진입 bar 포함)
    time_stop_bars   : 최대 보유 봉 수
    eod_time         : 장 마감 시간 (HH:MM)
    side             : "long" | "inverse"
    failure_check_fn : Optional[Callable[[List[BarData], int], bool]]
                       True 반환 시 failure_exit

    Returns
    -------
    (exit_price, exit_reason, hold_bars, mfe_pct, mae_pct)
    """
    if not bars_after_entry:
        return entry_price, "time_stop", 0, 0.0, 0.0

    mfe = 0.0  # max favorable excursion (%)
    mae = 0.0  # max adverse excursion (%)
    hwm_pct = 0.0  # high-water mark floating pnl for trailing stop
    is_long = (side == "long")
    stop_slip = max(0.0, float(sl_slippage_pct or 0.0))

    max_bars = min(time_stop_bars, len(bars_after_entry))

    for i in range(max_bars):
        bar = bars_after_entry[i]
        hold = i + 1

        # MFE / MAE 업데이트
        if entry_price > 0:
            if is_long:
                fav = (bar.high - entry_price) / entry_price
                adv = (entry_price - bar.low) / entry_price
            else:
                # inverse: 가격 하락이 유리
                fav = (entry_price - bar.low) / entry_price
                adv = (bar.high - entry_price) / entry_price
            mfe = max(mfe, fav)
            mae = max(mae, adv)

        # EOD exit 체크
        bar_time = bar.ts[11:16] if len(bar.ts) >= 16 else ""
        if bar_time >= eod_time:
            return bar.close, "eod_exit", hold, mfe, mae

        # HWM Trailing Stop
        if hwm_trail_activate_pct > 0 and entry_price > 0 and is_long:
            _bar_peak = (bar.high - entry_price) / entry_price
            hwm_pct = max(hwm_pct, _bar_peak)
            if hwm_pct >= hwm_trail_activate_pct:
                _trail_stop_pct = hwm_pct * (1.0 - hwm_trail_lock_ratio)
                _trail_price = entry_price * (1.0 + _trail_stop_pct)
                if bar.low <= _trail_price:
                    _fill = bar.open if bar.open <= _trail_price else _trail_price
                    return _fill, "hwm_trail_stop", hold, mfe, mae

        # SL 체크 (gap-down/gap-up 시 open 가격으로 체결)
        if is_long:
            if bar.low <= sl_price:
                if bar.open <= sl_price:
                    fill_price = bar.open * (1 - stop_slip)
                else:
                    fill_price = sl_price * (1 - stop_slip)
                return fill_price, "full_stop", hold, mfe, mae
        else:
            # inverse: SL은 상방
            if bar.high >= sl_price:
                if bar.open >= sl_price:
                    fill_price = bar.open * (1 + stop_slip)
                else:
                    fill_price = sl_price * (1 + stop_slip)
                return fill_price, "full_stop", hold, mfe, mae

        # TP 체크
        if is_long:
            if bar.high >= tp_price:
                return tp_price, "tp", hold, mfe, mae
        else:
            if bar.low <= tp_price:
                return tp_price, "tp", hold, mfe, mae

        # Failure exit 체크
        if failure_check_fn is not None:
            if failure_check_fn(bars_after_entry[:i + 1], i):
                return bar.close, "failure_exit", hold, mfe, mae

        # profit_time_exit 체크 (수익 포지션 조기 청산)
        if profit_time_exit_bars > 0 and hold == profit_time_exit_bars:
            if entry_price > 0:
                cur_ret = (bar.close - entry_price) / entry_price if is_long else (entry_price - bar.close) / entry_price
                if cur_ret >= profit_time_exit_min_pct:
                    return bar.close, "profit_time_exit", hold, mfe, mae

        # early_exit_stall 체크 (봉수+횡보 조기청산, TP1 미도달 && breakeven 이상)
        if early_exit_bars > 0 and hold >= early_exit_bars and entry_price > 0:
            if is_long and bar.close >= entry_price:
                # 현재 봉까지 최근 early_exit_bars봉의 range 계산
                _start = max(0, i + 1 - early_exit_bars)
                _recent = bars_after_entry[_start: i + 1]
                _range_high = max(b.high for b in _recent)
                _range_low  = min(b.low  for b in _recent)
                _range_ratio = (_range_high - _range_low) / entry_price
                if _range_ratio < early_exit_range_pct / 100.0:
                    return bar.close, "early_exit_stall", hold, mfe, mae

    # time_stop
    last_bar = bars_after_entry[max_bars - 1] if max_bars > 0 else bars_after_entry[0]
    return last_bar.close, "time_stop", max_bars, mfe, mae


def build_trade_result(
    strategy_name: str,
    symbol: str,
    signal_ts: str,
    entry_ts: str,
    entry_price: float,
    exit_price: float,
    exit_reason: str,
    hold_bars: int,
    mfe: float,
    mae: float,
    cost: float,
    side: str = "long",
    entry_reason: str = "",
    m_long: float = 0.0,
    m_short: float = 0.0,
    s_score: float = 0.0,
    d_score: float = 0.0,
    e_score: float = 0.0,
    rank_score: float = 0.0,
    sl_price: float = 0.0,
    tp_price: float = 0.0,
    cost_scenario: str = "base",
    exit_ts: str = "",
    signal_price: float = 0.0,
) -> TradeResult:
    """TradeResult 생성 헬퍼."""
    entry_slippage_pct = (
        (entry_price - signal_price) / signal_price
        if signal_price > 0 else 0.0
    )
    if entry_price > 0:
        if side == "long":
            gross_pnl = (exit_price - entry_price) / entry_price
        else:
            # inverse ETF: 매수 후 매도이므로 동일
            gross_pnl = (exit_price - entry_price) / entry_price
    else:
        gross_pnl = 0.0

    net_pnl = gross_pnl - cost

    return TradeResult(
        strategy_name=strategy_name,
        symbol=symbol,
        signal_ts=signal_ts,
        entry_ts=entry_ts,
        exit_ts=exit_ts,
        entry_price=entry_price,
        exit_price=exit_price,
        entry_reason=entry_reason,
        exit_reason=exit_reason,
        hold_bars=hold_bars,
        mfe=mfe,
        mae=mae,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        cost_scenario=cost_scenario,
        m_long=m_long,
        m_short=m_short,
        s_score=s_score,
        d_score=d_score,
        e_score=e_score,
        rank_score=rank_score,
        sl_price=sl_price,
        tp_price=tp_price,
        side=side,
        entry_slippage_pct=entry_slippage_pct,
    )


def trade_result_to_dict(tr: TradeResult) -> dict:
    """TradeResult → dict (KPI 계산기/리포트 호환)."""
    return {
        "strategy_id": tr.strategy_name,
        "ticker": tr.symbol,
        "signal_ts": tr.signal_ts,
        "entry_ts": tr.entry_ts,
        "exit_ts": tr.exit_ts,
        "entry_price": tr.entry_price,
        "exit_price": tr.exit_price,
        "entry_reason": tr.entry_reason,
        "exit_reason": tr.exit_reason,
        "hold_bars": tr.hold_bars,
        "mfe": tr.mfe,
        "mae": tr.mae,
        "gross_pnl_pct": tr.gross_pnl,
        "net_pnl_pct": tr.net_pnl,
        "cost_applied": float(tr.cost_scenario == "stress") * 0.0024 + 0.0026,
        "cost_scenario": tr.cost_scenario,
        "side": tr.side,
        "m_long": tr.m_long,
        "m_short": tr.m_short,
        "s_score": tr.s_score,
        "d_score": tr.d_score,
        "e_score": tr.e_score,
        "rank_score": tr.rank_score,
        "sl_price": tr.sl_price,
        "tp_price": tr.tp_price,
        "entry_slippage_pct": tr.entry_slippage_pct,
    }
