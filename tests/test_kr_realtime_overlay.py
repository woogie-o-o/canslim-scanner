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
        with patch("toss_invest.get_quote", return_value=None), patch(
            "naver_finance.get_quote",
            return_value={
                "price": 301000.0,
                "change_pct": -8.51,
                "market_cap_oku": 17568067.0,
            },
        ):
            app._overlay_kr_realtime_quote(row)

        self.assertEqual(row["Price"], 301000.0)
        self.assertEqual(row["DayChg"], -0.0851)
        self.assertEqual(row["_DayChgPct"], -8.51)
        self.assertEqual(row["_MarketCap"], 17568067.0 * 1e8)
        self.assertAlmostEqual(row["TargetUpside"], (426250.0 - 301000.0) / 301000.0)

    def test_overlay_prefers_toss_price_and_keeps_naver_derived_fields(self) -> None:
        row = {
            "Ticker": "005930.KS",
            "Price": 329000.0,
            "DayChg": -0.064,
            "TargetPrice": 426250.0,
        }
        with patch(
            "toss_invest.get_quote",
            return_value={
                "price": 302000.0,
                "timestamp": "2026-06-15T02:25:00Z",
                "source": "tossinvest",
            },
        ), patch(
            "naver_finance.get_quote",
            return_value={
                "price": 301000.0,
                "change_pct": -8.51,
                "market_cap_oku": 17568067.0,
            },
        ):
            app._overlay_kr_realtime_quote(row)

        self.assertEqual(row["Price"], 302000.0)
        self.assertEqual(row["DayChg"], -0.0851)
        self.assertEqual(row["_DayChgPct"], -8.51)
        self.assertEqual(row["_MarketCap"], 17568067.0 * 1e8)
        self.assertEqual(row["_QuoteSource"], "tossinvest")
        self.assertEqual(row["_QuoteTimestamp"], "2026-06-15T02:25:00Z")
        self.assertAlmostEqual(row["TargetUpside"], (426250.0 - 302000.0) / 302000.0)

    def test_overlay_ignores_non_kr_ticker(self) -> None:
        row = {"Ticker": "AAPL", "Price": 100.0}
        with patch("toss_invest.get_quote") as toss_get_quote, patch("naver_finance.get_quote") as get_quote:
            app._overlay_kr_realtime_quote(row)

        toss_get_quote.assert_not_called()
        get_quote.assert_not_called()
        self.assertEqual(row["Price"], 100.0)

    def test_override_batches_toss_prices_for_scan_results(self) -> None:
        rows = [
            {"Ticker": "005930.KS", "Price": 1.0},
            {"Ticker": "000660.KS", "Price": 1.0},
        ]

        def naver_quote(ticker: str) -> dict:
            return {
                "005930.KS": {"price": 301000.0, "change_pct": -8.51, "market_cap_oku": 17568067.0},
                "000660.KS": {"price": 201000.0, "change_pct": 2.34, "market_cap_oku": 1463280.0},
            }[ticker]

        with patch(
            "toss_invest.get_prices",
            return_value={
                "005930": {"price": 302000.0, "source": "tossinvest"},
                "000660": {"price": 202000.0, "source": "tossinvest"},
            },
        ) as get_prices, patch("toss_invest.get_quote") as get_quote, patch(
            "naver_finance.get_quote",
            side_effect=naver_quote,
        ):
            app._override_kr_day_chg(rows)

        get_prices.assert_called_once_with(["005930.KS", "000660.KS"])
        get_quote.assert_not_called()
        self.assertEqual(rows[0]["Price"], 302000.0)
        self.assertEqual(rows[0]["DayChg"], -0.0851)
        self.assertEqual(rows[1]["Price"], 202000.0)
        self.assertAlmostEqual(rows[1]["DayChg"], 0.0234)


if __name__ == "__main__":
    unittest.main()
