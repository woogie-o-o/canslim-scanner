"""engine/context_state.py
M/S/D/E 공통 상태 변수 계산기.

30분봉 기반:
  M_long / M_short : Market Pressure Score
  S                : Sector Pressure Score
  D                : Distortion / Residual Score

5분봉 기반 E score는 execution_validator.py에서 별도 계산.
DailyContext c_score는 mr_common.calc_c_score()로 계산.

사용법:
  ctx_map = build_context_states(bars_30m_index, bars_30m_universe, sector_map, ...)
  ctx = ctx_map[(ticker, bar_ts)]
"""
from __future__ import annotations

import heapq
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ContextState:
    """30분봉 기준 종목별 상태 변수."""
    bar_ts: str              # "2024-03-15 10:30"
    ticker: str

    # M (Market Pressure)
    m_long: float = 0.0      # [0, 1]
    m_short: float = 0.0     # [0, 1]

    # S (Sector Pressure)
    s_score: float = 0.0     # [0, 1]

    # D (Distortion / Residual)
    d_score: float = 0.0     # [0, 1] percentile rank (bar_ts별 cross-sectional)

    # DailyContext soft prior
    c_score: float = 0.0     # [-1, 1]

    # 메타
    breadth_ratio: float = 0.5
    liquidity_rank: float = 0.5
    sector_name: str = "unknown"
    m_vwap_up: bool = False
    m_ema_up: bool = False
    m_idx_up: bool = False
    m_breadth_ok: bool = False
    m_index_ret: float = 0.0


ContextStateMap = Dict[Tuple[str, str], ContextState]


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _ts_str(val) -> str:
    return str(val)[:16]


def _calc_ema_list(values: List[float], period: int = 20) -> List[Optional[float]]:
    """EMA 계산. seed = 첫 period개 SMA."""
    n = len(values)
    ema: List[Optional[float]] = [None] * n
    if n < period:
        return ema
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    ema[period - 1] = seed
    for i in range(period, n):
        prev = ema[i - 1]
        if prev is None:
            continue
        ema[i] = values[i] * k + prev * (1 - k)
    return ema


def _calc_vwap_intraday(
    dt_list: List[str],
    closes: List[float],
    volumes: List[float],
) -> List[Optional[float]]:
    """일중 VWAP (날짜 바뀌면 리셋)."""
    n = len(closes)
    vwap: List[Optional[float]] = [None] * n
    cum_pv = cum_v = 0.0
    cur_date: Optional[str] = None
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


def _rank_0_to_1(values: List[float]) -> List[float]:
    """값 목록을 0~1 rank로 변환. O(N log N) — list.index() O(N²) 제거."""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [0.5]
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * n
    i = 0
    while i < n:
        val = indexed[i][1]
        j = i
        while j + 1 < n and indexed[j + 1][1] == val:
            j += 1
        avg_rank = (i + j) / 2 / (n - 1)
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


# ---------------------------------------------------------------------------
# M (Market Pressure Score) 계산
# ---------------------------------------------------------------------------

