# -*- coding: utf-8 -*-
"""order_flow (모듈2 — 일봉 OFI/스마트머니 프록시) 단위 테스트."""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from order_flow import compute_ofi, ofi_from_row  # noqa: E402


KEYS = {"ofi", "smart_money", "accumulation", "vwap_pressure", "reasons"}


def _df(highs, lows, closes, vols):
    n = len(closes)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"Open": closes, "High": highs, "Low": lows, "Close": closes, "Volume": vols},
        index=idx,
    )


def _assert_shape(res):
    assert isinstance(res, dict)
    assert set(res.keys()) == KEYS
    assert -1.0 <= res["ofi"] <= 1.0
    assert 0.0 <= res["smart_money"] <= 1.0
    assert -1.0 <= res["vwap_pressure"] <= 1.0
    assert isinstance(res["accumulation"], bool)
    assert isinstance(res["reasons"], list)


# ───────── 매집 양성 케이스 ───────────────────────────────────────
def test_accumulation_positive():
    """타이트한 횡보 밴드 + 상승일에 거래량 집중(OBV 상승) → accumulation True."""
    rng = np.random.default_rng(7)
    n = 40
    base = 100.0
    highs, lows, closes, vols = [], [], [], []
    prev = base
    for i in range(n):
        # ±1% 이내 횡보. 후반부로 갈수록 변동폭 수축(밴드 contraction)
        amp = 0.012 * (1.0 - 0.6 * i / n)
        c = base * (1 + rng.uniform(-amp, amp))
        up = c >= prev
        # 상승일이면 고가권 마감(clv>0.5: 종가가 고가 근처) + 큰 거래량,
        # 하락일이면 저가권 마감 + 소량 거래.
        if up:
            h = c * 1.0005   # 고가는 종가 바로 위
            l = c * 0.995    # 저가는 멀리 아래 → clv ≈ +0.8
            v = rng.uniform(1.6e6, 2.2e6)
        else:
            h = c * 1.005    # 저가권 마감 → clv ≈ -0.8
            l = c * 0.9995
            v = rng.uniform(0.4e6, 0.7e6)
        highs.append(h); lows.append(l); closes.append(c); vols.append(v); prev = c

    res = compute_ofi(_df(highs, lows, closes, vols), window=20)
    _assert_shape(res)
    assert res["accumulation"] is True
    assert res["ofi"] > 0.15
    assert any("매집" in r for r in res["reasons"])


# ───────── 매집 음성 케이스 ───────────────────────────────────────
def test_accumulation_negative_trending():
    """강한 추세(넓은 레인지) → range_bound 깨짐 → accumulation False."""
    n = 40
    closes = list(np.linspace(100, 160, n))  # +60% 추세
    highs = [c * 1.02 for c in closes]
    lows = [c * 0.98 for c in closes]
    vols = [1.0e6] * n
    res = compute_ofi(_df(highs, lows, closes, vols), window=20)
    _assert_shape(res)
    assert res["accumulation"] is False


def test_accumulation_negative_distribution():
    """횡보지만 분산(하락일 거래량 집중) → stealth 깨짐 → accumulation False."""
    rng = np.random.default_rng(3)
    n = 40
    base = 100.0
    highs, lows, closes, vols = [], [], [], []
    prev = base
    for _ in range(n):
        c = base * (1 + rng.uniform(-0.008, 0.008))
        down = c < prev
        if down:
            h = c * 1.002; l = c * 0.996; v = rng.uniform(1.6e6, 2.2e6)  # 하락에 거래량
        else:
            h = c * 1.004; l = c * 0.998; v = rng.uniform(0.4e6, 0.7e6)
        highs.append(h); lows.append(l); closes.append(c); vols.append(v); prev = c
    res = compute_ofi(_df(highs, lows, closes, vols), window=20)
    _assert_shape(res)
    assert res["accumulation"] is False


