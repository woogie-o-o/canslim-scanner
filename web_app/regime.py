"""MF-003: regime-classifier — '스토리장 vs 펀더장' 분류.

월가 패널(Renaissance) 권고: 체제 따라 speculative 캡 강도를 동적으로 조정.
- story 체제: 시장이 내러티브로 움직임 → 캡 강화 (49)
- fundamental 체제: 실적 기반 시장 → 캡 완화 (64)
- mixed: 기본값 유지 (59)

입력 시그널 3종:
  - VIX (공포지수) — 25 초과 시 story 기여
  - SPY 200MA 상회율 — 0.6 미만 시 story 기여 (약세장)
  - 52w 신고가 비율 — 0.05 미만 시 story 기여 (breadth 약화)

3종 중 2개 이상 → story, 0~1개 → fundamental, 정확히 동률 케이스 → mixed.
"""

from __future__ import annotations

from typing import Literal, NamedTuple

Regime = Literal["story", "fundamental", "mixed"]


class RegimeResult(NamedTuple):
    regime: Regime
    score_cap: float
    signals: dict
    reason: str


_CAP_STORY = 49.0
_CAP_FUND = 64.0
_CAP_MIXED = 59.0


def classify_regime(
    vix: float | None,
    spy_above_ma200: float | None,
    new_highs_ratio: float | None,
) -> RegimeResult:
    signals = {
        "vix": vix,
        "spy_above_ma200": spy_above_ma200,
        "new_highs_ratio": new_highs_ratio,
    }

    story_votes = 0
    fund_votes = 0
    reasons = []

    if vix is not None:
        if vix > 25:
            story_votes += 1
            reasons.append(f"VIX={vix:.1f}>25 (story)")
        else:
            fund_votes += 1
            reasons.append(f"VIX={vix:.1f}≤25 (fund)")

    if spy_above_ma200 is not None:
        if spy_above_ma200 < 0.6:
            story_votes += 1
            reasons.append(f"SPY>MA200={spy_above_ma200:.0%}<60% (story)")
        else:
            fund_votes += 1
            reasons.append(f"SPY>MA200={spy_above_ma200:.0%}≥60% (fund)")

    if new_highs_ratio is not None:
        if new_highs_ratio < 0.05:
            story_votes += 1
            reasons.append(f"52w highs={new_highs_ratio:.1%}<5% (story)")
        else:
            fund_votes += 1
            reasons.append(f"52w highs={new_highs_ratio:.1%}≥5% (fund)")

    total = story_votes + fund_votes
    if total == 0:
        return RegimeResult("mixed", _CAP_MIXED, signals, "no signals")

    if story_votes >= 2:
        return RegimeResult("story", _CAP_STORY, signals, "; ".join(reasons))
    if fund_votes >= 2:
        return RegimeResult("fundamental", _CAP_FUND, signals, "; ".join(reasons))
    return RegimeResult("mixed", _CAP_MIXED, signals, "; ".join(reasons))
