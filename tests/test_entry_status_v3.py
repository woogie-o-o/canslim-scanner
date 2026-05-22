"""EntryStatus v3 — 월가 패널 P0 6항목 검증."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import quant_nexus_v20 as qn


def _flat_hist(n: int = 250, start: float = 100.0, end: float = 150.0) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    close = np.linspace(start, end, n)
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99,
         "Close": close, "Volume": [1_000_000] * n},
        index=idx,
    )


def _vol_spike_hist(n: int = 250, start: float = 100.0, end: float = 150.0) -> pd.DataFrame:
    """마지막 봉에 거래량 급등(>2×평균) + 양봉 → vol_jump_up 트리거."""
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    close = np.linspace(start, end, n)
    open_ = close.copy()
    open_[-1] = close[-1] * 0.99  # 마지막 봉 양봉(close > open)
    volume = [1_000_000] * n
    volume[-1] = 3_000_000         # 평균(1M) × 3 → 2× 기준 초과
    return pd.DataFrame(
        {"Open": open_, "High": close * 1.01, "Low": close * 0.99,
         "Close": close, "Volume": volume},
        index=idx,
    )


def _make(rsi=50.0, bb=0.0, macd="NONE", vwap_d=0.0,
          atr_p=2.0, regime="SIDEWAYS", pivot=False, s_conf=False,
          day_chg=0.0, fail_safe=False, bear_cap=False, hist=None, cur=150.0):
    return qn._compute_entry_status_v2(
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


# ── ET3-004: mutex pivot/vol_jump ──────────────────────────────
def test_mutex_pivot_breakout_blocks_vol_jump():
    """pivot+s_conf 발동 시 vol_jump_up breakdown 제외 (double-count 차단)."""
    hist = _vol_spike_hist()
    out = _make(pivot=True, s_conf=True, hist=hist)
    # vol_jump_up이 실제로 트리거됐는지 확인 (hist가 조건을 충족해야 테스트가 유효)
    assert out["signals"].get("vol_jump_up"), "사전조건: vol_jump_up이 트리거되어야 mutex 테스트 유효"
    # pivot+s_conf mutex → Volume 항목이 breakdown에서 제거되어야 함
    assert "Breakout" in out["breakdown"]
    assert "Volume" not in out["breakdown"], "mutex 미작동: pivot+s_conf 발동 시 Volume double-count 차단 실패"


# ── ET3-002: ATR-normalized day_chg ───────────────────────────
def test_daychg_atr_normalized_triggers_at_low_pct_in_low_vol():
    """atr_p=2, day_chg=0.05 → 2.5σ → 페널티 발동 (기존 0.07 threshold 미달이지만)."""
    base = _make(atr_p=2.0, day_chg=0.0)["score"]
    pen = _make(atr_p=2.0, day_chg=0.05)["score"]
    assert pen == base - 8, f"base={base}, pen={pen}"
    assert "DayChg" in _make(atr_p=2.0, day_chg=0.05)["breakdown"]


def test_daychg_atr_normalized_skips_in_high_vol():
    """atr_p=10, day_chg=0.08 → 0.8σ → 페널티 없음 (기존 0.07 threshold 초과지만)."""
    base = _make(atr_p=10.0, day_chg=0.0)["score"]
    pen = _make(atr_p=10.0, day_chg=0.08)["score"]
    # atr_p>8 변동성 페널티는 별도로 -6 적용됨. day_chg는 0.8σ로 trigger 안 됨.
    assert pen == base, f"base={base}, pen={pen} (only atr penalty, no daychg)"
    assert "DayChg" not in _make(atr_p=10.0, day_chg=0.08)["breakdown"]


def test_daychg_fallback_when_atr_zero():
    """atr_p<=0 → fallback to day_chg>0.07."""
    out_low = _make(atr_p=0.0, day_chg=0.05)
    out_high = _make(atr_p=0.0, day_chg=0.08)
    assert "DayChg" not in out_low["breakdown"]
    assert "DayChg" in out_high["breakdown"]


# ── ET3-006: degraded flag ────────────────────────────────────
def test_degraded_flag_on_bad_hist():
    """hist=None → degraded=True, 파생 신호 가산점 0."""
    out = qn._compute_entry_status_v2(
        mr={"rsi": 50.0, "bb_position": 0.0, "macd_divergence": "NONE"},
        vwap={"distance": 0.0, "vwap": 100.0},
        atr={"atr_percent": 2.0, "atr_value": 3.0, "stop_loss_long": 95.0},
        regime={"regime": "SIDEWAYS"},
        mom={"pivot_breakout": False, "near_52w_high": False},
        vol_a={"s_confirmed": False, "ratio": 1.0},
        hist=None, cur=100.0, day_chg=0.0,
    )
    assert out["signals"]["degraded"] is True
    assert out["signals"]["ma_aligned"] is False
    assert out["signals"]["vol_jump_up"] is False
    assert out["signals"]["atr_squeeze"] is False


def test_degraded_flag_false_on_good_hist():
    out = _make()
    assert out["signals"]["degraded"] is False


# ── ET3-001: Hysteresis ────────────────────────────────────────
def test_hysteresis_single_day_not_strong():
    """단발 score=55 → STRONG 아님 (consecutive<2)."""
    qn._ENTRY_STATUS_CACHE.clear()
    status, label, cons = qn._apply_status_hysteresis(
        "TST", 55, prev_score=None, prev_status=None, consecutive=0,
    )
    assert status == "NEUTRAL"
    assert cons == 1


def test_hysteresis_two_consecutive_strong():
    """2일 연속 55+ → STRONG."""
    qn._ENTRY_STATUS_CACHE.clear()
    # day 1
    s1, l1, c1 = qn._apply_status_hysteresis("TST", 60, None, None, 0)
    assert s1 == "NEUTRAL"
    # day 2 — prev_score>=50 AND in_strong → 2일째 STRONG 진입
    s2, l2, c2 = qn._apply_status_hysteresis("TST", 60, prev_score=60, prev_status="NEUTRAL", consecutive=c1)
    assert s2 == "STRONG", f"got {s2}"


def test_hysteresis_holds_strong_above_exit():
    """STRONG 상태, score=52 (strong_out=50 이상) → STRONG 유지."""
    s, l, c = qn._apply_status_hysteresis(
        "TST", 52, prev_score=58, prev_status="STRONG", consecutive=3,
    )
    assert s == "STRONG"


def test_hysteresis_exits_strong_below_out():
    """STRONG 상태, score=49 → NEUTRAL."""
    s, l, c = qn._apply_status_hysteresis(
        "TST", 49, prev_score=58, prev_status="STRONG", consecutive=3,
    )
    assert s == "NEUTRAL"


def test_hysteresis_avoid_entry_requires_persistence():
    """단발 score=20 → NEUTRAL, 2일 연속 → AVOID."""
    s1, l1, c1 = qn._apply_status_hysteresis("TST", 20, None, None, 0)
    assert s1 == "NEUTRAL"
    s2, l2, c2 = qn._apply_status_hysteresis("TST", 20, prev_score=20, prev_status="NEUTRAL", consecutive=c1)
    assert s2 == "AVOID"


def test_hysteresis_disabled_via_env():
    """ENTRY_HYSTERESIS=0이면 dispatcher skip."""
    os.environ["ENTRY_HYSTERESIS"] = "0"
    try:
        out = qn._compute_entry_status_dispatch(
            ticker="TST",
            mr={"rsi": 50.0, "bb_position": 0.0, "macd_divergence": "NONE"},
            vwap={"distance": 0.0, "vwap": 100.0},
            atr={"atr_percent": 2.0, "atr_value": 3.0, "stop_loss_long": 95.0},
            regime={"regime": "SIDEWAYS"},
            mom={"pivot_breakout": True, "near_52w_high": False},
            vol_a={"s_confirmed": True, "ratio": 1.0},
            hist=_flat_hist(), cur=150.0, day_chg=0.0,
        )
        # mutex로 vol_jump 차단되어도 pivot+s_conf로 +12 → 62 → STRONG (hysteresis 없으면 즉시 STRONG)
        assert out["status"] == "STRONG"
    finally:
        os.environ.pop("ENTRY_HYSTERESIS", None)


# ── ET3-003: percentile rank ──────────────────────────────────
def test_percentile_rank_basic():
    """동일 score는 같은 rank, 분포에 따라 0~1."""
    scores = {"A": 50, "B": 50, "C": 60, "D": 70, "E": 80}
    ranks = qn._percentile_rank(scores)
    assert 0.0 <= ranks["A"] <= 1.0
    assert ranks["A"] == ranks["B"]
    assert ranks["E"] > ranks["A"]
    assert ranks["D"] > ranks["C"] > ranks["B"]


def test_percentile_rank_empty():
    assert qn._percentile_rank({}) == {}


def test_percentile_rank_single():
    ranks = qn._percentile_rank({"X": 50})
    assert ranks["X"] in (0.5, 1.0)  # convention
