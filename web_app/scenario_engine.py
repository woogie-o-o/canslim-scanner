"""Phase 2: 시나리오 시그널 강도 계산 + 조건/대응 매핑 순수 함수 모듈"""

from __future__ import annotations


def compute_scenario_scores(stock: dict) -> dict:
    """강세/중립/약세 시그널 강도 산출. 정규화 후 비율로 변환."""

    bull_score = 0
    bear_score = 0
    contributions = []

    # 1. 퀄리티 축 (TotalScore, 0~100)
    ts = stock.get('TotalScore', 50)
    if ts >= 70:
        bull_score += 25
        contributions.append({'name': '높은 퀄리티 (S/A등급)', 'impact': +25})
    elif ts >= 55:
        bull_score += 15
        contributions.append({'name': '양호한 퀄리티 (B+등급)', 'impact': +15})
    elif ts < 40:
        bear_score += 20
        contributions.append({'name': '낮은 퀄리티 (C등급)', 'impact': -20})

    # 2. 타이밍 축 (EntryScore, 0~100)
    es = stock.get('EntryScore', 50)
    if es >= 50:
        bull_score += 20
        contributions.append({'name': '진입 적기 시그널', 'impact': +20})
    elif es <= 25:
        bear_score += 15
        contributions.append({'name': '진입 부적합 시그널', 'impact': -15})

    # 3. 모멘텀/과열 (RSI, 0~100)
    rsi = stock.get('RSI', 50)
    if rsi > 75:
        bear_score += 15
        contributions.append({'name': '과매수 구간 (RSI > 75)', 'impact': -15})
    elif rsi < 30:
        bull_score += 15
        contributions.append({'name': '과매도 반등 기대 (RSI < 30)', 'impact': +15})

    # 4. 시장 레짐 (실제 enum 매핑)
    regime = stock.get('Regime', '')
    regime_map_bull = {'STRONG_BULL': 15, 'BULL': 10, 'SIDEWAYS_BULL': 5}
    regime_map_bear = {'STRONG_BEAR': 20, 'BEAR': 15}
    r_bull = regime_map_bull.get(regime, 0)
    r_bear = regime_map_bear.get(regime, 0)
    if r_bull:
        bull_score += r_bull
        contributions.append({'name': f'상승 추세 ({regime})', 'impact': +r_bull})
    if r_bear:
        bear_score += r_bear
        contributions.append({'name': f'하락 추세 ({regime})', 'impact': -r_bear})

    # 5. 선반영도 (Phase 1, 0~100) -- Phase 1 미배포 시 폴백
    pil = stock.get('PriceInLevel', None)
    if pil is not None:
        if pil >= 70:
            bear_score += 20
            contributions.append({'name': '기대치 과반영', 'impact': -20})
        elif pil <= 30:
            bull_score += 15
            contributions.append({'name': '기대치 저반영', 'impact': +15})

    # 6. 밸류에이션 상대위치 (Phase 1) -- Phase 1 미배포 시 스킵
    srpe = stock.get('SectorRelPE', None)
    if srpe is not None:
        if srpe < -20:
            bull_score += 10
            contributions.append({'name': '섹터 대비 저평가', 'impact': +10})
        elif srpe > 30:
            bear_score += 10
            contributions.append({'name': '섹터 대비 고평가', 'impact': -10})

    # 정규화 -> 비율 -> 클램핑 -> 재정규화 (합계 100% 보장)
    total = bull_score + bear_score + 30  # 30 = 중립 베이스
    raw_bull = bull_score / total * 100
    raw_bear = bear_score / total * 100
    raw_neutral = 100 - raw_bull - raw_bear

    # 클램핑 (과신 방지)
    b = max(10, min(70, raw_bull))
    n = max(15, min(60, raw_neutral))
    e = max(10, min(70, raw_bear))

    # 재정규화 -> 합계 100% 보장
    s = b + n + e
    bull_final = round(b / s * 100)
    bear_final = round(e / s * 100)
    neutral_final = 100 - bull_final - bear_final

    # 핵심 변수 상위 3개 추출 (절대 영향력 기준)
    top_vars = sorted(contributions, key=lambda x: abs(x['impact']), reverse=True)[:3]

    return {
        'bull': bull_final,
        'neutral': neutral_final,
        'bear': bear_final,
        'key_variables': top_vars,
        'contributions': contributions,
    }


def generate_active_triggers(stock: dict) -> dict:
    """현재 종목 데이터에서 실제로 충족되는 조건만 추출."""
    es = stock.get('EntryScore', 50)
    triggers = {
        'bull': [],
        'neutral': [],
        'bear': [],
    }

    # 강세 트리거
    if stock.get('TotalScore', 0) >= 70:
        triggers['bull'].append('높은 종합 퀄리티 (S/A등급)')
    if es >= 50:
        triggers['bull'].append('진입 적기 시그널')
    if stock.get('PriceInLevel') is not None and stock['PriceInLevel'] <= 30:
        triggers['bull'].append('기대치 저반영 상태')
    if stock.get('Regime', '') in ('STRONG_BULL', 'BULL', 'SIDEWAYS_BULL'):
        triggers['bull'].append('상승 추세')

    # 약세 트리거
    if stock.get('RSI', 50) > 75:
        triggers['bear'].append('과매수 구간')
    if stock.get('PriceInLevel') is not None and stock['PriceInLevel'] >= 70:
        triggers['bear'].append('기대치 과반영')
    if stock.get('Regime', '') in ('BEAR', 'STRONG_BEAR'):
        triggers['bear'].append('하락 추세')
    if stock.get('SectorRelPE') is not None and stock['SectorRelPE'] > 30:
        triggers['bear'].append('섹터 대비 고평가')

    # 중립 트리거
    ts = stock.get('TotalScore', 50)
    if 50 <= ts < 70:
        triggers['neutral'].append('양호하지만 돌출 아님')
    pil = stock.get('PriceInLevel')
    if pil is not None and 30 < pil < 60:
        triggers['neutral'].append('적정 수준 반영')

    return triggers


SCENARIO_RESPONSE = {
    'bull': '눌림목 분할 매수',
    'neutral': '추격 매수 금지, 조정 시 재검토',
    'bear': '비중 축소 또는 손절',
}


def build_scenario_table(stock: dict) -> dict:
    """scenarios + triggers + responses를 하나의 dict로 반환."""
    scores = compute_scenario_scores(stock)
    triggers = generate_active_triggers(stock)

    return {
        'scores': scores,
        'triggers': triggers,
        'responses': SCENARIO_RESPONSE,
    }
