from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
_WEB_APP = os.path.join(_PROJECT_ROOT, "web_app")
if _WEB_APP not in sys.path:
    sys.path.insert(0, _WEB_APP)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import app  # noqa: E402


class TestKrRealtimeOverlay(unittest.TestCase):
    def test_overlay_updates_price_day_change_and_target_upside(self) -> None:
        row = {
            "Ticker": "005930.KS",
            "Price": 329000.0,
            "DayChg": -0.064,
            "TargetPrice": 426250.0,
            "TargetUpside": 0.295,
        }
        with patch(
            "naver_finance.get_quote",
            return_value={"price": 301000.0, "change_pct": -8.51},
        ):
            app._overlay_kr_realtime_quote(row)

        self.assertEqual(row["Price"], 301000.0)
        self.assertEqual(row["DayChg"], -0.0851)
        self.assertEqual(row["_DayChgPct"], -8.51)
        self.assertAlmostEqual(row["TargetUpside"], (426250.0 - 301000.0) / 301000.0)

    def test_overlay_ignores_non_kr_ticker(self) -> None:
        row = {"Ticker": "AAPL", "Price": 100.0}
        with patch("naver_finance.get_quote") as get_quote:
            app._overlay_kr_realtime_quote(row)

        get_quote.assert_not_called()
        self.assertEqual(row["Price"], 100.0)


if __name__ == "__main__":
    unittest.main()
