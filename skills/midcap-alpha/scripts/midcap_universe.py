#!/usr/bin/env python3
"""Midcap universe builder: SP400 + market cap filter → target ticker list."""
import sys
import json
import argparse
from pathlib import Path

def load_index_membership(project_root: Path) -> list[str]:
    """Load SP400 tickers from index_membership.json."""
    idx_path = project_root / "web_app" / "index_membership.json"
    if not idx_path.exists():
        print(f"ERROR: {idx_path} not found", file=sys.stderr)
        sys.exit(1)
    with open(idx_path, encoding="utf-8") as f:
        data = json.load(f)
    sp400 = data.get("SP400", [])
    if not sp400:
        print("WARNING: SP400 list is empty in index_membership.json", file=sys.stderr)
    return sp400


def fetch_market_caps(tickers: list[str]) -> dict[str, dict]:
    """Fetch market cap and sector for each ticker via yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        print("ERROR: yfinance not installed. Run: pip install yfinance", file=sys.stderr)
        sys.exit(1)

    results = {}
    batch_size = 50
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        batch_str = " ".join(batch)
        try:
            data = yf.download(batch_str, period="1d", progress=False, threads=True)
        except Exception as e:
            print(f"WARNING: yf.download batch failed: {e}", file=sys.stderr)

        for ticker in batch:
            try:
                info = yf.Ticker(ticker).fast_info
                mcap = getattr(info, "market_cap", None)
                if mcap and mcap > 0:
                    tk = yf.Ticker(ticker)
                    tk_info = tk.info or {}
                    results[ticker] = {
                        "marketCap": mcap,
                        "sector": tk_info.get("sector", "Unknown"),
                        "industry": tk_info.get("industry", "Unknown"),
                        "shortName": tk_info.get("shortName", ticker),
                    }
            except Exception as e:
                print(f"WARNING: {ticker} info fetch failed: {e}", file=sys.stderr)
    return results


def build_universe(
    tickers_info: dict[str, dict],
    min_cap_b: float,
    max_cap_b: float,
    top_n: int,
    sector_filter: str | None = None,
) -> list[dict]:
    """Filter and rank tickers by market cap within the midcap band."""
    min_cap = min_cap_b * 1e9
    max_cap = max_cap_b * 1e9

    universe = []
    for ticker, info in tickers_info.items():
        mcap = info["marketCap"]
        if mcap < min_cap or mcap > max_cap:
            continue
        if sector_filter and sector_filter.lower() not in info["sector"].lower():
            continue
        universe.append({
            "ticker": ticker,
            "shortName": info["shortName"],
            "sector": info["sector"],
            "industry": info["industry"],
            "marketCap": mcap,
            "marketCapB": round(mcap / 1e9, 2),
        })

    universe.sort(key=lambda x: x["marketCap"], reverse=True)
    return universe[:top_n]


def main():
    parser = argparse.ArgumentParser(description="Build midcap universe")
    parser.add_argument("--min-cap", type=float, default=3.0, help="Min market cap in $B")
    parser.add_argument("--max-cap", type=float, default=20.0, help="Max market cap in $B")
    parser.add_argument("--top-n", type=int, default=100, help="Max tickers to include")
    parser.add_argument("--sector", type=str, default=None, help="Sector filter (partial match)")
    parser.add_argument("--output", type=str, default="midcap_universe.json")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    sp400_tickers = load_index_membership(project_root)
    print(f"Loaded {len(sp400_tickers)} SP400 tickers", file=sys.stderr)

    tickers_info = fetch_market_caps(sp400_tickers)
    print(f"Fetched info for {len(tickers_info)}/{len(sp400_tickers)} tickers", file=sys.stderr)

    universe = build_universe(tickers_info, args.min_cap, args.max_cap, args.top_n, args.sector)
    print(f"Universe: {len(universe)} tickers in ${args.min_cap}B~${args.max_cap}B range", file=sys.stderr)

    output_path = Path(args.output)
    result = {
        "generated": __import__("datetime").datetime.now().isoformat(),
        "params": {"min_cap_b": args.min_cap, "max_cap_b": args.max_cap, "top_n": args.top_n, "sector": args.sector},
        "count": len(universe),
        "tickers": universe,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
