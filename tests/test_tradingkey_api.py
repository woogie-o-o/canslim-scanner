import pytest
from unittest.mock import patch, MagicMock
import tradingkey_api

MOCK_TK_RESPONSE = {
    "score": {
        "overall": 72, "valuation": 65, "growth": 78,
        "profitability": 80, "momentum": 70, "risk": 60,
        "industry_rank": 284, "industry_total": 488,
        "overall_rank": 169, "overall_total": 4571,
        "sector_percentile": 41.8,
    },
    "institutional": {
        "confidence_score": 0.72, "holding_pct": 62.3,
        "holding_qoq": -7.1, "top_holder": "Vanguard",
        "top_holder_pct": 8.2, "top_holder_chg": -0.3,
    },
    "analyst": {
        "consensus": "Buy", "target_price": 315.0,
        "upside_pct": 7.5, "analyst_count": 42,
        "buy_count": 28, "hold_count": 12, "sell_count": 2,
    },
    "valuation": {
        "pe_ttm": 29.5, "pe_dynamic": 27.1, "pe_static": 31.2,
        "pb": 8.4, "eps_ttm": 6.58, "market_cap": 2800000000000.0,
    },
    "fundamentals": {
        "roe": 0.147, "roa": 0.223, "gross_margin": 0.456,
        "net_profit": 0.253, "dividend_yield": 0.005, "payout_ratio": 0.15,
    },
    "risk_technical": {
        "beta": 1.21, "risk_rate": 3.2, "reward_risk": 2.1,
        "support": 278.0, "resistance": 351.0,
        "volume_ratio": 1.3, "amplitude": 2.8, "turnover_ratio": 0.7,
    },
    "performance": {
        "1d": 0.8, "5d": 2.1, "1m": 5.3,
        "6m": 12.4, "ytd": 18.7, "1y": 24.1,
    },
}


def test_is_kr_ticker_six_digit():
    assert tradingkey_api.is_kr_ticker("005930") is True

def test_is_kr_ticker_ks_suffix():
    assert tradingkey_api.is_kr_ticker("005930.KS") is True

def test_is_kr_ticker_kq_suffix():
    assert tradingkey_api.is_kr_ticker("035720.KQ") is True

def test_is_kr_ticker_us_stock():
    assert tradingkey_api.is_kr_ticker("AAPL") is False

def test_is_kr_ticker_us_with_numbers():
    assert tradingkey_api.is_kr_ticker("BRK.B") is False

def test_get_tradingkey_data_kr_returns_none():
    result = tradingkey_api.get_tradingkey_data("005930.KS")
    assert result is None

def test_get_score_kr_returns_none():
    result = tradingkey_api.get_score("005930")
    assert result is None

def test_get_support_resistance_kr_returns_none():
    result = tradingkey_api.get_support_resistance("005930.KS")
    assert result is None


@patch("tradingkey_api._fetch_raw")
def test_get_tradingkey_data_us_stock(mock_fetch):
    tradingkey_api._cache.clear()
    mock_fetch.return_value = MOCK_TK_RESPONSE
    result = tradingkey_api.get_tradingkey_data("AAPL")
    assert result is not None
    assert result["score"]["overall"] == 72
    assert result["_source"] == "tradingkey"
    assert "_cached_at" in result


@patch("tradingkey_api._fetch_raw")
def test_get_tradingkey_data_cache_hit(mock_fetch):
    tradingkey_api._cache.clear()
    mock_fetch.return_value = MOCK_TK_RESPONSE
    tradingkey_api.get_tradingkey_data("AAPL")
    tradingkey_api.get_tradingkey_data("AAPL")
    assert mock_fetch.call_count == 1  # 두 번째는 캐시


@patch("tradingkey_api._fetch_raw")
def test_get_support_resistance(mock_fetch):
    tradingkey_api._cache.clear()
    mock_fetch.return_value = MOCK_TK_RESPONSE
    result = tradingkey_api.get_support_resistance("AAPL")
    assert result == (278.0, 351.0)


@patch("tradingkey_api._fetch_raw")
def test_get_tradingkey_data_api_failure_returns_none(mock_fetch):
    tradingkey_api._cache.clear()
    mock_fetch.side_effect = Exception("network error")
    result = tradingkey_api.get_tradingkey_data("AAPL")
    assert result is None
