"""EG-006: backtest 순수 함수 단위 테스트 (yfinance fetch 없이)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from entry_threshold_backtest import (
    _bucket,
    _gap_pct,
    _max_dd,
    _sharpe,
    _win_rate,
)


def test_bucket_classification():
    assert _bucket(-0.5) == "neg"
    assert _bucket(0.0) == "lt_0_5"
    assert _bucket(0.49) == "lt_0_5"
    assert _bucket(0.5) == "lt_1_0"
    assert _bucket(0.99) == "lt_1_0"
    assert _bucket(1.0) == "ge_1_0"
    assert _bucket(2.5) == "ge_1_0"


def test_gap_pct_positive():
    # 시초가 +2% → gap_pct=+2.0
    assert _gap_pct(100, 102) == pytest.approx(2.0)


def test_gap_pct_negative():
    assert _gap_pct(100, 98) == pytest.approx(-2.0)


def test_gap_pct_zero_prev_close():
    assert _gap_pct(0, 100) == 0.0


def test_win_rate_all_winners():
    assert _win_rate([1.0, 2.0, 0.5]) == 100.0


def test_win_rate_all_losers():
    assert _win_rate([-1.0, -2.0, -0.5]) == 0.0


def test_win_rate_mixed():
    assert _win_rate([1.0, -1.0, 2.0, -2.0]) == 50.0


def test_win_rate_empty():
    assert _win_rate([]) == 0.0


def test_max_dd_monotonic_rises():
    # 계속 상승만 하면 mdd ≈ 0
    assert _max_dd([1.0, 1.0, 1.0]) == 0.0


def test_max_dd_drawdown():
    # +10%, -20%, +10% → peak 1.1, trough 0.88, dd ≈ -20%
    out = _max_dd([10.0, -20.0, 10.0])
    assert out < -15
    assert out > -25


def test_sharpe_zero_std():
    # 모든 수익이 동일 → std 0 → sharpe 0
    assert _sharpe([1.0, 1.0, 1.0]) == 0.0


def test_sharpe_positive():
    # 평균이 양수 + 분산 작음 → sharpe 양수
    assert _sharpe([2.0, 1.5, 2.5, 1.8, 2.2]) > 0


def test_sharpe_empty():
    assert _sharpe([]) == 0.0
    assert _sharpe([1.0]) == 0.0
