"""멀티배거 파인더 — 순수 평가/점수 함수."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# F1~F8 디폴트 임계
DEFAULTS = {
    "F1_MCAP_MIN": 200_000_000,
    "F1_MCAP_MAX": 2_000_000_000,
    "F3_ROIC_MIN": 0.10,
    "F4_FCF_YIELD_MIN": 0.05,
    "F4_PB_MAX": 3.0,
    "F5_REVENUE_YOY_MIN": 0.05,
    "F7_ICR_MIN": 3.0,
    "F7_DEBT_EBITDA_MAX": 3.0,
    "F7_ICR_MIN_HIRATE": 4.0,
    "F7_DEBT_EBITDA_MAX_HIRATE": 2.5,
    "F7_HIRATE_DGS10_PCT": 4.0,
    "F8_FROM_52W_HIGH_MIN": -0.50,
    "F8_FROM_52W_HIGH_MAX": -0.10,
    "F8_1M_RETURN_MAX": 0.30,
}

CORE_GATES_REQUIRED = ("F1", "F2", "F8")  # WATCH도 필수 통과
CORE_GATES_OPTIONAL = ("F3", "F4", "F5", "F6", "F7")  # WATCH는 1~2개 부족 허용
ALL_GATES = CORE_GATES_REQUIRED + CORE_GATES_OPTIONAL


@dataclass
class Fundamentals:
    """게이트 평가에 필요한 모든 입력. 결측은 None."""
    market_cap: Optional[float] = None
    ebitda: Optional[float] = None
    fcf: Optional[float] = None
    roic: Optional[float] = None
    roic_prev: Optional[float] = None
    fcf_yield: Optional[float] = None
    pb: Optional[float] = None
    revenue_yoy: Optional[float] = None
    revenue_yoy_prev: Optional[float] = None  # B4용 (1년 전 YoY)
    ebitda_yoy: Optional[float] = None
    fcf_yoy: Optional[float] = None
    assets_yoy: Optional[float] = None
    icr: Optional[float] = None
    debt_ebitda: Optional[float] = None
    from_52w_high: Optional[float] = None  # 음수 (예: -0.20 = 20% 빠짐)
    return_1m: Optional[float] = None
    sector: Optional[str] = None
    insider_net_buy_3m: Optional[float] = None
    buyback_yield_ttm: Optional[float] = None
    capex_yoy: Optional[float] = None  # F6 N/A 판정용
    dgs10_pct: Optional[float] = None


def _missing(*vals) -> bool:
    return any(v is None for v in vals)


def eval_f1(f: Fundamentals, t: dict) -> Optional[bool]:
    if _missing(f.market_cap):
        return None
    return t["F1_MCAP_MIN"] <= f.market_cap <= t["F1_MCAP_MAX"]


def eval_f2(f: Fundamentals, t: dict) -> Optional[bool]:
    if _missing(f.ebitda, f.fcf):
        return None
    return f.ebitda > 0 and f.fcf > 0


def eval_f8(f: Fundamentals, t: dict) -> Optional[bool]:
    if _missing(f.from_52w_high, f.return_1m):
        return None
    band_ok = t["F8_FROM_52W_HIGH_MIN"] <= f.from_52w_high <= t["F8_FROM_52W_HIGH_MAX"]
    momentum_ok = f.return_1m <= t["F8_1M_RETURN_MAX"]
    return band_ok and momentum_ok


def eval_f3(f: Fundamentals, t: dict) -> Optional[bool]:
    if _missing(f.roic):
        return None
    if f.roic >= t["F3_ROIC_MIN"]:
        return True
    if f.roic_prev is not None and f.roic > f.roic_prev:
        return True
    return False


def eval_f4(f: Fundamentals, t: dict) -> Optional[bool]:
    if _missing(f.fcf_yield) and _missing(f.pb):
        return None
    fcf_ok = f.fcf_yield is not None and f.fcf_yield >= t["F4_FCF_YIELD_MIN"]
    pb_ok = f.pb is not None and f.pb <= t["F4_PB_MAX"]
    return fcf_ok or pb_ok


def eval_f5(f: Fundamentals, t: dict) -> Optional[bool]:
    if _missing(f.revenue_yoy, f.ebitda_yoy):
        return None
    return f.revenue_yoy >= t["F5_REVENUE_YOY_MIN"] and f.ebitda_yoy >= f.revenue_yoy


def eval_f6(f: Fundamentals, t: dict) -> Optional[bool]:
    if _missing(f.ebitda_yoy, f.assets_yoy):
        return None
    return f.ebitda_yoy >= f.assets_yoy


def eval_f7(f: Fundamentals, t: dict) -> Optional[bool]:
    if _missing(f.icr, f.debt_ebitda):
        return None
    hirate = f.dgs10_pct is not None and f.dgs10_pct >= t["F7_HIRATE_DGS10_PCT"]
    icr_min = t["F7_ICR_MIN_HIRATE"] if hirate else t["F7_ICR_MIN"]
    de_max = t["F7_DEBT_EBITDA_MAX_HIRATE"] if hirate else t["F7_DEBT_EBITDA_MAX"]
    return f.icr >= icr_min and f.debt_ebitda <= de_max


GATE_EVALUATORS = {
    "F1": eval_f1, "F2": eval_f2, "F3": eval_f3, "F4": eval_f4,
    "F5": eval_f5, "F6": eval_f6, "F7": eval_f7, "F8": eval_f8,
}


@dataclass
class GateResult:
    layer: str  # "PASS" | "WATCH" | "MISS" | "EXCLUDED"
    gates_passed: set = field(default_factory=set)
    gates_failed: set = field(default_factory=set)
    gates_missing: set = field(default_factory=set)


def evaluate_all_gates(f: Fundamentals, t: dict) -> dict:
    return {g: GATE_EVALUATORS[g](f, t) for g in ALL_GATES}


def classify(f: Fundamentals, t: dict) -> GateResult:
    res = GateResult(layer="MISS")
    by_gate = evaluate_all_gates(f, t)
    for g, v in by_gate.items():
        if v is True:
            res.gates_passed.add(g)
        elif v is False:
            res.gates_failed.add(g)
        else:
            res.gates_missing.add(g)

    # 결측 3개+ → 제외
    missing_optional = res.gates_missing & set(CORE_GATES_OPTIONAL)
    if len(missing_optional) >= 3:
        res.layer = "EXCLUDED"
        return res

    # 필수 게이트 미통과 (실패 또는 결측) → MISS
    for g in CORE_GATES_REQUIRED:
        if g not in res.gates_passed:
            res.layer = "MISS"
            return res

    # 옵셔널 부족(실패+결측) 개수
    optional_short = (res.gates_failed | res.gates_missing) & set(CORE_GATES_OPTIONAL)
    if len(optional_short) == 0:
        res.layer = "PASS"
    elif len(optional_short) <= 2:
        res.layer = "WATCH"
    else:
        res.layer = "MISS"
    return res


# ---------------------------------------------------------------------------
# Q1~Q6 점수화 + Bonus + tie-break
# ---------------------------------------------------------------------------

def _clamp01(x: float, lo: float, hi: float) -> float:
    if x <= lo:
        return 0.0
    if x >= hi:
        return 100.0
    return round((x - lo) / (hi - lo) * 100.0, 10)


def score_q1(f: Fundamentals) -> Optional[float]:
    if f.roic is None:
        return None
    return _clamp01(f.roic, 0.10, 0.30)


def score_q2(f: Fundamentals) -> Optional[float]:
    parts = []
    if f.fcf_yield is not None:
        parts.append(_clamp01(f.fcf_yield, 0.05, 0.15))
    if f.pb is not None and f.pb > 0:
        bm = 1.0 / f.pb
        parts.append(_clamp01(bm, 0.33, 1.0))
    if not parts:
        return None
    return max(parts)


def score_q3(f: Fundamentals) -> Optional[float]:
    if f.ebitda_yoy is None or f.revenue_yoy is None:
        return None
    diff = f.ebitda_yoy - f.revenue_yoy
    if diff >= 0.10:
        return 100.0
    if diff <= -0.05:
        return 0.0
    if diff >= 0:
        return 50 + (diff / 0.10) * 50
    return 50 + (diff / 0.05) * 50  # diff 음수 → 0~50


def score_q4(f: Fundamentals) -> Optional[float]:
    if f.ebitda_yoy is None or f.assets_yoy is None:
        return None
    diff = f.ebitda_yoy - f.assets_yoy
    if diff < 0:
        return _clamp01(diff, -0.10, 0.0) * 0.5
    return 50 + _clamp01(diff, 0.0, 0.15) * 0.5


def score_q5(f: Fundamentals) -> Optional[float]:
    parts = []
    if f.icr is not None:
        parts.append(_clamp01(f.icr, 3.0, 10.0))
    if f.debt_ebitda is not None:
        parts.append(_clamp01(-f.debt_ebitda, -3.0, 0.0))
    if not parts:
        return None
    return min(parts)


def score_q6(f: Fundamentals) -> Optional[float]:
    if f.revenue_yoy is None:
        return None
    return _clamp01(f.revenue_yoy, 0.05, 0.30)


BAGGER_SECTORS = {"Healthcare", "Technology", "Consumer Discretionary"}


def score_bonus(f: Fundamentals) -> float:
    b = 0.0
    if f.sector and f.sector in BAGGER_SECTORS:
        b += 10
    if f.insider_net_buy_3m is not None and f.insider_net_buy_3m > 0:
        b += 10
    if f.buyback_yield_ttm is not None and f.buyback_yield_ttm > 0:
        b += 5
    if (f.revenue_yoy is not None and f.revenue_yoy_prev is not None
            and f.revenue_yoy > f.revenue_yoy_prev):
        b += 10
    return b


_Q_FUNCS = (score_q1, score_q2, score_q3, score_q4, score_q5, score_q6)


BONUS_MAX = 35  # sector(10) + insider(10) + buyback(5) + revenue_accel(10)


def compose_score(f: Fundamentals) -> float:
    """Core × 0.7 + Bonus × 0.3 (Bonus 는 0~100 정규화).

    P1-1: 명시적 정규화로 표현. 수치는 종전과 동일.
    """
    vals = [fn(f) for fn in _Q_FUNCS]
    vals = [v for v in vals if v is not None]
    core = sum(vals) / len(vals) if vals else 0.0
    bonus_pct = min(100.0, score_bonus(f) / BONUS_MAX * 100.0)
    return min(100.0, core * 0.7 + bonus_pct * 0.3)


def tie_break_key(f: Fundamentals) -> tuple:
    """동점 시 비교용. 큰 게 우선 (q4/roic/q2) + 시총 작은 게 우선 (멀티배거 전략).

    P1-6: 모든 필드를 의미적 양수로 반환. 정렬 방향은 호출측이 결정.
    """
    return (
        score_q4(f) or 0,
        f.roic or 0,
        score_q2(f) or 0,
        f.market_cap or 0,  # 양수 그대로 — 호출측이 '작은 게 우선'으로 반전
    )


# ---------------------------------------------------------------------------
# Orchestrator: build_results
# ---------------------------------------------------------------------------

def _pre_filter_F1(base: list, t: dict) -> list:
    """F1(시가총액 밴드) 만 사전 필터. F2(수익성) 는 enrichment 후 classify 단계로 위임.

    P1-7: base scan 에 EBITDA/FCF 가 누락된 종목을 침묵 누락하지 않도록 변경.
    market_cap 도 결측이면 enrichment 후 재시도 가능하도록 통과시킴.
    """
    out = []
    for row in base:
        ticker = row.get("Ticker") or row.get("ticker") or row.get("symbol")
        if not ticker:
            continue
        mc = row.get("market_cap") or row.get("MarketCap") or row.get("marketCap")
        if mc is not None and not (t["F1_MCAP_MIN"] <= mc <= t["F1_MCAP_MAX"]):
            continue
        out.append(ticker)
    return out


# 후방호환 별칭 — 외부 호출자 잔존 시.
_pre_filter_F1_F2 = _pre_filter_F1


def _row_summary(ticker: str, f: Fundamentals, cls: GateResult, score: float) -> dict:
    return {
        "ticker": ticker,
        "score": round(score, 1),
        "market_cap": f.market_cap,
        "roic": f.roic,
        "fcf_yield": f.fcf_yield,
        "pb": f.pb,
        "ebitda_yoy": f.ebitda_yoy,
        "revenue_yoy": f.revenue_yoy,
        "assets_yoy": f.assets_yoy,
        "from_52w_high": f.from_52w_high,
        "sector": f.sector,
        "layer": cls.layer,
        "gates_passed": sorted(cls.gates_passed),
        "gates_failed": sorted(cls.gates_failed),
        "gates_missing": sorted(cls.gates_missing),
    }


def build_results(base_rows: list, dgs10_pct: Optional[float],
                  enrich_fn, max_workers: int = 8,
                  thresholds: Optional[dict] = None,
                  hist_prefetch_fn=None) -> dict:
    """베이스 스캔 결과 → PASS/WATCH 분류 + 점수 랭킹.

    hist_prefetch_fn: 선택. 호출 시 {symbol: DataFrame} 반환하는 함수.
    enrich_fn 이 hist_cache 인자를 받으면 N+1 yfinance 호출을 1회 batch 로 단축.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import inspect

    t = thresholds or DEFAULTS
    candidates = _pre_filter_F1(base_rows, t)

    # 가능한 경우 hist 를 1회 batch 로 prefetch (N+1 완화).
    hist_cache = {}
    if hist_prefetch_fn and candidates:
        try:
            hist_cache = hist_prefetch_fn(candidates) or {}
        except Exception:
            hist_cache = {}
    enrich_supports_cache = "hist_cache" in inspect.signature(enrich_fn).parameters

    def _submit(ex, sym):
        if enrich_supports_cache:
            return ex.submit(enrich_fn, sym, dgs10_pct, hist_cache=hist_cache)
        return ex.submit(enrich_fn, sym, dgs10_pct)

    pass_rows, watch_rows = [], []
    enrich_failed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {_submit(ex, sym): sym for sym in candidates}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                f = fut.result()
            except Exception:
                enrich_failed += 1
                continue
            if f is None:
                enrich_failed += 1
                continue
            cls = classify(f, t)
            if cls.layer not in ("PASS", "WATCH"):
                continue
            score = compose_score(f)
            row = _row_summary(sym, f, cls, score)
            (pass_rows if cls.layer == "PASS" else watch_rows).append(row)

    def _blank_for_sort(r):
        return Fundamentals(
            ebitda_yoy=r.get("ebitda_yoy"), assets_yoy=r.get("assets_yoy"),
            roic=r.get("roic"), fcf_yield=r.get("fcf_yield"), pb=r.get("pb"),
            market_cap=r.get("market_cap"),
        )

    def _sort_key(r):
        q4, roic, q2, mc = tie_break_key(_blank_for_sort(r))
        # score/q4/roic/q2: 큰 게 우선(-x). market_cap: 작은 게 우선(+x).
        return (-r["score"], -q4, -roic, -q2, mc)

    pass_rows.sort(key=_sort_key)
    watch_rows.sort(key=lambda r: (len(r["gates_failed"]) + len(r["gates_missing"]), -r["score"]))

    return {
        "pass": pass_rows,
        "watch": watch_rows,
        "meta": {
            "universe_n": len(base_rows),
            "candidates_n": len(candidates),
            "pass_n": len(pass_rows),
            "watch_n": len(watch_rows),
            "enrich_failed_n": enrich_failed,
            "dgs10_pct": dgs10_pct,
        },
    }
