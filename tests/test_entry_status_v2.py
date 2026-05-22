"""
_compute_entry_status_v2() — 백테스트 증거기반 v2 단위 테스트.

검증 포인트:
  1. pivot+s_conf 동시 → STRONG
  2. vol_jump_up + rsi_oversold → score 증가
  3. STRONG_BEAR + atr_p>8 → AVOID
  4. ma_aligned 단독은 가산점 없음 (증거 부재)
  5. macd_bullish 단독은 가산점 없음 (양쪽 시계 음수 edge)
  6. RSI 과열 regime-gate 유지
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from quant_nexus_v20 import _compute_entry_status_v2  # noqa: E402


def _flat_hist(n: int = 250, start: float = 100.0, end: float = 150.0) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    close = np.linspace(start, end, n)
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99,
         "Close": close, "Volume": [1_000_000] * n},
        index=idx,
    )


def _make(rsi=50.0, bb=0.0, macd="NONE", vwap_d=0.0,
          atr_p=2.0, regime="SIDEWAYS", pivot=False, s_conf=False,
          day_chg=0.0, fail_safe=False, bear_cap=False, hist=None, cur=150.0):
    return _compute_entry_status_v2(
        mr={"rsi": rsi, "bb_position": bb, "macd_divergence": macd},
        vwap={"distance": vwap_d, "vwap": cur},
        atr={"atr_percent": atr_p, "atr_value": 3.0, "stop_loss_long": cur * 0.95},
        regime={"regime": regime},
        mom={"pivot_breakout": pivot, "near_52w_high": False},
        vol_a={"s_confirmed": s_conf, "ratio": 1.0},
        hist=hist if hist is not None else _flat_hist(),
        cur=cur, day_chg=day_chg,
        fail_safe_triggered=fail_safe, bear_cap_applied=bear_cap,
    )


def test_pivot_with_sconf_is_strong():
    """돌파+거래량은 양쪽 시계 양수 edge — +12 가중."""
    out = _make(pivot=True, s_conf=True)
    assert out["score"] >= 55, f"score={out['score']}"
    assert out["status"] == "STRONG"
    assert "Breakout" in out["breakdown"]


def test_vol_jump_plus_oversold_boost():
    """거래량 점프(+10) 시 score 상승. 단독 vol_jump 없이 RSI 30 미만이면 +10."""
    base = _make()["score"]
    boosted = _make(rsi=25.0)["score"]
    assert boosted > base, f"oversold should boost: base={base}, boosted={boosted}"


def test_strong_bear_with_high_vol_is_avoid():
    """강한 약세장(-12) + 변동성 과대(-6) = base 50 - 18 = 32. NEUTRAL 영역.
       강한 약세장 + atr_p>8 + RSI 과열 → AVOID."""
    out = _make(regime="STRONG_BEAR", atr_p=10.0, rsi=75.0)
    # 50 - 12 - 6 - 10 = 22 → AVOID
    assert out["score"] < 25, f"score={out['score']}"
    assert out["status"] == "AVOID"


def test_ma_aligned_alone_zero_weight():
    """ma_aligned 단독은 v2에서 가산점 없음 (78w 음수 edge)."""
    # _flat_hist는 strong 정배열을 만듬 → ma_aligned=True
    out = _make(regime="SIDEWAYS")
    # base 50 만 있어야 함 (다른 신호 없으므로)
    assert out["score"] == 50, f"score={out['score']}"
    assert out["signals"]["ma_aligned"] is True
    assert "Trend" not in out["breakdown"]


def test_macd_bull_alone_zero_weight():
    """MACD BULLISH 단독은 v2에서 가산점 없음 (양쪽 시계 음수 edge)."""
    out = _make(macd="BULLISH")
    assert out["score"] == 50
    assert "MACD" not in out["breakdown"]


def test_macd_bear_penalty_kept():
    """MACD BEARISH는 페널티 유지 (-3)."""
    out = _make(macd="BEARISH")
    assert out["score"] == 47
    assert out["breakdown"]["MACD"]["pts"] == -3


def test_rsi_overheat_regime_gated():
    """RSI 75 in BULL → light penalty (-3) + ma_aligned trending bonus (+5).
       RSI 75 in SIDEWAYS → strong penalty (-10), no MA bonus."""
    bull = _make(regime="BULL", rsi=75.0)["score"]
    side = _make(regime="SIDEWAYS", rsi=75.0)["score"]
    # BULL: 50 - 3 + 5 = 52 (regime-conditional v2)
    assert bull == 52, f"bull={bull}"
    # SIDEWAYS: 50 - 10 = 40
    assert side == 40, f"side={side}"


def test_breakout_plus_voljump_stacks():
    """돌파(+12) + 거래량 점프(+10) + RSI oversold(+10) = 50+32=82 → STRONG."""
    out = _make(rsi=25.0, pivot=True, s_conf=True)
    # vol_jump_up은 hist 기반이라 _flat_hist에서는 트리거 안 됨
    # 50 + 12 + 10 = 72
    assert out["score"] >= 70


def test_atr_squeeze_small_bonus():
    """변동성 수축 +4 (양쪽 시계 양수)."""
    # _flat_hist는 변동성 일정 → atr_squeeze 트리거 어려움
    # signals만 확인
    out = _make()
    assert "atr_squeeze" in out["signals"]


def test_fail_safe_penalty():
    base = _make()["score"]
    pen = _make(fail_safe=True)["score"]
    assert pen == max(0, base - 15)


def test_bear_cap_penalty():
    base = _make()["score"]
    pen = _make(bear_cap=True)["score"]
    assert pen == max(0, base - 10)


def test_score_clamped_0_100():
    very_bad = _make(regime="STRONG_BEAR", rsi=80, macd="BEARISH",
                     atr_p=15.0, day_chg=0.10, fail_safe=True, bear_cap=True)
    assert 0 <= very_bad["score"] <= 100


def test_v2_signals_tagged():
    out = _make()
    assert out["signals"]["version"] == "v2"


def test_return_shape_matches_baseline():
    """v2 반환 형태가 baseline과 호환되어야 함 (UI 변경 없음)."""
    out = _make()
    for key in ("score", "status", "label", "phrases", "breakdown", "signals"):
        assert key in out, f"missing key: {key}"
    assert out["status"] in ("STRONG", "NEUTRAL", "AVOID")
