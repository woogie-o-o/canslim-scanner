"""Phase 3: 가격대별 대응 전략 — 변동성(σ) 밴드 기반 분할매수 모듈.

진입가는 시장 변동성 지수(KR=VKOSPI, US=VIX) 또는 종목 ATR 폴백으로 환산한
일일 σ(σ_daily)를 기준으로 산출한다. (기존 ATR 1·2·3배 고정 진입가를 교체)

  σ_daily(%) = 변동성지수 ÷ √252        # 지수 없으면 ATR_pct 폴백
  진입가_k   = 현재가 × (1 − k·σ_daily/100)
  도달확률   = Φ(−k)                     # 정규분포 CDF
  평균단가   = Σ비중 ÷ Σ(비중 / 진입가)  # 총투입금 약분
"""

from __future__ import annotations

import math

TRADING_DAYS = 252

# 레짐별 밴드 프로파일 (k 시그마 배열)
_SHALLOW_KS = [0.0, 0.3, 0.6, 0.9]            # 얕은 눌림 (시장가 시작)
_DEEP_KS = [1.0, 1.2, 1.4, 1.6, 1.8, 2.0]     # 급락 깊이 분할


def _erf(x: float) -> float:
    """Abramowitz–Stegun erf 근사."""
    t = 1 / (1 + 0.3275911 * abs(x))
    y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
              - 0.284496736) * t + 0.254829592) * t * math.exp(-x * x)
    return y if x >= 0 else -y


def _norm_cdf(z: float) -> float:
    return 0.5 * (1 + _erf(z / math.sqrt(2)))


def select_profile(vol_level: float | None, source: str) -> tuple[str, list[float]]:
    """변동성 지수 수준 → (profile, k_list). 레짐 자동 선택.

    고변동성 = deep(1~2σ), 그 외 = shallow(0~1σ). ATR 폴백은 항상 shallow.
    임계값은 macro_gate VIX 밴드와 정합 (VIX≥30 / VKOSPI≥40 → 고변동성).
    """
    if vol_level is not None:
        if source == "VKOSPI" and vol_level >= 40:
            return "deep", list(_DEEP_KS)
        if source == "VIX" and vol_level >= 30:
            return "deep", list(_DEEP_KS)
    return "shallow", list(_SHALLOW_KS)


def compute_price_levels(
    stock: dict,
    sigma_daily: float | None = None,
    ks: list[float] | None = None,
    source: str = "",
    profile: str = "",
) -> dict | None:
    """σ 밴드 진입가 + 손절 + 목표 수준 산출.

    sigma_daily 미지정 시 종목 ATR_pct로 폴백(단독 호출 호환).
    """
    price = stock.get('Price') or 0
    if price <= 0:
        return None

    if sigma_daily is None:
        atr_pct = stock.get('ATR_pct') or (
            (stock.get('ATR', 0) / price * 100) if price else 0)
        sigma_daily = atr_pct or 2.0
        source = source or 'ATR'
    if sigma_daily <= 0:
        sigma_daily = 2.0
    if not ks:
        profile, ks = select_profile(None, source)

    high_52w = stock.get('high_52w', price)
    low_52w = stock.get('low_52w', None)
    atr = stock.get('ATR', 0) or (price * 0.02)
    target_price = stock.get('AnalystTargetPrice', 0)

    # σ 밴드 진입가
    entries = []
    for k in ks:
        rate = k * sigma_daily
        entries.append({
            'k': round(k, 2),
            'rate': round(rate, 2),
            'price': round(price * (1 - rate / 100), 2),
            'prob': round(_norm_cdf(-k) * 100, 2),
        })

    # 손절: 마지막 밴드보다 한 스텝(최소 0.5σ) 더 깊은 지점
    last_k = ks[-1]
    step = (ks[-1] - ks[-2]) if len(ks) >= 2 else 0.3
    stop_k = round(last_k + max(step, 0.5), 2)
    stop_loss = round(price * (1 - stop_k * sigma_daily / 100), 2)

    # 하위호환: entry_1/2/3 = 첫 3밴드 (부족 시 마지막으로 패딩)
    band_prices = [e['price'] for e in entries]

    def nth(i):
        return band_prices[i] if i < len(band_prices) else band_prices[-1]

    result = {
        # 분할 매수 구간 (σ 밴드)
        'entry_1': nth(0),
        'entry_2': nth(1),
        'entry_3': nth(2),

        # 손절 기준
        'stop_loss': stop_loss,

        # 목표가
        'target_analyst': round(target_price, 2) if target_price > 0 else None,
        'target_52w_high': round(high_52w, 2),

        # 메타
        'price': price,
        'atr': round(atr, 2),
        'atr_pct': round(atr / price * 100, 1),
        'sigma_daily': round(sigma_daily, 3),
        'vol_source': source,
        'profile': profile,

        # 변동성 밴드 (드로어 인터랙티브 계산용)
        'vol_band': {
            'source': source,
            'profile': profile,
            'sigma_daily': round(sigma_daily, 3),
            'stop_k': stop_k,
            'entries': entries,
        },
    }

    # 피보나치 되돌림 지지선 (52주 저가 확보 시만)
    if low_52w is not None and low_52w > 0:
        range_52w = high_52w - low_52w
        if range_52w > 0:
            result['fib_382'] = round(high_52w - range_52w * 0.382, 2)
            result['fib_500'] = round(high_52w - range_52w * 0.500, 2)
            result['fib_618'] = round(high_52w - range_52w * 0.618, 2)

    return result