# ───────── 경계값(랜덤) ──────────────────────────────────────────
def test_bounds_random():
    rng = np.random.default_rng(123)
    for _ in range(50):
        n = int(rng.integers(2, 60))
        closes = list(rng.uniform(10, 500, n))
        spread = rng.uniform(0.0, 0.05, n)
        highs = [c * (1 + s) for c, s in zip(closes, spread)]
        lows = [c * (1 - s) for c, s in zip(closes, spread)]
        vols = list(rng.uniform(0, 5e6, n))
        res = compute_ofi(_df(highs, lows, closes, vols), window=int(rng.integers(5, 30)))
        _assert_shape(res)


# ───────── 강건성 ────────────────────────────────────────────────
def test_robust_h_equals_l():
    n = 25
    closes = [100.0] * n
    res = compute_ofi(_df(closes, closes, closes, [1e6] * n), window=20)  # H==L 전부
    _assert_shape(res)
    assert res["ofi"] == 0.0  # CLV 전부 0


def test_robust_nan_rows():
    n = 30
    closes = list(np.linspace(100, 110, n))
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    vols = [1e6] * n
    df = _df(highs, lows, closes, vols)
    df.iloc[5] = np.nan
    df.iloc[12, df.columns.get_loc("Volume")] = np.nan
    res = compute_ofi(df, window=20)
    _assert_shape(res)


def test_robust_fewer_than_window():
    res = compute_ofi(_df([101, 102, 103], [99, 100, 101], [100, 101, 102], [1e6, 1.2e6, 1.1e6]), window=20)
    _assert_shape(res)


def test_robust_empty_and_bad():
    assert set(compute_ofi(pd.DataFrame()).keys()) == KEYS
    assert set(compute_ofi(None).keys()) == KEYS  # type: ignore[arg-type]
    # 필수 컬럼 누락
    bad = pd.DataFrame({"foo": [1, 2, 3]})
    r = compute_ofi(bad)
    assert r["ofi"] == 0.0 and r["smart_money"] == 0.5 and r["accumulation"] is False


def test_single_row():
    res = compute_ofi(_df([101], [99], [100], [1e6]), window=20)
    _assert_shape(res)


# ───────── ofi_from_row ──────────────────────────────────────────
def test_row_missing_fields_neutral():
    res = ofi_from_row({})
    assert res == {"ofi": 0.0, "smart_money": 0.5, "accumulation": False,
                   "vwap_pressure": 0.0, "reasons": []}
    # 비-dict 입력도 중립
    assert ofi_from_row(None)["smart_money"] == 0.5  # type: ignore[arg-type]
    assert ofi_from_row(42)["ofi"] == 0.0  # type: ignore[arg-type]


def test_row_with_fields_bounds():
    rng = np.random.default_rng(9)
    for _ in range(50):
        c = rng.uniform(10, 500)
        s = rng.uniform(0, 0.05)
        row = {
            "High": c * (1 + s),
            "Low": c * (1 - s),
            "Close": c,
            "_VolRatio": rng.uniform(0.3, 4.0),
            "RSI": rng.uniform(0, 100),
        }
        res = ofi_from_row(row)
        _assert_shape(res)
        assert res["accumulation"] is False  # 경량 프록시는 매집 판정 안 함


def test_row_strong_close_positive_ofi():
    # 고가권 마감 + 높은 거래량배수 → 양의 ofi
    row = {"High": 101.0, "Low": 99.0, "Close": 100.9, "_VolRatio": 2.5, "RSI": 65}
    res = ofi_from_row(row)
    _assert_shape(res)
    assert res["ofi"] > 0


def test_row_rsi_only():
    res = ofi_from_row({"RSI": 70})
    _assert_shape(res)
    assert res["ofi"] > 0  # RSI 50 초과 → 양


def test_row_garbage_values_no_throw():
    res = ofi_from_row({"High": "abc", "Low": None, "Close": float("nan"), "_VolRatio": "x"})
    _assert_shape(res)
    assert res["smart_money"] == 0.5