def _compute_m_both(
    index_closes: List[float],
    index_highs: List[float],
    index_dt_list: List[str],
    index_volumes: List[float],
    breadth_ratios: Dict[str, float],
    config: Optional[dict] = None,
) -> Tuple[Dict[str, Tuple[float, float]], Dict[str, Dict[str, object]]]:
    """Single VWAP/EMA pass → (m_scores_dict, m_components_dict).

    Eliminates the duplicate _calc_vwap_intraday + _calc_ema_list calls that
    compute_m_scores and inspect_m_score_components previously made separately.
    """
    cfg = config or {}
    ema_period = cfg.get("ema_period", 20)
    ema_lookback = cfg.get("ema_slope_lookback", 3)
    breadth_long_threshold = cfg.get("breadth_long_threshold", 0.50)
    breadth_short_threshold = cfg.get("breadth_short_threshold", 0.40)

    n = len(index_closes)
    scores: Dict[str, Tuple[float, float]] = {}
    diags: Dict[str, Dict[str, object]] = {}

    if n == 0:
        return scores, diags

    min_bars_needed = ema_period + ema_lookback
    use_ema = True
    if n < min_bars_needed:
        if n >= ema_lookback + 2:
            ema_period = max(n - ema_lookback - 1, 3)
            logger.debug("M_score adaptive EMA: period=%d (봉=%d, 원래=%d)",
                         ema_period, n, cfg.get("ema_period", 20))
        else:
            use_ema = False
            logger.warning(
                "M_score fallback (no EMA): n_bars=%d < min=%d — 3팩터 간이 계산",
                n, ema_lookback + 2,
            )

    vwap = _calc_vwap_intraday(index_dt_list, index_closes, index_volumes)
    ema20 = _calc_ema_list(index_closes, ema_period) if use_ema else [None] * n

    for i in range(n):
        ts = index_dt_list[i]
        c = index_closes[i]
        if c is None:
            scores[ts] = (0.0, 0.0)
            diags[ts] = {"vwap_up": False, "ema_up": False, "idx_up": False,
                         "breadth_ok": False, "index_ret": 0.0, "has_ema": False}
            continue

        vw = vwap[i] if vwap[i] is not None else c
        prev_c = index_closes[i - 1] if i > 0 and index_closes[i - 1] not in (None, 0.0) else None
        idx_ret = (c / prev_c - 1.0) if (prev_c is not None and prev_c > 0) else 0.0
        br = breadth_ratios.get(ts, 0.5) or 0.5

        e20_curr = ema20[i]
        e20_prev = ema20[i - ema_lookback] if use_ema and i >= ema_lookback else None
        has_ema = bool(e20_curr is not None and e20_prev is not None)
        has_vwap = abs(c - vw) > 1e-6
        has_prev = i > 0

        if has_ema:
            # ── 정상 4팩터 M_score (VWAP + EMA slope + idx_ret + breadth) ──
            score_long = 0.0
            if c > vw:
                score_long += 1.0
            if e20_curr > e20_prev:
                score_long += 1.0
            if idx_ret > 0:
                score_long += 1.0
            if br > breadth_long_threshold:
                score_long += 1.0
            m_long = score_long / 4.0

            score_short = 0.0
            if c < vw:
                score_short += 1.0
            if e20_curr < e20_prev:
                score_short += 1.0
            if i >= 2:
                h_now = index_highs[i] if index_highs[i] is not None else 0
                h_m1 = index_highs[i - 1] if index_highs[i - 1] is not None else 0
                h_m2 = index_highs[i - 2] if index_highs[i - 2] is not None else 0
                if h_now < h_m1 < h_m2:
                    score_short += 1.0
            if br < breadth_short_threshold:
                score_short += 1.0
            m_short = score_short / 4.0
        else:
            # ── EMA 미준비 구간: 3팩터 fallback (VWAP + idx_ret + breadth) ──
            s_long = 0.0
            n_factors = 1
            if br > breadth_long_threshold:
                s_long += 1.0
            if has_vwap:
                n_factors += 1
                if c > vw:
                    s_long += 1.0
            if has_prev:
                n_factors += 1
                if idx_ret > 0:
                    s_long += 1.0
            m_long = s_long / n_factors

            s_short = 0.0
            n_factors_s = 1
            if br < breadth_short_threshold:
                s_short += 1.0
            if has_vwap:
                n_factors_s += 1
                if c < vw:
                    s_short += 1.0
            if has_prev:
                n_factors_s += 1
                if idx_ret < 0:
                    s_short += 1.0
            m_short = s_short / n_factors_s

        scores[ts] = (m_long, m_short)
        diags[ts] = {
            "vwap_up": bool(has_vwap and c > vw),
            "ema_up": bool(has_ema and e20_curr > e20_prev),
            "idx_up": bool(has_prev and idx_ret > 0),
            "breadth_ok": bool(br > breadth_long_threshold),
            "index_ret": float(idx_ret),
            "has_ema": has_ema,
        }

    return scores, diags


def compute_m_scores(
    index_closes: List[float],
    index_highs: List[float],
    index_dt_list: List[str],
    index_volumes: List[float],
    breadth_ratios: Dict[str, float],
    config: Optional[dict] = None,
) -> Dict[str, Tuple[float, float]]:
    """index 30분봉 → bar_ts별 (M_long, M_short) 계산."""
    return _compute_m_both(
        index_closes, index_highs, index_dt_list, index_volumes, breadth_ratios, config,
    )[0]


def inspect_m_score_components(
    index_closes: List[float],
    index_highs: List[float],
    index_dt_list: List[str],
    index_volumes: List[float],
    breadth_ratios: Dict[str, float],
    config: Optional[dict] = None,
) -> Dict[str, Dict[str, object]]:
    """Return per-bar M_long factor diagnostics using the same thresholds as compute_m_scores."""
    return _compute_m_both(
        index_closes, index_highs, index_dt_list, index_volumes, breadth_ratios, config,
    )[1]


# ---------------------------------------------------------------------------
# S (Sector Pressure Score) 계산
# ---------------------------------------------------------------------------

