"""Phase 3: 가격대별 대응 전략 순수 함수 모듈"""

from __future__ import annotations


def compute_price_levels(stock: dict) -> dict | None:
    """진입/추가매수/손절/목표 가격 수준 산출."""
    price = stock.get('Price', 0)
    if price <= 0:
        return None

    high_52w = stock.get('high_52w', price)
    low_52w = stock.get('low_52w', None)
    atr = stock.get('ATR', 0) or (price * 0.02)
    target_price = stock.get('AnalystTargetPrice', 0)

    result = {
        # 분할 매수 구간
        'entry_1': round(price - atr * 1, 2),
        'entry_2': round(price - atr * 2, 2),
        'entry_3': round(price - atr * 3, 2),

        # 손절 기준
        'stop_loss': round(price - atr * 4, 2),

        # 목표가
        'target_analyst': round(target_price, 2) if target_price > 0 else None,
        'target_52w_high': round(high_52w, 2),

        # 메타
        'price': price,
        'atr': round(atr, 2),
        'atr_pct': round(atr / price * 100, 1),
    }

    # 피보나치 되돌림 지지선 (52주 저가 확보 시만)
    if low_52w is not None and low_52w > 0:
        range_52w = high_52w - low_52w
        if range_52w > 0:
            result['fib_382'] = round(high_52w - range_52w * 0.382, 2)
            result['fib_500'] = round(high_52w - range_52w * 0.500, 2)
            result['fib_618'] = round(high_52w - range_52w * 0.618, 2)

    return result


def generate_action_plan(
    stock: dict,
    price_levels: dict,
    scenarios: dict,
) -> dict:
    """보유 상태별 행동 지침 생성."""
    bull_pct = scenarios['bull']
    bear_pct = scenarios['bear']
    pil = stock.get('PriceInLevel', 50)

    plan = {
        'new_investor': {'action': '', 'details': []},
        'holder': {'action': '', 'details': []},
    }

    # 신규 진입자
    if bull_pct >= 40 and pil <= 50:
        plan['new_investor']['action'] = '분할 매수 고려'
        plan['new_investor']['details'] = [
            f"1차: {price_levels['entry_1']} (ATR 1배 하락)",
            f"2차: {price_levels['entry_2']} (ATR 2배 하락)",
            f"3차: {price_levels['entry_3']} (ATR 3배 하락)",
            f"손절: {price_levels['stop_loss']} 이탈 시",
        ]
    elif bull_pct >= 30:
        plan['new_investor']['action'] = '관망 (조정 대기)'
        fib = price_levels.get('fib_382')
        if fib:
            plan['new_investor']['details'].append(
                f"피보나치 38.2% ({fib}) 도달 시 재검토")
        plan['new_investor']['details'].append("추격 매수 지양")
    else:
        plan['new_investor']['action'] = '진입 보류'
        plan['new_investor']['details'] = [
            "하락 시그널이 상승 시그널을 초과",
            "추세 전환 확인 후 재검토",
        ]

    # 기존 보유자
    if bear_pct >= 40:
        plan['holder']['action'] = '비중 축소 고려'
        plan['holder']['details'] = [
            f"손절: {price_levels['stop_loss']} 이탈 시",
            "부분 이익 실현 고려",
        ]
    elif bull_pct >= 40:
        plan['holder']['action'] = '보유 유지'
        target = price_levels.get('target_analyst') or price_levels.get('target_52w_high')
        plan['holder']['details'] = [
            f"목표가: {target}",
            f"추가 매수 고려: {price_levels['entry_2']} 구간",
        ]
    else:
        plan['holder']['action'] = '보유 유지 (추가매수 보류)'
        plan['holder']['details'] = [
            "현 비중 유지, 추가 투입 지양",
            f"손절: {price_levels['stop_loss']} 이탈 시",
        ]

    return plan


def build_price_strategy(stock: dict, scenarios: dict | None = None) -> dict | None:
    """가격 수준 산출 + 행동 지침을 조합하는 통합 함수.

    Args:
        stock: 종목 데이터 dict
        scenarios: Phase 2 시나리오 점수 dict (없으면 기본값 사용)

    Returns:
        price_levels + action_plan 통합 dict 또는 None
    """
    price_levels = compute_price_levels(stock)
    if price_levels is None:
        return None

    if scenarios is None:
        scenarios = {'bull': 33, 'neutral': 34, 'bear': 33}

    action_plan = generate_action_plan(stock, price_levels, scenarios)

    return {
        'price_levels': price_levels,
        'action_plan': action_plan,
    }
