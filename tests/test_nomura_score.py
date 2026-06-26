"""tests/test_nomura_score.py — nomura_score 단위 테스트 (yfinance 1-fetch 구조 기준)."""
import pytest
from unittest.mock import patch
import nomura_score


# ── 공용 헬퍼 ────────────────────────────────────────────────────────────────

def _yf(curr, prev, market_cap=0.0, ebit=0.0, retained=0.0,
        ppe_curr=0.0, ppe_prev=0.0, depr=0.0,
        lt_curr=0.0, lt_prev=0.0, sga_curr=0.0, sga_prev=0.0):
    """_fetch_yf() 반환 형식과 동일한 dict 생성 헬퍼."""
    return dict(curr=curr, prev=prev, market_cap=market_cap,
                ebit=ebit, retained=retained,
                ppe_curr=ppe_curr, ppe_prev=ppe_prev, depr=depr,
                lt_curr=lt_curr, lt_prev=lt_prev,
                sga_curr=sga_curr, sga_prev=sga_prev)


# ── 9/9 Piotroski (F9: curr_at=2.0 > prev_at=1.78) ─────────────────────────
_C9 = {
    "Net Income": 1000, "Total Assets": 5000, "Operating Cash Flow": 1200,
    "Long Term Debt": 500, "Current Assets": 2000, "Current Liabilities": 800,
    "Ordinary Shares Number": 100, "Total Revenue": 10000, "Gross Profit": 4000,
}
_P9 = {
    "Net Income": 800, "Total Assets": 4500, "Long Term Debt": 600,
    "Current Assets": 1500, "Current Liabilities": 700, "Ordinary Shares Number": 105,
    "Total Revenue": 8000, "Gross Profit": 3000,
}
_YF9 = _yf(_C9, _P9, market_cap=25000.0, ebit=2500.0, retained=5000.0,
           ppe_curr=1000.0, ppe_prev=950.0, depr=200.0,
           lt_curr=500.0, lt_prev=480.0, sga_curr=500.0, sga_prev=480.0)

# ── 0/9 Piotroski (OCF=-600 → F4 실패: -0.12 > -0.10 = False) ───────────────
_C0 = {
    "Net Income": -500, "Total Assets": 5000, "Operating Cash Flow": -600,
    "Long Term Debt": 1000, "Current Assets": 800, "Current Liabilities": 900,
    "Ordinary Shares Number": 110, "Total Revenue": 8000, "Gross Profit": 2000,
}
_P0 = {
    "Net Income": 800, "Total Assets": 4000, "Long Term Debt": 800,
    "Current Assets": 1500, "Current Liabilities": 700, "Ordinary Shares Number": 100,
    "Total Revenue": 9000, "Gross Profit": 3200,
}
_YF0 = _yf(_C0, _P0, market_cap=500.0, ebit=-200.0, retained=-100.0)

# ── Beneish 분식 없음 (M ≈ -2.65 < -1.78) ────────────────────────────────────
_CB = {
    "Net Income": 1000, "Total Assets": 5000, "Operating Cash Flow": 1200,
    "Long Term Debt": 500, "Current Assets": 2000, "Current Liabilities": 800,
    "Ordinary Shares Number": 100, "Total Revenue": 10000, "Gross Profit": 4500,
}
_PB = {
    "Net Income": 900, "Total Assets": 4800, "Long Term Debt": 500,
    "Current Assets": 2000, "Current Liabilities": 800, "Ordinary Shares Number": 100,
    "Total Revenue": 9500, "Gross Profit": 4200,
}
_YFB = _yf(_CB, _PB, market_cap=25000.0, ebit=2000.0, retained=4000.0,
           ppe_curr=1000.0, ppe_prev=950.0, depr=200.0,
           lt_curr=500.0, lt_prev=480.0, sga_curr=500.0, sga_prev=480.0)

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


# ── Piotroski ────────────────────────────────────────────────────────────────

def test_piotroski_perfect_score():
    with patch("nomura_score._fetch_yf", return_value=_YF9):
        assert nomura_score.calculate_piotroski("AAPL") == 9


def test_piotroski_zero_score():
    with patch("nomura_score._fetch_yf", return_value=_YF0):
        assert nomura_score.calculate_piotroski("AAPL") == 0


