from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

import requests


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import toss_invest  # noqa: E402


class _Response:
    def __init__(self, status_code: int, payload: dict, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class TestTossInvest(unittest.TestCase):
    def setUp(self) -> None:
        toss_invest._TOKEN = None
        toss_invest._TOKEN_EXP = 0.0

    def test_missing_credentials_disables_client(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("toss_invest._request") as request:
            self.assertFalse(toss_invest.is_available())
            self.assertIsNone(toss_invest.get_quote("005930.KS"))

        request.assert_not_called()

    def test_get_quote_fetches_token_and_maps_price(self) -> None:
        env = {
            "TOSSINVEST_CLIENT_ID": "client-id",
            "TOSSINVEST_CLIENT_SECRET": "client-secret",
        }
        responses = [
            _Response(200, {"access_token": "token-1", "token_type": "Bearer", "expires_in": 3600}),
            _Response(
                200,
                {
                    "result": [
                        {
                            "symbol": "005930",
                            "timestamp": "2026-06-15T02:25:00Z",
                            "lastPrice": "302000",
                            "currency": "KRW",
                        }
                    ]
                },
            ),
        ]

        with patch.dict(os.environ, env, clear=True), patch("toss_invest._request", side_effect=responses) as request:
            quote = toss_invest.get_quote("005930.KS")

        self.assertEqual(quote["ticker"], "005930")
        self.assertEqual(quote["code"], "005930")
        self.assertEqual(quote["price"], 302000.0)
        self.assertEqual(quote["currency"], "KRW")
        self.assertEqual(quote["timestamp"], "2026-06-15T02:25:00Z")
        self.assertEqual(quote["source"], "tossinvest")

        token_call = request.call_args_list[0]
        self.assertEqual(token_call.args[:2], ("POST", toss_invest.TOKEN_PATH))
        self.assertEqual(token_call.kwargs["data"]["grant_type"], "client_credentials")
        self.assertEqual(token_call.kwargs["data"]["client_id"], "client-id")
        self.assertEqual(token_call.kwargs["data"]["client_secret"], "client-secret")

        price_call = request.call_args_list[1]
        self.assertEqual(price_call.args[:2], ("GET", toss_invest.PRICES_PATH))
        self.assertEqual(price_call.kwargs["headers"]["Authorization"], "Bearer token-1")
        self.assertEqual(price_call.kwargs["params"], {"symbols": "005930"})


if __name__ == "__main__":
    unittest.main()
