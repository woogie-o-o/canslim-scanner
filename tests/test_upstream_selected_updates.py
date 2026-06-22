import os
import tempfile
import unittest

import numpy as np
import pandas as pd

from stock_judge import SCENARIOS, build_judgment
from watchlist import WatchlistDB
from web_app.macro_calendar import get_macro_events


class SelectedUpstreamUpdatesTest(unittest.TestCase):
    def test_watchlist_round_trip(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db = WatchlistDB(path)
            self.assertTrue(db.add("005930.KS"))
            self.assertFalse(db.add("005930.KS"))
            self.assertEqual(db.list(), ["005930.KS"])
            self.assertTrue(db.remove("005930.KS"))
            db.close()
        finally:
            os.unlink(path)

    def test_macro_calendar_contains_future_events(self):
        events = get_macro_events()
        self.assertTrue(events)
        self.assertTrue(all(event.get("date") and event.get("kind") for event in events))

    def test_entry_judge_uses_existing_ohlcv(self):
        count = 90
        close = np.linspace(100, 140, count)
        frame = pd.DataFrame({
            "Open": close - 1,
            "High": close + 2,
            "Low": close - 2,
            "Close": close,
            "Volume": np.linspace(1_000_000, 1_300_000, count),
        })
        result = build_judgment(frame, "BULL")
        self.assertIn(result["scenario"], SCENARIOS)
        self.assertTrue(result["label"])
        self.assertTrue(result["timing"])

    def test_bear_regime_downgrades_opportunity(self):
        from stock_judge import apply_regime

        scenario, downgraded = apply_regime("BREAKOUT", "high_vol_downtrend")
        self.assertEqual(scenario, "OVERBOUGHT")
        self.assertTrue(downgraded)


if __name__ == "__main__":
    unittest.main()
