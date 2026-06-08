from __future__ import annotations

import os
import sys
import unittest


os.environ.setdefault("DISABLE_KR_WARMUP", "1")
os.environ.setdefault("DISABLE_US_WARMUP", "1")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
_WEB_APP = os.path.join(_PROJECT_ROOT, "web_app")
if _WEB_APP not in sys.path:
    sys.path.insert(0, _WEB_APP)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import app  # noqa: E402


class _StubAdapter:
    def __init__(self, market: str = "KR", strategy: str = "BALANCED") -> None:
        self.market = market
        self.strategy = strategy

    def get_sectors(self) -> dict[str, list[str]]:
        return {"반도체": ["005930.KS"]}

    def get_sector_groups(self) -> dict[str, dict[str, list[str]]]:
        return {"기술": {"반도체": ["005930.KS"]}}


class TestKrOnlyMode(unittest.TestCase):
    def setUp(self) -> None:
        app._adapter_pool.clear()
        app._get_scan_adapter_cls = lambda: _StubAdapter
        self.client = app.app.test_client()

    def test_us_market_routes_are_disabled(self) -> None:
        paths = [
            "/api/scan?market=US&strategy=BALANCED",
            "/api/sectors?market=US",
            "/api/ticker/AAPL?market=US&strategy=BALANCED",
            "/api/us-insight/AAPL?market=US",
            "/api/consensus/AAPL?market=US",
            "/api/score-history/AAPL?market=US",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 410)

    def test_kr_sector_route_still_works(self) -> None:
        resp = self.client.get("/api/sectors?market=KR")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["sectors"], {"반도체": ["005930.KS"]})


if __name__ == "__main__":
    unittest.main()
