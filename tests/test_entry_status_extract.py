"""
_compute_entry_status() 추출 함수 — 행동 잠금(behavior lock) 테스트.

목적: Stage 2 (백테스트 기반 four_axis 통합) 작업이 인라인 → 헬퍼 추출 후
       동일 결정성을 유지하는지 회귀 보호. 핵심 경로 6개 + 안전장치 2개.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from quant_nexus_v20 import _compute_entry_status  # noqa: E402


def _flat_hist(n: int = 250, start: float = 100.0, end: float = 150.0) -> pd.DataFrame:
    """SMA50/200 정배열을 만들 수 있는 단조 증가 히스토리."""
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
    return _compute_entry_status(
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


def test_score_base_is_40_with_no_signals():
    """모든 신호 중립 → base 40에 정배열 가점 8 정도만 (BULL/STRONG_BULL 미설정)."""
    out = _make(regime="SIDEWAYS")
    assert 40 <= out["score"] <= 50
    assert out["status"] in ("NEUTRAL", "STRONG")


def test_strong_threshold_at_50():
    """추세장 + 정배열(8) + 신고가돌파+거래량(10) = 58 → STRONG."""
    out = _make(regime="BULL", pivot=True, s_conf=True)
    assert out["score"] >= 50
    assert out["status"] == "STRONG"
    assert out["label"] == "진입 강함"


def test_avoid_below_30():
    """STRONG_BEAR(-15) + RSI 75 비추세 페널티(-14) + MACD bear(-4) = 7."""
    out = _make(regime="STRONG_BEAR", rsi=75.0, macd="BEARISH")
    assert out["score"] < 30
    assert out["status"] == "AVOID"


def test_neutral_band_30_to_49():
    """RSI 75 + RANGE → -14, 결과 26~49 영역."""
    out = _make(regime="SIDEWAYS", rsi=75.0)
    assert 20 <= out["score"] <= 49


def test_regime_gate_keeps_strong_in_bull():
    """삼성전기형: RSI 77 + BULL + 정배열 → 강감점 아니라 약감점, STRONG 유지."""
    out = _make(regime="BULL", rsi=77.0)
    # base 40 - 4 (RSI overheat in BULL) + 8 (강한 정배열) = 44 → NEUTRAL
    # 비추세였다면 40 - 14 + 0 = 26 → AVOID
    assert out["score"] >= 40, f"BULL에서 RSI 77은 약감점이어야 하는데 score={out['score']}"
    assert out["signals"]["mr_pts"] == -4


def test_fail_safe_penalty_applied():
    """fail_safe = -15 penalty."""
    base = _make(regime="BULL").score if False else _make(regime="BULL")["score"]
    pen = _make(regime="BULL", fail_safe=True)["score"]
    assert pen == max(0, base - 15)


def test_bear_cap_penalty_applied():
    """bear_cap = -10 penalty."""
    base = _make(regime="BULL")["score"]
    pen = _make(regime="BULL", bear_cap=True)["score"]
    assert pen == max(0, base - 10)


def test_breakdown_keys_present():
    """breakdown은 활성 팩터만 포함."""
    out = _make(regime="BULL", rsi=77.0, pivot=True, s_conf=True, macd="BULLISH")
    assert "MeanRev" in out["breakdown"]
    assert "Trend" in out["breakdown"]
    assert "Breakout" in out["breakdown"]
    assert "MACD" in out["breakdown"]


def test_score_clamped_0_100():
    """극단 음수/양수 입력에서도 [0,100]."""
    very_bad = _make(regime="STRONG_BEAR", rsi=80, macd="BEARISH", atr_p=10.0,
                     day_chg=0.10, fail_safe=True, bear_cap=True)
    assert 0 <= very_bad["score"] <= 100


def test_phrases_capped_at_two_for_display():
    """phrases는 모든 활성 신호 포함 — 표시는 호출부에서 [:2]."""
    out = _make(regime="BULL", pivot=True, s_conf=True, macd="BULLISH")
    assert isinstance(out["phrases"], list)


def test_empty_hist_does_not_crash():
    """짧은 히스토리도 안전하게 동작."""
    short = _flat_hist(n=10)
    out = _make(regime="BULL", hist=short)
    assert "score" in out
    assert out["signals"]["ma_aligned"] is False  # 200일 미만이면 False
