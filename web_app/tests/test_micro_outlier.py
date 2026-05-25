"""MF-004: microstructure-flag 테스트."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from micro_outlier import annotate, is_micro_outlier  # type: ignore


def test_normal_row_not_outlier():
    row = {"GapPct": 1.5, "Volume": 1e6, "AvgVol20": 9e5, "SpreadPct": 0.05, "ATR_Pct": 2.0}
    flag, reason = is_micro_outlier(row)
    assert flag is False
    assert reason == ""


def test_gap_outlier_detected():
    row = {"GapPct": 15.0, "Volume": 1e6, "AvgVol20": 9e5}
    flag, reason = is_micro_outlier(row)
    assert flag is True
    assert "gap" in reason


def test_low_volume_outlier_detected():
    row = {"GapPct": 1.0, "Volume": 1e5, "AvgVol20": 1e6}
    flag, reason = is_micro_outlier(row)
    assert flag is True
    assert "vol" in reason


def test_wide_spread_outlier_detected():
    row = {"SpreadPct": 1.5, "ATR_Pct": 2.0}
    flag, reason = is_micro_outlier(row)
    assert flag is True
    assert "spread" in reason


def test_annotate_applies_penalty():
    rows = [
        {"Ticker": "A", "GapPct": 15.0, "Volume": 1e6, "AvgVol20": 9e5, "TotalScore": 80.0},
        {"Ticker": "B", "GapPct": 1.0, "Volume": 1e6, "AvgVol20": 9e5, "TotalScore": 80.0},
    ]
    annotate(rows)
    assert rows[0]["MicroOutlier"] is True
    assert rows[0]["TotalScore"] == 75.0
    assert rows[0]["_RawTotalScoreMicro"] == 80.0
    assert rows[1]["MicroOutlier"] is False
    assert rows[1]["TotalScore"] == 80.0
