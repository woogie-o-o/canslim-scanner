from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

import toss_invest


KST = timezone(timedelta(hours=9))


class TestTossPreviousClose(unittest.TestCase):
    def setUp(self) -> None:
        toss_invest._PREVIOUS_CLOSE_CACHE.clear()

    def test_market_day_excludes_today_candle(self) -> None:
        candles = [
            {"timestamp": "2026-06-22T09:00:00+09:00", "close": 353500.0},
            {"timestamp": "2026-06-23T09:00:00+09:00", "close": 337000.0},
        ]
        with patch("toss_invest.get_daily_candles", return_value=candles):
            close = toss_invest.get_previous_close(
                "005930.KS",
                now=datetime(2026, 6, 23, 16, 0, tzinfo=KST),
            )
        self.assertEqual(close, 353500.0)

    def test_before_market_uses_latest_completed_candle(self) -> None:
        candles = [
            {"timestamp": "2026-06-19T09:00:00+09:00", "close": 345000.0},
            {"timestamp": "2026-06-22T09:00:00+09:00", "close": 353500.0},
        ]
        with patch("toss_invest.get_daily_candles", return_value=candles):
            close = toss_invest.get_previous_close(
                "005930.KS",
                now=datetime(2026, 6, 23, 8, 10, tzinfo=KST),
            )
        self.assertEqual(close, 353500.0)

    def test_weekend_uses_friday_close(self) -> None:
        candles = [
            {"timestamp": "2026-06-18T09:00:00+09:00", "close": 350000.0},
            {"timestamp": "2026-06-19T09:00:00+09:00", "close": 345000.0},
        ]
        with patch("toss_invest.get_daily_candles", return_value=candles):
            close = toss_invest.get_previous_close(
                "005930.KS",
                now=datetime(2026, 6, 20, 12, 0, tzinfo=KST),
            )
        self.assertEqual(close, 345000.0)
