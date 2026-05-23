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

import config_manager  # noqa: E402


class TestConfigManagerEnv(unittest.TestCase):
    def test_connection_status_uses_environment_defaults(self) -> None:
        env = {
            "DART_API_KEY": "dart-key",
            "NAVER_CLIENT_ID": "naver-id",
            "NAVER_CLIENT_SECRET": "naver-secret",
        }
        with patch.dict(os.environ, env, clear=False):
            status = config_manager.get_connection_status({})

        self.assertTrue(status["DART"]["connected"])
        self.assertTrue(status["Naver"]["connected"])

    def test_config_values_override_empty_environment(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            status = config_manager.get_connection_status({
                "DART_API_KEY": "dart-key",
                "NAVER_CLIENT_ID": "naver-id",
                "NAVER_CLIENT_SECRET": "naver-secret",
            })

        self.assertTrue(status["DART"]["connected"])
        self.assertTrue(status["Naver"]["connected"])


if __name__ == "__main__":
    unittest.main()
