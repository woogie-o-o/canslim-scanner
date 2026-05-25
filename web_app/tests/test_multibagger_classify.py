import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import multibagger as mb


def _passing_fundamentals():
    return mb.Fundamentals(
        market_cap=1_000_000_000,
        ebitda=1e8, fcf=5e7,
        roic=0.15, fcf_yield=0.08, pb=2.0,
        revenue_yoy=0.10, ebitda_yoy=0.15, assets_yoy=0.08,
        icr=5.0, debt_ebitda=2.0,
        from_52w_high=-0.20, return_1m=0.10,
        dgs10_pct=3.5,
    )


def test_classify_pass():
    res = mb.classify(_passing_fundamentals(), mb.DEFAULTS)
    assert res.layer == "PASS"
    assert res.gates_passed == set(mb.ALL_GATES)
    assert res.gates_failed == set()


def test_classify_watch_one_optional_fail():
    f = _passing_fundamentals()
    f.roic = 0.05  # F3 fail
    f.roic_prev = 0.05
    res = mb.classify(f, mb.DEFAULTS)
    assert res.layer == "WATCH"
    assert "F3" in res.gates_failed


def test_classify_miss_required_fail():
    f = _passing_fundamentals()
    f.market_cap = 5_000_000_000  # F1 fail
    res = mb.classify(f, mb.DEFAULTS)
    assert res.layer == "MISS"


def test_classify_miss_too_many_optional_fail():
    f = _passing_fundamentals()
    f.roic = 0.05; f.roic_prev = 0.05  # F3
    f.fcf_yield = 0.02; f.pb = 5.0     # F4
    f.icr = 2.0                         # F7
    res = mb.classify(f, mb.DEFAULTS)
    assert res.layer == "MISS"  # 3개 부족


def test_classify_excludes_when_3_missing_optional():
    f = mb.Fundamentals(
        market_cap=1e9, ebitda=1e8, fcf=5e7,
        from_52w_high=-0.20, return_1m=0.10,
        # F3·F4·F5·F6 입력 결측 → 4개 N/A
    )
    res = mb.classify(f, mb.DEFAULTS)
    assert res.layer == "EXCLUDED"


def test_build_results_pre_filters_by_size_and_profit(monkeypatch):
    """P1-7: F1(시총 밴드)만 사전 필터. F2(EBITDA/FCF 양수)는 enrichment 후 classify 가 판정."""
    base = [
        {"Ticker": "SMALL", "market_cap": 1e9, "ebitda": 1e8, "fcf": 5e7},
        {"Ticker": "BIG", "market_cap": 1e11, "ebitda": 1e10, "fcf": 1e9},
        {"Ticker": "LOSS", "market_cap": 1e9, "ebitda": -1e8, "fcf": -5e7},
    ]

    def fake_enrich(sym, dgs10_pct):
        ebitda = -1e8 if sym == "LOSS" else 1e8
        fcf = -5e7 if sym == "LOSS" else 5e7
        return mb.Fundamentals(
            market_cap=1e9, ebitda=ebitda, fcf=fcf,
            roic=0.15, fcf_yield=0.08, pb=2.0,
            revenue_yoy=0.10, ebitda_yoy=0.15, assets_yoy=0.08,
            icr=5.0, debt_ebitda=2.0,
            from_52w_high=-0.20, return_1m=0.10,
            dgs10_pct=3.5, sector="Healthcare",
        )
    res = mb.build_results(base, dgs10_pct=3.5, enrich_fn=fake_enrich, max_workers=2)
    tickers = {r["ticker"] for r in res["pass"] + res["watch"]}
    assert "SMALL" in tickers
    assert "BIG" not in tickers  # F1 사전 필터
    assert "LOSS" not in tickers  # F2 가 classify 단계에서 MISS


def test_tie_break_market_cap_smaller_first():
    """P1-6: 동점 시 시총 작은 게 우선(멀티배거 전략) — sort 후 결과 확인."""
    def enrich_factory(mc):
        def _e(sym, dgs10_pct):
            return mb.Fundamentals(
                market_cap=mc, ebitda=1e8, fcf=5e7,
                roic=0.15, fcf_yield=0.08, pb=2.0,
                revenue_yoy=0.10, ebitda_yoy=0.15, assets_yoy=0.08,
                icr=5.0, debt_ebitda=2.0,
                from_52w_high=-0.20, return_1m=0.10,
                dgs10_pct=3.5, sector="Healthcare",
            )
        return _e
    base = [{"Ticker": "SMALL", "market_cap": 3e8},
            {"Ticker": "MID", "market_cap": 1e9}]
    enrich_map = {"SMALL": enrich_factory(3e8), "MID": enrich_factory(1e9)}

    def dispatch(sym, dgs10_pct): return enrich_map[sym](sym, dgs10_pct)
    res = mb.build_results(base, dgs10_pct=3.5, enrich_fn=dispatch, max_workers=2)
    pass_tickers = [r["ticker"] for r in res["pass"]]
    assert pass_tickers[0] == "SMALL"  # 동점 → 작은 시총 우선


def test_build_results_includes_when_base_lacks_ebitda(monkeypatch):
    """P1-7 회귀: base scan 에 ebitda/fcf 가 없어도 enrichment 가 채우면 통과."""
    base = [{"Ticker": "NODATA", "market_cap": 1e9}]  # ebitda/fcf 누락

    def fake_enrich(sym, dgs10_pct):
        return mb.Fundamentals(
            market_cap=1e9, ebitda=1e8, fcf=5e7,
            roic=0.15, fcf_yield=0.08, pb=2.0,
            revenue_yoy=0.10, ebitda_yoy=0.15, assets_yoy=0.08,
            icr=5.0, debt_ebitda=2.0,
            from_52w_high=-0.20, return_1m=0.10,
            dgs10_pct=3.5, sector="Healthcare",
        )
    res = mb.build_results(base, dgs10_pct=3.5, enrich_fn=fake_enrich, max_workers=2)
    tickers = {r["ticker"] for r in res["pass"] + res["watch"]}
    assert "NODATA" in tickers


def test_build_results_sorts_pass_by_score():
    def enrich_factory(roic):
        def _e(sym, dgs10_pct):
            return mb.Fundamentals(
                market_cap=1e9, ebitda=1e8, fcf=5e7,
                roic=roic, fcf_yield=0.08, pb=2.0,
                revenue_yoy=0.10, ebitda_yoy=0.15, assets_yoy=0.08,
                icr=5.0, debt_ebitda=2.0,
                from_52w_high=-0.20, return_1m=0.10,
                dgs10_pct=3.5, sector="Healthcare",
            )
        return _e

    base_a = [{"Ticker": "A", "market_cap": 1e9, "ebitda": 1e8, "fcf": 5e7}]
    base_b = [{"Ticker": "B", "market_cap": 1e9, "ebitda": 1e8, "fcf": 5e7}]
    res_a = mb.build_results(base_a, 3.5, enrich_factory(0.25), max_workers=1)
    res_b = mb.build_results(base_b, 3.5, enrich_factory(0.12), max_workers=1)
    assert res_a["pass"][0]["score"] > res_b["pass"][0]["score"]
