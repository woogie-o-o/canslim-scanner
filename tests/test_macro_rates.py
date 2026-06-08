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

import macro  # noqa: E402


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self.body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class TestMacroRates(unittest.TestCase):
    def test_bok_base_rate_table_parser_uses_latest_table_row(self) -> None:
        body = """
        <table>
          <caption>한국은행 기준금리 추이</caption>
          <tbody>
            <tr><td class="fb">2025</td><td>05월 29일</td><td>2.50</td></tr>
            <tr><td class="fb">2025</td><td>02월 25일</td><td>2.75</td></tr>
          </tbody>
        </table>
        """
        with patch("macro.urllib.request.urlopen", return_value=_FakeResponse(body)):
            self.assertEqual(macro._fetch_bok_base_rate(), 2.5)

    def test_kr_rate_falls_back_to_naver_when_bok_page_fails(self) -> None:
        with patch("macro._fetch_bok_base_rate", return_value=None), patch(
            "macro._fetch_naver_rate", return_value=2.5
        ) as naver:
            self.assertEqual(macro._fetch_kr_rate(), 2.5)
            naver.assert_called_once_with("kr_rate", "한국은행 기준금리")


if __name__ == "__main__":
    unittest.main()
