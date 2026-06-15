# -*- coding: utf-8 -*-
"""모듈4 — regime_integration 통합/가중치 단위 테스트 (네트워크 비의존)."""
import os
import importlib

import regime_integration as ri


def _reload():
    return importlib.reload(ri)


def test_no_signal_invariant():
    """무신호 시 RegimeEntryScore == TotalScore (승수 전부 1.0)."""
    rows = [{"TotalScore": 70.0, "RegimeSignal": {"early_long": False, "early_exit": False,
                                                  "strength": 0.0, "fresh": 0.0},
             "OFIScore": 0.5, "Accumulation": False, "LeadLag": {"fired": False, "transfer": 0.5}}]
    ri.apply_regime_weighting(rows)
    assert abs(rows[0]["RegimeEntryScore"] - 70.0) < 1e-6
    assert rows[0]["RegimeMultipliers"]["regime"] == 1.0


def test_early_long_boosts_score():
    """early_long + fresh=1 → 점수 상승, regime 승수 = 1+W_REGIME*strength*1.0."""
    rows = [{"TotalScore": 60.0,
             "RegimeSignal": {"early_long": True, "early_exit": False, "strength": 1.0, "fresh": 1.0},
             "OFIScore": 0.5, "Accumulation": False, "LeadLag": {"fired": False, "transfer": 0.5}}]
    ri.apply_regime_weighting(rows)
    assert rows[0]["RegimeEntryScore"] > 60.0
    # 1 + 0.30*1.0*(0.5+0.5*1.0) = 1.30
    assert abs(rows[0]["RegimeMultipliers"]["regime"] - 1.30) < 1e-6


def test_fresh_decay_weights_less():
    """전환 성숙(fresh=0)이면 신선(fresh=1)보다 가중이 작다."""
    base_sig = {"early_long": True, "early_exit": False, "strength": 1.0}
    fresh_row = [{"TotalScore": 60.0, "RegimeSignal": {**base_sig, "fresh": 1.0},
                  "OFIScore": 0.5, "Accumulation": False, "LeadLag": {"fired": False, "transfer": 0.5}}]
    stale_row = [{"TotalScore": 60.0, "RegimeSignal": {**base_sig, "fresh": 0.0},
                  "OFIScore": 0.5, "Accumulation": False, "LeadLag": {"fired": False, "transfer": 0.5}}]
    ri.apply_regime_weighting(fresh_row)
    ri.apply_regime_weighting(stale_row)
    assert fresh_row[0]["RegimeEntryScore"] > stale_row[0]["RegimeEntryScore"]


def test_early_exit_penalizes():
    rows = [{"TotalScore": 60.0,
             "RegimeSignal": {"early_long": False, "early_exit": True, "strength": 1.0, "fresh": 1.0},
             "OFIScore": 0.5, "Accumulation": False, "LeadLag": {"fired": False, "transfer": 0.5}}]
    ri.apply_regime_weighting(rows)
    assert rows[0]["RegimeEntryScore"] < 60.0


def test_accumulation_amplifies_ofi():
    strong = [{"TotalScore": 60.0, "RegimeSignal": {}, "OFIScore": 1.0,
               "Accumulation": True, "LeadLag": {"fired": False, "transfer": 0.5}}]
    plain = [{"TotalScore": 60.0, "RegimeSignal": {}, "OFIScore": 1.0,
              "Accumulation": False, "LeadLag": {"fired": False, "transfer": 0.5}}]
    ri.apply_regime_weighting(strong)
    ri.apply_regime_weighting(plain)
    assert strong[0]["RegimeEntryScore"] > plain[0]["RegimeEntryScore"]


def test_leadlag_nudge():
    up = [{"TotalScore": 60.0, "RegimeSignal": {}, "OFIScore": 0.5, "Accumulation": False,
           "LeadLag": {"fired": True, "transfer": 1.0, "direction": 1}}]
    ri.apply_regime_weighting(up)
    assert up[0]["RegimeEntryScore"] > 60.0


def test_disabled_passthrough(monkeypatch):
    """REGIME_DISABLE=1 → 부착 no-op, RegimeEntryScore == TotalScore."""
    monkeypatch.setenv("REGIME_DISABLE", "1")
    rows = [{"TotalScore": 55.0, "Sector": "반도체"}]
    ri.attach_all(rows, "KR")
    assert rows[0]["RegimeEntryScore"] == 55.0


def test_rank_key_toggle(monkeypatch):
    row = {"TotalScore": 50.0, "RegimeEntryScore": 80.0}
    monkeypatch.delenv("REGIME_RANK", raising=False)
    assert ri.rank_key(row) == 50.0
    monkeypatch.setenv("REGIME_RANK", "1")
    assert ri.rank_key(row) == 80.0


def test_clip_bounds():
    rows = [{"TotalScore": 99.0,
             "RegimeSignal": {"early_long": True, "early_exit": False, "strength": 1.0, "fresh": 1.0},
             "OFIScore": 1.0, "Accumulation": True, "LeadLag": {"fired": True, "transfer": 1.0, "direction": 1}}]
    ri.apply_regime_weighting(rows)
    assert 0.0 <= rows[0]["RegimeEntryScore"] <= 100.0


def test_attach_order_flow_neutral_on_empty_row():
    rows = [{"TotalScore": 40.0}]
    ri.attach_order_flow(rows)
    assert "OFIScore" in rows[0]
    assert 0.0 <= rows[0]["OFIScore"] <= 1.0
