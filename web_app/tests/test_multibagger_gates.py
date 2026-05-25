import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import multibagger as mb


def test_defaults_present():
    assert mb.DEFAULTS["F1_MCAP_MIN"] == 200_000_000
    assert mb.DEFAULTS["F1_MCAP_MAX"] == 2_000_000_000


def test_fundamentals_all_optional():
    f = mb.Fundamentals()
    assert f.market_cap is None


def test_f1_size_band_pass():
    f = mb.Fundamentals(market_cap=1_000_000_000)
    assert mb.eval_f1(f, mb.DEFAULTS) is True

def test_f1_size_too_small():
    f = mb.Fundamentals(market_cap=100_000_000)
    assert mb.eval_f1(f, mb.DEFAULTS) is False

def test_f1_size_too_large():
    f = mb.Fundamentals(market_cap=5_000_000_000)
    assert mb.eval_f1(f, mb.DEFAULTS) is False

def test_f1_missing():
    f = mb.Fundamentals(market_cap=None)
    assert mb.eval_f1(f, mb.DEFAULTS) is None

def test_f2_profitability():
    assert mb.eval_f2(mb.Fundamentals(ebitda=1.0, fcf=1.0), mb.DEFAULTS) is True
    assert mb.eval_f2(mb.Fundamentals(ebitda=-1.0, fcf=1.0), mb.DEFAULTS) is False
    assert mb.eval_f2(mb.Fundamentals(ebitda=1.0, fcf=None), mb.DEFAULTS) is None

def test_f8_entry():
    ok = mb.Fundamentals(from_52w_high=-0.20, return_1m=0.10)
    assert mb.eval_f8(ok, mb.DEFAULTS) is True
    too_high = mb.Fundamentals(from_52w_high=-0.05, return_1m=0.10)
    assert mb.eval_f8(too_high, mb.DEFAULTS) is False
    too_deep = mb.Fundamentals(from_52w_high=-0.60, return_1m=0.10)
    assert mb.eval_f8(too_deep, mb.DEFAULTS) is False
    overheated = mb.Fundamentals(from_52w_high=-0.20, return_1m=0.40)
    assert mb.eval_f8(overheated, mb.DEFAULTS) is False


def test_f3_roic_absolute():
    assert mb.eval_f3(mb.Fundamentals(roic=0.15), mb.DEFAULTS) is True
    assert mb.eval_f3(mb.Fundamentals(roic=0.05), mb.DEFAULTS) is False

def test_f3_roic_improving():
    f = mb.Fundamentals(roic=0.08, roic_prev=0.05)
    assert mb.eval_f3(f, mb.DEFAULTS) is True  # 절대 미달이지만 개선

def test_f3_missing():
    assert mb.eval_f3(mb.Fundamentals(), mb.DEFAULTS) is None

def test_f4_valuation_either():
    assert mb.eval_f4(mb.Fundamentals(fcf_yield=0.08, pb=5.0), mb.DEFAULTS) is True  # FCF만
    assert mb.eval_f4(mb.Fundamentals(fcf_yield=0.02, pb=2.0), mb.DEFAULTS) is True  # PB만
    assert mb.eval_f4(mb.Fundamentals(fcf_yield=0.02, pb=5.0), mb.DEFAULTS) is False
    assert mb.eval_f4(mb.Fundamentals(fcf_yield=None, pb=None), mb.DEFAULTS) is None

def test_f5_growth_quality():
    ok = mb.Fundamentals(revenue_yoy=0.10, ebitda_yoy=0.15)
    assert mb.eval_f5(ok, mb.DEFAULTS) is True
    slow = mb.Fundamentals(revenue_yoy=0.03, ebitda_yoy=0.10)
    assert mb.eval_f5(slow, mb.DEFAULTS) is False  # rev<5%
    margin_drop = mb.Fundamentals(revenue_yoy=0.10, ebitda_yoy=0.05)
    assert mb.eval_f5(margin_drop, mb.DEFAULTS) is False

def test_f6_capital_allocation():
    ok = mb.Fundamentals(ebitda_yoy=0.20, assets_yoy=0.10)
    assert mb.eval_f6(ok, mb.DEFAULTS) is True
    waste = mb.Fundamentals(ebitda_yoy=0.05, assets_yoy=0.20)
    assert mb.eval_f6(waste, mb.DEFAULTS) is False


def test_f7_normal_rates():
    f = mb.Fundamentals(icr=5.0, debt_ebitda=2.0, dgs10_pct=3.0)
    assert mb.eval_f7(f, mb.DEFAULTS) is True

def test_f7_normal_icr_fail():
    f = mb.Fundamentals(icr=2.0, debt_ebitda=2.0, dgs10_pct=3.0)
    assert mb.eval_f7(f, mb.DEFAULTS) is False

def test_f7_hirate_strengthens():
    # 금리 4.5% → ICR≥4.0 D/E≤2.5 로 강화
    borderline = mb.Fundamentals(icr=3.5, debt_ebitda=2.7, dgs10_pct=4.5)
    assert mb.eval_f7(borderline, mb.DEFAULTS) is False  # 평시엔 통과지만 고금리에선 탈락

    strong = mb.Fundamentals(icr=4.5, debt_ebitda=2.0, dgs10_pct=4.5)
    assert mb.eval_f7(strong, mb.DEFAULTS) is True

def test_f7_dgs10_missing_uses_normal():
    f = mb.Fundamentals(icr=3.5, debt_ebitda=2.7, dgs10_pct=None)
    assert mb.eval_f7(f, mb.DEFAULTS) is True  # 평시 임계로 평가

def test_f7_inputs_missing():
    assert mb.eval_f7(mb.Fundamentals(dgs10_pct=3.0), mb.DEFAULTS) is None