def compute_allocation(
    n: int,
    mode: str = "equal",
    geo_ratio: float = 1.3,
    step_mult: float = 2.5,
    custom: list[float] | None = None,
) -> list[float]:
    """회차별 비중 배열(합=1) 산출.

    equal  — 균등
    geo    — 기하급수 (공비 geo_ratio, 후반 가중)
    step   — 후반 1/3 회차를 step_mult 배 가중 (4+2 단계형 일반화)
    custom — 사용자 입력 상대비율 정규화
    """
    n = max(1, int(n))
    if mode == "geo":
        raw = [geo_ratio ** i for i in range(n)]
    elif mode == "step":
        back = max(1, n // 3)               # 후반 가중 구간
        raw = [step_mult if i >= n - back else 1.0 for i in range(n)]
    elif mode == "custom" and custom:
        raw = [max(0.0, float(x)) for x in custom[:n]]
        raw += [0.0] * (n - len(raw))
    else:
        raw = [1.0] * n
    s = sum(raw) or 1.0
    return [x / s for x in raw]


def average_cost(entries: list[dict], weights: list[float]) -> float:
    """현금가중 평균단가 = Σ비중 ÷ Σ(비중 / 진입가). 총투입금 약분."""
    tot = sum(weights)
    shares = sum(w / e['price'] for w, e in zip(weights, entries) if e['price'] > 0)
    return tot / shares if shares > 0 else 0.0


def compute_risk_scenarios(
    entries: list[dict],
    weights: list[float],
    base: float,
    loss_limit_pct: float = 15.0,
    stress_sigma: float | None = None,
    sigma_daily: float = 0.0,
) -> list[dict]:
    """4종 시나리오: 1회만 체결 / 중간 회복 / 전량 회복 / 하방 스트레스.

    금액 무관 % 기반(총투입금은 프론트가 곱함). stress_sigma 미지정 시 마지막 k+1σ.
    """
    n = len(entries)
    if n == 0:
        return []

    def fill(j):  # 0..j 회차까지 체결
        w = weights[:j + 1]
        ents = entries[:j + 1]
        wsum = sum(w)
        ac = average_cost(ents, w)
        return {"wsum": wsum, "ac": ac}

    last_k = entries[-1]['k']
    if stress_sigma is None:
        stress_sigma = last_k + 1.0
    price_stress = base * (1 - stress_sigma * sigma_daily / 100)

    s0 = fill(0)
    mid = max(0, round((n - 1) / 2))
    s_mid = fill(mid)
    s_all = fill(n - 1)

    def rec(ac):
        return (base - ac) / ac * 100 if ac > 0 else 0.0

    loss_stress = ((s_all["ac"] - price_stress) / s_all["ac"] * 100) if s_all["ac"] > 0 else 0.0

    return [
        {"type": "idle", "label": "1회만 체결 후 반등",
         "invested_pct": round(s0["wsum"] * 100, 1),
         "note": "미투입분 반등 시 기회손실"},
        {"type": "up", "label": f"중간({entries[mid]['k']:.1f}σ) 도달 후 회복",
         "invested_pct": round(s_mid["wsum"] * 100, 1),
         "avg_cost": round(s_mid["ac"], 2),
         "return_pct": round(rec(s_mid["ac"]), 2)},
        {"type": "up", "label": "전량 체결 후 회복",
         "invested_pct": 100.0,
         "avg_cost": round(s_all["ac"], 2),
         "return_pct": round(rec(s_all["ac"]), 2)},
        {"type": "down", "label": f"하방 스트레스 ({stress_sigma:.1f}σ 급락)",
         "loss_pct": round(loss_stress, 2),
         "over_limit": abs(loss_stress) > loss_limit_pct},
    ]


def generate_action_plan(
    stock: dict,
    price_levels: dict,
    scenarios: dict,
) -> dict:
    """보유 상태별 행동 지침 생성."""
    bull_pct = scenarios['bull']
    bear_pct = scenarios['bear']
    pil = stock.get('PriceInLevel')
    if pil is None:
        pil = 50

    band = price_levels.get('vol_band', {})
    ents = band.get('entries', [])

    def klabel(i):
        return f"{ents[i]['k']:.1f}σ" if i < len(ents) else ""

    plan = {
        'new_investor': {'action': '', 'details': []},
        'holder': {'action': '', 'details': []},
    }

    # 신규 진입자
    if bull_pct >= 40 and pil <= 50:
        plan['new_investor']['action'] = '분할 매수 고려'
        plan['new_investor']['details'] = [
            f"1차: {price_levels['entry_1']} ({klabel(0)} 하락)",
            f"2차: {price_levels['entry_2']} ({klabel(1)} 하락)",
            f"3차: {price_levels['entry_3']} ({klabel(2)} 하락)",
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


def build_price_strategy(
    stock: dict,
    scenarios: dict | None = None,
    vol: dict | None = None,
) -> dict | None:
    """가격 수준 + 행동 지침 + 기본 배분/시나리오 통합.

    Args:
        stock: 종목 데이터 dict (Price, ATR_pct, high_52w 등)
        scenarios: Phase 2 시나리오 점수 dict (없으면 기본값)
        vol: macro_gate.get_vol_index(market) 결과 {source, level}. 없으면 ATR 폴백.

    Returns:
        price_levels + action_plan + vol_band + 기본 평균단가 통합 dict 또는 None
    """
    price = stock.get('Price') or 0
    if price <= 0:
        return None

    # σ_daily 산출 (시장지수 → ATR 폴백)
    level = vol.get('level') if vol else None
    if level:
        source = vol.get('source', '')
        sigma_daily = level / math.sqrt(TRADING_DAYS)
    else:
        atr_pct = stock.get('ATR_pct') or (
            (stock.get('ATR', 0) / price * 100) if price else 0)
        sigma_daily = atr_pct or 2.0
        source = 'ATR'

    profile, ks = select_profile(level, source)
    price_levels = compute_price_levels(stock, sigma_daily, ks, source, profile)
    if price_levels is None:
        return None

    if scenarios is None:
        scenarios = {'bull': 33, 'neutral': 34, 'bear': 33}

    action_plan = generate_action_plan(stock, price_levels, scenarios)

    # 기본 배분(균등) 기준 평균단가/할인율 + 리스크 시나리오 (프론트 초기값)
    entries = price_levels['vol_band']['entries']
    weights = compute_allocation(len(entries), 'equal')
    avg = average_cost(entries, weights)
    discount = ((price - avg) / price * 100) if price > 0 else 0.0
    risk = compute_risk_scenarios(
        entries, weights, base=price, sigma_daily=sigma_daily)

    return {
        'price_levels': price_levels,
        'action_plan': action_plan,
        'vol_band': price_levels['vol_band'],
        'allocation_default': {
            'mode': 'equal',
            'avg_cost': round(avg, 2),
            'discount_pct': round(discount, 2),
        },
        'risk_scenarios': risk,
    }
