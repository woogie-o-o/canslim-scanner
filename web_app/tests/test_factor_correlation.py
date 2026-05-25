"""MF-002: factor-orthogonalization 테스트."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from factor_correlation import _pearson, _spearman, correlate, extract_factors, report  # type: ignore


def test_pearson_perfect_positive():
    assert _pearson([1, 2, 3, 4], [2, 4, 6, 8]) > 0.99


def test_pearson_perfect_negative():
    assert _pearson([1, 2, 3, 4], [8, 6, 4, 2]) < -0.99


def test_pearson_zero_variance():
    assert _pearson([1, 1, 1], [1, 2, 3]) == 0.0


def test_spearman_monotonic():
    # 비선형 단조 — pearson 은 떨어지지만 spearman 은 1
    s = _spearman([1, 2, 3, 4], [1, 4, 9, 16])
    assert s > 0.99


def test_extract_factors_skips_non_numeric_score():
    rows = [
        {"TotalScore": 80, "MoatCategory": "NETWORK", "MoatData": {"story_risk": False}},
        {"TotalScore": "bad"},  # skipped
        {"TotalScore": 50, "MoatCategory": "NONE", "MoatData": {"story_risk": True}},
    ]
    f = extract_factors(rows)
    assert len(f["Score"]) == 2


def test_correlate_emits_3_pairs():
    rows = [
        {"TotalScore": 80, "MoatCategory": "NETWORK", "MoatData": {"story_risk": False}},
        {"TotalScore": 70, "MoatCategory": "INTANGIBLE", "MoatData": {"story_risk": False}},
        {"TotalScore": 50, "MoatCategory": "NONE", "MoatData": {"story_risk": True}},
        {"TotalScore": 40, "MoatCategory": "NONE", "MoatData": {"story_risk": True}},
    ]
    factors = extract_factors(rows)
    pairs = correlate(factors)
    assert len(pairs) == 3  # Score~Moat, Score~StoryRisk, Moat~StoryRisk
    names = {p["pair"] for p in pairs}
    assert "Score~Moat" in names


def test_report_warns_on_high_correlation():
    # Score 와 StoryRisk 가 완벽 상관 — 경고 발생
    rows = [
        {"TotalScore": 80, "MoatCategory": "NETWORK", "MoatData": {"story_risk": False}},
        {"TotalScore": 80, "MoatCategory": "NETWORK", "MoatData": {"story_risk": False}},
        {"TotalScore": 30, "MoatCategory": "NONE", "MoatData": {"story_risk": True}},
        {"TotalScore": 30, "MoatCategory": "NONE", "MoatData": {"story_risk": True}},
    ]
    _, warns = report(rows)
    assert any("Score~StoryRisk" in w or "StoryRisk" in w for w in warns)
