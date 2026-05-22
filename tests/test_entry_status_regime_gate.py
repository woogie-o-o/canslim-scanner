"""
EntryStatus MeanRev 과열 페널티 — regime-gate 회귀 테스트.

배경: 추세장에서 mean-revert factor는 역수익 부호 — RSI 77이라도
STRONG_BULL/BULL에서는 'still leading'이므로 약감점만 적용해야 한다.
이전: RSI≥70 무조건 -14, BB>0.95 무조건 -10 → 추세장 1등주 EntryStatus 추락.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from quant_nexus_v20 import _meanrev_overheat_penalty  # noqa: E402


REGIMES = ["STRONG_BULL", "BULL", "RANGE", "BEAR", "STRONG_BEAR"]
RSI_VALUES = [65, 70, 75, 80, 85]


@pytest.mark.parametrize("regime", REGIMES)
@pytest.mark.parametrize("rsi", RSI_VALUES)
def test_rsi_overheat_regime_gate(regime, rsi):
    """5 regime × 5 RSI = 25 케이스. STRONG_BULL/BULL은 |pen|≤4, 그 외는 ≥10."""
    pts, _ = _meanrev_overheat_penalty(rsi, bb_pos=0.0, regime=regime)
    if rsi < 70:
        assert pts == 0, f"{regime}/RSI={rsi}: 과열 임계 미만은 페널티 없어야 함 (got {pts})"
        return
    if regime in ("STRONG_BULL", "BULL"):
        assert -4 <= pts <= 0, f"{regime}/RSI={rsi}: 추세장은 약감점(|pen|≤4)인데 {pts}"
    else:
        assert pts <= -10, f"{regime}/RSI={rsi}: 비추세장은 강감점(≥10)인데 {pts}"


@pytest.mark.parametrize("regime", REGIMES)
def test_bb_overextension_regime_gate(regime):
    """BB > 0.95 과확장도 추세장은 약감점, 그 외는 -10."""
    pts, _ = _meanrev_overheat_penalty(rsi=50.0, bb_pos=0.97, regime=regime)
    if regime in ("STRONG_BULL", "BULL"):
        assert pts == -4
    else:
        assert pts == -10


def test_rsi_and_bb_combined_takes_stronger():
    """RSI 75 + BB 0.97이 동시일 때 더 강한(더 작은) 페널티가 채택된다."""
    # RANGE: RSI -14 vs BB -10 → -14 채택
    pts, _ = _meanrev_overheat_penalty(75.0, 0.97, "RANGE")
    assert pts == -14
    # BULL: RSI -4, BB -4 → 동일
    pts, _ = _meanrev_overheat_penalty(75.0, 0.97, "BULL")
    assert pts == -4


def test_tag_reflects_regime_context():
    """추세장 태그에는 '추세 유지' 문구가 포함되어야 한다."""
    _, tag = _meanrev_overheat_penalty(77.0, 0.0, "BULL")
    assert tag is not None and "추세 유지" in tag
    _, tag = _meanrev_overheat_penalty(77.0, 0.0, "RANGE")
    assert tag is not None and "추세 유지" not in tag
