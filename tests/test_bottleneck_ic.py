"""Tests for bottleneck_ic — forward IC 스냅샷 추적.

오늘 병목 등급을 스냅샷해 두고, 시간이 지난 뒤 실제 수익률과의 상관(IC)을 계산해
"병목 등급/게이트가 미래 수익을 예측하는가"를 검증한다. 순수 로직 단위테스트.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from bottleneck_ic import (  # noqa: E402
    compute_forward_ic,
    load_snapshots,
    record_snapshot,
)


class TestSnapshotStore(unittest.TestCase):
    def test_record_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "snap.jsonl")
            results = [
                {"Ticker": "AAA", "Price": 100, "BottleneckScore": 90,
                 "BottleneckEntryPass": True, "FinValue": 70},
                {"Ticker": "BBB", "Price": 50, "BottleneckScore": 0,
                 "BottleneckEntryPass": False, "FinValue": None},  # 비병목 → 제외
            ]
            n = record_snapshot(results, date="2026-01-01", store_path=path)
            self.assertEqual(n, 1)  # BottleneckScore 0 은 스냅샷 안 함
            rows = load_snapshots(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["ticker"], "AAA")
            self.assertEqual(rows[0]["date"], "2026-01-01")
            self.assertAlmostEqual(rows[0]["price"], 100)

    def test_append_accumulates(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "snap.jsonl")
            record_snapshot([{"Ticker": "AAA", "Price": 100, "BottleneckScore": 80}],
                            date="2026-01-01", store_path=path)
            record_snapshot([{"Ticker": "AAA", "Price": 110, "BottleneckScore": 80}],
                            date="2026-01-08", store_path=path)
            self.assertEqual(len(load_snapshots(path)), 2)


class TestForwardIC(unittest.TestCase):
    def _snaps(self, date):
        return [
            {"ticker": "HI", "date": date, "price": 100.0, "bottleneck_score": 90, "entry_pass": True, "finvalue": 80},
            {"ticker": "MID", "date": date, "price": 100.0, "bottleneck_score": 60, "entry_pass": False, "finvalue": 50},
            {"ticker": "LO", "date": date, "price": 100.0, "bottleneck_score": 30, "entry_pass": False, "finvalue": 20},
        ]

    def test_positive_ic_when_score_predicts_return(self):
        snaps = self._snaps("2026-01-01")
        # 점수 높을수록 수익 높게
        prices = {"HI": 130.0, "MID": 110.0, "LO": 95.0}
        out = compute_forward_ic(snaps, lambda t: prices.get(t),
                                 asof="2026-02-15", min_days=21)
        self.assertEqual(out["n_matured"], 3)
        self.assertGreater(out["ic_bottleneck"], 0.9)  # 단조 → +1 근처

    def test_gate_pass_beats_fail(self):
        snaps = self._snaps("2026-01-01")
        prices = {"HI": 130.0, "MID": 110.0, "LO": 95.0}
        out = compute_forward_ic(snaps, lambda t: prices.get(t),
                                 asof="2026-02-15", min_days=21)
        self.assertGreater(out["gate_pass_mean_ret"], out["gate_fail_mean_ret"])

    def test_immature_snapshots_excluded(self):
        snaps = self._snaps("2026-02-10")  # asof와 5일 차이
        out = compute_forward_ic(snaps, lambda t: 200.0,
                                 asof="2026-02-15", min_days=21)
        self.assertEqual(out["n_matured"], 0)
        self.assertIsNone(out["ic_bottleneck"])

    def test_missing_price_skipped(self):
        snaps = self._snaps("2026-01-01")
        out = compute_forward_ic(snaps, lambda t: None,  # 가격 못 구함
                                 asof="2026-02-15", min_days=21)
        self.assertEqual(out["n_matured"], 0)


if __name__ == "__main__":
    unittest.main()