def test_piotroski_returns_int():
    with patch("nomura_score._fetch_yf", return_value=_YF9):
        result = nomura_score.calculate_piotroski("AAPL")
    assert isinstance(result, int)
    assert 0 <= result <= 9


def test_piotroski_kr_returns_none():
    assert nomura_score.calculate_piotroski("005930.KS") is None


# ── Altman Z ─────────────────────────────────────────────────────────────────

def test_altman_z_safe_zone():
    with patch("nomura_score._fetch_yf", return_value=_YF9):
        z = nomura_score.calculate_altman_z("AAPL")
    assert z is not None
    assert z > 2.99


def test_altman_z_returns_float():
    _simple = _yf(
        {"Net Income": 100, "Total Assets": 1000, "Operating Cash Flow": 150,
         "Long Term Debt": 200, "Current Assets": 500, "Current Liabilities": 300,
         "Ordinary Shares Number": 50, "Total Revenue": 2000, "Gross Profit": 800},
        {"Net Income": 80, "Total Assets": 900, "Long Term Debt": 210,
         "Current Assets": 450, "Current Liabilities": 280, "Ordinary Shares Number": 52,
         "Total Revenue": 1800, "Gross Profit": 700},
        market_cap=2000.0, ebit=200.0, retained=300.0,
    )
    with patch("nomura_score._fetch_yf", return_value=_simple):
        z = nomura_score.calculate_altman_z("AAPL")
    assert isinstance(z, float)


# ── Beneish M ────────────────────────────────────────────────────────────────

def test_beneish_no_warning():
    with patch("nomura_score._fetch_yf", return_value=_YFB):
        result = nomura_score.calculate_beneish_m("AAPL")
    assert result is not None
    m, warning = result
    assert isinstance(m, float)
    assert warning is False


def test_beneish_kr_returns_none():
    assert nomura_score.calculate_beneish_m("005930.KS") is None


# ── get_nomura_score ──────────────────────────────────────────────────────────

@patch("nomura_score.get_tradingkey_data", return_value=MOCK_TK_RESPONSE)
@patch("nomura_score._fetch_yf", return_value=_YF9)
def test_get_nomura_score_structure(mock_yf, mock_tk):
    result = nomura_score.get_nomura_score("AAPL")
    assert result is not None
    for key in ("quantitative_score", "grade", "piotroski", "altman_z",
                "beneish_m", "beneish_warning", "nomura_rating",
                "nomura_target", "nomura_upside"):
        assert key in result, f"missing key: {key}"


@patch("nomura_score.get_tradingkey_data", return_value={
    **MOCK_TK_RESPONSE,
    "score": {**MOCK_TK_RESPONSE["score"], "overall": 95},
})
@patch("nomura_score._fetch_yf", return_value=_YF9)
def test_get_nomura_score_conviction_buy(mock_yf, mock_tk):
    # overall=95 → 76pt + piotroski=9 → 10pt + 1m=5.3 → 6pt = 92 → A+
    result = nomura_score.get_nomura_score("NVDA")
    assert result["grade"] == "A+"
    assert result["nomura_rating"] == "최우량"


@patch("nomura_score.get_tradingkey_data", return_value=MOCK_TK_RESPONSE)
@patch("nomura_score._fetch_yf", return_value=_YF9)
def test_get_nomura_score_range(mock_yf, mock_tk):
    result = nomura_score.get_nomura_score("AAPL")
    assert 0 <= result["quantitative_score"] <= 100


@patch("nomura_score._get_nomura_score_kr", return_value={
    "quantitative_score": 76,
    "grade": "A",
    "nomura_rating": "우량",
})
def test_get_nomura_score_kr_supported(mock_kr):
    result = nomura_score.get_nomura_score("005930.KS")
    assert result is not None
    assert result["quantitative_score"] == 76
    assert result["nomura_rating"] == "우량"
    mock_kr.assert_called_once_with("005930.KS")


# ── score_breakdown 필드 ──────────────────────────────────────────────────────

@patch("nomura_score.get_tradingkey_data", return_value=MOCK_TK_RESPONSE)
@patch("nomura_score._fetch_yf", return_value=_YF9)
def test_score_breakdown_keys(mock_yf, mock_tk):
    """score_breakdown dict에 6개 필수 키가 모두 존재해야 한다."""
    result = nomura_score.get_nomura_score("AAPL")
    assert result is not None
    sb = result.get("score_breakdown")
    assert sb is not None, "score_breakdown 키 누락"
    for key in ("tk_overall", "tk_contribution", "piotroski_contribution",
                "qoq_contribution", "momentum_1m_contribution", "rev_1m"):
        assert key in sb, f"score_breakdown에 '{key}' 키 누락"


