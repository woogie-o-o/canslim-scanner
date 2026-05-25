"""EG-006: ATR-정규화 진입 임계값 백테스트.

가설: STRONG 종목에서 entry_discount(%)/atr_pct(%) 비율 r 이
  - r<0.5  → 진입적기 (즉시 매수 시 우위)
  - 0.5≤r<1.0 → 분할진입 (절반 매수 + 풀백 절반 대기)
  - r≥1.0 → 풀백대기 (대기가 우위)

방법:
  1. 샘플 티커 1년치 일봉 다운로드 (yfinance)
  2. 매일 ATR(14), 단순 STRONG-proxy 신호 (5/20일 양 골든크로스 + 거래량≥1.5×avg)
  3. 신호일 종가 = cur, 다음날 시초 = entry, disc = -(시초-cur)/cur (음수면 갭상승 추격)
  4. 5일 forward return 으로 r-bucket 별 Sharpe·WR·MaxDD 산출

stdlib + pandas + yfinance 만 사용.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    print("pandas required", file=sys.stderr)
    sys.exit(1)

try:
    import yfinance as yf
except ImportError:
    print("yfinance required", file=sys.stderr)
    sys.exit(1)


DEFAULT_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
    "TSLA", "META", "AMD", "AVGO", "ORCL",
    "JPM", "BAC", "WMT", "PG", "JNJ",
    "UNH", "HD", "MA", "V", "DIS",
]


def _atr_pct(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """ATR(window) / Close * 100. 표준 ATR 정의 (Welles Wilder)."""
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([
        (h - l),
        (h - pc).abs(),
        (l - pc).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(window).mean()
    return (atr / c) * 100


def _strong_signal(df: pd.DataFrame) -> pd.Series:
    """간이 STRONG proxy: SMA5 > SMA20 + 당일 거래량 > 1.5*avg20."""
    sma5 = df["Close"].rolling(5).mean()
    sma20 = df["Close"].rolling(20).mean()
    vol20 = df["Volume"].rolling(20).mean()
    return (sma5 > sma20) & (df["Volume"] > 1.5 * vol20)


def _gap_pct(prev_close: float, next_open: float) -> float:
    """다음날 시초가 = 실제 진입 가격. cur(전일종가) 대비 갭."""
    if prev_close <= 0:
        return 0.0
    return (next_open - prev_close) / prev_close * 100


def _fwd_return(df: pd.DataFrame, idx: int, hold_days: int = 5) -> float | None:
    if idx + 1 + hold_days >= len(df):
        return None
    entry = float(df["Open"].iloc[idx + 1])
    exit_ = float(df["Close"].iloc[idx + 1 + hold_days])
    if entry <= 0:
        return None
    return (exit_ - entry) / entry * 100


def _max_dd(returns: list[float]) -> float:
    """누적 수익 곡선의 최대 낙폭(%)"""
    if not returns:
        return 0.0
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in returns:
        eq *= 1 + r / 100
        peak = max(peak, eq)
        if peak > 0:
            mdd = min(mdd, (eq - peak) / peak * 100)
    return mdd


def _sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mu = statistics.mean(returns)
    sd = statistics.stdev(returns)
    if sd == 0:
        return 0.0
    # 5일 보유 → 연환산 √(252/5)
    return mu / sd * math.sqrt(252 / 5)


def _win_rate(returns: list[float]) -> float:
    if not returns:
        return 0.0
    wins = sum(1 for r in returns if r > 0)
    return wins / len(returns) * 100


def _bucket(r: float) -> str:
    if r < 0:
        return "neg"
    if r < 0.5:
        return "lt_0_5"
    if r < 1.0:
        return "lt_1_0"
    return "ge_1_0"


def _human_bucket(b: str) -> str:
    return {
        "neg": "음수 갭(추격)",
        "lt_0_5": "진입적기 (r<0.5)",
        "lt_1_0": "분할진입 (0.5≤r<1.0)",
        "ge_1_0": "풀백대기 (r≥1.0)",
    }[b]


def run_backtest(tickers: list[str], period: str = "1y", hold_days: int = 5) -> dict:
    buckets: dict[str, list[float]] = {"neg": [], "lt_0_5": [], "lt_1_0": [], "ge_1_0": []}
    sample_count = 0

    for tk in tickers:
        try:
            df = yf.download(tk, period=period, progress=False, auto_adjust=False)
        except Exception as e:
            print(f"  {tk}: download fail ({e})", file=sys.stderr)
            continue
        if df is None or df.empty or len(df) < 30:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df["atr_pct"] = _atr_pct(df)
        df["sig"] = _strong_signal(df)

        for i in range(20, len(df) - hold_days - 1):
            if not bool(df["sig"].iloc[i]):
                continue
            atr_pct = float(df["atr_pct"].iloc[i] or 0)
            if atr_pct <= 0:
                continue
            cur = float(df["Close"].iloc[i])
            nxt_open = float(df["Open"].iloc[i + 1])
            disc = -_gap_pct(cur, nxt_open)  # 시초가가 위면 disc 음수 (추격)
            r = disc / atr_pct
            ret = _fwd_return(df, i, hold_days)
            if ret is None:
                continue
            buckets[_bucket(r)].append(ret)
            sample_count += 1

    return {
        "tickers": tickers,
        "period": period,
        "hold_days": hold_days,
        "total_samples": sample_count,
        "buckets": {
            b: {
                "n": len(xs),
                "win_rate": round(_win_rate(xs), 2),
                "sharpe": round(_sharpe(xs), 2),
                "max_dd": round(_max_dd(xs), 2),
                "mean_ret": round(statistics.mean(xs), 3) if xs else 0.0,
                "low_sample_warning": len(xs) < 30,
            }
            for b, xs in buckets.items()
        },
    }


def write_report(result: dict, out_path: Path) -> None:
    lines = [
        "# Entry Threshold Backtest (EG-006)",
        "",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"- Tickers: {len(result['tickers'])} ({', '.join(result['tickers'][:10])}{'...' if len(result['tickers']) > 10 else ''})",
        f"- Period: {result['period']}, Hold: {result['hold_days']} days",
        f"- Total samples: {result['total_samples']}",
        "",
        "## Bucket Performance",
        "",
        "| Bucket | n | WinRate(%) | Sharpe (annu.) | MaxDD(%) | MeanRet(%) | 표본 충분 |",
        "|---|---:|---:|---:|---:|---:|:---:|",
    ]
    for b in ["neg", "lt_0_5", "lt_1_0", "ge_1_0"]:
        stats = result["buckets"][b]
        flag = "⚠ 표본 부족" if stats["low_sample_warning"] else "✓"
        lines.append(
            f"| {_human_bucket(b)} | {stats['n']} | {stats['win_rate']} | "
            f"{stats['sharpe']} | {stats['max_dd']} | {stats['mean_ret']} | {flag} |"
        )

    lines.extend([
        "",
        "## 권고",
        "",
        "- `r<0.5` (진입적기) 버킷의 Sharpe/WR 가 가장 높으면 현재 임계값 유지.",
        "- `lt_1_0` 와 `ge_1_0` 간 격차가 크지 않으면 임계값을 1.5 ATR 로 늦춰도 무방.",
        "- 음수 갭(추격) 버킷이 양수 Sharpe 면 추격이 오히려 유효 → 라벨 재설계 필요.",
        "",
        "## Sample Sufficiency",
        "",
    ])
    enough = [b for b, s in result["buckets"].items() if s["n"] >= 30]
    if enough:
        lines.append(f"표본 ≥30 인 버킷: {', '.join(_human_bucket(b) for b in enough)}")
    else:
        lines.append("⚠ 모든 버킷이 표본 30 미만 — 결과는 참고용. 티커 확대 또는 period 늘리기 권장.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="+", default=None)
    ap.add_argument("--period", default="1y")
    ap.add_argument("--hold-days", type=int, default=5)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    tickers = args.tickers or DEFAULT_TICKERS
    print(f"Running backtest on {len(tickers)} tickers, period={args.period}, hold={args.hold_days}d ...")
    t0 = time.time()
    result = run_backtest(tickers, period=args.period, hold_days=args.hold_days)
    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s. Total samples: {result['total_samples']}")
    print(json.dumps(result["buckets"], ensure_ascii=False, indent=2))

    if args.output:
        out = Path(args.output)
    else:
        root = Path(__file__).resolve().parents[2]
        date_str = datetime.now().strftime("%Y-%m-%d")
        out = root / "docs" / "superpowers" / "reports" / f"{date_str}-entry-threshold-backtest.md"
    write_report(result, out)
    print(f"Report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
