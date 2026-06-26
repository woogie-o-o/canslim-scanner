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
            "toss_invest.get_previous_close",
            return_value=None,
        ), patch(
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
            "toss_invest.get_previous_close",
            return_value=329000.0,
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
        expected_change = 302000.0 / 329000.0 - 1.0
        self.assertAlmostEqual(row["DayChg"], expected_change)
        self.assertAlmostEqual(row["_DayChgPct"], expected_change * 100.0)
        self.assertEqual(row["_MarketCap"], 17568067.0 * 1e8)
        self.assertEqual(row["_QuoteSource"], "tossinvest")
        self.assertEqual(row["_QuoteTimestamp"], "2026-06-15T02:25:00Z")
        self.assertAlmostEqual(row["TargetUpside"], (426250.0 - 302000.0) / 302000.0)

    def test_overlay_uses_current_naver_previous_close_not_cached_scan_baseline(self) -> None:
        row = {
            "Ticker": "402340.KS",
            "Price": 1780000.0,
            "DayChg": 0.0,
        }
        with patch(
            "toss_invest.get_quote",
            return_value={
                "price": 1988000.0,
                "source": "tossinvest",
            },
        ), patch(
            "toss_invest.get_previous_close",
            return_value=None,
        ), patch(
            "naver_finance.get_quote",
            return_value={
                "price": 1994000.0,
                "change": 24000.0,
                "change_pct": 1.22,
            },
        ):
            app._overlay_kr_realtime_quote(row)

        self.assertEqual(row["Price"], 1988000.0)
        self.assertAlmostEqual(row["DayChg"], 1988000.0 / 1970000.0 - 1.0)
        self.assertLess(row["DayChg"], 0.02)

    def test_overlay_ignores_non_kr_ticker(self) -> None:
        row = {"Ticker": "AAPL", "Price": 100.0}
        with patch("toss_invest.get_quote") as toss_get_quote, patch("naver_finance.get_quote") as get_quote:
            app._overlay_kr_realtime_quote(row)

        toss_get_quote.assert_not_called()
        get_quote.assert_not_called()
        self.assertEqual(row["Price"], 100.0)

    def test_apply_kr_toss_stock_names_replaces_english_name(self) -> None:
        rows = [
            {"Ticker": "005930.KS", "Name": "Samsung Electronics", "Price": 340000.0},
            {"Ticker": "AAPL", "Name": "Apple", "Price": 200.0},
        ]

        with patch(
            "toss_invest.get_stocks",
            return_value={
                "005930": {
                    "name": "삼성전자",
                    "market": "KOSPI",
                    "shares_outstanding": 5919637922.0,
                }
            },
        ) as get_stocks:
            changed = app._apply_kr_toss_stock_names(rows)

        self.assertTrue(changed)
        get_stocks.assert_called_once_with(["005930.KS"])
        self.assertEqual(rows[0]["Name"], "삼성전자")
        self.assertEqual(rows[0]["_TossMarket"], "KOSPI")
        self.assertEqual(rows[0]["_MarketCap"], 5919637922.0 * 340000.0)
        self.assertEqual(rows[1]["Name"], "Apple")

    def test_attach_scan_deltas_adds_delta_fields_before_cache_store(self) -> None:
        rows = [{"Ticker": "005930.KS", "TotalScore": 80.0}]

        def annotate(results: list[dict], market: str) -> list[dict]:
            self.assertEqual(market, "KR")
            results[0]["ScoreDelta"] = 2.9
            results[0]["RankDelta"] = 1
            results[0]["DeltaDays"] = 1
            results[0]["IsNew"] = False
            return results

        with patch("history.annotate_deltas", side_effect=annotate), patch("history.save_snapshot") as save_snapshot:
            out = app._attach_scan_deltas(rows, "KR", save_snapshot=False)

        save_snapshot.assert_not_called()
        self.assertIs(out, rows)
        self.assertTrue(app._scan_rows_have_deltas(rows))
        self.assertEqual(rows[0]["ScoreDelta"], 2.9)
        self.assertEqual(rows[0]["RankDelta"], 1)

    def test_sync_detail_score_from_scan_cache_uses_list_score(self) -> None:
        detail = {
            "Ticker": "005930.KS",
            "TotalScore": 72.0,
            "ScoreDelta": -1.0,
            "Breakdown": [["[N] 신고가", 35.0, "keep", "keep"]],
        }
        scan_row = {
            "Ticker": "005930.KS",
            "TotalScore": 76.0,
            "ScoreDelta": 3.0,
            "ScoreDeltaState": "up",
            "RankDelta": 2,
        }
        key = ("KR", "BALANCED", "")
        with app._scan_results_cache_lock:
            old_cache = dict(app._scan_results_cache)
            app._scan_results_cache.clear()
            app._scan_results_cache[key] = {"_ts": 1, "data": [scan_row]}
        try:
            changed = app._sync_detail_score_from_scan_cache(detail, "KR", "BALANCED")
        finally:
            with app._scan_results_cache_lock:
                app._scan_results_cache.clear()
                app._scan_results_cache.update(old_cache)

        self.assertTrue(changed)
        self.assertEqual(detail["TotalScore"], 76.0)
        self.assertEqual(detail["ScoreDelta"], 3.0)
        self.assertEqual(detail["ScoreDeltaState"], "up")
        self.assertEqual(detail["RankDelta"], 2)
        self.assertEqual(detail["Breakdown"][0][1], 35.0)
        self.assertTrue(detail["_ScoreSyncedFromScan"])

    def test_sync_detail_rs_from_scan_cache_uses_list_bucket(self) -> None:
        detail = {"Ticker": "005930.KS", "RSRating": 52}
        scan_row = {
            "Ticker": "005930.KS",
            "RSRating": 99,
            "RSBucket": 1,
            "RSBucketName": "주도주",
        }
        key = ("KR", "BALANCED", "")
        with app._scan_results_cache_lock:
            old_cache = dict(app._scan_results_cache)
            app._scan_results_cache.clear()
            app._scan_results_cache[key] = {"_ts": 1, "data": [scan_row]}
        try:
            changed = app._sync_detail_rs_from_scan_cache(detail, "KR", "BALANCED")
        finally:
            with app._scan_results_cache_lock:
                app._scan_results_cache.clear()
                app._scan_results_cache.update(old_cache)

        self.assertTrue(changed)
        self.assertEqual(detail["RSRating"], 99)
        self.assertEqual(detail["RSBucket"], 1)
        self.assertEqual(detail["RSBucketName"], "주도주")

    def test_scan_cache_hit_skips_broker_repair_when_target_exists(self) -> None:
        key = ("KR", "BALANCED", "")
        cached_rows = [
            {
                "Ticker": "005930.KS",
                "Name": "삼성전자",
                "TotalScore": 76.0,
                "ScoreDelta": 1.0,
                "BrokerTarget": 455833.0,
                "BrokerTargetFetchedAt": int(__import__("time").time()),
                "EntryConsecutive": 0,
            }
        ]
        with app._scan_results_cache_lock:
            old_cache = dict(app._scan_results_cache)
            app._scan_results_cache.clear()
            app._scan_results_cache[key] = {"_ts": int(__import__("time").time()), "data": cached_rows}
        try:
            with app.app.test_client() as client, patch(
                "app._apply_kr_toss_stock_names",
            ) as stock_names, patch(
                "app._apply_kr_broker_target_fallback",
            ) as targets, patch(
                "app._apply_kr_toss_scan_cache_overlay",
                return_value=False,
            ) as price_overlay:
                resp = client.get("/api/scan?market=KR&strategy=BALANCED")
        finally:
            with app._scan_results_cache_lock:
                app._scan_results_cache.clear()
                app._scan_results_cache.update(old_cache)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()[0]["Ticker"], "005930.KS")
        stock_names.assert_not_called()
        price_overlay.assert_called_once()
        targets.assert_not_called()

    def test_scan_cache_hit_applies_realtime_price_overlay(self) -> None:
        key = ("KR", "BALANCED", "")
        cached_rows = [
            {
                "Ticker": "005930.KS",
                "Name": "삼성전자",
                "TotalScore": 76.0,
                "ScoreDelta": 1.0,
                "BrokerTarget": 455833.0,
                "BrokerTargetFetchedAt": int(__import__("time").time()),
                "Price": 340500.0,
                "DayChg": -0.01,
                "EntryConsecutive": 0,
            }
        ]

        def overlay(rows):
            rows[0]["Price"] = 359500.0
            rows[0]["DayChg"] = 0.0589
            return True

        with app._scan_results_cache_lock:
            old_cache = dict(app._scan_results_cache)
            app._scan_results_cache.clear()
            app._scan_results_cache[key] = {"_ts": int(__import__("time").time()), "data": cached_rows}
        try:
            with app.app.test_client() as client, patch(
                "app._apply_kr_toss_scan_cache_overlay",
                side_effect=overlay,
            ) as price_overlay, patch("app._apply_kr_broker_target_fallback") as targets:
                resp = client.get("/api/scan?market=KR&strategy=BALANCED")
        finally:
            with app._scan_results_cache_lock:
                app._scan_results_cache.clear()
                app._scan_results_cache.update(old_cache)

        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()[0]
        self.assertEqual(row["Price"], 359500.0)
        self.assertAlmostEqual(row["DayChg"], 0.0589)
        price_overlay.assert_called_once()
        targets.assert_not_called()

    def test_scan_cache_hit_repairs_missing_broker_target_and_latest_fields(self) -> None:
        key = ("KR", "BALANCED", "")
        cached_rows = [
            {
                "Ticker": "005930.KS",
                "Name": "삼성전자",
                "TotalScore": 76.0,
                "ScoreDelta": 1.0,
                "BrokerTarget": 0.0,
            }
        ]
        with app._scan_results_cache_lock:
            old_cache = dict(app._scan_results_cache)
            app._scan_results_cache.clear()
            app._scan_results_cache[key] = {"_ts": int(__import__("time").time()), "data": cached_rows}
        try:
            with app.app.test_client() as client, patch(
                "app._apply_kr_toss_scan_cache_overlay",
                return_value=False,
            ), patch(
                "app._apply_kr_broker_target_fallback",
                side_effect=lambda rows, limit=None, refresh_existing=False: rows[0].update({"BrokerTarget": 455833.0}) or True,
            ) as targets:
                resp = client.get("/api/scan?market=KR&strategy=BALANCED")
        finally:
            with app._scan_results_cache_lock:
                app._scan_results_cache.clear()
                app._scan_results_cache.update(old_cache)

        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()[0]
        self.assertEqual(row["BrokerTarget"], 455833.0)
        self.assertEqual(row["EntryConsecutive"], 0)
        targets.assert_called_once()

    def test_apply_curated_detail_sector_uses_scan_taxonomy(self) -> None:
        row = {"Ticker": "005930.KS", "Sector": "기술"}

        class _Adapter:
            def apply_curated_sector(self, item: dict, ticker: str = "") -> dict:
                self_applied = ticker == "005930.KS"
                if self_applied:
                    item["_EngineSector"] = item["Sector"]
                    item["Sector"] = "메모리·HBM"
                return item

        with patch("app._make_adapter", return_value=_Adapter()):
            app._apply_curated_detail_sector(row, "KR")

        self.assertEqual(row["Sector"], "메모리·HBM")
        self.assertEqual(row["_EngineSector"], "기술")

    def test_apply_kr_broker_target_fallback_uses_consensus_mean(self) -> None:
        rows = [{"Ticker": "005930.KS", "Name": "삼성전자", "Price": 350500.0, "BrokerTarget": 0.0}]

        with patch(
            "app._fetch_kr_consensus_target",
            return_value={
                "target": 455833.0,
                "source": "네이버증권 컨센서스 평균 (2026-06-17)",
                "count": 12,
            },
        ) as fetch_target:
            changed = app._apply_kr_broker_target_fallback(rows, limit=None)

        self.assertTrue(changed)
        fetch_target.assert_called_once_with("005930.KS", force=False)
        self.assertEqual(rows[0]["BrokerTarget"], 455833.0)
        self.assertEqual(rows[0]["BrokerTargetSource"], "네이버증권 컨센서스 평균 (2026-06-17)")
        self.assertEqual(rows[0]["BrokerAnalystCount"], 12)

    def test_apply_kr_broker_target_fallback_keeps_fresh_existing_target(self) -> None:
        rows = [
            {
                "Ticker": "005930.KS",
                "BrokerTarget": 420000.0,
                "BrokerTargetFetchedAt": int(__import__("time").time()),
            }
        ]

        with patch("app._fetch_kr_consensus_target") as fetch_target:
            changed = app._apply_kr_broker_target_fallback(rows, limit=None, refresh_existing=True)

        self.assertFalse(changed)
        fetch_target.assert_not_called()
        self.assertEqual(rows[0]["BrokerTarget"], 420000.0)

    def test_apply_kr_broker_target_fallback_refreshes_stale_existing_target(self) -> None:
        rows = [{"Ticker": "005930.KS", "BrokerTarget": 420000.0}]

        with patch(
            "app._fetch_kr_consensus_target",
            return_value={
                "target": 467708.0,
                "source": "네이버증권 컨센서스 평균 (2026-06-25)",
                "count": 12,
            },
        ) as fetch_target:
            changed = app._apply_kr_broker_target_fallback(rows, limit=None, refresh_existing=True)

        self.assertTrue(changed)
        fetch_target.assert_called_once_with("005930.KS", force=True)
        self.assertEqual(rows[0]["BrokerTarget"], 467708.0)
        self.assertEqual(rows[0]["BrokerTargetSource"], "네이버증권 컨센서스 평균 (2026-06-25)")
        self.assertGreater(rows[0]["BrokerTargetFetchedAt"], 0)

    def test_fetch_kr_consensus_target_uses_html_reports_when_integration_fails(self) -> None:
        with app._broker_target_cache_lock:
            app._broker_target_cache.clear()

        with patch("app.urllib.request.urlopen", side_effect=OSError("integration down")), patch(
            "app._fetch_kr_consensus_reports_html",
            return_value=[{"target": 450000}, {"target": 470000}],
        ) as html_reports:
            data = app._fetch_kr_consensus_target("005930.KS", force=True)

        html_reports.assert_called_once_with("005930")
        self.assertEqual(data["target"], 460000.0)
        self.assertEqual(data["source"], "네이버증권 리서치 목표가 평균")
        self.assertEqual(data["count"], 2)

    def test_broker_target_scan_limit_keeps_scan_refresh_light_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BROKER_TARGET_SCAN_LIMIT", None)
            self.assertEqual(app._broker_target_scan_limit(), 30)

        with patch.dict(os.environ, {"BROKER_TARGET_SCAN_LIMIT": "0"}):
            self.assertIsNone(app._broker_target_scan_limit())

    def test_apply_moat_bonus_is_idempotent(self) -> None:
        rows = [{"Ticker": "005930.KS", "TotalScore": 70.0, "MoatBonus": 7.5}]

        app._apply_moat_bonus(rows)
        app._apply_moat_bonus(rows)

        self.assertEqual(rows[0]["TotalScore"], 77.5)
        self.assertTrue(rows[0]["_MoatBonusApplied"])

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
        ) as get_prices, patch(
            "toss_invest.get_cached_previous_closes",
            return_value={"005930": 329000.0, "000660": 196000.0},
        ), patch("app._schedule_kr_toss_basis_warm") as schedule_warm, patch(
            "toss_invest.get_quote"
        ) as get_quote, patch(
            "naver_finance.get_quote",
            side_effect=naver_quote,
        ), patch("app._fetch_kr_consensus_target", return_value={}):
            app._override_kr_day_chg(rows)

        get_prices.assert_called_once_with(["005930.KS", "000660.KS"])
        schedule_warm.assert_not_called()
        get_quote.assert_not_called()
        self.assertEqual(rows[0]["Price"], 302000.0)
        self.assertAlmostEqual(rows[0]["DayChg"], 302000.0 / 329000.0 - 1.0)
        self.assertEqual(rows[1]["Price"], 202000.0)
        self.assertAlmostEqual(rows[1]["DayChg"], 202000.0 / 196000.0 - 1.0)

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
        self.assertNotIn("Toss 현재가/일봉", row["Breakdown"][0][3])
        self.assertIn("🔔[BREAKOUT]", row["Signal"])
        self.assertNotIn("[PIVOT]", row["Signal"])
        self.assertEqual(row["EntryPlan"]["current"], 112.0)
        self.assertEqual(row["EntryPlan"]["drawdown_pct"], -0.0)
        self.assertAlmostEqual(row["TotalScore"], 55.0)


if __name__ == "__main__":
    unittest.main()