@patch("nomura_score.get_tradingkey_data", return_value=MOCK_TK_RESPONSE)
@patch("nomura_score._fetch_yf", return_value=_YF9)
def test_score_breakdown_values(mock_yf, mock_tk):
    """score_breakdown 각 값이 예상 범위 내에 있어야 한다."""
    result = nomura_score.get_nomura_score("AAPL")
    sb = result["score_breakdown"]
    assert 0 <= sb["tk_contribution"] <= 80
    assert 0 <= sb["piotroski_contribution"] <= 10
    assert sb["qoq_contribution"] in (0, 2, 4)
    assert sb["momentum_1m_contribution"] in (0, 1, 3, 6)
    # overall=72, piotroski=9(F9), qoq=-7.1, 1m=5.3
    assert sb["tk_contribution"] == 57         # int(72/100*80)
    assert sb["piotroski_contribution"] == 10  # int(9/9*10)
    assert sb["qoq_contribution"] == 0         # qoq=-7.1 < 0
    assert sb["momentum_1m_contribution"] == 6 # 1m=5.3 > 5


# ── piotroski_detail 필드 ─────────────────────────────────────────────────────

@patch("nomura_score.get_tradingkey_data", return_value=MOCK_TK_RESPONSE)
@patch("nomura_score._fetch_yf", return_value=_YF9)
def test_piotroski_detail_keys(mock_yf, mock_tk):
    """piotroski_detail dict에 9개 필수 키가 모두 존재해야 한다."""
    result = nomura_score.get_nomura_score("AAPL")
    assert result is not None
    pd = result.get("piotroski_detail")
    assert pd is not None, "piotroski_detail 키 누락"
    for key in ("roa_positive", "ocf_positive", "roa_improved", "accrual_quality",
                "leverage_down", "liquidity_up", "no_dilution", "gm_improved", "at_improved"):
        assert key in pd, f"piotroski_detail에 '{key}' 키 누락"


@patch("nomura_score.get_tradingkey_data", return_value=MOCK_TK_RESPONSE)
@patch("nomura_score._fetch_yf", return_value=_YF9)
def test_piotroski_detail_all_true_for_f9(mock_yf, mock_tk):
    """_YF9(F-Score=9) 픽스처에서 piotroski_detail의 9개 항목이 모두 True여야 한다."""
    result = nomura_score.get_nomura_score("AAPL")
    pd = result["piotroski_detail"]
    for key, val in pd.items():
        assert val is True, f"_YF9에서 '{key}'가 False — F9 픽스처 검증 실패"


@patch("nomura_score.get_tradingkey_data", return_value=MOCK_TK_RESPONSE)
@patch("nomura_score._fetch_yf", return_value=None)
def test_piotroski_detail_empty_when_no_yf_data(mock_yf, mock_tk):
    """yfinance 데이터 없을 때 piotroski_detail은 빈 dict여야 한다."""
    result = nomura_score.get_nomura_score("AAPL")
    # yf_data=None → piotroski=0, altman_z=None 등도 확인
    pd = result["piotroski_detail"]
    assert pd == {}, f"yf_data=None 시 piotroski_detail이 비어있지 않음: {pd}"


# ── 기존 구조 테스트 확장 ─────────────────────────────────────────────────────

@patch("nomura_score.get_tradingkey_data", return_value=MOCK_TK_RESPONSE)
@patch("nomura_score._fetch_yf", return_value=_YF9)
def test_get_nomura_score_all_keys(mock_yf, mock_tk):
    """get_nomura_score()가 11개 키를 모두 반환해야 한다."""
    result = nomura_score.get_nomura_score("AAPL")
    assert result is not None
    expected_keys = {
        "quantitative_score", "grade", "piotroski", "altman_z",
        "beneish_m", "beneish_warning", "nomura_rating",
        "nomura_target", "nomura_upside",
        "score_breakdown", "piotroski_detail",  # 새 필드
    }
    missing = expected_keys - set(result.keys())
    assert not missing, f"누락된 키: {missing}"
