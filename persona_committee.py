# -*- coding: utf-8 -*-
"""
7-Persona Investment Committee — 5/7 매수 게이트.

월스트리트 20년차 퀀트의 "Phase 7" 다중 페르소나 위원회를 가벼운 룰베이스로 구현.
이미 계산된 4축 분석 결과 + (옵션) CAN SLIM 메트릭을 받아 7명의 의견을 도출한다.

Personas:
  1. Value      — 밸류에이션·재무 건전성 중심
  2. Growth     — 매출/EPS 성장, 모멘텀 점수
  3. Momentum   — 추세·돌파·거래량 컨펌
  4. Macro      — 시장 레짐/거시 게이트 (선택)
  5. ESG        — (데이터 없으면 중립)
  6. Risk       — 변동성·MDD·손절폭
  7. AI Oracle  — 4축 종합 + 시너지 패턴

Returns:
  CommitteeResult — verdicts: List[PersonaVerdict], buy_count, gate_pass(bool),
                    integrated_score(0~100), grade(str), summary(str)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PersonaVerdict:
    name: str           # "Value" 등
    verdict: str        # "매수" / "보유" / "매도"
    rationale: str      # 2줄 이내 근거


@dataclass
class CommitteeResult:
    verdicts:         List[PersonaVerdict]
    buy_count:        int
    sell_count:       int
    gate_pass:        bool       # 5/7 이상 매수
    integrated_score: float      # 0~100
    grade:            str        # "Must Buy" / "Strong Buy" / ...
    summary:          str        # 한 줄 결론
    weak_trend_warning: bool = False
    canslim_low_warning: bool = False  # CANSLIM TotalScore < 40이면 관망


_GRADE_TABLE = [
    (90, "Top Pick"),
    (80, "Strong"),
    (70, "Positive"),
    (60, "Neutral"),
    (50, "Cautious"),
    (40, "Weak"),
    (0,  "Avoid"),
]


def _grade(score: float) -> str:
    for thr, label in _GRADE_TABLE:
        if score >= thr:
            return label
    return "Strong Sell"


def _v_value(canslim: dict) -> PersonaVerdict:
    val   = float(canslim.get("ValueScore",   50))
    qual  = float(canslim.get("QualityScore", 50))
    rsi   = float(canslim.get("RSI", 50))
    avg = (val + qual) / 2
    if avg >= 60 and rsi <= 65:
        return PersonaVerdict("Value", "매수",
            f"품질·가치 평균 {avg:.0f} (양호) · RSI {rsi:.0f} 과열 아님")
    if avg <= 40 or rsi >= 75:
        return PersonaVerdict("Value", "매도",
            f"품질·가치 {avg:.0f} (부족) 또는 RSI {rsi:.0f} 과열")
    return PersonaVerdict("Value", "보유",
        f"품질·가치 평균 {avg:.0f} 중립")


def _v_growth(four: Any, canslim: dict) -> PersonaVerdict:
    mom_score = float(canslim.get("MomentumScore", 50))
    mom_12m   = float(canslim.get("Mom12M", 0))
    eps_acc   = bool(canslim.get("EPSAcceleration", False))
    m_axis    = four.momentum.score if four else 3
    if mom_score >= 60 and m_axis >= 4:
        rationale = f"모멘텀 점수 {mom_score:.0f} · 12M 수익률 {mom_12m:+.1%}"
        if eps_acc: rationale += " · EPS 가속"
        return PersonaVerdict("Growth", "매수", rationale)
    if mom_score <= 40 or m_axis <= 2:
        return PersonaVerdict("Growth", "매도",
            f"모멘텀 {mom_score:.0f}/4축 모멘텀 {m_axis}/5 — 추진력 부족")
    return PersonaVerdict("Growth", "보유",
        f"모멘텀 {mom_score:.0f} · 4축 모멘텀 {m_axis}/5 중립")


def _v_momentum(four: Any) -> PersonaVerdict:
    if four is None:
        return PersonaVerdict("Momentum", "보유", "4축 데이터 없음")
    t, m, vt, vl = four.trend.score, four.momentum.score, four.volatility.score, four.volume.score
    bull = sum(1 for s in (t, m, vt, vl) if s >= 4)
    bear = sum(1 for s in (t, m, vt, vl) if s <= 2)
    squeeze = four.volatility.details.get("squeeze", False)
    upper_break = four.volatility.details.get("upper_break", False)
    if bull >= 3 and t >= 4:
        return PersonaVerdict("Momentum", "매수",
            f"4축 정렬 {bull}/4 · 추세 {t}/5"
            + (" · BB 상단 돌파" if upper_break else ""))
    if squeeze and m >= 4:
        return PersonaVerdict("Momentum", "매수",
            f"BB 스퀴즈 + 모멘텀 {m}/5 — 브레이크아웃 임박")
    if bear >= 3 or t <= 2:
        return PersonaVerdict("Momentum", "매도",
            f"4축 약세 {bear}/4 · 추세 {t}/5 — 관망")
    return PersonaVerdict("Momentum", "보유",
        f"4축 혼조 (정렬 {bull}, 약세 {bear}) — 트리거 대기")


def _v_macro(macro: Optional[dict]) -> PersonaVerdict:
    if not macro or not macro.get("regime"):
        return PersonaVerdict("Macro", "보유", "거시 데이터 없음 — 중립")
    reg = macro["regime"]
    vix = macro.get("vix")
    vix_str = f"VIX {vix:.1f}" if isinstance(vix, (int, float)) else "VIX -"
    if reg == "Risk-On":
        return PersonaVerdict("Macro", "매수", f"Risk-On · {vix_str}")
    if reg == "Risk-Off":
        return PersonaVerdict("Macro", "매도", f"Risk-Off · {vix_str} — 전반 위험")
    return PersonaVerdict("Macro", "보유", f"{reg} · {vix_str}")


def _v_esg(canslim: dict) -> PersonaVerdict:
    # ESG raw 데이터 없음 → 품질 점수로 프록시
    q = float(canslim.get("QualityScore", 50))
    if q >= 70:
        return PersonaVerdict("ESG", "매수", f"Quality 프록시 {q:.0f} — ESG 양호 추정")
    if q <= 35:
        return PersonaVerdict("ESG", "매도", f"Quality 프록시 {q:.0f} — 부실 위험")
    return PersonaVerdict("ESG", "보유", f"Quality {q:.0f} 중립 (ESG 데이터 부재)")


def _v_risk(four: Any, canslim: dict) -> PersonaVerdict:
    atr_pct = float(canslim.get("ATRPercent", 3.0))
    dd      = float(canslim.get("Drawdown", 0))
    stop_pct = 0.0
    if four and four.risk:
        stop_pct = float(four.risk.get("stop_pct", 0))
    if atr_pct > 6 or dd < -0.20:
        return PersonaVerdict("Risk", "매도",
            f"ATR {atr_pct:.1f}% · MDD {dd:.0%} — 변동성·낙폭 과다")
    if atr_pct <= 3 and stop_pct <= 6 and dd > -0.10:
        return PersonaVerdict("Risk", "매수",
            f"ATR {atr_pct:.1f}% · 손절폭 {stop_pct:.1f}% — 통제 가능")
    return PersonaVerdict("Risk", "보유",
        f"ATR {atr_pct:.1f}% · MDD {dd:.0%} 중간 — 비중 축소 권장")


def _v_ai_oracle(four: Any, canslim: dict) -> PersonaVerdict:
    """4축 별점 + CAN SLIM 점수의 종합 — 진입 게이트 가장 엄격."""
    if four is None:
        sc = float(canslim.get("TotalScore", 50))
        if sc >= 70: return PersonaVerdict("AI Oracle", "매수", f"종합 {sc:.0f}")
        if sc <= 45: return PersonaVerdict("AI Oracle", "매도", f"종합 {sc:.0f}")
        return PersonaVerdict("AI Oracle", "보유", f"종합 {sc:.0f}")

    stars = four.signal_stars
    t = four.trend.score
    sc = float(canslim.get("TotalScore", 50))
    # 추세 약(≤2) + 별점 ≥3 인 케이스를 명시적으로 경고
    if t <= 2 and stars >= 3:
        return PersonaVerdict("AI Oracle", "보유",
            f"별점 ★{stars} BUT 추세 {t}/5 — 추세 게이트 통과 못함, 관망")
    if stars >= 4 and sc >= 70:
        return PersonaVerdict("AI Oracle", "매수",
            f"별점 ★{stars} · 종합 {sc:.0f} — 다축 컨펌")
    if stars <= 2 or sc <= 40:
        return PersonaVerdict("AI Oracle", "매도",
            f"별점 ★{stars} · 종합 {sc:.0f} — 신호 부재")
    return PersonaVerdict("AI Oracle", "보유",
        f"별점 ★{stars} · 종합 {sc:.0f} — 트리거 대기")


def evaluate(four_axis_result: Any = None,
             canslim: Optional[dict] = None,
             macro: Optional[dict] = None) -> CommitteeResult:
    """
    Args:
        four_axis_result: FourAxisResult (선택)
        canslim:          {"TotalScore","ValueScore","QualityScore",
                           "MomentumScore","RSI","Mom12M","EPSAcceleration",
                           "ATRPercent","Drawdown",...}
        macro:            {"regime":"Risk-On"/"Neutral"/"Risk-Off","vix":float}

    Returns: CommitteeResult
    """
    cs = canslim or {}
    verdicts = [
        _v_value(cs),
        _v_growth(four_axis_result, cs),
        _v_momentum(four_axis_result),
        _v_macro(macro),
        _v_esg(cs),
        _v_risk(four_axis_result, cs),
        _v_ai_oracle(four_axis_result, cs),
    ]
    buy = sum(1 for v in verdicts if v.verdict == "매수")
    sell = sum(1 for v in verdicts if v.verdict == "매도")

    # 통합 스코어 — 가중 합성
    total = float(cs.get("TotalScore", 50))
    star_norm = (four_axis_result.signal_stars * 20) if four_axis_result else 50
    persona_pts = (buy - sell) * 8 + 50   # -50~+50 → 약 0~100
    integrated = max(0.0, min(100.0,
        0.45 * total + 0.30 * star_norm + 0.25 * persona_pts))

    weak_trend = bool(four_axis_result and four_axis_result.trend.score <= 2
                      and four_axis_result.signal_stars >= 3)

    # CANSLIM 점수 구간별 게이트 요건 및 스코어 캡
    # 85+: 우수 — buy>=4로 완화  /  70~84: 양호 — buy>=5 정상
    # 55~69: 보통 — buy>=5 유지  /  40~54: 미흡 — buy>=6 강화
    # <40:  부족 — 진입 차단
    if total >= 85:
        gate = buy >= 4
        canslim_label = f"CANSLIM {total:.0f}점 (우수)"
        score_cap = 100
    elif total >= 70:
        gate = buy >= 5
        canslim_label = f"CANSLIM {total:.0f}점 (양호)"
        score_cap = 100
    elif total >= 55:
        gate = buy >= 5
        canslim_label = f"CANSLIM {total:.0f}점 (보통)"
        score_cap = 85
    elif total >= 40:
        gate = buy >= 6
        canslim_label = f"CANSLIM {total:.0f}점 (미흡, 진입 조건 강화)"
        score_cap = 70
        integrated = min(integrated, 70)
    else:
        gate = False
        canslim_label = f"CANSLIM {total:.0f}점 (부족)"
        score_cap = 60
        integrated = min(integrated, 60)

    integrated = min(integrated, float(score_cap))
    canslim_low = total < 55  # 55점 미만이면 주의 표시

    if weak_trend:
        integrated = min(integrated, 65)
        gate = False

    grade = _grade(integrated)
    if gate:
        summary = f"위원회 {buy}/7 매수 / {canslim_label} — {grade} ({integrated:.0f}/100)"
    elif total < 40:
        summary = f"⚠ {canslim_label} — 펀더멘털 미달 ({integrated:.0f}/100)"
    elif total < 55:
        summary = f"⚠ {canslim_label} — 관망 (위원회 {buy}/7) ({integrated:.0f}/100)"
    elif weak_trend:
        summary = f"⚠ 추세 약 — {canslim_label}, 관망 ({integrated:.0f}/100)"
    else:
        summary = f"위원회 {buy}/7 / {canslim_label} — {grade} ({integrated:.0f}/100), 관찰"

    return CommitteeResult(
        verdicts=verdicts, buy_count=buy, sell_count=sell,
        gate_pass=gate, integrated_score=round(integrated, 1),
        grade=grade, summary=summary, weak_trend_warning=weak_trend,
        canslim_low_warning=canslim_low,
    )


if __name__ == "__main__":
    # Self-check
    cs = dict(TotalScore=72, ValueScore=65, QualityScore=70,
              MomentumScore=68, RSI=58, Mom12M=0.18,
              EPSAcceleration=True, ATRPercent=2.4, Drawdown=-0.05)
    r = evaluate(None, cs, {"regime":"Risk-On","vix":15.2})
    print(r.summary)
    for v in r.verdicts:
        print(f"  {v.name:<10} {v.verdict}  — {v.rationale}")
    assert r.buy_count >= 1
    print("[OK] persona_committee self-check passed")