def compute_s_scores(
    ticker_returns: Dict[str, Dict[str, float]],
    index_returns: Dict[str, float],
    sector_map: Dict[str, str],
    config: Optional[dict] = None,
) -> Dict[Tuple[str, str], float]:
    """종목×bar_ts별 S score 계산.

    Parameters
    ----------
    ticker_returns : {ticker: {bar_ts: intraday_return}}
    index_returns  : {bar_ts: index_intraday_return}
    sector_map     : {ticker: sector_name}

    Returns
    -------
    {(sector_name, bar_ts): s_score}
    """
    cfg = config or {}
    w_rel = cfg.get("w_sector_rel_strength", 0.4)
    w_breadth = cfg.get("w_sector_breadth", 0.3)
    w_leader = cfg.get("w_sector_leader", 0.15)
    w_align = cfg.get("w_sector_alignment", 0.15)

    result: Dict[Tuple[str, str], float] = {}

    # 모든 bar_ts 수집
    all_bar_ts = set()
    for tr in ticker_returns.values():
        all_bar_ts.update(tr.keys())

    for bar_ts in sorted(all_bar_ts):
        idx_ret = index_returns.get(bar_ts, 0.0)

        # 섹터별 종목 수익률 그룹
        sector_groups: Dict[str, List[Tuple[str, float]]] = {}
        for ticker, returns in ticker_returns.items():
            ret = returns.get(bar_ts)
            if ret is None:
                continue
            sec = sector_map.get(ticker, "unknown")
            sector_groups.setdefault(sec, []).append((ticker, ret))

        for sec, ticker_rets in sector_groups.items():
            if len(ticker_rets) < 2:
                result[(sec, bar_ts)] = 0.5
                continue

            rets = [r for _, r in ticker_rets]
            sec_avg_ret = sum(rets) / len(rets)

            # 1. 상대 강도: sector avg vs index
            rel_strength = sec_avg_ret - idx_ret
            # 정규화 [-0.02, 0.02] → [-1, 1]
            rel_norm = max(-1.0, min(1.0, rel_strength / 0.02))
            rel_01 = (rel_norm + 1.0) / 2.0  # [0, 1]

            # 2. 섹터 breadth
            up_count = sum(1 for r in rets if r > 0)
            sec_breadth = up_count / len(rets)

            # 3. leader breakout (섹터 내 최고 수익률 종목이 양의 수익률)
            max_ret = max(rets)
            leader_breakout = 1.0 if max_ret > 0.005 else 0.0

            # 4. alignment (상위 3종목 모두 양수)
            top3 = heapq.nlargest(min(3, len(rets)), rets)
            alignment = 1.0 if all(r > 0 for r in top3) else 0.0

            s = (w_rel * rel_01 +
                 w_breadth * sec_breadth +
                 w_leader * leader_breakout +
                 w_align * alignment)

            result[(sec, bar_ts)] = max(0.0, min(1.0, s))

    return result


# ---------------------------------------------------------------------------
# D (Distortion / Residual Score) 계산
# ---------------------------------------------------------------------------

def compute_d_scores(
    ticker_returns: Dict[str, Dict[str, float]],
    index_returns: Dict[str, float],
    sector_map: Dict[str, str],
    config: Optional[dict] = None,
) -> Dict[Tuple[str, str], float]:
    """종목×bar_ts별 D score (잔차 z-score) 계산.

    D = (stock_ret - beta * index_ret - sector_avg_ret) / sigma

    beta는 단순화하여 1.0 사용 (장중 단기이므로).
    sigma는 해당 bar_ts의 유니버스 cross-sectional std.

    Returns
    -------
    {(ticker, bar_ts): d_score_zscore}
    """
    cfg = config or {}
    beta = cfg.get("beta", 1.0)

    result: Dict[Tuple[str, str], float] = {}

    all_bar_ts = set()
    for tr in ticker_returns.values():
        all_bar_ts.update(tr.keys())

    for bar_ts in sorted(all_bar_ts):
        idx_ret = index_returns.get(bar_ts, 0.0)

        # 단일 패스: 섹터 그룹과 잔차 계산을 위한 (ret, sec) 동시 수집
        sector_groups: Dict[str, List[float]] = {}
        _raw: Dict[str, tuple] = {}
        for ticker, returns in ticker_returns.items():
            ret = returns.get(bar_ts)
            if ret is None:
                continue
            sec = sector_map.get(ticker, "unknown")
            sector_groups.setdefault(sec, []).append(ret)
            _raw[ticker] = (ret, sec)

        sector_avg: Dict[str, float] = {
            s: sum(v) / len(v) for s, v in sector_groups.items()
        }

        residuals: Dict[str, float] = {
            t: ret - beta * idx_ret - sector_avg.get(sec, 0.0)
            for t, (ret, sec) in _raw.items()
        }

        if len(residuals) < 3:
            continue

        # cross-sectional z-score
        vals = list(residuals.values())
        mu = sum(vals) / len(vals)
        var = sum((v - mu) ** 2 for v in vals) / len(vals)
        sigma = var ** 0.5

        for ticker, resid in residuals.items():
            if sigma > 0:
                result[(ticker, bar_ts)] = (resid - mu) / sigma
            else:
                result[(ticker, bar_ts)] = 0.0

    return result


