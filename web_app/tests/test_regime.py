"""MF-003: regime-classifier 테스트."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from regime import classify_regime  # type: ignore


def test_story_regime_high_vix_weak_breadth():
    r = classify_regime(vix=30, spy_above_ma200=0.40, new_highs_ratio=0.03)
    assert r.regime == "story"
    assert r.score_cap == 49.0


def test_fundamental_regime_low_vix_strong_breadth():
    r = classify_regime(vix=15, spy_above_ma200=0.80, new_highs_ratio=0.12)
    assert r.regime == "fundamental"
    assert r.score_cap == 64.0


def test_mixed_regime_one_each():
    # VIX 정상, breadth 약함, 신고가 정상 → 1 story / 2 fund → fundamental
    r = classify_regime(vix=18, spy_above_ma200=0.5, new_highs_ratio=0.08)
    assert r.regime == "fundamental"


def test_only_one_signal_missing_others():
    # 단일 시그널만 — 결과 따라감
    r = classify_regime(vix=30, spy_above_ma200=None, new_highs_ratio=None)
    assert r.regime in ("story", "mixed")  # 단일 표는 mixed 도 허용


def test_all_none_returns_mixed():
    r = classify_regime(vix=None, spy_above_ma200=None, new_highs_ratio=None)
    assert r.regime == "mixed"
    assert r.score_cap == 59.0


def test_story_boundary_vix_25():
    # 정확히 25 는 fund (>25 만 story)
    r = classify_regime(vix=25, spy_above_ma200=0.7, new_highs_ratio=0.1)
    assert r.regime == "fundamental"


def test_score_caps_differ_by_regime():
    s = classify_regime(vix=35, spy_above_ma200=0.3, new_highs_ratio=0.02).score_cap
    f = classify_regime(vix=12, spy_above_ma200=0.85, new_highs_ratio=0.15).score_cap
    assert s < f, f"story cap should be tighter: {s} vs {f}"
