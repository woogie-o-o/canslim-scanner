"""SWING-MOM 인트라데이 모멘텀 전략.

강한 시장과 섹터에서 장중 눌림을 매수한다.
추세 반전이 아니라 모멘텀 추세추종 전략이다.

30분봉 게이트:
  1. M_long >= 0.60
  2. S_score >= 0.60
  3. D_score 0.10 ~ 0.50
  4. liquidity_rank >= 0.40

5분봉 신호:
  - 진입 구간: 09:30 ~ 13:30
  - 1~3봉 눌림
  - 회복봉 확인
  - close >= VWAP * 0.995
  - E_long >= e_long_min

진입: 다음 봉 시가
청산: 손절 / 익절 / 시간청산, 강제 EOD 청산 없음
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, replace as dc_replace
from typing import Dict, List, Optional, Tuple

import polars as pl

from engine.context_state import ContextState, ContextStateMap, get_context_for_5m_bar
from engine.execution_validator import Bar5m, bars_from_lists, compute_e_long
from engine.risk_exit_engine import BarData, TradeResult, build_trade_result, simulate_exit

logger = logging.getLogger(__name__)

STRATEGY_ID = "SWING_MOM"


def validate_tp_config(cfg: dict) -> None:
    """TP 鍮꾩쑉 config 寃利???Codex US-03 (Fix #4 REVISE).

    寃利?洹쒖튃:
        - 0 <= tp1_exit_ratio <= 1
        - 0 <= tp2_trail_ratio <= 1
        - tp1_exit_ratio + tp2_trail_ratio <= 1.0 (?섏튂?ㅼ감 1e-9 ?덉슜)

    ?꾨컲 ??ValueError 瑜?紐낇솗??硫붿떆吏? ?④퍡 raise.
    KPI/Live ?묒そ?먯꽌 ?몄텧?섏뿬 ?섎せ??config 媛 ?ㅼ슫?ㅽ듃由쇱쑝濡??섎윭媛??寃?諛⑹?.

    Args:
        cfg: ?꾨왂 config dict (tp1_exit_ratio, tp2_trail_ratio ??媛吏????덉쓬).
    Raises:
        ValueError: ?대뒓 ??議곌굔?대씪???꾨컲??寃쎌슦.
    """
    tp1 = float(cfg.get("tp1_exit_ratio", 0.5))
    tp2 = float(cfg.get("tp2_trail_ratio", 0.0))
    if not (0.0 <= tp1 <= 1.0):
        raise ValueError(
            f"tp1_exit_ratio out of range: {tp1} (must be in [0, 1])"
        )
    if not (0.0 <= tp2 <= 1.0):
        raise ValueError(
            f"tp2_trail_ratio out of range: {tp2} (must be in [0, 1])"
        )
    if tp1 + tp2 > 1.0 + 1e-9:
        raise ValueError(
            f"tp1_exit_ratio + tp2_trail_ratio = {tp1 + tp2:.6f} > 1.0 "
            f"(tp1={tp1}, tp2={tp2}). Combined exit + trail allocation must "
            f"not exceed 100% of position."
        )


def _has_30min_surge(
    df_5m,
    body_pct: float = 0.03,
    vol_mult: float = 5.0,
    min_periods: int = 3,
) -> bool:
    """吏곸쟾 ?꾩꽦 30遺꾨큺????뚮큺 + 嫄곕옒??湲됱쬆?대㈃ True ??吏꾩엯 李⑤떒.

    live_swing_mom_trader.py ? ?숈씪 濡쒖쭅 (SSOT ?쇱튂).
    df_5m: ?좏샇遊됯퉴吏??5遺꾨큺 ?곗씠??(lookahead 諛⑹?瑜??꾪빐 signal_idx+1 ?됰쭔 ?꾨떖)
    """
    try:
        if df_5m is None or df_5m.is_empty():
            return False

        df = df_5m.sort("datetime")

        df_30 = (
            df.with_columns(
                pl.col("datetime").dt.truncate("30m").alias("dt_30m")
            )
            .group_by("dt_30m")
            .agg([
                pl.col("open").first().alias("open"),
                pl.col("close").last().alias("close"),
                pl.col("volume").sum().alias("volume"),
            ])
            .sort("dt_30m")
            .with_columns(
                (pl.col("close") * pl.col("volume")).alias("trade_val")
            )
        )

        # ?꾩옱 吏꾪뻾 以묒씤 30遺꾨큺???쒖옉 ?쒓컖 (lookahead 諛⑹?)
        current_30m_start = df.select(pl.col("datetime").dt.truncate("30m")).row(-1)[0]
        df_30_complete = df_30.filter(pl.col("dt_30m") < current_30m_start)

        if df_30_complete.height < min_periods + 1:
            return False

        prev = df_30_complete.row(-1, named=True)
        o, c, tv = prev["open"], prev["close"], prev["trade_val"]
        if not o or o == 0:
            return False

        avg_tv = df_30_complete["trade_val"][:-1].mean()
        if not avg_tv or avg_tv == 0:
            return False

        is_big_bear  = (o - c) / o >= body_pct
        is_vol_spike = tv >= vol_mult * avg_tv
        return is_big_bear and is_vol_spike
    except Exception:
        return False


def _make_trail_fn(entry_price: float, trail_pct: float):
    """Peak 異붿쟻 ?몃젅?쇰쭅 ?ㅽ깙 ?대줈? (Leg2 trail ?꾩슜).

    entry_price ?鍮?0.5% ?댁긽 ?곸듅 ???쒖꽦??
    peak ?鍮?trail_pct ?섎씫 ??failure_exit ?좏샇 諛섑솚.
    """
    state = {"peak": entry_price}
    activation = entry_price * 1.005

    def fn(bars, idx):
        bar = bars[idx]
        state["peak"] = max(state["peak"], bar.high)
        if state["peak"] <= activation:
            return False
        return bar.low <= state["peak"] * (1 - trail_pct)

    return fn


def _should_skip_fill(ticker: str, signal_ts: str, fail_rate: float, seed) -> bool:
    """Deterministic entry fill-failure gate for stress tests."""
    rate = max(0.0, min(1.0, float(fail_rate or 0.0)))
    if rate <= 0.0:
        return False
    key = f"{ticker}|{signal_ts}|{seed}".encode("utf-8")
    draw = int.from_bytes(hashlib.sha256(key).digest()[:8], "big") / float(1 << 64)
    return draw < rate

SWING_MOM_DEFAULT_CONFIG = {
    # Default settings used by live, backtest, and tests.
    # This block mirrors the active DW configuration.
    # 실거래, 백테스트, 테스트가 공유하는 기본 설정이다.
    # 현재 활성 DW 설정과 동일하다.
    "m_long_min": 0.50,
    "s_min": 0.50,
    "liquidity_rank_min": 0.30,
    "pullback_bars_max": 3,
    "vwap_gap_tol": 0.012019,
    "e_long_min": 0.036024,
    "tp_pct": 0.09,
    "tp1_pct": 0.03,
    "tp2_pct": 0.056577,
    "tp2_trail_pct": 0.05,
    "tp2_trail_ratio": 0.55,
    "tp1_exit_ratio": 0.15,
    "partial_ratio": 0.51,
    "time_stop_bars": 120,
    "eod_time": "15:20",
    "entry_start": "09:30",
    "entry_end": "14:30",
    "entry_method": "typical_vwap",
    "entry_delay_bars": 0,
    "fill_fail_rate": 0.01,
    "fill_fail_seed": 0,
    "min_rank_score": 0.0,
    "top_n": 999,
    "sl_atr_mult": None,
    "sl_method": "fixed",
    "sl_pct": 0.02,
    "sl_cap_pct": 0.014,
    "sl_signal_low_buf": 0.002621,
    "sl_slippage_pct": 0.001157,
    # Same-day re-entry stays disabled by default.
    # Live, backtest, and tests share the same configuration.
    "prevent_same_day_reentry": False,
    "add_on_buy": True,
    "add_trigger_buf": 0.0015,
    "addon_ratio": 0.42,
    # conditional add-on: "unconditional" = original, "conditional" = volume+MA checks
    "addon_mode": "unconditional",
    "addon_vol_mult": 0.5,       # bar_vol >= avg_vol * this to pass volume check (0.5 = not dead market)
    "addon_vol_lookback": 20,    # bars before signal for avg volume
    "addon_ma_period": 20,       # MA period for trend-still-bullish check
    "addon_min_conditions": 1,   # of 2 conditions (vol, ma) required to add
    "use_flow_filter": False,    # True: c_score > 0 required (foreign+inst net buy)
    "max_same_sector": 1,
    "recovery_tp_pct": 0.01,      # 마이너스 경험 후 +1% 회복 시 전량 청산
    # 같은 종목의 당일 재진입은 기본 비활성화다.
    # 실거래, 백테스트, 테스트가 같은 설정을 공유한다.
    "early_exit_range_pct": 1.5,
    "early_exit_flow_check": True,
    "use_30min_surge_filter": True,
    # ── 횡보봉 진입 차단 필터 (flat bar filter) ──────────────────────────
    # flat_filter_mode: None | "body_ratio" | "slope" | "momentum"
    #   None        → 필터 비활성 (기존 동작)
    #   body_ratio  → 방안A: 회복봉 바디/전체범위 비율이 임계값 미만이면 진입 금지
    #   slope       → 방안B: 최근 N봉 선형 기울기가 임계값 미만이면 진입 금지
    #   momentum    → 방안C: 최근 N봉 가격 변화율이 임계값 미만이면 진입 금지
    "flat_filter_mode": "momentum",  # 2026-04-28: mom5bar<0.20% best Sharpe+MDD
    # HWM Trailing Stop: preserve profit when price reverses before TP1
    "hwm_trail_activate_pct": 0.0,   # 0.0=disabled; 0.015=activate at +1.5%
    "hwm_trail_lock_ratio": 0.50,    # lock >= 50% of HWM (e.g. HWM=2% -> stop at +1%)
    "flat_body_ratio_min": 0.30,   # A: 바디가 고저범위의 30% 미만이면 횡보
    "flat_slope_lookback": 5,      # B: 기울기 계산에 사용할 봉 수
    "flat_slope_pct_min": 0.0005,  # B: 최소 기울기 (close 대비 비율/봉, 0.05%/봉)
    "flat_mom_lookback": 5,        # C: 모멘텀 계산 봉 수
    "flat_mom_pct_min": 0.002,     # C: 최소 누적 변화율 (0.2%)
    "default_equity": 1_000_000,
    "windows": [
        {"name": "AM", "start": "09:30", "end": "11:00", "top_n": 5},
        {"name": "MID", "start": "11:00", "end": "13:00", "top_n": 1},
        {"name": "PM", "start": "13:00", "end": "14:30", "top_n": 1},
    ],
}


@dataclass
class SwingMOMSignal:
    """SWING-MOM ?좏샇."""
    signal_ts: str
    signal_idx: int
    ticker: str
    entry_price_est: float
    sl_price: float
    tp_price: float
    time_stop_bars: int
    m_long: float
    s_score: float
    e_long: float
    rank_score: float


def _ts_str(val) -> str:
    return str(val)[:16]


def _calc_atr(highs: list, lows: list, closes: list, period: int = 14) -> list:
    """ATR14 怨꾩궛. ?곗씠??遺議???None."""
    n = len(highs)
    tr = []
    for i in range(n):
        h = highs[i] or 0.0
        lo = lows[i] or 0.0
        if i == 0:
            tr.append(h - lo)
        else:
            pc = closes[i - 1] or 0.0
            tr.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    atr = [None] * n
    for i in range(period - 1, n):
        atr[i] = sum(tr[i - period + 1: i + 1]) / period
    return atr


def _calc_intraday_turnover(df_5m: "pl.DataFrame", signal_ts: str) -> float:
    """signal_ts 이전까지의 당일 누적 거래대금(KRW) 계산. lookahead 없음."""
    date_str = signal_ts[:10]
    open_time = date_str + " 09:00:00"
    bars = df_5m.filter(
        (pl.col("datetime").cast(pl.Utf8) >= open_time) &
        (pl.col("datetime").cast(pl.Utf8) <= signal_ts)
    )
    if len(bars) == 0:
        return 0.0
    return float((bars["volume"].cast(pl.Float64) * bars["close"].cast(pl.Float64)).sum())


def _atr_sl(entry: float, atr: Optional[float], mult: float, cap_pct: float) -> float:
    """ATR 湲곕컲 SL. atr??None?대㈃ cap_pct 怨좎젙 SL濡??대갚."""
    if atr and atr > 0:
        sl = entry - mult * atr
        floor = entry * (1 - cap_pct)
        return max(sl, floor)
    return entry * (1 - cap_pct)


# KRX ?멸??⑥쐞 ?뚯씠釉?(7?④퀎)
_TICK_TABLE: List[Tuple[float, float]] = [
    (2_000,    1),
    (5_000,    5),
    (20_000,  10),
    (50_000,  50),
    (200_000, 100),
    (500_000, 500),
    (float("inf"), 1_000),
]


def _tick_size(price: float) -> float:
    """KRX ?멸??⑥쐞 諛섑솚."""
    for limit, tick in _TICK_TABLE:
        if price < limit:
            return tick
    return 1_000


def _try_limit_fill(
    idx: int, limit_px: float, opens: list, lows: list, dt_list: list, n: int
) -> Tuple[Optional[float], Optional[str]]:
    """idx 遊됱뿉??limit_px 吏?뺢? 泥닿껐 ?쒕룄.

    泥닿껐 議곌굔:
      - opens[idx] <= limit_px  ??gap-down, ?쒓? 泥닿껐
      - lows[idx]  <= limit_px  ??intrabar 泥닿껐, limit_px 泥닿껐
    Returns (entry_price, entry_ts) or (None, None)
    """
    if idx >= n:
        return None, None
    o = opens[idx]
    lo = lows[idx]
    if o is None or o <= 0:
        return None, None
    if o <= limit_px:
        return o, dt_list[idx]      # gap-down ???쒓???泥닿껐
    if lo is not None and lo <= limit_px:
        return limit_px, dt_list[idx]
    return None, None


def _calc_vwap_intraday(dt_list: list, closes: list, volumes: list) -> list:
    n = len(closes)
    vwap = [None] * n
    cum_pv = cum_v = 0.0
    cur_date = None
    for i in range(n):
        d = dt_list[i][:10]
        if d != cur_date:
            cur_date = d
            cum_pv = cum_v = 0.0
        c = closes[i] if closes[i] is not None else 0.0
        v = volumes[i] if volumes[i] is not None else 0.0
        cum_pv += c * v
        cum_v += v
        vwap[i] = cum_pv / cum_v if cum_v > 0 else None
    return vwap


def _check_pullback_swing(
    closes: list,
    _volumes: list,
    i: int,
    max_pb: int = 3,
    vol_confirm_ratio: float = 0.0,  # >0이면 회복봉 거래량 >= 풀백 평균 * ratio 요구
) -> Tuple[bool, int]:
    """1~max_pb遊?pullback ???꾩옱遊됱씠 ?뚮났遊됱씤吏 ?뺤씤.

    ?ㅼ쐷 ?꾨왂? 嫄곕옒??議곌굔 ?놁쓬 (?먯뒯?섍쾶).

    Returns (is_valid_pullback, pullback_length)
    """
    if i < 2:
        return False, 0

    # pullback 湲몄씠 ?먯?: 吏곸쟾 遊됰?????갑?μ쑝濡??섎씫 ?곗냽???뺤씤
    pb_len = 0
    for k in range(1, max_pb + 1):
        if i - k < 0 or i - k - 1 < 0:
            break
        c_cur = closes[i - k]
        c_prev = closes[i - k - 1]
        if c_cur is None or c_prev is None:
            break
        if c_cur < c_prev:
            pb_len = k
        else:
            break

    if pb_len == 0:
        return False, 0

    # ?꾩옱遊됱씠 吏곸쟾遊됰낫???믪?吏 ?뺤씤 (?뚮났遊?
    curr_close = closes[i]
    prev_close = closes[i - 1]
    if curr_close is None or prev_close is None:
        return False, 0
    if curr_close <= prev_close:
        return False, 0

    # 회복봉 거래량 확인 (vol_confirm_ratio > 0 일 때만 활성)
    if vol_confirm_ratio > 0.0 and _volumes is not None:
        rec_vol = _volumes[i] if _volumes[i] is not None else 0.0
        pb_vols = [_volumes[i - k] for k in range(1, pb_len + 1)
                   if 0 <= i - k < len(_volumes) and _volumes[i - k] is not None]
        avg_pb_vol = sum(pb_vols) / len(pb_vols) if pb_vols else 0.0
        if avg_pb_vol > 0 and rec_vol < avg_pb_vol * vol_confirm_ratio:
            return False, 0

    return True, pb_len


def check_regime_gate_swing(
    ctx: ContextState,
    config: Optional[dict] = None,
) -> bool:
    """SWING-MOM 30遺꾨큺 寃뚯씠??

    ?듦낵 議곌굔 (AND):
    1. M_long >= m_long_min
    2. S_score >= s_min
    3. liquidity_rank >= liquidity_rank_min
    """
    cfg = config or SWING_MOM_DEFAULT_CONFIG
    if ctx.m_long < cfg.get("m_long_min", 0.50):
        return False
    if ctx.s_score < cfg.get("s_min", 0.60):
        return False
    if ctx.liquidity_rank < cfg.get("liquidity_rank_min", 0.40):
        return False
    return True


def _generate_signals_for_ticker(
    df_5m: pl.DataFrame,
    context_map: ContextStateMap,
    ticker: str,
    config: Optional[dict] = None,
) -> List[SwingMOMSignal]:
    """?⑥씪 醫낅ぉ SWING-MOM ?좏샇 ?앹꽦."""
    cfg = {**SWING_MOM_DEFAULT_CONFIG, **(config or {})}
    n = len(df_5m)
    if n < 5:
        return []

    dt_list = [_ts_str(v) for v in df_5m["datetime"].cast(pl.Utf8).to_list()]
    opens   = df_5m["open"].cast(pl.Float64).to_list()
    highs   = df_5m["high"].cast(pl.Float64).to_list()
    lows    = df_5m["low"].cast(pl.Float64).to_list()
    closes  = df_5m["close"].cast(pl.Float64).to_list()
    volumes = df_5m["volume"].cast(pl.Float64).to_list()

    vwap = _calc_vwap_intraday(dt_list, closes, volumes)
    _use_atr_sl = cfg.get("sl_atr_mult") is not None
    atr14 = _calc_atr(highs, lows, closes, cfg.get("atr_period", 14)) if _use_atr_sl else None

    signals: List[SwingMOMSignal] = []

    for i in range(3, n):
        ts = dt_list[i]
        if not ts:
            continue

        # 吏꾩엯 ?쒓컙 ?꾪꽣
        time_part = ts[11:16]
        if time_part < cfg["entry_start"] or time_part > cfg["entry_end"]:
            continue

        # 30遺꾨큺 context 議고쉶
        ctx = get_context_for_5m_bar(ts, context_map, ticker)
        if ctx is None:
            continue

        # Regime gate
        if not check_regime_gate_swing(ctx, cfg):
            continue

        # Flow filter: 외국인+기관 순매수 c_score > 0 필요
        if cfg.get("use_flow_filter") and ctx.c_score <= 0:
            continue

        # 5遺꾨큺 pullback 泥댄겕 (?뚮났遊??뺤씤 ?ы븿)
        pb_ok, pb_len = _check_pullback_swing(closes, volumes, i, cfg.get("pullback_bars_max", 3), cfg.get("vol_confirm_ratio", 0.0))
        if not pb_ok:
            continue

        # 횡보봉 진입 차단 필터 ──────────────────────────────────
        flat_mode = cfg.get("flat_filter_mode")
        if flat_mode == "body_ratio":
            _o, _h, _l, _c = opens[i], highs[i], lows[i], closes[i]
            _rng = (_h - _l) if (_h and _l) else 0.0
            if _rng > 0:
                _body_ratio = abs(_c - _o) / _rng
                if _body_ratio < cfg.get("flat_body_ratio_min", 0.30):
                    continue
        elif flat_mode == "slope":
            _lb = cfg.get("flat_slope_lookback", 5)
            if i >= _lb:
                _pts = closes[i - _lb + 1: i + 1]
                _n = len(_pts)
                if _n >= 2:
                    _xm = (_n - 1) / 2.0
                    _ym = sum(_pts) / _n
                    _num = sum((k - _xm) * (v - _ym) for k, v in enumerate(_pts))
                    _den = sum((k - _xm) ** 2 for k in range(_n))
                    _slope = _num / _den if _den > 0 else 0.0
                    _ref = closes[i] or 1.0
                    if (_slope / _ref) < cfg.get("flat_slope_pct_min", 0.0005):
                        continue
        elif flat_mode == "momentum":
            _lb = cfg.get("flat_mom_lookback", 5)
            if i >= _lb:
                _base = closes[i - _lb]
                if _base and _base > 0:
                    _mom_pct = (closes[i] - _base) / _base
                    if _mom_pct < cfg.get("flat_mom_pct_min", 0.002):
                        continue

        curr_close = closes[i]
        if curr_close is None or curr_close <= 0:
            continue

        # VWAP 吏吏 ?뺤씤
        vw = vwap[i]
        if vw is None:
            continue
        vwap_lower = vw * (1 - cfg.get("vwap_gap_tol", 0.0))
        if curr_close < vwap_lower:
            continue

        # E_long 怨꾩궛
        start_idx = max(i - 2, 0)
        bars = bars_from_lists(dt_list, opens, highs, lows, closes, volumes, start_idx, 3)
        e_long = compute_e_long(bars, vw)

        if e_long < cfg.get("e_long_min", 0.0):
            continue

        # SL / TP
        tp_pct = cfg["tp_pct"]
        if cfg.get("sl_atr_mult") is not None:
            sl = _atr_sl(curr_close, atr14[i], cfg["sl_atr_mult"], cfg["sl_cap_pct"])
        else:
            sl = curr_close * (1 - cfg["sl_pct"])
        tp = curr_close * (1 + tp_pct)

        rank_score = ctx.m_long * 0.5 + ctx.s_score * 0.3 + e_long * 0.2

        signals.append(SwingMOMSignal(
            signal_ts=ts,
            signal_idx=i,
            ticker=ticker,
            entry_price_est=curr_close,
            sl_price=sl,
            tp_price=tp,
            time_stop_bars=cfg["time_stop_bars"],
            m_long=ctx.m_long,
            s_score=ctx.s_score,
            e_long=e_long,
            rank_score=rank_score,
        ))

    return signals


def inspect_signal_bar_swing_mom(
    df_5m: pl.DataFrame,
    context_map: ContextStateMap,
    ticker: str,
    config: Optional[dict] = None,
    signal_idx: Optional[int] = None,
) -> Optional[dict]:
    """Return bar-level diagnostics for the requested 5m bar.

    Used by live logging to explain why a post-gate candidate did or did not
    produce a signal on the current completed bar.
    """
    cfg = {**SWING_MOM_DEFAULT_CONFIG, **(config or {})}
    n = len(df_5m)
    if n < 5:
        return None

    i = signal_idx if signal_idx is not None else (n - 1)
    if i < 3 or i >= n:
        return None

    dt_list = [_ts_str(v) for v in df_5m["datetime"].cast(pl.Utf8).to_list()]
    opens = df_5m["open"].cast(pl.Float64).to_list()
    highs = df_5m["high"].cast(pl.Float64).to_list()
    lows = df_5m["low"].cast(pl.Float64).to_list()
    closes = df_5m["close"].cast(pl.Float64).to_list()
    volumes = df_5m["volume"].cast(pl.Float64).to_list()
    vwap = _calc_vwap_intraday(dt_list, closes, volumes)

    ts = dt_list[i]
    ctx = get_context_for_5m_bar(ts, context_map, ticker)
    curr_close = closes[i]
    vw = vwap[i]
    pb_ok, pb_len = _check_pullback_swing(closes, volumes, i, cfg.get("pullback_bars_max", 3), cfg.get("vol_confirm_ratio", 0.0))

    vwap_lower = None
    vwap_gap_pct = None
    vwap_ok = False
    if curr_close is not None and curr_close > 0 and vw is not None and vw > 0:
        vwap_lower = vw * (1 - cfg.get("vwap_gap_tol", 0.0))
        vwap_ok = curr_close >= vwap_lower
        vwap_gap_pct = (curr_close / vw) - 1.0

    e_long = None
    if curr_close is not None and curr_close > 0 and vw is not None:
        start_idx = max(i - 2, 0)
        bars = bars_from_lists(dt_list, opens, highs, lows, closes, volumes, start_idx, 3)
        e_long = compute_e_long(bars, vw)

    return {
        "signal_ts": ts,
        "signal_idx": i,
        "ctx": ctx,
        "pullback_ok": pb_ok,
        "pullback_len": pb_len,
        "curr_close": curr_close,
        "vwap": vw,
        "vwap_lower": vwap_lower,
        "vwap_ok": vwap_ok,
        "vwap_gap_pct": vwap_gap_pct,
        "e_long": e_long,
        "e_long_min": cfg.get("e_long_min", 0.0),
    }


def generate_signals_swing_mom(
    universe_5m: Dict[str, pl.DataFrame],
    context_map: ContextStateMap,
    config: Optional[dict] = None,
) -> List[SwingMOMSignal]:
    """SWING-MOM ?좏샇 ?앹꽦 (?좊땲踰꾩뒪 ?꾩껜).

    Returns
    -------
    List[SwingMOMSignal] ??rank_score ?대┝李⑥닚 ?뺣젹
    """
    cfg = {**SWING_MOM_DEFAULT_CONFIG, **(config or {})}
    all_signals: List[SwingMOMSignal] = []

    for ticker, df_5m in universe_5m.items():
        sigs = _generate_signals_for_ticker(df_5m, context_map, ticker, cfg)
        all_signals.extend(sigs)

    all_signals.sort(key=lambda s: s.rank_score, reverse=True)
    logger.info("SWING-MOM ?좏샇 ?앹꽦: %d媛?(%d醫낅ぉ)", len(all_signals), len(universe_5m))
    return all_signals


def _chronological_unique_top_n(
    signals: List[SwingMOMSignal],
    top_n: int,
) -> List[SwingMOMSignal]:
    """Select candidates in chronological order to avoid future-aware ranking."""
    if top_n <= 0:
        return []

    ordered = sorted(signals, key=lambda s: (s.signal_ts, -s.rank_score, s.ticker))
    seen_tickers: set[str] = set()
    selected: List[SwingMOMSignal] = []
    for sig in ordered:
        if sig.ticker in seen_tickers:
            continue
        seen_tickers.add(sig.ticker)
        selected.append(sig)
        if len(selected) >= top_n:
            break
    return selected


def backtest_swing_mom(
    universe_5m: Dict[str, pl.DataFrame],
    context_map: ContextStateMap,
    config: Optional[dict] = None,
    cost_scenarios: Optional[Dict[str, float]] = None,
) -> List[TradeResult]:
    """SWING-MOM 諛깊뀒?ㅽ듃.

    Parameters
    ----------
    universe_5m    : {ticker: df_5m}
    context_map    : ContextStateMap
    config         : ?뚮씪誘명꽣 ?ㅻ쾭?쇱씠??
    cost_scenarios : {"base": 0.0026, "stress": 0.0050}

    Returns
    -------
    List[TradeResult]
    """
    cfg   = {**SWING_MOM_DEFAULT_CONFIG, **(config or {})}
    costs = cost_scenarios or {"base": 0.0026, "stress": 0.0050}
    top_n = cfg["top_n"]
    entry_delay_bars = max(0, int(cfg.get("entry_delay_bars", 0) or 0))
    fill_fail_rate = float(cfg.get("fill_fail_rate", 0.0) or 0.0)
    fill_fail_seed = cfg.get("fill_fail_seed", 0)
    sl_slippage_pct = max(0.0, float(cfg.get("sl_slippage_pct", 0.0) or 0.0))

    signals = generate_signals_swing_mom(universe_5m, context_map, cfg)
    if not signals:
        return []

    from collections import defaultdict
    date_signals: Dict[str, List[SwingMOMSignal]] = defaultdict(list)
    for sig in signals:
        date_signals[sig.signal_ts[:10]].append(sig)

    trades: List[TradeResult] = []

    # Precompute Polars→Python lists once per ticker (avoids 5960 redundant conversions)
    _ticker_cache: Dict[str, tuple] = {}
    for _tk, _df in universe_5m.items():
        _n = len(_df)
        _dt = [_ts_str(v) for v in _df["datetime"].cast(pl.Utf8).to_list()]
        _o  = _df["open"].cast(pl.Float64).to_list()
        _h  = _df["high"].cast(pl.Float64).to_list()
        _l  = _df["low"].cast(pl.Float64).to_list()
        _c  = _df["close"].cast(pl.Float64).to_list()
        _ticker_cache[_tk] = (_n, _dt, _o, _h, _l, _c)

    for date, day_sigs in sorted(date_signals.items()):
        selected = _chronological_unique_top_n(day_sigs, top_n)

        for sig in selected:
            if _should_skip_fill(sig.ticker, sig.signal_ts, fill_fail_rate, fill_fail_seed):
                continue

            _cached = _ticker_cache.get(sig.ticker)
            if _cached is None:
                continue
            n, dt_list, opens, highs, lows, closes = _cached

            entry_idx = sig.signal_idx + 1 + entry_delay_bars
            if entry_idx >= n:
                continue

            entry_ts    = dt_list[entry_idx]
            entry_price = opens[entry_idx]
            if entry_price is None or entry_price <= 0:
                continue

            if entry_ts == sig.signal_ts:
                continue

            # ?? entry_method: limit_minus3tick (?좏깮) ??????????????????
            entry_method = cfg.get("entry_method", "next_open")
            if entry_method == "limit_minus3tick":
                ref_open   = opens[entry_idx]
                if ref_open is None or ref_open <= 0:
                    continue
                limit_px   = ref_open - 3 * _tick_size(ref_open)
                # 1李??쒕룄: bar[n+1]
                ep, ets = _try_limit_fill(entry_idx, limit_px, opens, lows, dt_list, n)
                if ep is None:
                    # 2李??쒕룄: bar[n+2] (1-bar ?湲????ㅽ궢)
                    ep, ets = _try_limit_fill(entry_idx + 1, limit_px, opens, lows, dt_list, n)
                if ep is None:
                    continue  # 誘몄껜寃????ㅽ궢
                entry_price = ep
                entry_ts    = ets
                entry_idx   = entry_idx + 1 if ets == dt_list[min(entry_idx + 1, n - 1)] else entry_idx
            elif entry_method == "limit_minus_pct":
                # next_open * (1 - entry_limit_pct) limit order, 2bar wait
                ref_open = opens[entry_idx]
                if ref_open is None or ref_open <= 0:
                    continue
                pct = cfg.get("entry_limit_pct", 0.005)
                limit_px = ref_open * (1 - pct)
                ep, ets = _try_limit_fill(entry_idx, limit_px, opens, lows, dt_list, n)
                if ep is None:
                    ep, ets = _try_limit_fill(entry_idx + 1, limit_px, opens, lows, dt_list, n)
                if ep is None:
                    continue
                entry_price = ep
                entry_ts    = ets
                entry_idx   = entry_idx + 1 if ets == dt_list[min(entry_idx + 1, n - 1)] else entry_idx

            # ATR ?숈쟻 SL vs ?좏샇遊??媛 SL vs 怨좎젙 SL
            tp_pct = cfg["tp_pct"]
            if cfg.get("sl_atr_mult") is not None:
                atr_vals = _calc_atr(highs, lows, closes, cfg.get("atr_period", 14))
                atr_at_entry = atr_vals[sig.signal_idx]
                sl_price = _atr_sl(entry_price, atr_at_entry, cfg["sl_atr_mult"], cfg["sl_cap_pct"])
            elif cfg.get("sl_method") == "signal_low":
                sig_low = lows[sig.signal_idx] or 0.0
                buf     = cfg.get("sl_signal_low_buf", 0.002)
                cap_pct = cfg.get("sl_cap_pct", 0.020)
                raw_sl  = sig_low * (1 - buf) if sig_low > 0 else entry_price * (1 - cfg["sl_pct"])
                sl_price = max(raw_sl, entry_price * (1 - cap_pct))
            else:
                sl_price = entry_price * (1 - cfg["sl_pct"])
            tp_price = entry_price * (1 + tp_pct)

            if sl_price >= entry_price or tp_price <= entry_price:
                continue

            bars_after: List[BarData] = []
            entry_date = entry_ts[:10]
            max_exit_idx = min(entry_idx + cfg["time_stop_bars"] + 10, n)
            for j in range(entry_idx, max_exit_idx):
                if dt_list[j][:10] != entry_date:
                    break
                bars_after.append(BarData(
                    ts=dt_list[j],
                    open=opens[j] if opens[j] is not None else 0.0,
                    high=highs[j] if highs[j] is not None else 0.0,
                    low=lows[j] if lows[j] is not None else 0.0,
                    close=closes[j] if closes[j] is not None else 0.0,
                ))

            if not bars_after:
                continue

            tp1_pct = cfg.get("tp1_pct")
            tp2_pct = cfg.get("tp2_pct", cfg.get("tp_pct", 0.030))

            if tp1_pct:
                # Multi-TP 紐⑤뱶
                tp1_exit_ratio = float(cfg.get("tp1_exit_ratio", 0.5))
                l2_exit_ratio = 1.0 - tp1_exit_ratio
                tp1_price = entry_price * (1 + tp1_pct)
                tp2_price = entry_price * (1 + tp2_pct)
                sl_price_leg1 = sl_price  # ?꾩뿉???대? ATR/怨좎젙 怨꾩궛??

                # Leg1: tp1源뚯? ?쒕?
                ex1_price, ex1_reason, ex1_bars, mfe1, mae1 = simulate_exit(
                    entry_price=entry_price,
                    sl_price=sl_price_leg1,
                    tp_price=tp1_price,
                    bars_after_entry=bars_after,
                    time_stop_bars=cfg["time_stop_bars"],
                    eod_time=cfg["eod_time"],
                    side="long",
                    sl_slippage_pct=sl_slippage_pct,
                    profit_time_exit_bars=cfg.get("profit_time_exit_bars", 0),
                    profit_time_exit_min_pct=cfg.get("profit_time_exit_min_pct", 0.0),
                    early_exit_bars=cfg.get("early_exit_bars", 0),
                    early_exit_range_pct=cfg.get("early_exit_range_pct", 1.5),
                    hwm_trail_activate_pct=cfg.get("hwm_trail_activate_pct", 0.0),
                    hwm_trail_lock_ratio=cfg.get("hwm_trail_lock_ratio", 0.50),
                )

                if ex1_reason == "tp":
                    # Leg1 WIN (tp1 ?꾨떖)
                    exit_ts_l1 = bars_after[min(ex1_bars - 1, len(bars_after) - 1)].ts
                    for scenario, cost_rt in costs.items():
                        tr = build_trade_result(
                            strategy_name=STRATEGY_ID + "_L1",
                            symbol=sig.ticker,
                            signal_ts=sig.signal_ts,
                            entry_ts=entry_ts,
                            exit_ts=exit_ts_l1,
                            entry_price=entry_price,
                            exit_price=ex1_price,
                            exit_reason=ex1_reason,
                            hold_bars=ex1_bars,
                            mfe=mfe1,
                            mae=mae1,
                            cost=cost_rt,
                            cost_scenario=scenario,
                            side="long",
                            entry_reason="swing_mom_l1",
                            m_long=sig.m_long,
                            s_score=sig.s_score,
                            e_score=sig.e_long,
                            rank_score=sig.rank_score,
                            sl_price=sl_price_leg1,
                            tp_price=tp1_price,
                            signal_price=sig.entry_price_est,
                        )
                        tr = dc_replace(tr, gross_pnl=tr.gross_pnl * tp1_exit_ratio,
                                        net_pnl=tr.net_pnl * tp1_exit_ratio)
                        trades.append(tr)

                    # Leg2: breakeven SL + tp2 (+ optional 70/30 trail split)
                    remaining = bars_after[ex1_bars:]
                    if remaining:
                        sl_be          = entry_price  # breakeven
                        tp2_trail_pct  = cfg.get("tp2_trail_pct")
                        trail_ratio    = cfg.get("tp2_trail_ratio", 0.30) if tp2_trail_pct else 0.0
                        l2_fixed_w     = 1.0 - trail_ratio
                        time_stop_l2   = max(1, cfg["time_stop_bars"] - ex1_bars)

                        ex2_price, ex2_reason, ex2_bars, mfe2, mae2 = simulate_exit(
                            entry_price=entry_price,
                            sl_price=sl_be,
                            tp_price=tp2_price,
                            bars_after_entry=remaining,
                            time_stop_bars=time_stop_l2,
                            eod_time=cfg["eod_time"],
                            side="long",
                            sl_slippage_pct=sl_slippage_pct,
                        )
                        exit_ts_l2 = remaining[min(ex2_bars - 1, len(remaining) - 1)].ts
                        for scenario, cost_rt in costs.items():
                            tr = build_trade_result(
                                strategy_name=STRATEGY_ID + "_L2",
                                symbol=sig.ticker,
                                signal_ts=sig.signal_ts,
                                entry_ts=entry_ts,
                                exit_ts=exit_ts_l2,
                                entry_price=entry_price,
                                exit_price=ex2_price,
                                exit_reason=ex2_reason,
                                hold_bars=ex1_bars + ex2_bars,
                                mfe=mfe2,
                                mae=mae2,
                                cost=cost_rt,
                                cost_scenario=scenario,
                                side="long",
                                entry_reason="swing_mom_l2",
                                m_long=sig.m_long,
                                s_score=sig.s_score,
                                e_score=sig.e_long,
                                rank_score=sig.rank_score,
                                sl_price=sl_be,
                                tp_price=tp2_price,
                                signal_price=sig.entry_price_est,
                            )
                            tr = dc_replace(tr, gross_pnl=tr.gross_pnl * l2_exit_ratio,
                                            net_pnl=tr.net_pnl * l2_exit_ratio)
                            if l2_fixed_w < 1.0:
                                tr = dc_replace(tr, gross_pnl=tr.gross_pnl * l2_fixed_w,
                                                net_pnl=tr.net_pnl * l2_fixed_w)
                            trades.append(tr)

                        # Trail ?ъ뀡 (tp2_trail_ratio 鍮꾩쑉, trailing stop ?곸슜)
                        if tp2_trail_pct and trail_ratio > 0:
                            trail_fn = _make_trail_fn(entry_price, tp2_trail_pct)
                            ex2t_price, ex2t_reason, ex2t_bars, mfe2t, mae2t = simulate_exit(
                                entry_price=entry_price,
                                sl_price=sl_be,
                                tp_price=entry_price * 5.0,
                                bars_after_entry=remaining,
                                time_stop_bars=time_stop_l2,
                                eod_time=cfg["eod_time"],
                                side="long",
                                failure_check_fn=trail_fn,
                                sl_slippage_pct=sl_slippage_pct,
                            )
                            exit_ts_l2t = remaining[min(ex2t_bars - 1, len(remaining) - 1)].ts
                            for scenario, cost_rt in costs.items():
                                tr = build_trade_result(
                                    strategy_name=STRATEGY_ID + "_L2T",
                                    symbol=sig.ticker,
                                    signal_ts=sig.signal_ts,
                                    entry_ts=entry_ts,
                                    exit_ts=exit_ts_l2t,
                                    entry_price=entry_price,
                                    exit_price=ex2t_price,
                                    exit_reason=ex2t_reason,
                                    hold_bars=ex1_bars + ex2t_bars,
                                    mfe=mfe2t,
                                    mae=mae2t,
                                    cost=cost_rt,
                                    cost_scenario=scenario,
                                    side="long",
                                    entry_reason="swing_mom_l2t",
                                    m_long=sig.m_long,
                                    s_score=sig.s_score,
                                    e_score=sig.e_long,
                                    rank_score=sig.rank_score,
                                    sl_price=sl_be,
                                    tp_price=entry_price * 5.0,
                                    signal_price=sig.entry_price_est,
                                )
                                tr = dc_replace(tr, gross_pnl=tr.gross_pnl * l2_exit_ratio,
                                                net_pnl=tr.net_pnl * l2_exit_ratio)
                                tr = dc_replace(tr, gross_pnl=tr.gross_pnl * trail_ratio,
                                                net_pnl=tr.net_pnl * trail_ratio)
                                trades.append(tr)
                else:
                    # tp1 誘몃룄?? ?꾨웾 1嫄?泥섎━ (湲곗〈怨??숈씪)
                    exit_ts_l1 = bars_after[min(ex1_bars - 1, len(bars_after) - 1)].ts
                    for scenario, cost_rt in costs.items():
                        tr = build_trade_result(
                            strategy_name=STRATEGY_ID,
                            symbol=sig.ticker,
                            signal_ts=sig.signal_ts,
                            entry_ts=entry_ts,
                            exit_ts=exit_ts_l1,
                            entry_price=entry_price,
                            exit_price=ex1_price,
                            exit_reason=ex1_reason,
                            hold_bars=ex1_bars,
                            mfe=mfe1,
                            mae=mae1,
                            cost=cost_rt,
                            cost_scenario=scenario,
                            side="long",
                            entry_reason="swing_mom",
                            m_long=sig.m_long,
                            s_score=sig.s_score,
                            e_score=sig.e_long,
                            rank_score=sig.rank_score,
                            sl_price=sl_price_leg1,
                            tp_price=tp1_price,
                            signal_price=sig.entry_price_est,
                        )
                        trades.append(tr)
            else:
                # Single-TP 紐⑤뱶 (湲곗〈 濡쒖쭅)
                exit_price, exit_reason, hold_bars, mfe, mae = simulate_exit(
                    entry_price=entry_price,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    bars_after_entry=bars_after,
                    time_stop_bars=cfg["time_stop_bars"],
                    eod_time=cfg["eod_time"],
                    side="long",
                    sl_slippage_pct=sl_slippage_pct,
                    hwm_trail_activate_pct=cfg.get("hwm_trail_activate_pct", 0.0),
                    hwm_trail_lock_ratio=cfg.get("hwm_trail_lock_ratio", 0.50),
                )

                exit_ts = ""
                if hold_bars > 0 and hold_bars <= len(bars_after):
                    exit_ts = bars_after[hold_bars - 1].ts

                for scenario_name, cost_rt in costs.items():
                    tr = build_trade_result(
                        strategy_name=STRATEGY_ID,
                        symbol=sig.ticker,
                        signal_ts=sig.signal_ts,
                        entry_ts=entry_ts,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        exit_reason=exit_reason,
                        hold_bars=hold_bars,
                        mfe=mfe,
                        mae=mae,
                        cost=cost_rt,
                        side="long",
                        entry_reason=(
                            f"m_long={sig.m_long:.2f},s={sig.s_score:.2f},"
                            f"e={sig.e_long:.2f}"
                        ),
                        m_long=sig.m_long,
                        s_score=sig.s_score,
                        e_score=sig.e_long,
                        rank_score=sig.rank_score,
                        sl_price=sl_price,
                        tp_price=tp_price,
                        cost_scenario=scenario_name,
                        exit_ts=exit_ts,
                        signal_price=sig.entry_price_est,
                    )
                    trades.append(tr)

    logger.info(
        "SWING-MOM 諛깊뀒?ㅽ듃 ?꾨즺: %d trades (%d cost scenarios, top-%d/day)",
        len(trades), len(costs), top_n,
    )
    return trades


# ?? ?댁쨷 ?덈룄???ㅼ젙 (AM + PM) ?????????????????????????????????????????????
SWING_MOM_DW_CONFIG = {
    **SWING_MOM_DEFAULT_CONFIG,
    # ?? LIVE ?ㅼ슫???뚮씪誘명꽣 (SSOT: ?ш린媛 湲곗?) ??????????????????????????
    # 2026-04-22 v2: Optuna ?붿뿬 ?뚮씪誘명꽣 ?ㅼ쐲 (trial#0, score=5.46)
    # 2026-04-22 v1: 洹몃━???쒖튂 15媛?理쒖쟻???곸슜
    # 2026-04-16: TP/SL ?ㅼ쐲 ??TP1=3.0% SL=fix_1.0% 2-tier TP+trailing ?꾪솚
    # ?? ?좏샇 ?꾪꽣 ??????????????????????????????????????????????????????????
    "m_long_min":      0.50,    # 2026-04-29 relaxed: OOS Sharpe 7.09→9.42, Calmar 66→81
    "s_min":           0.50,    # 2026-04-23 retuned after bias fixes
    "min_rank_score":  0.0,     # locked (S=2.090)
    "liquidity_rank_min": 0.30, # locked (S=2.461)
    # 당일 누적 거래대금 기반 유니버스 동적 확장
    "intraday_expansion": False,                # True: 전날 top-N 미포함 종목도 당일 거래대금 조건 충족 시 포함
    "intraday_min_turnover_krw": 2_000_000_000, # 당일 신호 시점까지 누적 거래대금 하한 (기본 20억)
    # ?? ?덈룄?곕퀎 ?낅┰ ?좏깮 ????????????????????????????????????????????????
    "windows": [
        {"name": "AM",  "start": "09:30", "end": "11:00", "top_n": 5},
        {"name": "MID", "start": "11:00", "end": "13:00", "top_n": 1},
        {"name": "PM",  "start": "13:00", "end": "14:30", "top_n": 1},
    ],
    "entry_start":       "09:30",  # 09:05??9:30 (Optuna v2)
    "entry_end":         "14:30",  # 13:30??4:30 (?섎룞 議곗젙)
    "entry_delay_bars":  0,        # locked (S=4.132)
    "entry_method":      "typical_vwap",
    "top_n": 999,  # ?좏샇 ?앹꽦 ???꾪꽣 鍮꾪솢?깊솕 (?덈룄?곕퀎 ?좏깮?쇰줈 ?泥?
    # ?? 吏꾩엯 議곌굔 ??????????????????????????????????????????????????????????
    "pullback_bars_max": 3,
    "vwap_gap_tol":      0.012019,
    "e_long_min":        0.036024,
    # ?? 2-tier TP + trailing + 怨좎젙 SL ?????????????????????????????????????
    "tp1_pct":         0.040,   # 2026-04-28 OOS sweep 최적 (Sharpe +7.12, SL=3.5% 기준)
    "tp1_exit_ratio":  0.15,
    "tp_pct":          0.09,    # locked (S=2.909)
    "tp2_pct":         0.056577,
    "tp2_trail_pct":   0.05,    # locked (S=2.909)
    "tp2_trail_ratio": 0.55,
    "time_stop_bars":  120,     # locked (S=2.909)
    # ?? SL (怨좎젙) ??????????????????????????????????????????????????????????
    "sl_method":       "fixed", # locked (S=3.053)
    "sl_pct":          0.035,   # 2026-04-28 OOS sweep 최적 (Sharpe +6.71, MDD -3.46%)
    "sl_atr_mult":     None,    # ATR SL 鍮꾪솢?깊솕
    "sl_cap_pct":      0.035,
    "sl_signal_low_buf": 0.002621,
    "sl_slippage_pct": 0.001157,
    # ?? ?ъ쭊??諛⑹? ????????????????????????????????????????????????????????
    "prevent_same_day_reentry": False,  # locked (S=4.132)
    # ?? 遺꾪븷留ㅼ닔 (S2 ?ㅽ??? ??????????????????????????????????????????????
    "add_on_buy":      True,
    "add_trigger_buf": 0.0015,
    "addon_ratio":     0.42,
    "addon_mode":      "unconditional",  # "conditional" = volume+MA 조건 적용
    "addon_vol_mult":  0.5,   # 0.5 = not a dead market (MA20 is the real filter)
    "addon_vol_lookback": 20,
    "addon_ma_period": 20,
    "addon_min_conditions": 1,
    "partial_ratio":   0.51,
    # ?? 泥닿껐 ?꾩떎????????????????????????????????????????????????????????????
    "fill_fail_rate":  0.01,
    # ?? ?뱁꽣 吏묒쨷???쒕룄 ??????????????????????????????????????????????????
    "max_same_sector": 1,
    # ?? profit_time_exit: TP1 誘몃떖 ?섏씡 ?ъ???議곌린 泥?궛 ??????????????????
    # ?? EOD 媛뺤젣泥?궛 ??????????????????????????????????????????????????????
    # 2026-04-23 fixed-position beam search winner.
    "profit_time_exit_bars": 6,
    "profit_time_exit_min_pct": 0.014,
    "eod_time": "15:20",
    # ?? early_exit: TP1 ?댁쟾 議곌린泥?궛 (遊됱닔+?〓낫 / ?섍툒?댄깉) ??????????????
    # A. 遊됱닔+?〓낫: bars_held >= early_exit_bars AND 理쒓렐 遊?range < early_exit_range_pct% AND breakeven ?댁긽
    # B. ?섍툒?댄깉: foreign_net_buy < 0 AND institution_net_buy < 0 AND breakeven ?댁긽 (?쇱씠釉뚮쭔)
    "early_exit_bars":       0,
    "early_exit_range_pct":  1.5,
    "early_exit_flow_check": True,
    # ?? 30遺꾨큺 ??뚮큺+嫄곕옒?됯툒利?吏꾩엯 李⑤떒 (live? ?숈씪) ??????????????????
    "use_30min_surge_filter": True,
}


def backtest_swing_mom_dw(
    universe_5m: Dict[str, pl.DataFrame],
    context_map: ContextStateMap,
    config: Optional[dict] = None,
    cost_scenarios: Optional[Dict[str, float]] = None,
    universe_by_date: Optional[Dict[str, List[str]]] = None,
) -> List[TradeResult]:
    """SWING-MOM ?댁쨷 ?덈룄??AM+PM) 諛깊뀒?ㅽ듃.

    AM ?덈룄??09:05~11:30)? PM ?덈룄??13:00~14:30)?먯꽌 媛곴컖
    top_n???낅┰ ?좏깮?쒕떎. 媛숈? 醫낅ぉ??AM怨?PM??紐⑤몢 吏꾩엯 媛??

    config['windows'] ?ㅻ줈 ?덈룄??而ㅼ뒪?곕쭏?댁쫰 媛??
        [{"name": "AM", "start": "09:05", "end": "11:30", "top_n": 4}, ...]
    """
    base_cfg = {**SWING_MOM_DW_CONFIG, **(config or {})}
    cfg      = base_cfg
    if cfg.get("entry_method") == "signal_close":
        logger.warning(
            "SWING-MOM DW backtest does not support signal_close without look-ahead; falling back to next_open."
        )
        cfg["entry_method"] = "next_open"
    # Codex US-03: TP 鍮꾩쑉 config 寃利???KPI/Live ?숈씪 湲곗? SSOT.
    validate_tp_config(cfg)
    costs    = cost_scenarios or {"base": 0.0026}
    windows  = cfg.get("windows", SWING_MOM_DW_CONFIG["windows"])
    entry_delay_bars = max(0, int(cfg.get("entry_delay_bars", 0) or 0))
    fill_fail_rate = float(cfg.get("fill_fail_rate", 0.0) or 0.0)
    fill_fail_seed = cfg.get("fill_fail_seed", 0)
    sl_slippage_pct = max(0.0, float(cfg.get("sl_slippage_pct", 0.0) or 0.0))

    signals = generate_signals_swing_mom(universe_5m, context_map, cfg)
    if not signals:
        return []

    # RF-D10: BUG #5 fix ??live (_check_signals) ? ?숈씪??min_rank_score ?꾩쿂由??꾪꽣.
    # ?쇱씠釉??몃젅?대뜑??LIVE_PARAMS["min_rank_score"]=0.30 ???곸슜?섏?留?諛깊뀒?ㅽ듃??
    # ?곸슜?섏? ?딆븘 ?쇱씠釉뚭? 諛깊뀒?ㅽ듃??遺遺꾩쭛?⑹씠?덈뜕 ?뺥빀??踰꾧렇 ?섏젙.
    min_rank = float(cfg.get("min_rank_score", 0.0) or 0.0)
    if min_rank > 0:
        before = len(signals)
        signals = [s for s in signals if s.rank_score >= min_rank]
        logger.info(
            "SWING-MOM DW min_rank_score ?꾪꽣: %d ??%d (cutoff=%.2f)",
            before, len(signals), min_rank,
        )
        if not signals:
            return []

    from collections import defaultdict

    def _which_window(ts: str) -> Optional[dict]:
        t = ts[11:16]
        for w in windows:
            if w["start"] <= t <= w["end"]:
                return w
        return None

    # (date, window_name) ??[signals]
    slot_signals: Dict[tuple, List[SwingMOMSignal]] = defaultdict(list)
    for sig in signals:
        w = _which_window(sig.signal_ts)
        if w is None:
            continue
        slot_signals[(sig.signal_ts[:10], w["name"])].append(sig)

    trades: List[TradeResult] = []

    # Codex US-02: 媛숈? 醫낅ぉ ?쇱쨷 以묐났 吏꾩엯 諛⑹? ?듭뀡 (default False ??KPI ?숈옉 蹂댁〈).
    # True ?????쇰퀎 entered_today_tickers 濡?AM/MID/PM ?덈룄??媛?李⑤떒.
    prevent_reentry = bool(cfg.get("prevent_same_day_reentry", False))
    entered_today: Dict[str, set] = defaultdict(set)  # {date_str: {ticker, ...}}

    for (date, win_name), slot_sigs in sorted(slot_signals.items()):
        # ?덈룄???뺤쓽 李얘린
        win_def = next((w for w in windows if w["name"] == win_name), None)
        top_n_w = win_def["top_n"] if win_def else 4

        # ?쇰퀎 ?좊땲踰꾩뒪 ?꾪꽣 (universe_by_date媛 ?덉쑝硫??대떦 ??top-N 醫낅ぉ留??덉슜)
        allowed = set(universe_by_date[date]) if universe_by_date and date in universe_by_date else None

        # ?щ’ ??醫낅ぉ蹂?理쒓퀬 rank_score ?좏샇留??좎?
        eligible_sigs: List[SwingMOMSignal] = []
        best_per_ticker: Dict[str, SwingMOMSignal] = {}
        for sig in sorted(slot_sigs, key=lambda s: (s.signal_ts, -s.rank_score, s.ticker)):
            if allowed is not None and sig.ticker not in allowed:
                # 당일 누적 거래대금 기반 동적 확장
                if cfg.get('intraday_expansion', False):
                    _df_exp = universe_5m.get(sig.ticker)
                    if _df_exp is None:
                        continue
                    _tv = _calc_intraday_turnover(_df_exp, sig.signal_ts)
                    if _tv < cfg.get('intraday_min_turnover_krw', 2_000_000_000):
                        continue
                    # 조건 충족: 당일 거래대금 기반으로 유니버스 확장 진입 허용
                else:
                    continue
            # US-02: 媛숈? ???대? 吏꾩엯??醫낅ぉ? ?꾩냽 ?덈룄?곗뿉???ㅽ궢
            if prevent_reentry and sig.ticker in entered_today[date]:
                continue
            existing = best_per_ticker.get(sig.ticker)
            if existing is None:
                best_per_ticker[sig.ticker] = sig
                eligible_sigs.append(sig)
            elif existing.signal_ts == sig.signal_ts and sig.rank_score > existing.rank_score:
                best_per_ticker[sig.ticker] = sig
                for idx, prev in enumerate(eligible_sigs):
                    if prev.ticker == sig.ticker and prev.signal_ts == sig.signal_ts:
                        eligible_sigs[idx] = sig
                        break

        selected = _chronological_unique_top_n(eligible_sigs, top_n_w)
        # Codex US-02 (REVISE): selection ?④퀎 add ?쒓굅.
        # ?ㅼ젣 trade 媛 trades ??append ??吏곹썑??add ??fill ?ㅽ뙣/?곗씠??遺議?
        # entry_idx 珥덇낵/entry_price invalid/SL>=entry/TP<=entry ?깆쑝濡?吏꾩엯??
        # ?깅┰?섏? ?딆쑝硫?媛숈? 醫낅ぉ???꾩냽 ?덈룄?곗뿉???ㅼ떆 ?쒕룄?????덉뼱????
        # (?쇱씠釉?trader 媛 二쇰Ц ?깃났 ??add ?섎뒗 ?숈옉怨??쇱튂).

        for sig in selected:
            # US-02: 媛숈? ???대? 吏꾩엯??醫낅ぉ? ?꾩냽 ?덈룄?곗뿉???ㅽ궢.
            # selection ?④퀎?먯꽌 1李??꾪꽣留곷룄 ?덉?留? 媛숈? ?덈룄?????ъ쭊??
            # ?쒕룄(theoretical) ??李⑤떒.
            if prevent_reentry and sig.ticker in entered_today[date]:
                continue
            if _should_skip_fill(sig.ticker, sig.signal_ts, fill_fail_rate, fill_fail_seed):
                continue

            df_5m = universe_5m.get(sig.ticker)
            if df_5m is None:
                continue

            # 30遺꾨큺 ??뚮큺+嫄곕옒?됯툒利??꾪꽣 (live? ?숈씪 濡쒖쭅)
            # signal_idx+1 源뚯???遊됰쭔 ?꾨떖 ??lookahead 諛⑹?
            if cfg.get("use_30min_surge_filter", True):
                df_up_to_signal = df_5m.slice(0, sig.signal_idx + 1)
                if _has_30min_surge(df_up_to_signal):
                    continue

            n = len(df_5m)
            dt_list = [_ts_str(v) for v in df_5m["datetime"].cast(pl.Utf8).to_list()]
            opens   = df_5m["open"].cast(pl.Float64).to_list()
            highs   = df_5m["high"].cast(pl.Float64).to_list()
            lows    = df_5m["low"].cast(pl.Float64).to_list()
            closes  = df_5m["close"].cast(pl.Float64).to_list()
            volumes = df_5m["volume"].cast(pl.Float64).to_list()

            entry_idx = sig.signal_idx + 1 + entry_delay_bars
            if entry_idx >= n:
                continue

            entry_ts    = dt_list[entry_idx]
            entry_price = opens[entry_idx]
            if entry_price is None or entry_price <= 0:
                continue
            if entry_ts == sig.signal_ts:
                continue

            # ?? entry_method: 吏꾩엯媛 ?곗텧 諛⑹떇 ?좏깮 (湲곕낯: next_open) ?
            entry_method = cfg.get("entry_method", "next_open")
            if entry_method == "signal_close":
                entry_method = "next_open"
            if entry_method != "next_open":
                sig_c    = closes[sig.signal_idx] or 0.0
                sig_h    = highs[sig.signal_idx]  or 0.0
                sig_l    = lows[sig.signal_idx]   or 0.0
                next_low = lows[entry_idx]        or entry_price

                if entry_method == "signal_close" and sig_c > 0:
                    # ?좏샇遊?醫낃? 吏꾩엯 (?대줎??理쒖쟻)
                    entry_price = sig_c

                elif entry_method == "typical_vwap" and sig_c > 0:
                    # ?좏샇遊?(H+L+C)/3 吏?뺢? ???ㅼ쓬遊됱씠 洹??댄븯硫?泥닿껐
                    typical = (sig_h + sig_l + sig_c) / 3
                    if next_low <= typical:
                        entry_price = typical
                    # else: ?ㅼ쓬遊됱씠 typical ????next_open ?좎?

                elif entry_method == "limit_03pct" and sig_c > 0:
                    # ?좏샇醫낃? +0.3% 吏?뺢?, 誘몄껜寃?skip
                    limit = sig_c * 1.003
                    if opens[entry_idx] <= limit:
                        entry_price = opens[entry_idx]
                    elif next_low <= limit:
                        entry_price = limit
                    else:
                        continue  # 誘몄껜寃?

                elif entry_method == "limit_01pct" and sig_c > 0:
                    # ?좏샇醫낃? +0.1% 吏?뺢?, 誘몄껜寃?skip
                    limit = sig_c * 1.001
                    if opens[entry_idx] <= limit:
                        entry_price = opens[entry_idx]
                    elif next_low <= limit:
                        entry_price = limit
                    else:
                        continue  # 誘몄껜寃?

                elif entry_method == "limit_minus3tick":
                    # bar[n+1].open - 3?멸? 吏?뺢?, 1-bar ?湲???誘몄껜寃??ㅽ궢
                    ref_open = opens[entry_idx]
                    if ref_open is None or ref_open <= 0:
                        continue
                    limit_px = ref_open - 3 * _tick_size(ref_open)
                    ep, ets = _try_limit_fill(entry_idx, limit_px, opens, lows, dt_list, n)
                    if ep is None:
                        ep, ets = _try_limit_fill(entry_idx + 1, limit_px, opens, lows, dt_list, n)
                    if ep is None:
                        continue  # 誘몄껜寃????ㅽ궢
                    entry_price = ep
                    entry_ts    = ets
                    entry_idx   = entry_idx + 1 if ets == dt_list[min(entry_idx + 1, n - 1)] else entry_idx
                elif entry_method == "limit_minus_pct":
                    # next_open * (1 - entry_limit_pct) limit order, 2bar wait
                    ref_open = opens[entry_idx]
                    if ref_open is None or ref_open <= 0:
                        continue
                    pct = cfg.get("entry_limit_pct", 0.005)
                    limit_px = ref_open * (1 - pct)
                    ep, ets = _try_limit_fill(entry_idx, limit_px, opens, lows, dt_list, n)
                    if ep is None:
                        ep, ets = _try_limit_fill(entry_idx + 1, limit_px, opens, lows, dt_list, n)
                    if ep is None:
                        continue
                    entry_price = ep
                    entry_ts    = ets
                    entry_idx   = entry_idx + 1 if ets == dt_list[min(entry_idx + 1, n - 1)] else entry_idx

            tp_pct = cfg["tp_pct"]
            # 珥덇린 guard??sl/tp (entry_price 湲곗?)
            if cfg.get("sl_atr_mult") is not None:
                atr_vals     = _calc_atr(highs, lows, closes, cfg.get("atr_period", 14))
                atr_at_entry = atr_vals[sig.signal_idx]
                sl_price     = _atr_sl(entry_price, atr_at_entry, cfg["sl_atr_mult"], cfg["sl_cap_pct"])
            elif cfg.get("sl_method") == "signal_low":
                sig_low = lows[sig.signal_idx] or 0.0
                buf     = cfg.get("sl_signal_low_buf", 0.002)
                cap_pct = cfg.get("sl_cap_pct", 0.020)
                raw_sl  = sig_low * (1 - buf) if sig_low > 0 else entry_price * (1 - cfg["sl_pct"])
                sl_price = max(raw_sl, entry_price * (1 - cap_pct))
            else:
                sl_price = entry_price * (1 - cfg["sl_pct"])
            tp_price = entry_price * (1 + tp_pct)

            if sl_price >= entry_price or tp_price <= entry_price:
                continue

            bars_after: List[BarData] = []
            entry_date = entry_ts[:10]
            max_exit_idx = min(entry_idx + cfg["time_stop_bars"] + 10, n)
            for j in range(entry_idx, max_exit_idx):
                if dt_list[j][:10] != entry_date:
                    break
                bars_after.append(BarData(
                    ts=dt_list[j],
                    open=opens[j]   if opens[j]   is not None else 0.0,
                    high=highs[j]   if highs[j]   is not None else 0.0,
                    low=lows[j]     if lows[j]     is not None else 0.0,
                    close=closes[j] if closes[j]   is not None else 0.0,
                ))

            if not bars_after:
                continue

            # 분할매수 (add-on buy): add_on_buy=True 시 신호봉 저점 아래에서 2차 진입
            # addon_mode="conditional" 이면 거래량 급증 + MA 추세 조건을 추가로 확인한다
            avg_entry = entry_price  # 기본값: 1차 진입가
            if cfg.get("add_on_buy", False):
                sig_low_val = lows[sig.signal_idx] or 0.0
                add_buf = cfg.get("add_trigger_buf", 0.001)
                add_trigger_px = sig_low_val * (1 - add_buf) if sig_low_val > 0 else None

                if add_trigger_px and add_trigger_px < entry_price:
                    sl_price_1 = entry_price * (1 - cfg["sl_pct"])
                    tp1_check = (entry_price * (1 + cfg["tp1_pct"])
                                 if cfg.get("tp1_pct") else None)

                    # conditional 모드: 신호봉 이전 거래량 평균 + MA 사전 계산
                    addon_mode = cfg.get("addon_mode", "unconditional")
                    _avg_vol = None
                    _ma_slow = None
                    if addon_mode == "conditional":
                        lookback = cfg.get("addon_vol_lookback", 20)
                        _vs = max(0, sig.signal_idx - lookback)
                        _vols = [volumes[k] for k in range(_vs, sig.signal_idx) if volumes[k]]
                        _avg_vol = sum(_vols) / len(_vols) if _vols else None

                        ma_p = cfg.get("addon_ma_period", 20)
                        _cs = max(0, sig.signal_idx - ma_p + 1)
                        _cls = [closes[k] for k in range(_cs, sig.signal_idx + 1) if closes[k]]
                        _ma_slow = sum(_cls) / len(_cls) if _cls else None

                    for _k, _bar in enumerate(bars_after):
                        if _bar.low <= sl_price_1:
                            break
                        if tp1_check and _bar.high >= tp1_check:
                            break
                        if _bar.low <= add_trigger_px:
                            # conditional 모드: 거래량급증(§3.15 Donchian) + MA추세(§3.13 Three-MA)
                            if addon_mode == "conditional":
                                _bar_idx = entry_idx + _k
                                _bar_vol = volumes[_bar_idx] if _bar_idx < len(volumes) else 0.0
                                vol_mult = cfg.get("addon_vol_mult", 1.5)
                                vol_ok = (_avg_vol is not None and _avg_vol > 0
                                          and _bar_vol >= _avg_vol * vol_mult)
                                ma_ok = (_ma_slow is not None and _bar.close >= _ma_slow * 0.998)
                                min_cond = cfg.get("addon_min_conditions", 1)
                                if (int(vol_ok) + int(ma_ok)) < min_cond:
                                    break  # 조건 미달 → 분할매수 건너뜀
                            _ar = cfg.get("addon_ratio", 0.5)
                            avg_entry = entry_price * _ar + add_trigger_px * (1 - _ar)
                            break            # avg_entry 湲곗??쇰줈 sl_price / tp_price ?ш퀎??
            if avg_entry != entry_price:
                if cfg.get("sl_atr_mult") is not None:
                    sl_price = _atr_sl(avg_entry, atr_at_entry, cfg["sl_atr_mult"], cfg["sl_cap_pct"])
                elif cfg.get("sl_method") == "signal_low":
                    sig_low = lows[sig.signal_idx] or 0.0
                    buf     = cfg.get("sl_signal_low_buf", 0.002)
                    cap_pct = cfg.get("sl_cap_pct", 0.020)
                    raw_sl  = sig_low * (1 - buf) if sig_low > 0 else avg_entry * (1 - cfg["sl_pct"])
                    sl_price = max(raw_sl, avg_entry * (1 - cap_pct))
                else:
                    sl_price = avg_entry * (1 - cfg["sl_pct"])
                tp_price = avg_entry * (1 + tp_pct)

                if sl_price >= avg_entry or tp_price <= avg_entry:
                    continue

            # Codex US-02 (REVISE): 吏꾩엯???뺤젙???쒖젏??dedup set 媛깆떊.
            # ?ш린源뚯? ?꾨떖?덈떎??寃껋? fill skip / ?곗씠??遺議?/ entry_idx 珥덇낵 /
            # entry_price invalid / SL/TP 臾댄슚 ??紐⑤뱺 entry-fail 遺꾧린瑜??듦낵??
            # ?곸뼱??1媛쒖쓽 trade 媛 trades ??append ??寃껋씠 蹂댁옣??
            # ?쇱씠釉?trader 媛 二쇰Ц ?깃났 ??add ?섎뒗 ?숈옉怨??깃?.
            if prevent_reentry:
                entered_today[date].add(sig.ticker)

            tp1_pct = cfg.get("tp1_pct")
            tp2_pct = cfg.get("tp2_pct", cfg.get("tp_pct", 0.030))

            if tp1_pct:
                tp1_exit_ratio = float(cfg.get("tp1_exit_ratio", 0.5))
                l2_exit_ratio = 1.0 - tp1_exit_ratio
                tp1_price = avg_entry * (1 + tp1_pct)
                tp2_price = avg_entry * (1 + tp2_pct)
                sl_leg1   = sl_price

                ex1_price, ex1_reason, ex1_bars, mfe1, mae1 = simulate_exit(
                    entry_price=avg_entry, sl_price=sl_leg1, tp_price=tp1_price,
                    bars_after_entry=bars_after, time_stop_bars=cfg["time_stop_bars"],
                    eod_time=cfg["eod_time"], side="long",
                    sl_slippage_pct=sl_slippage_pct,
                    profit_time_exit_bars=cfg.get("profit_time_exit_bars", 0),
                    profit_time_exit_min_pct=cfg.get("profit_time_exit_min_pct", 0.0),
                    early_exit_bars=cfg.get("early_exit_bars", 0),
                    early_exit_range_pct=cfg.get("early_exit_range_pct", 1.5),
                    hwm_trail_activate_pct=cfg.get("hwm_trail_activate_pct", 0.0),
                    hwm_trail_lock_ratio=cfg.get("hwm_trail_lock_ratio", 0.50),
                )

                if ex1_reason == "tp":
                    exit_ts_l1 = bars_after[min(ex1_bars - 1, len(bars_after) - 1)].ts
                    for scenario, cost_rt in costs.items():
                        tr = build_trade_result(
                            strategy_name=f"{STRATEGY_ID}_DW_{win_name}_L1",
                            symbol=sig.ticker, signal_ts=sig.signal_ts,
                            entry_ts=entry_ts, exit_ts=exit_ts_l1,
                            entry_price=avg_entry, exit_price=ex1_price,
                            exit_reason=ex1_reason, hold_bars=ex1_bars,
                            mfe=mfe1, mae=mae1, cost=cost_rt, cost_scenario=scenario,
                            side="long", entry_reason=f"sm_dw_{win_name}_l1",
                            m_long=sig.m_long, s_score=sig.s_score,
                            e_score=sig.e_long, rank_score=sig.rank_score,
                            sl_price=sl_leg1, tp_price=tp1_price,
                            signal_price=sig.entry_price_est,
                        )
                        tr = dc_replace(tr, gross_pnl=tr.gross_pnl * tp1_exit_ratio,
                                        net_pnl=tr.net_pnl * tp1_exit_ratio)
                        trades.append(tr)

                    remaining = bars_after[ex1_bars:]
                    if remaining:
                        sl_be         = avg_entry
                        tp2_trail_pct = cfg.get("tp2_trail_pct")
                        trail_ratio   = cfg.get("tp2_trail_ratio", 0.30) if tp2_trail_pct else 0.0
                        l2_fixed_w    = 1.0 - trail_ratio
                        time_stop_l2  = max(1, cfg["time_stop_bars"] - ex1_bars)

                        ex2_price, ex2_reason, ex2_bars, mfe2, mae2 = simulate_exit(
                            entry_price=avg_entry, sl_price=sl_be, tp_price=tp2_price,
                            bars_after_entry=remaining,
                            time_stop_bars=time_stop_l2,
                            eod_time=cfg["eod_time"], side="long",
                            sl_slippage_pct=sl_slippage_pct,
                        )
                        exit_ts_l2 = remaining[min(ex2_bars - 1, len(remaining) - 1)].ts
                        for scenario, cost_rt in costs.items():
                            tr = build_trade_result(
                                strategy_name=f"{STRATEGY_ID}_DW_{win_name}_L2",
                                symbol=sig.ticker, signal_ts=sig.signal_ts,
                                entry_ts=entry_ts, exit_ts=exit_ts_l2,
                                entry_price=avg_entry, exit_price=ex2_price,
                                exit_reason=ex2_reason,
                                hold_bars=ex1_bars + ex2_bars,
                                mfe=mfe2, mae=mae2, cost=cost_rt, cost_scenario=scenario,
                                side="long", entry_reason=f"sm_dw_{win_name}_l2",
                                m_long=sig.m_long, s_score=sig.s_score,
                                e_score=sig.e_long, rank_score=sig.rank_score,
                                sl_price=sl_be, tp_price=tp2_price,
                                signal_price=sig.entry_price_est,
                            )
                            tr = dc_replace(tr, gross_pnl=tr.gross_pnl * l2_exit_ratio,
                                            net_pnl=tr.net_pnl * l2_exit_ratio)
                            if l2_fixed_w < 1.0:
                                tr = dc_replace(tr, gross_pnl=tr.gross_pnl * l2_fixed_w,
                                                net_pnl=tr.net_pnl * l2_fixed_w)
                            trades.append(tr)

                        # Trail ?ъ뀡 (tp2_trail_ratio 鍮꾩쑉, trailing stop ?곸슜)
                        if tp2_trail_pct and trail_ratio > 0:
                            trail_fn = _make_trail_fn(avg_entry, tp2_trail_pct)
                            ex2t_price, ex2t_reason, ex2t_bars, mfe2t, mae2t = simulate_exit(
                                entry_price=avg_entry, sl_price=sl_be,
                                tp_price=avg_entry * 5.0,
                                bars_after_entry=remaining,
                                time_stop_bars=time_stop_l2,
                                eod_time=cfg["eod_time"], side="long",
                                failure_check_fn=trail_fn,
                                sl_slippage_pct=sl_slippage_pct,
                            )
                            exit_ts_l2t = remaining[min(ex2t_bars - 1, len(remaining) - 1)].ts
                            for scenario, cost_rt in costs.items():
                                tr = build_trade_result(
                                    strategy_name=f"{STRATEGY_ID}_DW_{win_name}_L2T",
                                    symbol=sig.ticker, signal_ts=sig.signal_ts,
                                    entry_ts=entry_ts, exit_ts=exit_ts_l2t,
                                    entry_price=avg_entry, exit_price=ex2t_price,
                                    exit_reason=ex2t_reason,
                                    hold_bars=ex1_bars + ex2t_bars,
                                    mfe=mfe2t, mae=mae2t, cost=cost_rt, cost_scenario=scenario,
                                    side="long", entry_reason=f"sm_dw_{win_name}_l2t",
                                    m_long=sig.m_long, s_score=sig.s_score,
                                    e_score=sig.e_long, rank_score=sig.rank_score,
                                    sl_price=sl_be, tp_price=avg_entry * 5.0,
                                    signal_price=sig.entry_price_est,
                                )
                                tr = dc_replace(tr, gross_pnl=tr.gross_pnl * l2_exit_ratio,
                                                net_pnl=tr.net_pnl * l2_exit_ratio)
                                tr = dc_replace(tr, gross_pnl=tr.gross_pnl * trail_ratio,
                                                net_pnl=tr.net_pnl * trail_ratio)
                                trades.append(tr)
                else:
                    exit_ts_l1 = bars_after[min(ex1_bars - 1, len(bars_after) - 1)].ts
                    for scenario, cost_rt in costs.items():
                        trades.append(build_trade_result(
                            strategy_name=f"{STRATEGY_ID}_DW_{win_name}",
                            symbol=sig.ticker, signal_ts=sig.signal_ts,
                            entry_ts=entry_ts, exit_ts=exit_ts_l1,
                            entry_price=avg_entry, exit_price=ex1_price,
                            exit_reason=ex1_reason, hold_bars=ex1_bars,
                            mfe=mfe1, mae=mae1, cost=cost_rt, cost_scenario=scenario,
                            side="long", entry_reason=f"sm_dw_{win_name}",
                            m_long=sig.m_long, s_score=sig.s_score,
                            e_score=sig.e_long, rank_score=sig.rank_score,
                            sl_price=sl_leg1, tp_price=tp1_price,
                            signal_price=sig.entry_price_est,
                        ))
            else:
                exit_price, exit_reason, hold_bars, mfe, mae = simulate_exit(
                    entry_price=avg_entry, sl_price=sl_price, tp_price=tp_price,
                    bars_after_entry=bars_after, time_stop_bars=cfg["time_stop_bars"],
                    eod_time=cfg["eod_time"], side="long",
                    sl_slippage_pct=sl_slippage_pct,
                )
                exit_ts = bars_after[min(hold_bars - 1, len(bars_after) - 1)].ts if hold_bars > 0 else ""
                for scenario, cost_rt in costs.items():
                    trades.append(build_trade_result(
                        strategy_name=f"{STRATEGY_ID}_DW_{win_name}",
                        symbol=sig.ticker, signal_ts=sig.signal_ts,
                        entry_ts=entry_ts, exit_ts=exit_ts,
                        entry_price=avg_entry, exit_price=exit_price,
                        exit_reason=exit_reason, hold_bars=hold_bars,
                        mfe=mfe, mae=mae, cost=cost_rt, cost_scenario=scenario,
                        side="long", entry_reason=f"sm_dw_{win_name}",
                        m_long=sig.m_long, s_score=sig.s_score,
                        e_score=sig.e_long, rank_score=sig.rank_score,
                        sl_price=sl_price, tp_price=tp_price,
                        signal_price=sig.entry_price_est,
                    ))

    base_trades = [t for t in trades if t.cost_scenario == "base"]
    n_days = len(set(s.signal_ts[:10] for s in signals))
    logger.info(
        "SWING-MOM DW 諛깊뀒?ㅽ듃 ?꾨즺: %d base trades (%d days, %.1f/day)",
        len(base_trades), n_days, len(base_trades) / max(n_days, 1),
    )
    return trades