# ---------------------------------------------------------------------------
# Breadth 계산
# ---------------------------------------------------------------------------

def compute_breadth_ratios(
    ticker_returns: Dict[str, Dict[str, float]],
) -> Dict[str, float]:
    """bar_ts별 상승 종목 비율 계산.

    Returns
    -------
    {bar_ts: ratio} where ratio = 상승종목수 / 전체종목수
    """
    ts_up: Dict[str, int] = defaultdict(int)
    ts_total: Dict[str, int] = defaultdict(int)
    for tr in ticker_returns.values():
        for bar_ts, ret in tr.items():
            ts_total[bar_ts] += 1
            if ret > 0:
                ts_up[bar_ts] += 1
    return {bar_ts: ts_up[bar_ts] / total for bar_ts, total in ts_total.items()}


# ---------------------------------------------------------------------------
# 통합 빌드
# ---------------------------------------------------------------------------

def build_context_states(
    index_30m_closes: List[float],
    index_30m_highs: List[float],
    index_30m_dt_list: List[str],
    index_30m_volumes: List[float],
    ticker_returns_30m: Dict[str, Dict[str, float]],
    index_returns_30m: Dict[str, float],
    sector_map: Dict[str, str],
    liquidity_scores: Dict[str, float],
    daily_ctx_map: Optional[Dict[str, "DailyContext"]] = None,
    config: Optional[dict] = None,
    c_scores_by_date: Optional[Dict[str, Dict[str, float]]] = None,
) -> ContextStateMap:
    """30분봉 데이터 → 종목×bar_ts별 ContextState 빌드.

    Parameters
    ----------
    index_30m_closes  : KOSPI 30분봉 close 리스트
    index_30m_highs   : KOSPI 30분봉 high 리스트
    index_30m_dt_list : KOSPI 30분봉 datetime 문자열 리스트
    index_30m_volumes : KOSPI 30분봉 volume 리스트
    ticker_returns_30m: {ticker: {bar_ts: intraday_return}}
    index_returns_30m : {bar_ts: index_intraday_return}
    sector_map        : {ticker: sector_name}
    liquidity_scores  : {ticker: 5일 평균 거래대금}
    daily_ctx_map     : {ticker: DailyContext} T-1 기준 (optional)
    config            : 파라미터 오버라이드

    Returns
    -------
    ContextStateMap = {(ticker, bar_ts): ContextState}
    """
    # c_score 계산 (DailyContext soft prior)
    c_scores: Dict[str, float] = {}
    if daily_ctx_map:
        try:
            from strategies.mr_common import calc_c_score
            for ticker, ctx in daily_ctx_map.items():
                c_scores[ticker] = calc_c_score(ctx)
        except ImportError:
            logger.warning("mr_common.calc_c_score import 실패 — c_score=0.0")

    # Breadth
    breadth_ratios = compute_breadth_ratios(ticker_returns_30m)

    # M scores + diagnostics in a single VWAP/EMA pass
    m_scores, m_components = _compute_m_both(
        index_30m_closes, index_30m_highs,
        index_30m_dt_list, index_30m_volumes,
        breadth_ratios, config,
    )

    # S scores
    s_scores = compute_s_scores(
        ticker_returns_30m, index_returns_30m, sector_map, config,
    )

    # D scores (raw z-score → bar_ts별 percentile rank [0,1] 변환)
    d_scores_raw = compute_d_scores(
        ticker_returns_30m, index_returns_30m, sector_map, config,
    )

    # Liquidity rank
    tickers = list(ticker_returns_30m.keys())
    liq_vals = [liquidity_scores.get(t, 0.0) for t in tickers]
    liq_ranks = _rank_0_to_1(liq_vals)
    liq_rank_map = dict(zip(tickers, liq_ranks))

    # D_score: bar_ts별 cross-sectional percentile rank 변환
    # raw z-score를 그대로 쓰면 gate [0.25, 0.45]와 스케일 불일치
    all_bar_ts_set = set()
    for tr in ticker_returns_30m.values():
        all_bar_ts_set.update(tr.keys())

    # group d_scores_raw by bar_ts first — eliminates B×T miss-heavy dict probing
    _d_by_ts: Dict[str, list] = defaultdict(list)
    for (t, bar_ts), val in d_scores_raw.items():
        _d_by_ts[bar_ts].append((t, val))

    d_ranked: Dict[Tuple[str, str], float] = {}
    for bar_ts, tv_list in _d_by_ts.items():
        bar_tickers_t, bar_d_vals_t = zip(*tv_list)
        ranked = _rank_0_to_1(list(bar_d_vals_t))
        for t, r in zip(bar_tickers_t, ranked):
            d_ranked[(t, bar_ts)] = r

    # 결과 조립
    result: ContextStateMap = {}

    # 사전 계산: ticker→returns dict 캐싱 (inner loop에서 중복 dict 조회 제거)
    _tr_fast = {t: ticker_returns_30m.get(t, {}) for t in tickers}

    for bar_ts in sorted(all_bar_ts_set):
        m_long, m_short = m_scores.get(bar_ts, (0.0, 0.0))
        br = breadth_ratios.get(bar_ts, 0.5)
        m_diag = m_components.get(bar_ts, {})

        # c_score 날짜 조회는 bar_ts당 1회로 제한 (ticker당이 아님)
        if c_scores_by_date is not None:
            _day_c = c_scores_by_date.get(bar_ts[:10], {})
        else:
            _day_c = None

        for ticker in tickers:
            ret = _tr_fast[ticker].get(bar_ts)
            if ret is None:
                continue

            sec = sector_map.get(ticker, "unknown")
            s = s_scores.get((sec, bar_ts), 0.5)
            d = d_ranked.get((ticker, bar_ts), 0.5)

            _c = _day_c.get(ticker, 0.0) if _day_c is not None else c_scores.get(ticker, 0.0)
            result[(ticker, bar_ts)] = ContextState(
                bar_ts=bar_ts,
                ticker=ticker,
                m_long=m_long,
                m_short=m_short,
                s_score=s,
                d_score=d,
                c_score=_c,
                breadth_ratio=br,
                liquidity_rank=liq_rank_map.get(ticker, 0.5),
                sector_name=sec,
                m_vwap_up=bool(m_diag.get("vwap_up", False)),
                m_ema_up=bool(m_diag.get("ema_up", False)),
                m_idx_up=bool(m_diag.get("idx_up", False)),
                m_breadth_ok=bool(m_diag.get("breadth_ok", False)),
                m_index_ret=float(m_diag.get("index_ret", 0.0)),
            )

    logger.info("ContextState 빌드 완료: %d entries", len(result))
    return result


