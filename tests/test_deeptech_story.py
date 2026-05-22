"""Tests for deeptech story correction gate."""
import pytest
from quant_nexus_v20 import _is_deeptech_story


def _row(sector="드론·우주", rev_growth=0.10, market_cap=200_000_000_000):
    return {
        "Sector": sector,
        "_RevenueGrowth": rev_growth,
        "_MarketCap": market_cap,
    }


def test_deeptech_story_pass_with_growing_revenue_and_large_cap():
    assert _is_deeptech_story("099320.KQ", _row()) is True


def test_deeptech_story_fail_when_sector_not_in_whitelist():
    assert _is_deeptech_story("005930.KS", _row(sector="반도체")) is False


def test_deeptech_story_fail_when_revenue_not_growing():
    assert _is_deeptech_story("099320.KQ", _row(rev_growth=-0.05)) is False


def test_deeptech_story_fail_when_revenue_flat():
    assert _is_deeptech_story("099320.KQ", _row(rev_growth=0.0)) is False


def test_deeptech_story_fail_when_market_cap_too_small():
    assert _is_deeptech_story("X.KQ", _row(market_cap=50_000_000_000)) is False


def test_deeptech_story_fail_on_missing_sector():
    row = _row()
    row["Sector"] = ""
    assert _is_deeptech_story("X", row) is False


def test_deeptech_story_fail_on_missing_revenue_growth():
    row = _row()
    row.pop("_RevenueGrowth", None)
    assert _is_deeptech_story("X", row) is False


def test_deeptech_story_fail_on_missing_market_cap():
    row = _row()
    row.pop("_MarketCap", None)
    assert _is_deeptech_story("X", row) is False


import os as _os
import sys as _sys

_THIS_DIR = _os.path.dirname(_os.path.abspath(__file__))
_PROJECT_ROOT = _os.path.dirname(_THIS_DIR)
_WEB_APP = _os.path.join(_PROJECT_ROOT, "web_app")
for _p in (_PROJECT_ROOT, _WEB_APP):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

from one_liner import _raw_bucket  # noqa: E402


def _row_for_bucket(score=20, sector="드론·우주", rev_growth=0.10,
                     market_cap=200_000_000_000, signal="", grade=""):
    return {
        "Ticker": "099320.KQ",
        "TotalScore": score,
        "Sector": sector,
        "_RevenueGrowth": rev_growth,
        "_MarketCap": market_cap,
        "Signal": signal,
        "Grade": grade,
        "_PER": 0, "_ROE": -10, "_EPSGrowth": -20,
        "RSI": 50, "Mom12M": 5, "_Mom3M": 0, "Drawdown": -5,
        "_OperatingMargin": -5,
    }


def test_deeptech_story_routes_to_story_stock_when_low_score():
    row = _row_for_bucket(score=20)
    assert _raw_bucket(row) == "STORY_STOCK"


def test_non_deeptech_low_score_still_avoid():
    row = _row_for_bucket(score=20, sector="반도체")
    assert _raw_bucket(row) == "AVOID"


def test_deeptech_with_red_grade_still_avoid():
    row = _row_for_bucket(score=20, grade="RED")
    assert _raw_bucket(row) == "AVOID"


def test_deeptech_with_explicit_avoid_signal_still_avoid():
    row = _row_for_bucket(score=20, signal="AVOID")
    assert _raw_bucket(row) == "AVOID"


def test_satrec_initiative_scenario():
    """쎄트렉아이(099320) 시나리오: 적자지만 위성 수주 성장 → STORY_STOCK."""
    row = {
        "Ticker": "099320.KQ",
        "Sector": "위성·발사체",
        "TotalScore": 25,
        "_RevenueGrowth": 0.30,
        "_MarketCap": 1_500_000_000_000,
        "Signal": "",
        "Grade": "YELLOW",
        "_PER": 0, "_ROE": -8, "_EPSGrowth": -15,
        "RSI": 55, "Mom12M": 40, "_Mom3M": 5, "Drawdown": -10,
        "_OperatingMargin": -3,
    }
    assert _raw_bucket(row) == "STORY_STOCK"


def test_paper_space_themed_smallcap_stays_avoid():
    """이름만 우주테마인 좌비주(매출 정체, 소형주) → 여전히 AVOID."""
    row = {
        "Ticker": "X.KQ",
        "Sector": "드론·우주",
        "TotalScore": 20,
        "_RevenueGrowth": -0.05,
        "_MarketCap": 30_000_000_000,
        "Signal": "",
        "Grade": "YELLOW",
        "_PER": 0, "_ROE": -20, "_EPSGrowth": -50,
        "RSI": 30, "Mom12M": -40, "_Mom3M": -15, "Drawdown": -50,
        "_OperatingMargin": -25,
    }
    assert _raw_bucket(row) == "AVOID"


def test_profitable_deeptech_unaffected():
    """흑자 딥테크 종목은 게이트와 무관하게 정상 분류."""
    row = {
        "Ticker": "012450.KS",
        "Sector": "드론·우주",
        "TotalScore": 75,
        "_RevenueGrowth": 0.25,
        "_MarketCap": 10_000_000_000_000,
        "Signal": "",
        "Grade": "GREEN",
        "_PER": 18, "_ROE": 14, "_EPSGrowth": 30,
        "RSI": 65, "Mom12M": 50, "_Mom3M": 8, "Drawdown": -5,
        "_OperatingMargin": 8,
    }
    result = _raw_bucket(row)
    assert result not in ("AVOID", "STORY_STOCK")
