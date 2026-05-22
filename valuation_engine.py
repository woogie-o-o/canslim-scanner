"""Valuation Engine: 3-Stage DCF, Relative Valuation (PER/PBR/EV-EBITDA), Reverse DCF.

Supports:
- 3-Stage Discounted Cash Flow (DCF) with sensitivity range
- Relative valuation via PER, PBR, EV/EBITDA multiples
- Reverse DCF: binary search for implied growth rate at current price
- Composite fair value range and discount percentage
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ValuationResult:
    """Container for all valuation outputs.

    Attributes:
        dcf_value: Base DCF intrinsic value per share (WACC as discount rate).
        dcf_low: DCF value using WACC + 1% (conservative / lower bound).
        dcf_high: DCF value using WACC - 1% (optimistic / upper bound).
        per_fair: PER-based fair value (EPS * per_multiple).
        pbr_fair: PBR-based fair value (Book Value * pbr_multiple).
        ev_ebitda_fair: EV/EBITDA-based fair value (EBITDA * ev_ebitda_multiple).
        reverse_dcf_growth: Implied Stage-1 growth rate at current price.
        fair_value_range: (dcf_low, weighted_mid, dcf_high).
        discount_pct: Upside/downside from current price to weighted midpoint (%).
        method_scores: Raw valuation estimates keyed by method name.
    """

    dcf_value: float
    dcf_low: float
    dcf_high: float
    per_fair: float
    pbr_fair: float
    ev_ebitda_fair: float
    reverse_dcf_growth: float
    fair_value_range: tuple[float, float, float]
    discount_pct: float
    method_scores: dict[str, float] = field(default_factory=dict)


def target_upside_score(distance: float) -> tuple[float, str]:
    """Convert target-price upside/downside into a continuous score plus view.

    The legacy implementation used stepwise bins, which made sector bias changes
    invisible whenever the upside stayed inside the same bucket. This helper
    keeps the same view bands but interpolates score inside each band so the
    final score moves continuously with the target-price delta.

    Args:
        distance: (target_price - current_price) / current_price

    Returns:
        ``(score, view)``
    """
    d = float(distance or 0.0)

    if d > 0.40:
        return 15.0, "STRONG_BUY"
    if d > 0.30:
        return 12.0 + ((d - 0.30) / 0.10) * 3.0, "BUY"
    if d > 0.15:
        return 8.0 + ((d - 0.15) / 0.15) * 4.0, "MODERATE_BUY"
    if d > 0.05:
        return 4.0 + ((d - 0.05) / 0.10) * 4.0, "SLIGHT_UPSIDE"
    if d < -0.15:
        return -12.0, "OVERVALUED"
    if d < -0.10:
        return -12.0 + ((d + 0.15) / 0.05) * 4.0, "SLIGHT_OVERVALUED"
    if d < 0:
        return -8.0 + ((d + 0.10) / 0.10) * 3.0, "AT_TARGET"
    return -5.0 + (d / 0.05) * 9.0, "NEUTRAL"


# ---------------------------------------------------------------------------
# Internal DCF helper
# ---------------------------------------------------------------------------


def _dcf_value(
    fcf: float,
    wacc: float,
    growth_stage1: float,
    growth_stage2: float,
    terminal_growth: float,
) -> float:
    """Compute 3-Stage DCF intrinsic value.

    Stage 1: 5 years at ``growth_stage1``.
    Stage 2: 5 years at ``growth_stage2``.
    Terminal: Gordon-Growth perpetuity discounted back.

    Args:
        fcf: Free cash flow (base year).
        wacc: Weighted average cost of capital.
        growth_stage1: Annual growth rate for Stage 1.
        growth_stage2: Annual growth rate for Stage 2.
        terminal_growth: Perpetual terminal growth rate.

    Returns:
        Intrinsic value, or 0.0 on any arithmetic error.
    """
    try:
        if wacc <= terminal_growth:
            return 0.0

        pv: float = 0.0
        cf: float = fcf

        # Stage 1: years 1-5
        for t in range(1, 6):
            cf = cf * (1.0 + growth_stage1)
            pv += cf / math.pow(1.0 + wacc, t)

        # Stage 2: years 6-10
        for t in range(6, 11):
            cf = cf * (1.0 + growth_stage2)
            pv += cf / math.pow(1.0 + wacc, t)

        # Terminal value at end of year 10
        terminal_cf: float = cf * (1.0 + terminal_growth)
        terminal_value: float = terminal_cf / (wacc - terminal_growth)
        pv += terminal_value / math.pow(1.0 + wacc, 10)

        return pv
    except (ZeroDivisionError, ValueError, OverflowError):
        return 0.0


# ---------------------------------------------------------------------------
# Reverse DCF via binary search
# ---------------------------------------------------------------------------


def _reverse_dcf_growth(
    target_price: float,
    fcf: float,
    wacc: float,
    growth_stage2: float,
    terminal_growth: float,
    tolerance: float = 0.001,
    low: float = 0.0,
    high: float = 1.0,
    max_iter: int = 64,
) -> float:
    """Binary-search for the Stage-1 growth rate implied by ``target_price``.

    Args:
        target_price: Current market price to match.
        fcf: Base free cash flow.
        wacc: Discount rate.
        growth_stage2: Fixed Stage-2 growth rate used during search.
        terminal_growth: Fixed terminal growth rate.
        tolerance: Convergence threshold on the growth rate.
        low: Lower bound of search range.
        high: Upper bound of search range.
        max_iter: Maximum iterations before returning best estimate.

    Returns:
        Implied Stage-1 growth rate in [0, 1], or 0.0 on error.
    """
    try:
        if target_price <= 0.0 or fcf == 0.0:
            return 0.0

        for _ in range(max_iter):
            mid: float = (low + high) / 2.0
            estimated: float = _dcf_value(
                fcf, wacc, mid, growth_stage2, terminal_growth
            )
            if abs(high - low) < tolerance:
                return mid
            if estimated < target_price:
                low = mid
            else:
                high = mid

        return (low + high) / 2.0
    except (ZeroDivisionError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run(
    ticker: str,
    current_price: float,
    financials: dict[str, Any],
    wacc: float = 0.10,
    growth_stage1: float = 0.15,
    growth_stage2: float = 0.08,
    terminal_growth: float = 0.03,
    per_multiple: float = 15.0,
    pbr_multiple: float = 1.5,
    ev_ebitda_multiple: float = 10.0,
) -> ValuationResult:
    """Run a composite valuation for ``ticker``.

    Args:
        ticker: Stock ticker symbol (informational; not used in math).
        current_price: Current market price per share.
        financials: Dict containing any of:
            - ``"fcf"``: Free cash flow (base year).
            - ``"eps"``: Earnings per share.
            - ``"book_value"``: Book value per share.
            - ``"ebitda"``: EBITDA (absolute, same units as price).
        wacc: Weighted average cost of capital (default 10%).
        growth_stage1: Stage-1 (years 1-5) annual growth rate (default 15%).
        growth_stage2: Stage-2 (years 6-10) annual growth rate (default 8%).
        terminal_growth: Terminal perpetual growth rate (default 3%).

    Returns:
        A :class:`ValuationResult` with all computed metrics.
    """
    fcf: float = float(financials.get("fcf", 0) or 0)
    eps: float = float(financials.get("eps", 0) or 0)
    book_value: float = float(financials.get("book_value", 0) or 0)
    ebitda: float = float(financials.get("ebitda", 0) or 0)
    shares: float = float(financials.get("shares_outstanding", financials.get("shares", 0)) or 0)
    # EV → Equity bridge: 비영업자산(현금) − 부채. 둘 다 총액(원/달러). 미공급 시 0.
    cash: float = float(financials.get("cash", 0) or 0)
    debt: float = float(financials.get("debt", 0) or 0)
    net_cash_ps: float = (cash - debt) / shares if shares > 0 else 0.0

    # 총액 → 주당 정규화: shares가 있으면 무조건 per-share로 변환
    if shares > 0:
        if fcf != 0:
            fcf = fcf / shares
        if ebitda != 0:
            ebitda = ebitda / shares

    # --- 3-Stage DCF (base / low / high) + EV→Equity bridge ---
    # 표준 공식: (Σ Stage PV) + 비영업자산(현금) − 부채 ÷ 발행주식수
    dcf_base: float = _dcf_value(fcf, wacc, growth_stage1, growth_stage2, terminal_growth) + net_cash_ps
    dcf_low:  float = _dcf_value(fcf, wacc + 0.01, growth_stage1, growth_stage2, terminal_growth) + net_cash_ps
    dcf_high: float = _dcf_value(fcf, wacc - 0.01, growth_stage1, growth_stage2, terminal_growth) + net_cash_ps

    # --- Relative valuation ---
    try:
        per_fair: float = eps * per_multiple  # equity multiple, no bridge
    except (ZeroDivisionError, ValueError):
        per_fair = 0.0

    try:
        pbr_fair: float = book_value * pbr_multiple  # equity multiple, no bridge
    except (ZeroDivisionError, ValueError):
        pbr_fair = 0.0

    try:
        # EV/EBITDA 는 enterprise multiple → equity 변환 시 bridge 적용
        ev_ebitda_fair: float = ebitda * ev_ebitda_multiple + net_cash_ps
    except (ZeroDivisionError, ValueError):
        ev_ebitda_fair = 0.0

    # --- Weighted midpoint (0인 방법은 제외하고 가중치 재분배) ---
    try:
        _methods = [
            (dcf_base, 0.50),
            (per_fair, 0.20),
            (pbr_fair, 0.15),
            (ev_ebitda_fair, 0.15),
        ]
        _active = [(v, w) for v, w in _methods if v > 0]
        if _active:
            _total_w = sum(w for _, w in _active)
            weighted_mid = sum(v * (w / _total_w) for v, w in _active)
        else:
            weighted_mid = 0.0
    except (ZeroDivisionError, ValueError):
        weighted_mid = 0.0

    fair_value_range: tuple[float, float, float] = (dcf_low, weighted_mid, dcf_high)

    # --- Discount / premium to current price ---
    try:
        if current_price == 0.0:
            raise ZeroDivisionError
        discount_pct: float = (weighted_mid - current_price) / current_price * 100.0
    except (ZeroDivisionError, ValueError):
        discount_pct = 0.0

    # --- Reverse DCF ---
    reverse_growth: float = _reverse_dcf_growth(
        target_price=current_price,
        fcf=fcf,
        wacc=wacc,
        growth_stage2=growth_stage2,
        terminal_growth=terminal_growth,
    )

    # --- Method scores ---
    method_scores: dict[str, float] = {
        "DCF": dcf_base,
        "PER": per_fair,
        "PBR": pbr_fair,
        "EV_EBITDA": ev_ebitda_fair,
    }

    return ValuationResult(
        dcf_value=dcf_base,
        dcf_low=dcf_low,
        dcf_high=dcf_high,
        per_fair=per_fair,
        pbr_fair=pbr_fair,
        ev_ebitda_fair=ev_ebitda_fair,
        reverse_dcf_growth=reverse_growth,
        fair_value_range=fair_value_range,
        discount_pct=discount_pct,
        method_scores=method_scores,
    )


# ---------------------------------------------------------------------------
# Nomura-style target price (sector-routed)
# ---------------------------------------------------------------------------
#
# Nomura의 12개월 선행 목표주가 산정 방식을 종목 유형별로 라우팅한다.
#   - 시클리컬/메모리/소재: forward BPS × target P/B (P/B는 Gordon으로 정당화)
#   - 은행·보험·증권:       2-stage Gordon Growth로 도출한 P/B × 2y forward BPS
#   - 다각화 대기업(SOTP):  사업부별 EBITDA(또는 순이익) × peer 멀티플 합산
#   - 안정 성장주:          forward EPS × peer P/E
#   - 고성장/현금흐름:      DCF (기존 _dcf_value 재사용)
#
# 참고: SK하이닉스 사례 - BPS 668,186원 × 3.5배 = 약 2,340,000원

_CYCLICAL_SECTORS  = {"반도체", "디스플레이", "철강", "조선", "화학", "정유", "해운", "자동차"}
_FINANCIAL_SECTORS = {"은행", "보험", "증권", "지주", "금융"}
_STABLE_SECTORS    = {"소비재", "통신", "유틸리티", "건설", "유통", "음식료", "방산"}
_GROWTH_SECTORS    = {"바이오", "제약", "플랫폼", "게임", "엔터", "인터넷", "AI인프라"}

# 섹터별 보정 계수 — 모두 중립(1.0).
# 섹터 라우팅(밸류에이션 방식 분기)은 유지하되, 주관적 bias는 적용하지 않음.
_NOMURA_SECTOR_BIAS: dict[str, float] = {}


def _nomura_bias(sector: str) -> float:
    """섹터 보정 계수. 현재 모든 섹터 중립(1.0)."""
    return 1.0


def _route_sector(sector: str) -> str:
    """입력 섹터명을 라우팅 키워드로 정규화 (부분일치).

    예: "🔧 반도체 / 메모리" → "반도체", "은행/지방은행" → "은행".
    매칭 실패 시 빈 문자열 (기본 Forward-PE 경로).
    """
    s = (sector or "").strip()
    if not s:
        return ""
    all_keys = _CYCLICAL_SECTORS | _FINANCIAL_SECTORS | _STABLE_SECTORS | _GROWTH_SECTORS
    if s in all_keys:
        return s
    for key in all_keys:
        if key in s:
            return key
    return ""


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    u = (text or "").upper()
    return any(tok in u for tok in tokens)


_CYCLICAL_SECTOR_ALIASES = (
    "SEMICON", "SEMICONDUCTOR", "CHIP", "HBM", "GPU", "MEMORY", "FABLESS",
    "AUTO", "EV", "VEHICLE", "TRANSPORT", "SHIPPING",
    "OIL", "GAS", "ENERGY", "UTILITY", "POWER",
    "STEEL", "METAL", "MINING", "CHEM",
)
_FINANCIAL_SECTOR_ALIASES = (
    "BANK", "FINANCE", "FINTECH", "INSURANCE", "EXCHANGE", "PAYMENT",
    "BROKER", "ASSET", "CAPITAL",
)
_GROWTH_SECTOR_ALIASES = (
    "SOFTWARE", "SAAS", "CLOUD", "PLATFORM", "BIOTECH", "PHARMA",
    "HEALTH", "MEDICAL", "INTERNET", "GAMING", "CONTENT", "AI", "DATA",
)
_EQUIPMENT_SECTOR_ALIASES = (
    "EQUIPMENT", "TOOL", "TOOLS", "PROCESS", "PACKAGING", "TEST",
    "INSPECTION", "ETCH", "DEPOSITION", "LITHO", "MATERIAL",
)


def _gordon_target_pb(roe: float, coe: float, g: float) -> float:
    """Gordon Growth 기반 정당화 P/B: (ROE - g) / (COE - g).

    Args:
        roe: 지속가능 자기자본이익률 (예: 0.20 = 20%).
        coe: 자기자본비용 (예: 0.10 = 10%).
        g:   장기 성장률 (보통 2~3%).

    Returns:
        정당화 P/B. COE <= g 이면 0.0.
    """
    try:
        if coe <= g:
            return 0.0
        pb = (roe - g) / (coe - g)
        return max(pb, 0.0)
    except (ZeroDivisionError, ValueError):
        return 0.0


def _forward_bps(bps_current: float, roe: float, payout_ratio: float, months: int = 12) -> float:
    """현재 BPS를 N개월 후로 롤포워드.

    BPS_{t+1} = BPS_t × (1 + ROE × (1 - payout))
    """
    try:
        if bps_current <= 0 or months <= 0:
            return bps_current
        retention = max(0.0, 1.0 - payout_ratio)
        years = months / 12.0
        return bps_current * math.pow(1.0 + roe * retention, years)
    except (ZeroDivisionError, ValueError, OverflowError):
        return bps_current


def nomura_target_price(
    sector: str,
    financials: dict[str, Any],
    *,
    coe: float = 0.10,
    terminal_growth: float = 0.025,
    payout_ratio: float = 0.30,
    forward_months: int = 12,
    sotp_segments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """노무라식 12개월 선행 목표주가.

    Args:
        sector: 종목 업종명 (한글). 라우팅에만 사용.
        financials: 입력 재무 데이터. 키:
            - ``bps``: 현재 BPS (per share).
            - ``eps``: 12M 선행 EPS (per share).
            - ``roe``: 지속가능 ROE (decimal, 예: 0.20).
            - ``shares_outstanding`` (SOTP 시 필요).
        coe: Cost of Equity. 한국 일반 종목 기본 10%.
        terminal_growth: 장기 성장률 g.
        payout_ratio: 배당성향 (BPS 롤포워드용).
        forward_months: 선행 개월수. 금융은 24개월 권장.
        sotp_segments: SOTP용 사업부 리스트. 각 dict:
            ``{"value": float}``  사업부 가치 (총액, 같은 통화).

    Returns:
        ``{"target_price": float, "method": str, "components": dict}``
    """
    raw_sector = (sector or "").strip()
    sector = _route_sector(raw_sector)
    raw_u = raw_sector.upper()
    bps   = float(financials.get("bps", financials.get("book_value", 0)) or 0)
    eps   = float(financials.get("eps", 0) or 0)
    roe   = float(financials.get("roe", 0) or 0)
    shares = float(financials.get("shares_outstanding", financials.get("shares", 0)) or 0)
    bias  = _nomura_bias(raw_sector)

    # --- A. SOTP (segments이 명시되면 최우선) ---
    if sotp_segments:
        total_value = sum(float(s.get("value", 0) or 0) for s in sotp_segments)
        if shares > 0 and total_value > 0:
            tp = (total_value / shares) * bias
            return {
                "target_price": tp,
                "method": "SOTP",
                "components": {
                    "segments": sotp_segments,
                    "total_value": total_value,
                    "shares": shares,
                    "nomura_bias": bias,
                },
            }

    # --- B. 은행·보험·증권: 2-stage Gordon Growth ---
    if sector in _FINANCIAL_SECTORS or _contains_any(raw_u, _FINANCIAL_SECTOR_ALIASES):
        months = max(forward_months, 24)
        fwd_bps = _forward_bps(bps, roe, payout_ratio, months=months)
        target_pb = _gordon_target_pb(roe, coe, terminal_growth) * bias
        tp = fwd_bps * target_pb
        return {
            "target_price": tp,
            "method": "Gordon-PB",
            "components": {
                "forward_bps": fwd_bps,
                "target_pb": target_pb,
                "roe": roe, "coe": coe, "g": terminal_growth,
                "forward_months": months,
                "nomura_bias": bias,
            },
        }

    # --- C. 시클리컬/메모리/소재: forward BPS × target P/B ---
    # --- C. 반도체 장비: Cyclical-PB + Forward-PE blend ---
    if "장비" in raw_sector or _contains_any(raw_u, _EQUIPMENT_SECTOR_ALIASES):
        fwd_bps = _forward_bps(bps, roe, payout_ratio, months=forward_months)
        target_pb = _gordon_target_pb(roe, coe, terminal_growth) * bias
        cyc_tp = fwd_bps * target_pb
        peer_pe = max(float(financials.get("peer_pe", 25.0) or 25.0), 22.0)
        pe_tp = eps * peer_pe * bias
        tp = (0.30 * cyc_tp) + (0.70 * pe_tp)
        return {
            "target_price": tp,
            "method": "Semicon-Blend",
            "components": {
                "forward_bps": fwd_bps,
                "target_pb": target_pb,
                "cyclical_target": cyc_tp,
                "forward_eps": eps,
                "peer_pe": peer_pe,
                "pe_target": pe_tp,
                "blend_weights": {"cyclical_pb": 0.30, "forward_pe": 0.70},
                "nomura_bias": bias,
            },
        }

    # --- D. ????/??/??: forward BPS ? target P/B ---
    if sector in _CYCLICAL_SECTORS or _contains_any(raw_u, _CYCLICAL_SECTOR_ALIASES):
        fwd_bps = _forward_bps(bps, roe, payout_ratio, months=forward_months)
        target_pb = _gordon_target_pb(roe, coe, terminal_growth) * bias
        tp = fwd_bps * target_pb
        return {
            "target_price": tp,
            "method": "Cyclical-PB",
            "components": {
                "forward_bps": fwd_bps,
                "target_pb": target_pb,
                "roe": roe, "coe": coe, "g": terminal_growth,
                "nomura_bias": bias,
            },
        }

    if sector in _GROWTH_SECTORS or _contains_any(raw_u, _GROWTH_SECTOR_ALIASES):
        fcf = float(financials.get("fcf", 0) or 0)
        if shares > 0 and fcf != 0 and abs(fcf) > shares:
            fcf = fcf / shares
        cash = float(financials.get("cash", 0) or 0)
        debt = float(financials.get("debt", 0) or 0)
        net_cash_ps = (cash - debt) / shares if shares > 0 else 0.0
        dcf = (_dcf_value(fcf, coe, 0.20, 0.10, terminal_growth) + net_cash_ps) * bias
        return {
            "target_price": dcf,
            "method": "DCF",
            "components": {"fcf_ps": fcf, "wacc": coe, "nomura_bias": bias},
        }

    # --- F. 기본/안정 성장주: forward EPS × peer P/E ---
    peer_pe = float(financials.get("peer_pe", 15.0) or 15.0)
    tp = eps * peer_pe * bias
    return {
        "target_price": tp,
        "method": "Forward-PE",
        "components": {"forward_eps": eps, "peer_pe": peer_pe, "nomura_bias": bias},
    }


# ---------------------------------------------------------------------------
# 투자지주사 NAV-할인율 평가
# ---------------------------------------------------------------------------
#
# SK스퀘어·LG·㈜SK 같은 투자지주사는 실적/DCF/PER로 평가하면 안 된다.
# 보유 자산(특히 상장 자회사 지분)의 시가총액 합 = NAV(순자산가치)이고,
# 지주사 주가는 NAV에 "지주사 할인율"이 적용된 값이다.
#
# 평가 축 2개:
#   1) 보유지분 시가 대비 현재 시총에 할인율이 얼마나 적용되어 고/저평가인가
#   2) 주주환원 정책(배당·자사주 매입/소각)이 목표 할인율을 얼마나 좁히는가
#
# BPS·EPS 기반 Gordon-PB/PER 경로는 지분법 장부가가 시가를 반영하지 못해
# 구조적으로 틀리므로, 투자지주사는 이 함수로 라우팅한다.


def holdco_nav_target_price(
    nav_ps: float,
    current_price: float,
    shareholder_yield: float = 0.0,
    *,
    base_discount: float = 0.50,
    floor_yield: float = 0.02,
    yield_sensitivity: float = 4.0,
    min_discount: float = 0.25,
) -> dict[str, Any]:
    """투자지주사 NAV-할인율 기반 목표가.

    Args:
        nav_ps: 주당 순자산가치. (Σ 상장지분 시가 + 비상장 장부가 + 순현금 − 차입) / 발행주식수.
            호출부에서 실시간 자회사 시총으로 산출해 넘긴다.
        current_price: 지주사 현재가.
        shareholder_yield: 주주환원 수익률(배당+자사주, decimal). 0.05 = 5%.
        base_discount: 주주환원이 평범할 때의 목표 할인율(한국 투자지주 통상 ~50%).
        floor_yield: "평범한" 주주환원 기준선. 이를 초과한 만큼 할인율이 좁혀진다.
        yield_sensitivity: 초과 주주환원 1%p당 목표 할인율 축소 폭(%p) 계수.
        min_discount: 목표 할인율 하한(주주환원이 강해도 이 밑으로는 안 좁힘).

    Returns:
        ``{"target_price", "method": "NAV-Discount", "components": {...}}``
    """
    nav = float(nav_ps or 0.0)
    px = float(current_price or 0.0)
    if nav <= 0.0:
        return {"target_price": 0.0, "method": "NAV-Discount",
                "components": {"error": "nav_ps<=0"}}

    sy = max(0.0, float(shareholder_yield or 0.0))
    # 주주환원이 floor를 넘는 만큼 목표 할인율을 좁힌다.
    excess = max(0.0, sy - floor_yield)
    target_discount = base_discount - yield_sensitivity * excess
    target_discount = max(min_discount, min(base_discount, target_discount))

    fair_value = nav * (1.0 - target_discount)

    # 현재 시장이 NAV에 매기고 있는 할인율
    current_discount = 1.0 - (px / nav) if px > 0 else 1.0

    # 시장 할인율이 목표보다 깊으면 저평가(상승여력), 얕으면 고평가
    if current_discount > target_discount + 0.03:
        verdict = "UNDERVALUED"
    elif current_discount < target_discount - 0.03:
        verdict = "OVERVALUED"
    else:
        verdict = "FAIR"

    return {
        "target_price": fair_value,
        "method": "NAV-Discount",
        "components": {
            "nav_ps": nav,
            "fair_value": fair_value,
            "current_discount": current_discount,
            "target_discount": target_discount,
            "base_discount": base_discount,
            "shareholder_yield": sy,
            "verdict": verdict,
        },
    }


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Samsung Electronics (005930.KS) — approximate FY2023 figures
    # Units: KRW per share / absolute KRW billions normalised to per-share basis
    # FCF proxy: operating CF - capex ≈ 4,500 KRW/share
    samsung_financials: dict[str, float] = {
        "fcf": 4_500,        # KRW per share
        "eps": 4_344,        # KRW per share (FY2023)
        "book_value": 52_000,  # KRW per share (approx)
        "ebitda": 8_200,     # KRW per share (approx)
    }

    current_price: float = 78_500  # KRW (approximate market price)

    result: ValuationResult = run(
        ticker="005930",
        current_price=current_price,
        financials=samsung_financials,
        wacc=0.10,
        growth_stage1=0.15,
        growth_stage2=0.08,
        terminal_growth=0.03,
    )

    print("=" * 60)
    print("  Samsung Electronics (005930) - Valuation Summary")
    print("=" * 60)
    print(f"  Current Price      : {current_price:>12,.0f} KRW")
    print()
    print(f"  DCF Value (base)   : {result.dcf_value:>12,.0f} KRW")
    print(f"  DCF Low  (WACC+1%) : {result.dcf_low:>12,.0f} KRW")
    print(f"  DCF High (WACC-1%) : {result.dcf_high:>12,.0f} KRW")
    print()
    print(f"  PER Fair Value     : {result.per_fair:>12,.0f} KRW")
    print(f"  PBR Fair Value     : {result.pbr_fair:>12,.0f} KRW")
    print(f"  EV/EBITDA Fair     : {result.ev_ebitda_fair:>12,.0f} KRW")
    print()
    lo, mid, hi = result.fair_value_range
    print(f"  Fair Value Range   : {lo:,.0f} ~ {mid:,.0f} ~ {hi:,.0f} KRW")
    print(f"  Discount / Premium : {result.discount_pct:>+.2f}%")
    print()
    print(f"  Implied Growth (Reverse DCF): {result.reverse_dcf_growth * 100:.2f}%")
    print()
    print("  Method Scores:")
    for method, score in result.method_scores.items():
        print(f"    {method:<12}: {score:>12,.0f} KRW")
    print("=" * 60)
