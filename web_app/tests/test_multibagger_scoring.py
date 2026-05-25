import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import multibagger as mb


def test_q1_roic_normalization():
    assert mb.score_q1(mb.Fundamentals(roic=0.30)) == 100
    assert mb.score_q1(mb.Fundamentals(roic=0.10)) == 0
    assert mb.score_q1(mb.Fundamentals(roic=0.20)) == 50
    assert mb.score_q1(mb.Fundamentals(roic=0.05)) == 0  # clamp
    assert mb.score_q1(mb.Fundamentals(roic=None)) is None


def test_q2_max_of_fcf_or_bm():
    # FCF 강함
    s1 = mb.score_q2(mb.Fundamentals(fcf_yield=0.15, pb=10.0))
    assert s1 == 100
    # PB 강함 (B/M = 1.0)
    s2 = mb.score_q2(mb.Fundamentals(fcf_yield=0.02, pb=1.0))
    assert s2 == 100
    # 둘다 결측
    assert mb.score_q2(mb.Fundamentals()) is None


def test_q3_q4_growth_normalization():
    # Q3: EBITDA YoY − Revenue YoY
    s = mb.score_q3(mb.Fundamentals(ebitda_yoy=0.20, revenue_yoy=0.10))  # +10pp
    assert s == 100
    s = mb.score_q3(mb.Fundamentals(ebitda_yoy=0.10, revenue_yoy=0.10))  # 0pp
    assert s == 50

    s = mb.score_q4(mb.Fundamentals(ebitda_yoy=0.20, assets_yoy=0.05))  # +15pp
    assert s == 100


def test_q5_min_of_icr_or_de():
    # ICR 약점
    f = mb.Fundamentals(icr=3.0, debt_ebitda=0.0)
    assert mb.score_q5(f) == 0  # ICR 약점이 binding


def test_q6_revenue_acceleration():
    assert mb.score_q6(mb.Fundamentals(revenue_yoy=0.30)) == 100
    assert mb.score_q6(mb.Fundamentals(revenue_yoy=0.05)) == 0


def test_bonus_sum():
    f = mb.Fundamentals(
        sector="Healthcare",
        insider_net_buy_3m=1.0,
        buyback_yield_ttm=0.02,
        revenue_yoy=0.20, revenue_yoy_prev=0.10,
    )
    assert mb.score_bonus(f) == 35  # all four


def test_compose_score():
    f = mb.Fundamentals(
        roic=0.20, fcf_yield=0.10, pb=2.0,
        ebitda_yoy=0.20, revenue_yoy=0.10, assets_yoy=0.05,
        icr=10.0, debt_ebitda=1.0,
        sector="Healthcare", buyback_yield_ttm=0.01,
    )
    s = mb.compose_score(f)
    assert 0 <= s <= 100


def test_tie_break_prefers_q4():
    a = mb.Fundamentals(ebitda_yoy=0.30, assets_yoy=0.05)  # Q4 강
    b = mb.Fundamentals(ebitda_yoy=0.10, assets_yoy=0.10)  # Q4 약
    assert mb.tie_break_key(a)[0] > mb.tie_break_key(b)[0]


def _fake_enrich_factory(f: mb.Fundamentals):
    def _enrich(sym, dgs10_pct=None):
        return f
    return _enrich


def test_curated_moat_boosts_score_only_for_tagged_ticker():
    """curated 해자 보유 종목만 4% 가중 — rule/llm/none 폴백은 무가중."""
    f = mb.Fundamentals(
        market_cap=2e9, ebitda=2e8, fcf=1e8,
        roic=0.20, fcf_yield=0.10, pb=2.0,
        revenue_yoy=0.15, ebitda_yoy=0.20, assets_yoy=0.05,
        icr=10.0, debt_ebitda=1.0,
        from_52w_high=-0.10, return_1m=0.05,
        sector="Healthcare",
    )
    base = [
        {"Ticker": "MOAT", "market_cap": 2e9,
         "MoatData": {"source": "curated", "label": "브랜드·IP"}},
        {"Ticker": "PLAIN", "market_cap": 2e9,
         "MoatData": {"source": "rule", "label": "브랜드 충성도"}},
        {"Ticker": "NOTAG", "market_cap": 2e9},
    ]
    out = mb.build_results(base, dgs10_pct=4.0, enrich_fn=_fake_enrich_factory(f))
    rows = {r["ticker"]: r for r in out["pass"] + out["watch"]}
    assert set(rows.keys()) == {"MOAT", "PLAIN", "NOTAG"}
    assert rows["MOAT"]["moat_curated"] is True
    assert rows["PLAIN"]["moat_curated"] is False
    assert rows["NOTAG"]["moat_curated"] is False
    # 동일 Fundamentals → MOAT 만 1.04배 (반올림 오차 허용).
    assert rows["MOAT"]["score"] >= rows["PLAIN"]["score"] * 1.03
    assert rows["MOAT"]["score"] == rows["PLAIN"]["score"] == rows["NOTAG"]["score"] or \
           rows["MOAT"]["score"] > rows["PLAIN"]["score"]
    assert rows["PLAIN"]["score"] == rows["NOTAG"]["score"]