def get_context_for_5m_bar(
    bar_ts_5m: str,
    context_map_30m: ContextStateMap,
    ticker: str,
) -> Optional[ContextState]:
    """5분봉 bar_ts 시점에서 이미 완성된 가장 최근 30분봉 ContextState 반환.

    FIX (Codex 교차검증): 이전 구현은 현재 30분 구간(예: 10:00~10:29)을 10:00 ~ 10:29
    사이의 5분봉이 조회할 때 반환했으나, 해당 30분봉은 10:30 에 확정되므로 룩어헤드.

    올바른 매핑:
      - 5분봉 09:30 → 09:00 봉(09:00~09:29, 09:30 확정)
      - 5분봉 10:00 → 09:30 봉(09:30~09:59, 10:00 확정)
      - 5분봉 10:15 → 09:30 봉(10:00 봉은 10:30 에 확정 → 미완성)
      - 5분봉 10:30 → 10:00 봉(10:00~10:29, 10:30 확정)
    """
    hh = bar_ts_5m[11:13]
    mm = bar_ts_5m[14:16]
    try:
        hour = int(hh)
        minute = int(mm)
    except (ValueError, IndexError):
        return None

    total_minutes = hour * 60 + minute
    # 이미 완성된 가장 최근 30분봉의 시작:
    # 30분봉 [T:T+29]는 T+30 에 확정되므로 current >= T+30 이어야 함
    # → T <= current - 30 → 시작 = ((current - 30) // 30) * 30
    last_confirmed_start_min = ((total_minutes - 30) // 30) * 30

    # 한국 정규장 09:00 이전은 30분봉 없음
    if last_confirmed_start_min < 9 * 60:
        return None

    c_hour = last_confirmed_start_min // 60
    c_min = last_confirmed_start_min % 60
    bar_ts_30m = f"{bar_ts_5m[:11]}{c_hour:02d}:{c_min:02d}"
    return context_map_30m.get((ticker, bar_ts_30m))
