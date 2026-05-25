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
    base = [
        {"Ticker": "SMALL", "market_cap": 1e9, "ebitda": 1e8, "fcf": 5e7},
        {"Ticker": "BIG", "market_cap": 1e11, "ebitda": 1e10, "fcf": 1e9},
        {"Ticker": "LOSS", "market_cap": 1e9, "ebitda": -1e8, "fcf": -5e7},
    ]
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
    assert "SMALL" in tickers
    assert "BIG" not in tickers
    assert "LOSS" not in tickers


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
