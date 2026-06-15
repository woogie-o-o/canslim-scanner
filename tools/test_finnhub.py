#!/usr/bin/env python3
"""Finnhub API 데이터 테스트."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web_app.config_manager import apply_to_environ
apply_to_environ()

fk = os.environ.get("FINNHUB_API_KEY", "")
print(f"KEY: {'SET' if fk else 'NOT_SET'} (len={len(fk)})")
if not fk:
    print("Finnhub API key not configured. Set it in /settings page.")
    sys.exit(1)

import finnhub
fc = finnhub.Client(api_key=fk)

# 1) Insider transactions
print("\n=== Insider Transactions (AAPL, recent 3) ===")
try:
    ins = fc.stock_insider_transactions("AAPL", "2026-01-01", "2026-05-18")
    for t in (ins.get("data") or [])[:3]:
        name = t.get("name", "?")
        tx = t.get("transactionType", "?")
        sh = t.get("share", 0)
        pr = t.get("transactionPrice", 0)
        print(f"  {name} | {tx} | {sh} shares | ${pr}")
except Exception as e:
    print(f"  Error: {e}")

# 2) Recommendation trends
print("\n=== Recommendation Trends (AAPL, recent 2) ===")
try:
    rec = fc.recommendation_trends("AAPL")
    for r in rec[:2]:
        p = r.get("period", "?")
        print(f"  {p}: strongBuy={r.get('strongBuy')} buy={r.get('buy')} hold={r.get('hold')} sell={r.get('sell')} strongSell={r.get('strongSell')}")
except Exception as e:
    print(f"  Error: {e}")

# 3) Earnings surprises
print("\n=== Earnings Surprises (AAPL, recent 4) ===")
try:
    earn = fc.company_earnings("AAPL", limit=4)
    for e in earn:
        p = e.get("period", "?")
        act = e.get("actual", "?")
        est = e.get("estimate", "?")
        surp = e.get("surprisePercent", "?")
        print(f"  {p}: actual={act} estimate={est} surprise%={surp}")
except Exception as e:
    print(f"  Error: {e}")

# 4) Social sentiment
print("\n=== Social Sentiment (AAPL) ===")
try:
    ss = fc.stock_social_sentiment("AAPL", _from="2026-05-01", to="2026-05-18")
    rd = ss.get("reddit") or []
    tw = ss.get("twitter") or []
    print(f"  Reddit entries: {len(rd)}, Twitter entries: {len(tw)}")
    for s in rd[:2]:
        print(f"  reddit: mention={s.get('mention')} pos={s.get('positiveScore')} neg={s.get('negativeScore')}")
except Exception as e:
    print(f"  Error: {e}")

# 5) Company profile2 (무료 여부 실측)
print("\n=== Company Profile2 (AAPL) ===")
try:
    p = fc.company_profile2(symbol="AAPL")
    print(f"  name={p.get('name')} ipo={p.get('ipo')} "
          f"shareOut={p.get('shareOutstanding')} industry={p.get('finnhubIndustry')} "
          f"logo={'Y' if p.get('logo') else 'N'}")
except Exception as e:
    print(f"  Error: {e}")

# 6) Insider sentiment MSPR (무료 여부 실측)
print("\n=== Insider Sentiment (AAPL) ===")
try:
    s = fc.stock_insider_sentiment("AAPL", "2025-01-01", "2026-06-01")
    rows = s.get("data") or []
    print(f"  rows={len(rows)}")
    for r in rows[-2:]:
        print(f"  {r.get('year')}-{r.get('month')}: mspr={r.get('mspr')} change={r.get('change')}")
except Exception as e:
    print(f"  Error: {e}")

# 7) IPO calendar + general news (무료 여부 실측)
print("\n=== IPO Calendar (다음 30일) ===")
try:
    from datetime import datetime as _dt, timedelta as _td
    _t = _dt.now()
    ic = fc.ipo_calendar(_from=_t.strftime("%Y-%m-%d"),
                         to=(_t + _td(days=30)).strftime("%Y-%m-%d"))
    print(f"  ipos={len(ic.get('ipoCalendar') or [])}")
except Exception as e:
    print(f"  Error: {e}")
print("\n=== General News ===")
try:
    gn = fc.general_news("general", min_id=0)
    print(f"  news={len(gn or [])}; first={ (gn or [{}])[0].get('headline','')[:50] }")
except Exception as e:
    print(f"  Error: {e}")

print("\nDone.")
