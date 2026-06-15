"""finnhub_api 순수 파서 단위테스트 — 네트워크 호출 없음."""
from __future__ import annotations

import os
import sys
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from finnhub_api import (  # noqa: E402
    _parse_profile2, _parse_insider_sentiment,
    _parse_ipo_calendar, _parse_general_news,
)


class TestParseProfile2(unittest.TestCase):
    def test_full_profile(self) -> None:
        raw = {
            "name": "Apple Inc", "ipo": "1980-12-12",
            "shareOutstanding": 15000.0, "finnhubIndustry": "Technology",
            "exchange": "NASDAQ NMS - GLOBAL MARKET",
            "weburl": "https://www.apple.com/", "logo": "https://x/aapl.png",
        }
        out = _parse_profile2(raw)
        self.assertEqual(out["logo"], "https://x/aapl.png")
        self.assertEqual(out["ipo"], "1980-12-12")
        self.assertEqual(out["share_outstanding"], 15000.0)
        self.assertEqual(out["industry"], "Technology")
        self.assertEqual(out["exchange"], "NASDAQ NMS - GLOBAL MARKET")

    def test_empty_input(self) -> None:
        self.assertEqual(_parse_profile2(None), {})
        self.assertEqual(_parse_profile2({}), {})

    def test_partial_fields_only_present_keys(self) -> None:
        out = _parse_profile2({"name": "X", "logo": "https://x/x.png"})
        self.assertEqual(out["logo"], "https://x/x.png")
        self.assertNotIn("ipo", out)


class TestParseInsiderSentiment(unittest.TestCase):
    def test_latest_and_trend(self) -> None:
        raw = {"data": [
            {"year": 2026, "month": 1, "mspr": -10.0, "change": -100},
            {"year": 2026, "month": 2, "mspr": 5.0, "change": 50},
            {"year": 2026, "month": 3, "mspr": 20.0, "change": 200},
        ]}
        out = _parse_insider_sentiment(raw)
        self.assertEqual(out["mspr"], 20.0)
        self.assertEqual(out["mspr_trend"], [-10.0, 5.0, 20.0])
        self.assertEqual(out["mspr_change"], 15.0)

    def test_empty(self) -> None:
        self.assertEqual(_parse_insider_sentiment(None), {})
        self.assertEqual(_parse_insider_sentiment({"data": []}), {})

    def test_single_month_no_change(self) -> None:
        out = _parse_insider_sentiment({"data": [{"year": 2026, "month": 3, "mspr": 7.0}]})
        self.assertEqual(out["mspr"], 7.0)
        self.assertEqual(out["mspr_trend"], [7.0])
        self.assertEqual(out["mspr_change"], 0.0)


class TestParseIpoCalendar(unittest.TestCase):
    def test_basic(self) -> None:
        raw = {"ipoCalendar": [
            {"date": "2026-06-10", "symbol": "ABC", "name": "Abc Inc",
             "price": "15.00-17.00", "numberOfShares": 1000000, "exchange": "NASDAQ"},
        ]}
        out = _parse_ipo_calendar(raw)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["symbol"], "ABC")
        self.assertEqual(out[0]["date"], "2026-06-10")
        self.assertEqual(out[0]["price"], "15.00-17.00")

    def test_empty(self) -> None:
        self.assertEqual(_parse_ipo_calendar(None), [])
        self.assertEqual(_parse_ipo_calendar({"ipoCalendar": []}), [])


class TestParseGeneralNews(unittest.TestCase):
    def test_basic_limit_and_fields(self) -> None:
        raw = [
            {"headline": f"H{i}", "url": f"https://x/{i}", "source": "CNBC",
             "datetime": 1700000000 + i, "category": "top news"}
            for i in range(30)
        ]
        out = _parse_general_news(raw, limit=10)
        self.assertEqual(len(out), 10)
        self.assertEqual(out[0]["headline"], "H0")
        self.assertEqual(out[0]["source"], "CNBC")

    def test_empty(self) -> None:
        self.assertEqual(_parse_general_news(None), [])
        self.assertEqual(_parse_general_news([]), [])

    def test_skips_entries_without_headline(self) -> None:
        out = _parse_general_news([{"url": "https://x/1"}, {"headline": "ok", "url": "https://x/2"}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["headline"], "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
