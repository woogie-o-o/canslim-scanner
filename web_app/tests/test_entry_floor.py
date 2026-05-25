"""EG-002: strong_entry_floor — ATR 정규화 floor 검증."""

from __future__ import annotations

import pytest

from entry_pricing import strong_entry_floor


def test_high_atr_uses_pct_floor():
    """ATR이 cur 의 20% → -3% pct floor 가 더 보수적, entry ≥ 97."""
    out = strong_entry_floor(cur=100.0, vwap=85.0, atr_abs=20.0)
    assert out >= 97.0
    assert out <= 100.0


def test_low_atr_uses_atr_floor_or_pct():
    """ATR이 cur 의 0.5% → entry 가 -3% 보다 훨씬 얕고, ATR-floor 우세."""
    out = strong_entry_floor(cur=100.0, vwap=99.0, atr_abs=0.5)
    # 두 floor 중 더 큰 값을 채택 → 99.75 (= cur - 0.5*ATR)
    assert out >= 97.0
    assert out >= 99.5  # 저변동성에서 너무 깊게 내려가지 않음
    assert out <= 100.0


def test_medium_atr_normalized_floor():
    """ATR이 cur 의 3% → -3% 와 -1.5% (=0.5*ATR) 둘 다 비슷; 후자가 더 보수적."""
    out = strong_entry_floor(cur=200.0, vwap=195.0, atr_abs=6.0)
    # base = min(200, 195, 200-1.8=198.2) = 195
    # floor = max(195, 194, 197) = 197
    assert out == pytest.approx(197.0, abs=0.01)


def test_vwap_zero_falls_back_to_cur():
    """VWAP=0 (데이터 누락) 시 cur 로 fallback."""
    out = strong_entry_floor(cur=100.0, vwap=0.0, atr_abs=4.0)
    # base = min(100, 100, 98.8) = 98.8
    # floor = max(98.8, 97, 98.0) = 98.8
    assert out == pytest.approx(98.8, abs=0.01)


def test_floor_never_exceeds_cur():
    """floor 가 절대 cur 를 초과하지 않아야 (그러면 entry > cur 추격)."""
    out = strong_entry_floor(cur=50.0, vwap=60.0, atr_abs=1.0)
    assert out <= 50.0
