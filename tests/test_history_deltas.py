from __future__ import annotations

from datetime import date, timedelta
import unittest
from unittest.mock import patch

from web_app import history


class TestHistoryDeltas(unittest.TestCase):
    def test_missing_baseline_entry_is_not_marked_new(self) -> None:
        baseline = {
            "005930.KS": {
                "score": None,
                "rank": None,
                "missing": True,
            }
        }
        rows = [{"Ticker": "005930.KS", "TotalScore": 75.8}]

        with patch.object(
            history,
            "_find_baseline",
            return_value=(baseline, date.today() - timedelta(days=1)),
        ):
            history.annotate_deltas(rows, "KR")

        self.assertIsNone(rows[0]["ScoreDelta"])
        self.assertFalse(rows[0]["IsNew"])

    def test_ticker_absent_from_baseline_is_marked_new(self) -> None:
        baseline = {"000660.KS": {"score": 74.7, "rank": 1}}
        rows = [{"Ticker": "005930.KS", "TotalScore": 75.8}]

        with patch.object(
            history,
            "_find_baseline",
            return_value=(baseline, date.today() - timedelta(days=1)),
        ):
            history.annotate_deltas(rows, "KR")

        self.assertIsNone(rows[0]["ScoreDelta"])
        self.assertTrue(rows[0]["IsNew"])
