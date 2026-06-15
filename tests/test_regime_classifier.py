# -*- coding: utf-8 -*-
"""Unit tests for regime_classifier (Module 1).

Run: py -3.13 -m pytest tests/test_regime_classifier.py -q
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import regime_classifier as rc  # noqa: E402


# ---------------------------------------------------------------------------
# synthetic-series builders
# ---------------------------------------------------------------------------
def _segment(start_price, n, mu, sigma, seed):
    rng = np.random.default_rng(seed)
    rets = rng.normal(mu, sigma, n)
    logp = np.log(start_price) + np.cumsum(rets)
    return np.exp(logp)


def _make_ohlcv(prices, seed=0):
    idx = pd.date_range("2019-01-01", periods=len(prices), freq="B")
    rng = np.random.default_rng(seed + 99)
    noise = np.abs(rng.normal(0, 0.002, len(prices)))
    high = prices * (1 + noise)
    low = prices * (1 - noise)
    vol = rng.uniform(8e5, 1.2e6, len(prices))
    return pd.DataFrame({"Close": prices, "High": high, "Low": low,
                         "Open": prices, "Volume": vol}, index=idx)


def _three_regime_series(tail="uptrend"):
    """Uptrend (low vol, +drift) → high-vol downtrend → flat chop, tail configurable.

    Tail segments are long and use moderate (non-outlier) vol so the HMM forms
    coherent states rather than a degenerate micro-spike state.
    """
    up = _segment(100, 320, 0.0010, 0.008, 1)
    fl = _segment(up[-1], 300, 0.0000, 0.005, 3)
    if tail == "uptrend":
        dn = _segment(fl[-1], 300, -0.0022, 0.024, 2)
        tl = _segment(dn[-1], 280, 0.0014, 0.007, 4)
        parts = [up, dn, fl, tl]
    elif tail == "downtrend":
        tl = _segment(fl[-1], 300, -0.0022, 0.024, 5)
        parts = [up, fl, tl]
    else:  # chop tail
        dn = _segment(fl[-1], 280, -0.0022, 0.024, 2)
        parts = [up, dn, fl]
    prices = np.concatenate(parts)
    return _make_ohlcv(prices)


# ---------------------------------------------------------------------------
# 1. synthetic dominant-regime identification
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not rc._HAS_HMM, reason="hmmlearn required for HMM path")
def test_uptrend_tail_identified():
    df = _three_regime_series(tail="uptrend")
    res = rc.classify_regime(df)
    assert res.model_status in ("hmm", "two_state")
    assert res.state == rc.R_BULL, f"expected uptrend, got {res.state} probs={res.probs}"


@pytest.mark.skipif(not rc._HAS_HMM, reason="hmmlearn required for HMM path")
def test_downtrend_tail_identified():
    df = _three_regime_series(tail="downtrend")
    res = rc.classify_regime(df)
    assert res.model_status in ("hmm", "two_state")
    assert res.state == rc.R_BEAR, f"expected downtrend, got {res.state} probs={res.probs}"


def test_result_shape_and_invariants():
    df = _three_regime_series(tail="uptrend")
    res = rc.classify_regime(df)
    assert res.state in rc._REGIMES
    assert set(res.probs) == set(rc._REGIMES)
    assert set(res.p_next) == set(rc._REGIMES)
    assert abs(sum(res.probs.values()) - 1.0) < 1e-6
    assert abs(sum(res.p_next.values()) - 1.0) < 1e-6
    sig = res.transition_signal
    for k in ("early_long", "early_exit", "strength", "fresh"):
        assert k in sig
    assert 0.0 <= sig["strength"] <= 1.0
    assert 0.0 <= sig["fresh"] <= 1.0


# ---------------------------------------------------------------------------
# 2. label stability across different restart seeds
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not rc._HAS_HMM, reason="hmmlearn required for HMM path")
def test_label_stability_across_seeds():
    df = _three_regime_series(tail="uptrend")

    cfg_a = dict(rc.REGIME_CONFIG)
    cfg_a["random_state"] = 42
    cfg_b = dict(rc.REGIME_CONFIG)
    cfg_b["random_state"] = 7  # different restart seeds

    res_a = rc.classify_regime(df, config=cfg_a)
    res_b = rc.classify_regime(df, config=cfg_b)

    # state->regime mapping must be consistent => same tail label
    assert res_a.state == res_b.state, (
        f"label switching: {res_a.state} vs {res_b.state}")


# ---------------------------------------------------------------------------
# 3. no-lookahead: live posterior at day t must not change when future appended
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not rc._HAS_HMM, reason="hmmlearn required for HMM path")
def test_no_lookahead_forward_filter():
    """Forward-only posteriors at day t must be invariant to future bars.

    Fit one frozen model, then compare alpha[:t] computed on X[:t] vs X[:t+50].
    Forward filtering depends only on the past => rows must be identical.
    """
    df = _three_regime_series(tail="uptrend")
    feat = rc.compute_features(df)
    z = rc.standardize_features(feat)
    Z = z[rc.REGIME_CONFIG["features"]].dropna()
    X = Z.to_numpy()
    assert len(X) > 200

    seeds = [42 + i for i in range(8)]
    model = rc._fit_hmm(X, 3, rc.REGIME_CONFIG, seeds)
    assert model is not None

    t = len(X) - 60
    alpha_short = rc._forward_posteriors(model, X[:t])
    alpha_long = rc._forward_posteriors(model, X[:t + 50])

    # the posterior AT day t-1 (last row of the short slice) must match
    np.testing.assert_allclose(alpha_short[-1], alpha_long[t - 1], atol=1e-8)
    # and the full overlapping prefix must match
    np.testing.assert_allclose(alpha_short, alpha_long[:t], atol=1e-8)


# ---------------------------------------------------------------------------
# 4. fallback paths
# ---------------------------------------------------------------------------
def test_fallback_when_hmm_disabled(monkeypatch):
    monkeypatch.setattr(rc, "_HAS_HMM", False)
    df = _three_regime_series(tail="uptrend")
    res = rc.classify_regime(df)
    assert res.model_status == "rule_based"
    assert res.state in rc._REGIMES
    assert abs(sum(res.probs.values()) - 1.0) < 1e-6


def test_fallback_short_series():
    prices = _segment(100, 120, 0.001, 0.01, 1)  # < min_fit_bars
    df = _make_ohlcv(prices)
    res = rc.classify_regime(df)
    assert res.model_status == "rule_based"
    assert res.state in rc._REGIMES


def test_never_raises_on_garbage():
    # empty
    res = rc.classify_regime(pd.DataFrame())
    assert res.model_status == "rule_based"
    # all-NaN close
    df = _make_ohlcv(_segment(100, 600, 0.0, 0.01, 1))
    df["Close"] = np.nan
    res2 = rc.classify_regime(df)
    assert res2.state in rc._REGIMES


# ---------------------------------------------------------------------------
# 5. early transition signal mechanics
# ---------------------------------------------------------------------------
def test_transition_signal_strength_formula():
    idx = pd.date_range("2020-01-01", periods=5, freq="B")
    # P_bull rising and crossing 0.50 on the LAST bar, while the prior bar was
    # still bear/chop-dominant (the lead). prior-bar argmax = bear (0.44).
    prob_hist = pd.DataFrame({
        rc.R_BULL: [0.20, 0.28, 0.36, 0.42, 0.62],
        rc.R_BEAR: [0.55, 0.47, 0.44, 0.43, 0.22],
        rc.R_CHOP: [0.25, 0.25, 0.20, 0.15, 0.16],
    }, index=idx)
    pnext = {rc.R_BULL: 0.60, rc.R_BEAR: 0.2, rc.R_CHOP: 0.2}
    sig = rc._compute_transition_signal(prob_hist, pnext, rc.REGIME_CONFIG)
    assert sig["early_long"] is True
    # strength = max(0.62-0.5,0)*2 = 0.24
    assert abs(sig["strength"] - 0.24) < 1e-9
    assert sig["fresh"] > 0.0


def test_transition_signal_no_fire_when_falling():
    idx = pd.date_range("2020-01-01", periods=5, freq="B")
    prob_hist = pd.DataFrame({
        rc.R_BULL: [0.62, 0.55, 0.48, 0.40, 0.30],   # falling
        rc.R_BEAR: [0.18, 0.25, 0.32, 0.40, 0.50],
        rc.R_CHOP: [0.20, 0.20, 0.20, 0.20, 0.20],
    }, index=idx)
    pnext = {rc.R_BULL: 0.20, rc.R_BEAR: 0.55, rc.R_CHOP: 0.25}
    sig = rc._compute_transition_signal(prob_hist, pnext, rc.REGIME_CONFIG)
    assert sig["early_long"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
