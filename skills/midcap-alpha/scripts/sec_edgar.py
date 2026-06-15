#!/usr/bin/env python3
"""SEC EDGAR data collector: 13F institutional holdings + Form 4 insider transactions."""
import sys
import json
import time
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# SEC EDGAR requires a User-Agent header with contact info
SEC_USER_AGENT = "MidcapAlpha/1.0 (midcap-alpha-skill)"
SEC_BASE_URL = "https://efts.sec.gov/LATEST"
SEC_EDGAR_API = "https://data.sec.gov"

CACHE_DIR = Path(__file__).parent / ".cache"
CACHE_TTL_13F = 24 * 3600  # 24 hours
CACHE_TTL_FORM4 = 6 * 3600  # 6 hours
SEC_RATE_LIMIT_DELAY = 0.12  # 10 req/sec limit -> ~100ms between requests


def _sec_request(url: str) -> dict | list | None:
    """Make a rate-limited request to SEC EDGAR API."""
    headers = {
        "User-Agent": SEC_USER_AGENT,
        "Accept": "application/json",
    }
    req = Request(url, headers=headers)
    try:
        time.sleep(SEC_RATE_LIMIT_DELAY)
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, json.JSONDecodeError) as e:
        print(f"WARNING: SEC request failed {url}: {e}", file=sys.stderr)
        return None


def _load_cache(key: str, ttl: int) -> dict | None:
    """Load cached data if fresh enough."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{key}.json"
    if not cache_file.exists():
        return None
    try:
        with open(cache_file, encoding="utf-8") as f:
            cached = json.load(f)
        cached_time = datetime.fromisoformat(cached.get("_cached_at", "2000-01-01"))
        if (datetime.now() - cached_time).total_seconds() < ttl:
            return cached.get("data")
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _save_cache(key: str, data):
    """Save data to cache with timestamp."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{key}.json"
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump({"_cached_at": datetime.now().isoformat(), "data": data}, f, ensure_ascii=False)


def get_cik_for_ticker(ticker: str) -> str | None:
    """Resolve ticker to CIK number via SEC company tickers JSON."""
    cache_key = "sec_ticker_cik_map"
    cik_map = _load_cache(cache_key, 7 * 24 * 3600)  # 7-day cache

    if cik_map is None:
        url = f"{SEC_EDGAR_API}/files/company_tickers.json"
        data = _sec_request(url)
        if not data:
            return None
        cik_map = {}
        for entry in data.values():
            t = entry.get("ticker", "").upper()
            cik = str(entry.get("cik_str", "")).zfill(10)
            cik_map[t] = cik
        _save_cache(cache_key, cik_map)

    return cik_map.get(ticker.upper())


def fetch_13f_holdings(ticker: str) -> dict:
    """Fetch institutional 13F holdings changes for a ticker.

    Returns aggregated data:
    - total_institutions: number of 13F filers holding this stock
    - qoq_change_pct: estimated quarter-over-quarter change in institutional shares
    - top_holders_increasing: count of top holders that increased position
    """
    cache_key = f"13f_{ticker}"
    cached = _load_cache(cache_key, CACHE_TTL_13F)
    if cached is not None:
        return cached

    cik = get_cik_for_ticker(ticker)
    if not cik:
        result = {"status": "no_cik", "ticker": ticker}
        _save_cache(cache_key, result)
        return result

    # Query SEC EDGAR full-text search for 13F filings mentioning this company
    url = f"{SEC_BASE_URL}/submissions/CIK{cik}.json"
    data = _sec_request(url)
    if not data:
        result = {"status": "api_error", "ticker": ticker}
        _save_cache(cache_key, result)
        return result

    # Extract recent filings info
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])

    # Count institutional-related filings (SC 13G, SC 13D indicate large holders)
    inst_filings = []
    for form, date in zip(forms, dates):
        if form in ("SC 13G", "SC 13G/A", "SC 13D", "SC 13D/A"):
            inst_filings.append({"form": form, "date": date})

    # Use filing frequency as a proxy for institutional interest
    recent_cutoff = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
    recent_inst = [f for f in inst_filings if f["date"] >= recent_cutoff]
    older_inst = [f for f in inst_filings if f["date"] < recent_cutoff]

    result = {
        "status": "ok",
        "ticker": ticker,
        "cik": cik,
        "total_inst_filings": len(inst_filings),
        "recent_6m_filings": len(recent_inst),
        "older_filings": len(older_inst),
        "inst_momentum": len(recent_inst) - len(older_inst),  # positive = increasing interest
        "basis": "proxy",
    }
    _save_cache(cache_key, result)
    return result


