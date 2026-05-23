from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
_WEB_APP = os.path.join(_PROJECT_ROOT, "web_app")
for _path in (_PROJECT_ROOT, _WEB_APP):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _load_engine_adapter_for_cache_test():
    # engine_adapter imports quant_nexus_v20 at module import time. These tests
    # only exercise the cache lookup branch, so keep them independent from
    # optional market-data packages required by the full engine without leaving
    # a fake quant_nexus_v20 in sys.modules for the rest of the suite.
    module_path = os.path.join(_WEB_APP, "engine_adapter.py")
    original = sys.modules.get("quant_nexus_v20")
    had_original = "quant_nexus_v20" in sys.modules
    sys.modules["quant_nexus_v20"] = types.SimpleNamespace()
    try:
        spec = importlib.util.spec_from_file_location(
            "_engine_adapter_cache_test", module_path,
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if had_original:
            sys.modules["quant_nexus_v20"] = original
        else:
            sys.modules.pop("quant_nexus_v20", None)


engine_adapter = _load_engine_adapter_for_cache_test()


class FakeCache:
    def __init__(self, payloads: dict[str, dict] | None = None) -> None:
        self.payloads = payloads or {}
        self.calls: list[tuple[str, int]] = []

    def get(self, key: str, max_age_minutes: int = 5):
        self.calls.append((key, max_age_minutes))
        return self.payloads.get(key)


def _adapter_with_cache(cache: FakeCache):
    adapter = engine_adapter.ScanAdapter.__new__(engine_adapter.ScanAdapter)
    adapter.cache = cache
    adapter._scan_strategy = "BALANCED"
    return adapter


def _dated_key(days: int = 0) -> str:
    day = engine_adapter.datetime.now() - engine_adapter.timedelta(days=days)
    return f"AAPL__BALANCED__{day.strftime('%Y%m%d')}"


class TestEngineAdapterCache(unittest.TestCase):
    def test_prefer_cache_reads_engine_dated_key_first(self) -> None:
        key = _dated_key()
        cache = FakeCache({key: {"Ticker": "AAPL", "TotalScore": 91}})

        result = _adapter_with_cache(cache).analyze_ticker(
            "AAPL", prefer_cache=True, cache_only=True,
        )

        self.assertEqual(result, {"Ticker": "AAPL", "TotalScore": 91})
        self.assertEqual(cache.calls, [(key, 60 * 24 * 7)])

    def test_prefer_cache_keeps_legacy_key_fallback(self) -> None:
        dated_keys = [_dated_key(days) for days in range(7)]
        legacy_key = "AAPL__BALANCED"
        cache = FakeCache({legacy_key: {"Ticker": "AAPL", "TotalScore": 77}})

        result = _adapter_with_cache(cache).analyze_ticker(
            "AAPL", prefer_cache=True, cache_only=True,
        )

        self.assertEqual(result, {"Ticker": "AAPL", "TotalScore": 77})
        self.assertEqual(
            cache.calls,
            [(key, 60 * 24 * 7) for key in dated_keys]
            + [(legacy_key, 60 * 24 * 7)],
        )

    def test_prefer_cache_reads_recent_dated_key_before_legacy(self) -> None:
        key = _dated_key(1)
        cache = FakeCache({key: {"Ticker": "AAPL", "TotalScore": 82}})

        result = _adapter_with_cache(cache).analyze_ticker(
            "AAPL", prefer_cache=True, cache_only=True,
        )

        self.assertEqual(result, {"Ticker": "AAPL", "TotalScore": 82})
        self.assertEqual(cache.calls[-1], (key, 60 * 24 * 7))


if __name__ == "__main__":
    unittest.main()
