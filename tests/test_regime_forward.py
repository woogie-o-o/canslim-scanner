# -*- coding: utf-8 -*-
"""regime_forward + history 스냅샷 레짐 필드 — 단위 테스트 (네트워크 비의존)."""
import json
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

_WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_app")
if _WEB not in sys.path:
    sys.path.insert(0, _WEB)

import history
import regime_forward as rf


def test_save_snapshot_includes_regime_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "_SNAP_DIR", str(tmp_path))
    rows = [
        {"Ticker": "A.KS", "TotalScore": 80, "RegimeEntryScore": 85.5,
         "RegimeState": "low_vol_uptrend", "OFIScore": 0.62, "Accumulation": True},
        {"Ticker": "B.KS", "TotalScore": 60},  # 레짐 필드 없음
    ]
    history.save_snapshot(rows, "KR")
    p = os.path.join(str(tmp_path), f"scanner_KR_{date.today().isoformat()}.json")
    d = json.load(open(p, encoding="utf-8"))
    assert d["A.KS"]["regime_entry"] == 85.5
    assert d["A.KS"]["regime_state"] == "low_vol_uptrend"
    assert d["A.KS"]["accum"] is True
    # 후방호환: RegimeEntryScore 없으면 regime_entry 키 부재, score는 유지
    assert "regime_entry" not in d["B.KS"]
    assert d["B.KS"]["score"] == 60.0


def _write_snap(snap_dir, market, d: date, rows: dict):
    p = os.path.join(snap_dir, f"scanner_{market}_{d.isoformat()}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rows, f)


def test_load_dual_snapshots_parses_both(tmp_path):
    _write_snap(str(tmp_path), "KR", date(2026, 1, 5),
                {"A": {"score": 70, "regime_entry": 75}, "B": {"score": 50}})
    snaps = rf.load_dual_snapshots("KR", snap_dir=str(tmp_path))
    assert len(snaps) == 1
    _, rows = snaps[0]
    assert rows["A"]["score"] == 70.0 and rows["A"]["regime"] == 75.0
    assert rows["B"]["regime"] is None


def test_evaluate_compare_detects_regime_superiority(tmp_path):
    # 합성 가격 패널 + 스냅샷: regime 점수 = 실제 1일 forward 수익(완전 예측),
    # total 점수 = 노이즈 → regime IC > total IC, 판정 '레짐 우월'.
    tickers = [f"T{i}" for i in range(8)]
    dates = pd.bdate_range("2026-01-05", periods=12)
    rng = np.random.default_rng(0)
    closes = pd.DataFrame(index=dates, columns=tickers, dtype=float)
    for t in tickers:
        closes[t] = 100 * np.exp(rng.normal(0, 0.02, len(dates)).cumsum())

    snap_dates = list(dates[:4])  # 4 일자 (>= _MIN_DATES)
    for d in snap_dates:
        pos = dates.get_loc(d)
        fwd = (closes.iloc[pos + 1] / closes.iloc[pos] - 1.0)  # 1일 forward
        rows = {}
        for t in tickers:
            rows[t] = {"score": float(rng.normal()),          # 노이즈
                       "regime_entry": float(fwd[t])}          # 완전 예측
        _write_snap(str(tmp_path), "KR", d.date(), rows)

    rep = rf.evaluate_compare("KR", horizons=(1,), min_names=5,
                              snap_dir=str(tmp_path), closes=closes)
    h = rep["horizons"][1]
    assert h["status"] == "OK"
    assert h["regime"]["mean_ic"] > h["total"]["mean_ic"]
    assert h["delta_ic"] > 0
    assert h["verdict"] == "레짐 우월"


def test_evaluate_compare_accumulating_when_no_regime(tmp_path):
    # regime 필드 없는 스냅샷만 → regime 상태 ACCUMULATING
    tickers = [f"T{i}" for i in range(8)]
    dates = pd.bdate_range("2026-01-05", periods=12)
    rng = np.random.default_rng(1)
    closes = pd.DataFrame(index=dates, columns=tickers, dtype=float)
    for t in tickers:
        closes[t] = 100 * np.exp(rng.normal(0, 0.02, len(dates)).cumsum())
    for d in dates[:4]:
        rows = {t: {"score": float(rng.normal())} for t in tickers}
        _write_snap(str(tmp_path), "KR", d.date(), rows)
    rep = rf.evaluate_compare("KR", horizons=(1,), min_names=5,
                              snap_dir=str(tmp_path), closes=closes)
    h = rep["horizons"][1]
    assert h["status"] == "OK"
    assert h["regime"]["status"] == "ACCUMULATING"
    assert rep["regime_snapshots"] == 0