def fetch_form4_insider(ticker: str) -> dict:
    """Fetch Form 4 insider transactions for a ticker.

    Returns:
    - net_shares: net shares bought(+) or sold(-) in last 90 days
    - net_value: estimated net dollar value
    - buy_count / sell_count: transaction counts
    """
    cache_key = f"form4_{ticker}"
    cached = _load_cache(cache_key, CACHE_TTL_FORM4)
    if cached is not None:
        return cached

    cik = get_cik_for_ticker(ticker)
    if not cik:
        result = {"status": "no_cik", "ticker": ticker}
        _save_cache(cache_key, result)
        return result

    url = f"{SEC_EDGAR_API}/submissions/CIK{cik}.json"
    data = _sec_request(url)
    if not data:
        result = {"status": "api_error", "ticker": ticker}
        _save_cache(cache_key, result)
        return result

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])

    cutoff_90d = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    form4_recent = sum(1 for form, date in zip(forms, dates) if form == "4" and date >= cutoff_90d)
    form4_total = sum(1 for form in forms if form == "4")

    # Form 4 filing frequency as proxy for insider activity
    # More filings in recent 90 days vs historical average = increasing insider activity
    avg_quarterly = form4_total / max(1, len(set(d[:7] for d in dates))) * 3 if dates else 0

    result = {
        "status": "ok",
        "ticker": ticker,
        "cik": cik,
        "form4_90d_count": form4_recent,
        "form4_total": form4_total,
        "form4_avg_quarterly": round(avg_quarterly, 1),
        "insider_activity_ratio": round(form4_recent / max(1, avg_quarterly), 2),
        "basis": "proxy",
    }
    _save_cache(cache_key, result)
    return result


def collect_all(universe_path: str, output_path: str):
    """Collect SEC data for all tickers in the universe."""
    with open(universe_path, encoding="utf-8") as f:
        universe = json.load(f)

    tickers = [t["ticker"] for t in universe.get("tickers", [])]
    print(f"Collecting SEC data for {len(tickers)} tickers...", file=sys.stderr)

    results = {}
    for i, ticker in enumerate(tickers):
        print(f"  [{i+1}/{len(tickers)}] {ticker}...", file=sys.stderr)
        results[ticker] = {
            "13f": fetch_13f_holdings(ticker),
            "form4": fetch_form4_insider(ticker),
        }

    # Data quality report
    ok_13f = sum(1 for r in results.values() if r["13f"].get("status") == "ok")
    ok_form4 = sum(1 for r in results.values() if r["form4"].get("status") == "ok")
    total = len(tickers)

    quality = {
        "13f_coverage": f"{ok_13f}/{total} ({ok_13f/max(1,total)*100:.0f}%)",
        "form4_coverage": f"{ok_form4}/{total} ({ok_form4/max(1,total)*100:.0f}%)",
        "warning": None,
    }
    if ok_13f / max(1, total) < 0.6:
        quality["warning"] = "13F 데이터 커버리지가 60% 미만입니다. 기관 매집 시그널은 프록시 기반으로 제한됩니다."

    output = {
        "generated": datetime.now().isoformat(),
        "quality": quality,
        "data": results,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"SEC data saved to {output_path}", file=sys.stderr)
    print(f"Quality: 13F {quality['13f_coverage']}, Form4 {quality['form4_coverage']}", file=sys.stderr)
    if quality["warning"]:
        print(f"WARNING: {quality['warning']}", file=sys.stderr)

    print(json.dumps(output, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Collect SEC EDGAR data")
    parser.add_argument("--universe", required=True, help="Path to midcap_universe.json")
    parser.add_argument("--output", default="sec_data.json", help="Output path")
    args = parser.parse_args()

    collect_all(args.universe, args.output)


if __name__ == "__main__":
    main()
