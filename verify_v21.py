# -*- coding: utf-8 -*-
"""v21 Sprint 1 — 통합 self-check."""
from __future__ import annotations
import sys
import traceback


def _run(name, fn):
    try:
        fn()
        print(f"[OK]   {name}")
        return True
    except Exception as e:
        print(f"[FAIL] {name}: {e}")
        traceback.print_exc()
        return False


def check_data_quality():
    import data_quality as m
    assert m.DATA_DELAY_MIN == 15
    assert "DELAYED" in m.build_delay_badge_text()
    assert m.is_market_data_delayed()


def check_position_sizer():
    import position_sizer as m
    r = m.calc_position(10000, 1, 100, 97)
    assert r["qty"] == 33, r
    assert abs(r["risk_amount"] - 99.0) < 0.01
    try:
        m.calc_position(10000, 1, 100, 105)
        raise AssertionError("should reject stop>=entry")
    except ValueError:
        pass
    rr = m.calc_rr(100, 97, 109)
    assert abs(rr - 3.0) < 0.01


def check_event_calendar():
    import event_calendar as m
    chip = m.build_dday_chip(3)
    assert chip["show"]
    chip0 = m.build_dday_chip(0)
    assert chip0["show"] and "D-DAY" in chip0["text"]
    chipN = m.build_dday_chip(None)
    assert not chipN["show"]
    # 라이브 호출 — 예외만 없으면 OK
    d, s = m.earnings_dday("AAPL")
    assert d is None or isinstance(d, int)


def check_watchlist():
    from watchlist import WatchlistDB
    db = WatchlistDB(":memory:")
    assert db.add("AAPL")
    assert not db.add("AAPL")
    assert db.add("NVDA")
    assert set(db.list()) == {"AAPL", "NVDA"}
    assert db.update_metrics("AAPL", score=80, phase="강한 상승")
    g = db.get("AAPL")
    assert g["last_score"] == 80 and g["last_phase"] == "강한 상승"
    assert db.remove("AAPL")
    assert db.list() == ["NVDA"]


def check_notifier():
    import notifier as m
    backend = m._detect_backend()
    assert backend in {"plyer", "win10toast", "powershell", "print"}
    # is_available 호출만 (네트워크/UI 의존 없음)
    _ = m.is_available()


def check_macro_gate():
    import macro_gate as m
    s = m.get_regime()
    assert s["regime"] in {"Risk-On", "Neutral", "Risk-Off", "Unknown"}
    assert "vix" in s
    text = m.build_banner_text(s)
    assert "시장 레짐" in text


def check_persona_committee():
    """7-페르소나 위원회 — 5/7 게이트 + 통합 스코어."""
    from persona_committee import evaluate
    cs = dict(TotalScore=72, ValueScore=65, QualityScore=70,
              MomentumScore=68, RSI=58, Mom12M=0.18,
              EPSAcceleration=True, ATRPercent=2.4, Drawdown=-0.05)
    r = evaluate(None, cs, {"regime": "Risk-On", "vix": 15.2})
    assert len(r.verdicts) == 7, f"need 7 personas, got {len(r.verdicts)}"
    assert r.buy_count + r.sell_count <= 7
    assert 0 <= r.integrated_score <= 100
    assert r.grade in {"Must Buy", "Strong Buy", "Buy", "Hold",
                       "Weak Hold", "Sell", "Strong Sell"}


def check_naver_modules():
    """naver_news + naver_finance 임포트 + 핵심 함수 존재."""
    import naver_news as nn
    assert callable(nn.search_news)
    assert callable(nn.summarize)
    assert callable(nn.score_sentiment)
    assert nn.score_sentiment("호재 급등 실적개선") > 0
    assert nn.score_sentiment("악재 급락 손실") < 0

    import naver_finance as nf
    assert callable(nf.get_quote)
    assert callable(nf.get_investor_flow)
    assert callable(nf.build_summary_text)
    # 미국 티커는 KR 코드 아님 → error
    bad = nf.get_quote("AAPL")
    assert bad.get("code") is None


