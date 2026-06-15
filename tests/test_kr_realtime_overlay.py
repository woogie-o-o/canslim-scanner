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

    def test_detail_overlay_syncs_new_high_breakdown_from_toss_candles(self) -> None:
        row = {
            "Ticker": "005930.KS",
            "Price": 100.0,
            "TotalScore": 50.0,
            "SuperMult": 1.0,
            "Signal": "⭐ WATCH LIST — Accumulate [PIVOT]",
            "NearHighPass": False,
            "SConfirmed": True,
            "EntryPlan": {"current": 100.0, "drawdown_pct": -20.0},
            "Breakdown": [
                ["[N] 신고가·피벗 돌파 (New Highs)", 0.0, "old summary", "old detail"],
            ],
        }
        candles = [
            {
                "timestamp": f"2026-05-{idx + 1:02d}T09:00:00+09:00",
                "open": 99.0,
                "high": 101.0,
                "low": 98.0,
                "close": 100.0,
                "volume": 1000.0,
            }
            for idx in range(25)
        ]

        with app.app.test_request_context("/?strategy=BALANCED"), patch(
            "toss_invest.get_daily_candles",
            return_value=candles,
        ), patch(
            "naver_finance.get_quote",
            return_value={"price": 101.0, "change_pct": 1.23, "market_cap_oku": 1.0},
        ):
            app._overlay_kr_realtime_quote(
                row,
                toss_quote={"price": 112.0, "timestamp": "2026-06-15T12:39:55+09:00", "source": "tossinvest"},
                fetch_toss=False,
                sync_new_high=True,
            )

        self.assertEqual(row["Price"], 112.0)
        self.assertTrue(row["NearHighPass"])
        self.assertTrue(row["_RealtimePivotBreakout"])
        self.assertEqual(row["_RealtimeNRaw"], 35.0)
        self.assertEqual(row["Breakdown"][0][1], 35.0)
        self.assertIn("Toss 현재가/일봉", row["Breakdown"][0][3])
        self.assertIn("🔔[BREAKOUT]", row["Signal"])
        self.assertNotIn("[PIVOT]", row["Signal"])
        self.assertEqual(row["EntryPlan"]["current"], 112.0)
        self.assertEqual(row["EntryPlan"]["drawdown_pct"], -0.0)
        self.assertAlmostEqual(row["TotalScore"], 55.0)


if __name__ == "__main__":
    unittest.main()
