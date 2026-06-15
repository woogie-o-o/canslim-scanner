# -*- coding: utf-8 -*-
"""fundamental_value_grade.py — 재무가치 등급 (횡단면 백분위 합성).

Phase 2a (DART 연동 + 퀄리티/밸류 고도화):
    성장 QoQ 4종(매출·영업이익·순이익·OCF) 15%
    성장 YoY 4종(매출·영업이익·순이익·OCF) 15%
    퀄리티  3종(GPA·Accrual·연속성장)        10%
    밸류에이션 6종(ROE·PEGR·PBR·PSR·FCF Yield·EV/EBITDA) 60%

설계 원칙 (월가 퀀트 패널 리뷰 반영):
    - 방향성: ROE·성장·GPA·FCF Yield·streak = 정순 /
              PBR·PSR·PEGR·EV/EBITDA·Accrual = 역순(저평가·고퀄리티=고득점).
    - 결측(None): 해당 종목 비중을 가용 지표로 재정규화 -> 0점 왜곡 방지.
    - DART 미연동(API 키 미설정) 시: DART 의존 지표가 전부 None ->
      가용 지표(네이버 QoQ/YoY + ROE/PBR/PSR/PEGR/EV-EBITDA/streak)만으로 자동 폴백.
    - basis="universe"(전체종목) / "sector"(섹터내) 두 버전을 동일 함수로 산출.

순수 함수 — 네트워크/IO 는 콜백(quarter_fetch, ttm_fetch, dart_fetch)으로 주입.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

# Phase 2a 가중치 (16 개 지표, 합 = 1.0).
# DART 미연동 시 ocf_qoq·ocf_yoy·gpa·accrual·fcf_yield 가 None -> 자동 재정규화.
DEFAULT_WEIGHTS: dict[str, float] = {
    # 성장 QoQ (15 %)
    "rev_qoq":   0.0375,
    "op_qoq":    0.0375,
    "ni_qoq":    0.0375,
    "ocf_qoq":   0.0375,
    # 성장 YoY (15 %)
    "rev_yoy":   0.0375,
    "op_yoy":    0.0375,
    "ni_yoy":    0.0375,
    "ocf_yoy":   0.0375,
    # 퀄리티 (10 %)
    "gpa":       0.05,      # Gross Profit / Assets (Novy-Marx)
    "accrual":   0.03,      # (NI - OCF) / Assets  (Sloan)
    "streak":    0.02,      # 연속 성장 분기 수
    # 밸류에이션 (60 %)
    "roe":       0.12,
    "pegr":      0.13,      # PEG Ratio
    "pbr":       0.12,
    "psr":       0.12,
    "fcf_yield": 0.05,      # FCF / MarketCap
    "ev_ebitda": 0.06,      # EV / EBITDA
}

# 낮을수록 좋은 지표 — 백분위를 역순(1 - pct)으로 점수화.
INVERTED_METRICS: frozenset[str] = frozenset({
    "pbr", "psr", "pegr", "ev_ebitda", "accrual",
})

# DART BS/IS/CF 필드 추출용 regex 패턴 (account_nm 공백 제거 후 매칭).
_DART_PATTERNS = {
    "BS": {
        "total_assets":  [r"^자산총계"],
        "total_equity":  [r"^자본총계"],
        "current_assets": [r"^유동자산$"],
        "current_liab":  [r"^유동부채$"],
    },
    "IS": {
        "gross_profit":  [r"^매출총이익$", r"^매출총이익\(손실\)"],
        "revenue_is":    [r"^매출액$", r"^수익\(매출액\)", r"^영업수익$"],
    },
    "CF": {
        "ocf":           [r"^영업활동(으로인한)?현금흐름", r"영업활동현금흐름"],
        "capex":         [r"유형자산의?취득", r"유형자산취득"],
    },
}


def _percentiles(values: dict[str, float]) -> dict[str, float]:
    """ticker->값 dict 를 cross-sectional 백분위(0.0~1.0)로. 동률은 평균 rank."""
    if not values:
        return {}
    items = sorted(values.items(), key=lambda kv: kv[1])
    n = len(items)
    if n == 1:
        return {items[0][0]: 0.5}
    ranks: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        pct = ((i + j) / 2.0) / (n - 1)
        for k in range(i, j + 1):
            ranks[items[k][0]] = pct
        i = j + 1
    return ranks


def compute_grades(
    records: Iterable[dict[str, Any]],
    *,
    basis: str = "universe",
    weights: dict[str, float] | None = None,
) -> dict[str, dict[str, Any]]:
    """종목별 재무가치 등급(0~100)을 횡단면 백분위로 산출.

    Args:
        records: 종목별 dict 리스트. 필수 키 ``ticker``, ``sector``.
        basis: "universe" | "sector".
        weights: 지표->가중치. 미지정 시 ``DEFAULT_WEIGHTS``.

    Returns:
        ``{ticker: {"grade": float, "percentiles": {metric: score}, "basis": str}}``
    """
    w = dict(weights or DEFAULT_WEIGHTS)
    metrics = list(w.keys())
    recs = list(records)

    def group_of(rec: dict[str, Any]) -> str:
        return str(rec.get("sector", "")) if basis == "sector" else "__ALL__"

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in recs:
        groups[group_of(rec)].append(rec)

    metric_score: dict[str, dict[str, float]] = {m: {} for m in metrics}
    for grp in groups.values():
        for m in metrics:
            vals: dict[str, float] = {}
            for rec in grp:
                v = rec.get(m)
                if v is None:
                    continue
                try:
                    vals[str(rec["ticker"])] = float(v)
                except (TypeError, ValueError):
                    continue
            for t, p in _percentiles(vals).items():
                metric_score[m][t] = (1.0 - p) if m in INVERTED_METRICS else p

    out: dict[str, dict[str, Any]] = {}
    for rec in recs:
        t = str(rec["ticker"])
        num = 0.0
        wsum = 0.0
        comp: dict[str, float] = {}
        for m in metrics:
            if t in metric_score[m]:
                s = metric_score[m][t]
                num += s * w[m]
                wsum += w[m]
                comp[m] = round(s, 6)
        grade = (num / wsum) * 100.0 if wsum > 0 else 50.0
        out[t] = {"grade": round(grade, 4), "percentiles": comp, "basis": basis}
    return out


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """단순 Pearson 상관 (numpy 비의존). 표본<3 이면 None."""
    n = len(xs)
    if n < 3 or len(ys) != n:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / (sxx ** 0.5 * syy ** 0.5)


# ── DART 데이터에서 파생 지표 산출 헬퍼 ─────────────────────────────────

def _safe_div(a, b):
    """a / b. 둘 중 None 이거나 b==0 이면 None."""
    if a is None or b is None:
        return None
    try:
        af, bf = float(a), float(b)
    except (TypeError, ValueError):
        return None
    if abs(bf) < 1e-9:
        return None
    return af / bf


def _yoy_growth(ths, frm):
    """(당기 - 전기) / |전기|. 전기 0/음수면 None."""
    if ths is None or frm is None:
        return None
    try:
        t, f = float(ths), float(frm)
    except (TypeError, ValueError):
        return None
    if f <= 0:
        return None
    return (t - f) / f


def _extract_dart_metrics(
    dart_data: dict[str, Any],
    market_cap: float,
    net_income_ttm: float | None,
) -> dict[str, Any]:
    """DART 재무제표에서 Phase 2a 파생 지표를 산출.

    Returns:
        dict with keys: gpa, accrual, fcf_yield, ocf_qoq (=None), ocf_yoy,
        ev_ebitda (DART 기반 보조).
    """
    try:
        from dart_api import extract_fields
    except ImportError:
        return {}

    if not dart_data or not dart_data.get("available"):
        return {}

    bs = extract_fields(dart_data, "BS", _DART_PATTERNS["BS"])
    is_ = extract_fields(dart_data, "IS", _DART_PATTERNS["IS"])
    cf = extract_fields(dart_data, "CF", _DART_PATTERNS["CF"])

    total_assets = bs["total_assets"]["ths"]
    total_equity = bs["total_equity"]["ths"]
    gross_profit = is_["gross_profit"]["ths"]
    ocf_ths = cf["ocf"]["ths"]
    ocf_frm = cf["ocf"]["frm"]
    capex_ths = cf["capex"]["ths"]

    out: dict[str, Any] = {
        "gpa": None, "accrual": None, "fcf_yield": None,
        "ocf_qoq": None, "ocf_yoy": None,
    }

    # GPA = Gross Profit / Total Assets
    out["gpa"] = _safe_div(gross_profit, total_assets)

    # Accrual Ratio = (Net Income - OCF) / Total Assets
    if net_income_ttm is not None and ocf_ths is not None and total_assets is not None:
        try:
            out["accrual"] = (float(net_income_ttm) - float(ocf_ths)) / float(total_assets)
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # FCF Yield = (OCF - CAPEX) / Market Cap
    if ocf_ths is not None and market_cap and market_cap > 0:
        capex = abs(float(capex_ths)) if capex_ths is not None else 0.0
        try:
            fcf = float(ocf_ths) - capex
            out["fcf_yield"] = fcf / market_cap
        except (TypeError, ValueError):
            pass

    # OCF YoY = (당기 OCF - 전기 OCF) / |전기 OCF|
    out["ocf_yoy"] = _yoy_growth(ocf_ths, ocf_frm)

    return out


def apply_grades(
    results: list[dict[str, Any]],
    *,
    quarter_fetch,
    ttm_fetch=None,
    dart_fetch=None,
    weights: dict[str, float] | None = None,
    log=None,
) -> dict[str, Any]:
    """스캔 결과에 재무가치 등급을 부여 (Phase 2a — 16 개 지표).

    각 result dict 에 ``FinValue``, ``FinValueSec`` 키를 in-place 추가.

    Args:
        results: 스캔 결과. 필수 ``Ticker``; 선택 ``Sector``, ``_MarketCap``, ``_PBR``.
        quarter_fetch: ``fn(code)->dict`` (naver_quarter.get_quarter_metrics).
        ttm_fetch: ``fn(code)->dict`` (naver_quarter.get_ttm_financials). PSR/PEGR/EV 용.
        dart_fetch: ``fn(code)->dict`` (dart_api.get_financials_cached). GPA/Accrual/FCF 용.
            None 이면 DART 의존 지표는 전부 None (자동 재정규화로 폴백).
        weights: compute_grades 가중치 오버라이드.
        log: ``fn(str)`` 진행 로그 콜백.

    Returns:
        상관 요약 ``{"n", "pearson_value", "pearson_quality"}``.
    """
    records: list[dict[str, Any]] = []
    graded_tickers: list[str] = []
    dart_ok_count = 0

    for r in results:
        ticker = str(r.get("Ticker", ""))
        if not ticker:
            r["FinValue"] = None
            r["FinValueSec"] = None
            continue
        code = ticker.split(".")[0]

        # ── 네이버 분기 (QoQ, YoY, streak, ROE, PBR) ──
        try:
            q = quarter_fetch(code) or {}
        except Exception:
            q = {}
        if not q.get("available"):
            r["FinValue"] = None
            r["FinValueSec"] = None
            continue

        # ── 네이버 TTM (PSR, PEGR, EV/EBITDA) ──
        ttm: dict[str, Any] = {}
        if ttm_fetch is not None:
            try:
                ttm = ttm_fetch(code) or {}
            except Exception:
                ttm = {}

        mc = float(r.get("_MarketCap") or 0)

        # PSR = MarketCap / TTM Revenue
        psr = None
        rev_ttm = float(ttm.get("revenue") or 0)
        if rev_ttm > 0 and mc > 0:
            psr = mc / rev_ttm

        # PEGR = PER / (EPS Growth% ). EPS 역성장이면 None.
        pegr = None
        ni_ttm = float(ttm.get("net_income") or 0)
        eps_growth = ttm.get("eps_growth")
        if mc > 0 and ni_ttm > 0 and eps_growth is not None and eps_growth > 0.001:
            per = mc / ni_ttm
            pegr = per / (eps_growth * 100.0)

        # EV/EBITDA (간이 EV = MC + 추정 부채)
        ev_ebitda = None
        ebitda = float(ttm.get("ebitda") or ttm.get("operating_income") or 0)
        debt_ratio = float(q.get("debt_ratio") or ttm.get("debt_ratio") or 0)
        pbr_val = q.get("pbr")
        if ebitda > 0 and mc > 0:
            # 추정 총부채 = (MC / PBR) * (debt_ratio / 100), PBR 가용 시
            est_debt = 0.0
            if pbr_val and float(pbr_val) > 0.01:
                book_equity = mc / float(pbr_val)
                est_debt = book_equity * (debt_ratio / 100.0)
            ev = mc + est_debt
            ev_ebitda = ev / ebitda

        # PBR 폴백
        pbr = q.get("pbr")
        if pbr is None:
            _p = r.get("_PBR")
            pbr = float(_p) if _p else None

        # ── DART (GPA, Accrual, FCF Yield, OCF YoY) ──
        dart_metrics: dict[str, Any] = {}
        if dart_fetch is not None:
            try:
                dart_data = dart_fetch(code)
                dart_metrics = _extract_dart_metrics(
                    dart_data, mc, ni_ttm if ni_ttm else None)
                if dart_metrics.get("gpa") is not None:
                    dart_ok_count += 1
            except Exception:
                pass

        records.append({
            "ticker":    ticker,
            "sector":    r.get("Sector", ""),
            # QoQ
            "rev_qoq":   q.get("rev_qoq"),
            "op_qoq":    q.get("op_qoq"),
            "ni_qoq":    q.get("ni_qoq"),
            "ocf_qoq":   dart_metrics.get("ocf_qoq"),   # Phase 2a: 분기 OCF 미지원 -> None
            # YoY
            "rev_yoy":   q.get("rev_yoy"),
            "op_yoy":    q.get("op_yoy"),
            "ni_yoy":    q.get("ni_yoy"),
            "ocf_yoy":   dart_metrics.get("ocf_yoy"),
            # Quality
            "gpa":       dart_metrics.get("gpa"),
            "accrual":   dart_metrics.get("accrual"),
            "streak":    q.get("streak", 0) or 0,
            # Valuation
            "roe":       q.get("roe"),
            "pegr":      pegr,
            "pbr":       pbr,
            "psr":       psr,
            "fcf_yield": dart_metrics.get("fcf_yield"),
            "ev_ebitda": ev_ebitda,
        })
        graded_tickers.append(ticker)

    if not records:
        if log:
            log("[FinValue] Phase 2a: 등급 부여 가능한 종목 없음")
        return {"n": 0, "pearson_value": None, "pearson_quality": None}

    g_uni = compute_grades(records, basis="universe", weights=weights)
    g_sec = compute_grades(records, basis="sector", weights=weights)

    by_ticker = {str(r.get("Ticker", "")): r for r in results}
    for t in graded_tickers:
        r = by_ticker.get(t)
        if r is None:
            continue
        r["FinValue"] = round(g_uni[t]["grade"], 1)
        r["FinValueSec"] = round(g_sec[t]["grade"], 1)

    # ── 기존 팩터와의 상관 계측 ──
    fv, vs, qs = [], [], []
    for t in graded_tickers:
        r = by_ticker.get(t)
        if r is None:
            continue
        fv.append(r["FinValue"])
        vs.append(float(r.get("ValueScore", 0) or 0))
        qs.append(float(r.get("QualityScore", 0) or 0))
    p_val = _pearson(fv, vs)
    p_qual = _pearson(fv, qs)

    if log:
        def _fmt(x):
            return f"{x:+.2f}" if x is not None else "N/A"
        w_eff = weights or DEFAULT_WEIGHTS
        n_metrics = sum(1 for m in w_eff
                        if any(m in (rec or {}) and rec.get(m) is not None
                               for rec in records[:1]))
        log(f"[FinValue] Phase 2a: {len(graded_tickers)}종목 · "
            f"DART {dart_ok_count}종목 · "
            f"ValueScore r={_fmt(p_val)} · QualityScore r={_fmt(p_qual)}")

    return {"n": len(graded_tickers), "pearson_value": p_val, "pearson_quality": p_qual}