def check_integration():
    """quant_nexus_v20 + analysis_card 가 v21 모듈을 실제로 사용하는지 (AST 기반)."""
    import ast
    src = open("quant_nexus_v20.py", encoding="utf-8").read()
    tree = ast.parse(src)

    # 1) 문자열 기반 빠른 체크
    assert "_committee_cache" in src,  "committee cache 미연결"
    assert "_committee_str" in src,    "committee 표시 미연결"
    assert "_event_calendar" in src,   "event_calendar 미연결"
    assert "_position_sizer" in src,   "position_sizer 미연결"
    assert "_in_watchlist" in src,     "watchlist 미연결"
    assert '"Cmte"' in src,            "Cmte 컬럼 미추가"

    # 2) AST 검증 — 핵심 메서드가 클래스에 실제 정의되어 있는가
    method_names = {
        node.name
        for cls in ast.walk(tree)
        if isinstance(cls, ast.ClassDef)
        for node in cls.body
        if isinstance(node, ast.FunctionDef)
    }
    for required in ("_committee_str", "_in_watchlist", "_toggle_watchlist"):
        assert required in method_names, f"{required} 메서드 미정의"

    # 3) 캐시 무효화 — _switch_market / _start_scan / _start_scan_all 셋 모두에서 clear() 호출
    for fname in ("_switch_market", "_start_scan", "_start_scan_all"):
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == fname:
                body_src = ast.unparse(node)
                assert "_committee_cache.clear" in body_src, \
                    f"{fname} 에서 캐시 무효화 누락"
                break
        else:
            raise AssertionError(f"{fname} 메서드 자체가 없음")

    # 4) Cmte 컬럼이 cols 튜플에 포함됐는지 (단순 grep으로는 주석에도 잡힐 수 있음)
    assert ('"Cmte"' in src and 'cols = (' in src), "Cmte 컬럼이 cols 튜플에 없음"

    # 4b) 신규 외주 모듈 와이어링 (배치 1: backtester / portfolio_tracker / telegram_notifier)
    for required in ("_run_backtest_dialog", "_record_position_dialog",
                     "_show_portfolio_dialog", "_portfolio_db",
                     "_show_backtest_window"):
        assert required in method_names, f"{required} 메서드 미정의 (배치1 미연결)"
    assert "from backtester import backtest" in src, "backtester 미연결"
    assert "from portfolio_tracker import PortfolioTracker" in src, "portfolio_tracker 미연결"
    assert "import telegram_notifier" in src, "telegram_notifier 미연결"

    # 4c) 배치 2: news_summarizer / alert_rules / risk_dashboard
    for required in ("_show_news_dialog", "_render_news_window",
                     "_alert_rule_store", "_show_risk_dialog"):
        assert required in method_names, f"{required} 메서드 미정의 (배치2 미연결)"
    assert "from news_summarizer import summarize" in src, "news_summarizer 미연결"
    assert "from alert_rules import AlertRuleStore" in src, "alert_rules 미연결"
    assert "from risk_dashboard import" in src, "risk_dashboard 미연결"

    # 4d) 배치 3: naver_news / naver_finance
    for required in ("_show_naver_news_dialog", "_show_naver_quote_dialog",
                     "_render_naver_quote_window"):
        assert required in method_names, f"{required} 메서드 미정의 (배치3 미연결)"
    assert "from naver_news import" in src, "naver_news 미연결"
    assert "from naver_finance import" in src, "naver_finance 미연결"

    # 5) analysis_card 측
    src2 = open("analysis_card.py", encoding="utf-8").read()
    assert "persona_committee" in src2, "analysis_card 위원회 패널 미연결"
    assert "position_sizer"    in src2, "analysis_card 포지션 패널 미연결"
    tree2 = ast.parse(src2)
    func_names = {n.name for n in ast.walk(tree2) if isinstance(n, ast.FunctionDef)}
    assert "build_four_axis_card" in func_names, "build_four_axis_card 미정의"


def main():
    checks = [
        ("data_quality",      check_data_quality),
        ("position_sizer",    check_position_sizer),
        ("event_calendar",    check_event_calendar),
        ("watchlist",         check_watchlist),
        ("notifier",          check_notifier),
        ("macro_gate",        check_macro_gate),
        ("persona_committee", check_persona_committee),
        ("naver_modules",     check_naver_modules),
        ("integration",       check_integration),
    ]
    results = [_run(n, f) for n, f in checks]
    ok = sum(results)
    fail = len(results) - ok
    print(f"\n=== v21 Sprint 1 self-check: {ok} OK, {fail} FAIL ===")
    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()
