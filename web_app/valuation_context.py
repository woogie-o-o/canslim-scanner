"""Phase 1: 밸류에이션 맥락화 순수 함수 모듈"""

from __future__ import annotations


def _valid_per(v) -> float | None:
    """PER이 유효(양수, 비0)하면 반환, 아니면 None."""
    if v is None or v <= 0:
        return None
    return float(v)


def compute_val_pctile(
    current_per: float | None,
    price_history: list[float] | None,
    eps_ttm: float | None,
) -> float | None:
    """현재 PER이 과거 추정 PER 분포에서 어느 백분위인지 산출 (해법 B: EPS 역산).

    Args:
        current_per: 현재 PER (양수만 유효)
        price_history: 최근 12개월 종가 리스트
        eps_ttm: Trailing 12M EPS (양수만 유효)

    Returns:
        0~100 백분위 또는 None (산출 불가 시)
    """
    per = _valid_per(current_per)
    if per is None:
        return None

    if not price_history or eps_ttm is None or eps_ttm <= 0:
        return None

    # EPS 역산으로 과거 PER 추정
    historical_pers = []
    for p in price_history:
        if p is not None and p > 0:
            estimated_per = p / eps_ttm
            if estimated_per > 0:
                historical_pers.append(estimated_per)

    if len(historical_pers) < 3:
        return None

    # 현재 PER이 과거 분포에서 몇 번째인지 계산
    below_count = sum(1 for hp in historical_pers if hp <= per)
    pctile = below_count / len(historical_pers) * 100
    return round(pctile, 1)


def compute_sector_rel_pe(stock: dict, sector_peers: list[dict]) -> float | None:
    """섹터 내 PER 중앙값 대비 프리미엄/할인율 (%) 산출."""
    per = _valid_per(stock.get('_PER'))
    if per is None:
        return None

    valid_peers = [_valid_per(s.get('_PER')) for s in sector_peers]
    valid_peers = [p for p in valid_peers if p is not None]

    if len(valid_peers) < 3:
        return None

    sector_median = sorted(valid_peers)[len(valid_peers) // 2]
    if sector_median <= 0:
        return None

    return round((per - sector_median) / sector_median * 100, 1)


def compute_price_in_level(
    val_pctile: float | None,
    dist_from_52w_high: float | None,
    consensus_gap: float | None,
) -> float | None:
    """선반영 복합 점수 (0~100). 가용 요소만으로 가중합."""
    components = []

    if val_pctile is not None:
        components.append(('val', 40, val_pctile))

    if dist_from_52w_high is not None and dist_from_52w_high < 1.0:
        score_52w = max(0, min(100, (1 - dist_from_52w_high) * 100))
        components.append(('52w', 30, score_52w))

    if consensus_gap is not None and consensus_gap > 0:
        score_con = max(0, min(100, consensus_gap * 100))
        components.append(('con', 30, score_con))

    if not components:
        return None

    total_weight = sum(w for _, w, _ in components)
    weighted_sum = sum(w * s for _, w, s in components)
    return round(weighted_sum / total_weight, 1)


def attach_valuation_context(
    stock: dict,
    sector_peers: list[dict],
    price_history: list[float] | None = None,
    eps_ttm: float | None = None,
) -> dict:
    """stock dict에 ValPctile, SectorRelPE, PriceInLevel 키를 부착.

    Args:
        stock: 종목 데이터 dict (변경됨)
        sector_peers: 동일 섹터 종목 리스트
        price_history: 최근 12개월 종가 리스트
        eps_ttm: TTM EPS

    Returns:
        ValPctile, SectorRelPE, PriceInLevel이 부착된 stock dict
    """
    # ValPctile
    val_pctile = compute_val_pctile(stock.get('_PER'), price_history, eps_ttm)
    stock['ValPctile'] = val_pctile

    # SectorRelPE
    stock['SectorRelPE'] = compute_sector_rel_pe(stock, sector_peers)

    # PriceInLevel 산출을 위한 consensus_gap 계산
    price = stock.get('Price', 0)
    mean_target = stock.get('AnalystTargetPrice', 0) or stock.get('mean_target', 0)
    consensus_gap = None
    if price > 0 and mean_target and mean_target > 0:
        consensus_gap = price / mean_target

    dist_from_52w_high = stock.get('dist_from_52w_high')

    stock['PriceInLevel'] = compute_price_in_level(
        val_pctile, dist_from_52w_high, consensus_gap
    )

    return stock
