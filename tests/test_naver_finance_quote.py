from __future__ import annotations

import unittest
from unittest.mock import patch

import naver_finance


class TestNaverFinanceQuote(unittest.TestCase):
    def test_parse_market_cap_oku_with_jo_and_eok(self) -> None:
        self.assertEqual(
            naver_finance._parse_market_cap_oku("1,742조 1,910억"),
            17421910.0,
        )

    def test_parse_market_cap_oku_with_eok_only(self) -> None:
        self.assertEqual(naver_finance._parse_market_cap_oku("9,123억"), 9123.0)

    def test_mobile_quote_overrides_price_change_volume_and_market_cap(self) -> None:
        out = {
            "price": 329000.0,
            "change": -22500.0,
            "change_pct": -6.40,
            "volume": None,
            "market_cap_oku": 17420000.0,
        }

        def fake_fetch_json(url: str):
            if "/price" in url:
                return [
                    {
                        "closePrice": "298,000",
                        "compareToPreviousClosePrice": "-31,000",
                        "fluctuationsRatio": "-9.42",
                        "accumulatedTradingVolume": 32254114,
                    }
                ]
            if "/integration" in url:
                return {
                    "totalInfos": [
                        {"code": "marketValue", "key": "시총", "value": "1,742조 1,910억"},
                    ]
                }
            raise AssertionError(url)

        with patch("naver_finance._fetch_json", side_effect=fake_fetch_json):
            naver_finance._apply_mobile_quote_overrides(out, "005930")

        self.assertEqual(out["price"], 298000.0)
        self.assertEqual(out["change"], -31000.0)
        self.assertEqual(out["change_pct"], -9.42)
        self.assertEqual(out["volume"], 32254114.0)
        self.assertEqual(out["market_cap_oku"], 17421910.0)


if __name__ == "__main__":
    unittest.main()
