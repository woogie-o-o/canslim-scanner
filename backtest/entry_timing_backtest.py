"""
Entry-Timing Score Backtest
===========================

진입 타이밍 점수 로직(quant_nexus_v20.py)이 실제로 더 나은 매수 시점을 골라내는지
과거 일봉으로 검증한다.

- 샘플: quant_nexus_v20.US_DESC / KR_DESC 의 상위 N개 (default 100/100)
- 기간: 최근 3년 일봉 (yfinance)
- 점수: NEW 로직(현재) vs OLD 로직(베이스 50, 임계 70/50) 둘 다 계산
- 평가: 진입 신호(GREEN) 시점 → +5d / +10d / +20d 가격 변화율, 승률, MDD

산출물:
  backtest/reports/entry_timing_YYYYMMDD.csv
  backtest/reports/entry_timing_YYYYMMDD.html
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import json
import math
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

# 프로젝트 루트 import 경로
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("backtest")

CACHE_DIR = _HERE / "cache"
REPORT_DIR = _HERE / "reports"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────
# 1) Ticker universe — quant_nexus_v20 의 DESC dict 에서 추출
# ─────────────────────────────────────────────────────────────────────────
def load_universe(n_us: int = 100, n_kr: int = 100) -> tuple[list[str], list[str]]:
    """quant_nexus_v20 의 US_DESC / KR_DESC 키를 가져온다."""
    from quant_nexus_v20 import QuantNexusApp  # type: ignore
    us = list(QuantNexusApp.US_DESC.keys())[:n_us]
    kr = list(QuantNexusApp.KR_DESC.keys())[:n_kr]
    return us, kr


# ─────────────────────────────────────────────────────────────────────────
# 2) 일봉 캐시 — parquet
# ─────────────────────────────────────────────────────────────────────────
def fetch_history(ticker: str, period: str = "3y") -> pd.DataFrame | None:
    cache_path = CACHE_DIR / f"{ticker.replace('.', '_')}_{period}.parquet"
    if cache_path.exists():
        try:
            df = pd.read_parquet(cache_path)
            if not df.empty:
                return df
        except Exception:
            pass
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(period=period, auto_adjust=True)
        if h is None or h.empty or len(h) < 60:
            return None
        h = h[["Open", "High", "Low", "Close", "Volume"]].copy()
        h.to_parquet(cache_path)
        return h
    except Exception as e:
        log.warning("fetch %s: %s", ticker, e)
        return None


# ─────────────────────────────────────────────────────────────────────────
# 3) 지표 계산 (점수 함수 입력)
# ─────────────────────────────────────────────────────────────────────────
def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).rolling(period).mean()
    down = -delta.clip(upper=0).rolling(period).mean()
    rs = up / down.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _bb_position(close: pd.Series, period: int = 20, std: float = 2.0) -> pd.Series:
    ma = close.rolling(period).mean()
    sd = close.rolling(period).std()
    upper = ma + std * sd
    lower = ma - std * sd
    return (close - ma) / (upper - ma).replace(0, np.nan)  # 0 = mid, +1 upper, -1 lower


def _atr_percent(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat(
        [(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)
    atr = tr.rolling(period).mean()
    return atr / c * 100.0


def _vwap_distance(df: pd.DataFrame, period: int = 20) -> pd.Series:
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    pv = tp * df["Volume"]
    vwap = pv.rolling(period).sum() / df["Volume"].rolling(period).sum().replace(0, np.nan)
    return (df["Close"] - vwap) / vwap


def _macd_div(close: pd.Series) -> pd.Series:
    """간이 MACD 다이버전스 — BULLISH/BEARISH/NONE 문자열 시리즈."""
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    sig = macd.ewm(span=9, adjust=False).mean()
    hist = macd - sig
    out = pd.Series(["NONE"] * len(close), index=close.index, dtype=object)
    # 히스토그램 부호 전환 = 간이 다이버전스
    out[(hist > 0) & (hist.shift() <= 0)] = "BULLISH"
    out[(hist < 0) & (hist.shift() >= 0)] = "BEARISH"
    return out


def _regime(close: pd.Series) -> pd.Series:
    """SMA200 대비 위치 + 최근 60일 수익률로 간이 레짐 추정."""
    sma200 = close.rolling(200).mean()
    ret60 = close.pct_change(60)
    reg = pd.Series(["SIDEWAYS"] * len(close), index=close.index, dtype=object)
    reg[(close > sma200) & (ret60 > 0.15)] = "STRONG_BULL"
    reg[(close > sma200) & (ret60 > 0.03) & (ret60 <= 0.15)] = "BULL"
    reg[(close < sma200) & (ret60 < -0.15)] = "STRONG_BEAR"
    reg[(close < sma200) & (ret60 < -0.03) & (ret60 >= -0.15)] = "BEAR"
    return reg


def _high_52w(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    """returns (near_52w_high bool, dist_from_52w_high)."""
    hi = close.rolling(252, min_periods=60).max()
    dist = (hi - close) / hi
    return dist <= 0.05, dist


def _pivot_breakout(close: pd.Series, vol: pd.Series) -> pd.Series:
    """20일 고가 돌파 + 거래량 1.5x."""
    hi20 = close.rolling(20).max().shift(1)
    vavg = vol.rolling(20).mean().shift(1)
    return (close > hi20) & (vol > vavg * 1.5)


def _s_confirmed(vol: pd.Series) -> pd.Series:
    """거래량 동반 — 당일 거래량 > 20일평균 * 1.3."""
    return vol > vol.rolling(20).mean() * 1.3


# ─────────────────────────────────────────────────────────────────────────
# 4) 점수 함수 (NEW = 현재 quant_nexus_v20 로직, OLD = 이전 로직)
# ─────────────────────────────────────────────────────────────────────────
def score_new(row: dict) -> int:
    """현재 점수 로직 (베이스 55, 임계 65/45)."""
    s = 55
    rsi = row["rsi"]
    bb = row["bb"]
    vwap_d = row["vwap_d"]
    atr_p = row["atr_p"]
    near52 = row["near52"]
    pivot = row["pivot"]
    s_conf = row["s_conf"]
    macd_div = row["macd_div"]
    reg = row["reg"]
    day_chg = row["day_chg"]

    # RSI
    if rsi < 30: s += 15
    elif rsi < 40: s += 10
    elif rsi < 55: s += 4
    elif rsi < 70: s += 5
    elif rsi < 80: s -= 3
    else: s -= 12

    # BB
    if bb < -0.7: s += 10
    elif bb < -0.3: s += 5
    elif bb < 0.5: s += 2
    elif bb < 0.85: s += 0
    else: s -= 6

    # VWAP
    if vwap_d > 0.08: s -= 5
    elif vwap_d >= 0.0: s += 3
    elif vwap_d >= -0.03: s += 6
    else: s -= 2

    # ATR
    if atr_p < 2.0: s += 2
    elif atr_p < 5.0: s += 4
    elif atr_p < 8.0: s -= 2
    else: s -= 8

    # 52w
    if pivot and s_conf: s += 16
    elif near52 and s_conf: s += 10
    elif near52: s += 3

    # MACD
    if macd_div == "BULLISH": s += 10
    elif macd_div == "BEARISH": s -= 8

    # regime
    if reg == "STRONG_BULL": s += 8
    elif reg == "BULL": s += 5
    elif reg == "BEAR": s -= 10
    elif reg == "STRONG_BEAR": s -= 18

    # day_chg
    if day_chg > 0.10: s -= 8
    elif day_chg > 0.07: s -= 4
    elif day_chg < -0.05: s += 4

    return max(0, min(100, int(s)))


def score_old(row: dict) -> int:
    """이전 로직 추정치 — 베이스 50, mean-reversion 편향(RSI 55+ 감점)."""
    s = 50
    rsi = row["rsi"]
    bb = row["bb"]
    vwap_d = row["vwap_d"]
    atr_p = row["atr_p"]
    near52 = row["near52"]
    pivot = row["pivot"]
    s_conf = row["s_conf"]
    macd_div = row["macd_div"]
    reg = row["reg"]
    day_chg = row["day_chg"]

    if rsi < 30: s += 15
    elif rsi < 40: s += 10
    elif rsi < 55: s += 3
    elif rsi < 70: s -= 3
    else: s -= 12

    if bb < -0.7: s += 10
    elif bb < -0.3: s += 5
    elif bb > 0.85: s -= 6
    elif bb > 0.5: s -= 2

    if vwap_d > 0.05: s -= 5
    elif vwap_d >= 0.0: s += 2
    elif vwap_d >= -0.03: s += 5
    else: s -= 2

    if atr_p > 8.0: s -= 10
    elif atr_p > 5.0: s -= 3

    if pivot and s_conf: s += 12
    elif near52 and s_conf: s += 6

    if macd_div == "BULLISH": s += 8
    elif macd_div == "BEARISH": s -= 8

    if reg == "STRONG_BULL": s += 5
    elif reg == "BULL": s += 3
    elif reg == "BEAR": s -= 8
    elif reg == "STRONG_BEAR": s -= 15

    if day_chg > 0.07: s -= 10
    elif day_chg > 0.04: s -= 5
    elif day_chg < -0.05: s += 5

    return max(0, min(100, int(s)))


def grade(score: int, hi: int, lo: int) -> str:
    if score >= hi: return "GREEN"
    if score >= lo: return "YELLOW"
    return "RED"


# ─────────────────────────────────────────────────────────────────────────
# 5) 종목별 백테스트
# ─────────────────────────────────────────────────────────────────────────
def backtest_ticker(ticker: str, df: pd.DataFrame) -> pd.DataFrame:
    if len(df) < 260:
        return pd.DataFrame()

    close = df["Close"]
    vol = df["Volume"]

    rsi = _rsi(close)
    bb = _bb_position(close)
    atr_p = _atr_percent(df)
    vwap_d = _vwap_distance(df)
    macd_div = _macd_div(close)
    reg = _regime(close)
    near52, dist52 = _high_52w(close)
    pivot = _pivot_breakout(close, vol)
    s_conf = _s_confirmed(vol)
    day_chg = close.pct_change(fill_method=None)

    # 전방 수익률
    fwd5 = close.pct_change(5).shift(-5)
    fwd10 = close.pct_change(10).shift(-10)
    fwd20 = close.pct_change(20).shift(-20)

    # 20일 최대낙폭 (진입 후)
    def max_dd_next(n: int) -> pd.Series:
        # 다음 n일 내 (저가-종가)/종가 최저값
        low = df["Low"]
        out = pd.Series(np.nan, index=df.index)
        c = close.values
        l = low.values
        for i in range(len(df) - n):
            window_low = l[i+1:i+1+n].min() if (i+1+n) <= len(df) else np.nan
            if not np.isnan(window_low):
                out.iat[i] = (window_low - c[i]) / c[i]
        return out

    mdd20 = max_dd_next(20)

    rows = []
    idx = df.index
    for i in range(220, len(df) - 20):  # SMA200 + 전방 20일 확보
        feat = dict(
            rsi=rsi.iat[i] if not math.isnan(rsi.iat[i]) else 50.0,
            bb=bb.iat[i] if not math.isnan(bb.iat[i]) else 0.0,
            vwap_d=vwap_d.iat[i] if not math.isnan(vwap_d.iat[i]) else 0.0,
            atr_p=atr_p.iat[i] if not math.isnan(atr_p.iat[i]) else 3.0,
            near52=bool(near52.iat[i]),
            pivot=bool(pivot.iat[i]),
            s_conf=bool(s_conf.iat[i]),
            macd_div=str(macd_div.iat[i]),
            reg=str(reg.iat[i]),
            day_chg=day_chg.iat[i] if not math.isnan(day_chg.iat[i]) else 0.0,
        )
        sn = score_new(feat)
        so = score_old(feat)
        rows.append(dict(
            ticker=ticker,
            date=idx[i],
            score_new=sn,
            grade_new=grade(sn, 65, 45),
            score_old=so,
            grade_old=grade(so, 70, 50),
            fwd5=fwd5.iat[i],
            fwd10=fwd10.iat[i],
            fwd20=fwd20.iat[i],
            mdd20=mdd20.iat[i],
        ))
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────
# 6) 집계 + 리포트
# ─────────────────────────────────────────────────────────────────────────
def aggregate(df: pd.DataFrame) -> dict:
    out = {}
    for variant, gcol, scol in [("new", "grade_new", "score_new"),
                                 ("old", "grade_old", "score_old")]:
        rec = {}
        for g in ("GREEN", "YELLOW", "RED"):
            sub = df[df[gcol] == g]
            if sub.empty:
                rec[g] = dict(n=0)
                continue
            rec[g] = dict(
                n=int(len(sub)),
                avg_fwd5=float(sub["fwd5"].mean() * 100),
                avg_fwd10=float(sub["fwd10"].mean() * 100),
                avg_fwd20=float(sub["fwd20"].mean() * 100),
                win10=float((sub["fwd10"] > 0).mean() * 100),
                win20=float((sub["fwd20"] > 0).mean() * 100),
                avg_mdd20=float(sub["mdd20"].mean() * 100),
            )
        # baseline (전체 평균)
        rec["ALL"] = dict(
            n=int(len(df)),
            avg_fwd5=float(df["fwd5"].mean() * 100),
            avg_fwd10=float(df["fwd10"].mean() * 100),
            avg_fwd20=float(df["fwd20"].mean() * 100),
            win10=float((df["fwd10"] > 0).mean() * 100),
            win20=float((df["fwd20"] > 0).mean() * 100),
            avg_mdd20=float(df["mdd20"].mean() * 100),
        )
        out[variant] = rec

    # 임계값 sweep — 점수 ≥ thr 진입 시 +10d 평균
    sweep = []
    for thr in range(30, 91, 5):
        sub_new = df[df["score_new"] >= thr]
        sub_old = df[df["score_old"] >= thr]
        sweep.append(dict(
            thr=thr,
            n_new=int(len(sub_new)),
            avg10_new=float(sub_new["fwd10"].mean() * 100) if len(sub_new) else None,
            win10_new=float((sub_new["fwd10"] > 0).mean() * 100) if len(sub_new) else None,
            n_old=int(len(sub_old)),
            avg10_old=float(sub_old["fwd10"].mean() * 100) if len(sub_old) else None,
            win10_old=float((sub_old["fwd10"] > 0).mean() * 100) if len(sub_old) else None,
        ))
    out["sweep"] = sweep
    return out


def render_html(agg: dict, total_rows: int, out_path: Path) -> None:
    def fmt(v, suffix="%"):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "—"
        return f"{v:.2f}{suffix}"

    def grade_table(rec: dict, label: str) -> str:
        rows = ""
        for g in ("GREEN", "YELLOW", "RED", "ALL"):
            r = rec.get(g, {})
            rows += (
                f"<tr><td>{g}</td><td>{r.get('n', 0):,}</td>"
                f"<td>{fmt(r.get('avg_fwd5'))}</td>"
                f"<td>{fmt(r.get('avg_fwd10'))}</td>"
                f"<td>{fmt(r.get('avg_fwd20'))}</td>"
                f"<td>{fmt(r.get('win10'))}</td>"
                f"<td>{fmt(r.get('win20'))}</td>"
                f"<td>{fmt(r.get('avg_mdd20'))}</td></tr>"
            )
        return (
            f"<h3>{label}</h3>"
            "<table><thead><tr><th>등급</th><th>n</th>"
            "<th>+5d</th><th>+10d</th><th>+20d</th>"
            "<th>승률(+10d)</th><th>승률(+20d)</th><th>MDD20</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )

    sweep_rows = "".join(
        f"<tr><td>{r['thr']}</td>"
        f"<td>{r['n_new']:,}</td><td>{fmt(r['avg10_new'])}</td><td>{fmt(r['win10_new'])}</td>"
        f"<td>{r['n_old']:,}</td><td>{fmt(r['avg10_old'])}</td><td>{fmt(r['win10_old'])}</td>"
        "</tr>"
        for r in agg["sweep"]
    )

    html = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>Entry Timing Backtest</title>
<style>
  body {{ font-family: -apple-system, 'Segoe UI', sans-serif; padding: 24px; max-width: 1100px; margin: auto; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0 28px; font-size: 13px; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: right; }}
  th {{ background: #f4f4f4; }}
  td:first-child, th:first-child {{ text-align: left; }}
  h1 {{ margin-bottom: 4px; }}
  .meta {{ color: #666; margin-bottom: 24px; }}
  .summary {{ background: #f8fafc; border-left: 4px solid #2563eb; padding: 12px 16px; margin: 16px 0; }}
</style></head><body>
<h1>진입 타이밍 점수 백테스트</h1>
<div class="meta">총 관측치: {total_rows:,}건 · 기준일: {datetime.now():%Y-%m-%d %H:%M}</div>

<div class="summary">
  <strong>읽는 법</strong> — 각 시점에서 점수를 계산하고 그 후 +5/+10/+20일 수익률을 측정함.
  GREEN 진입 시점의 평균 수익률이 ALL(무작위 진입)보다 높고, RED보다 명확히 높아야 점수가 유의미함.
</div>

{grade_table(agg["new"], "NEW 로직 (베이스 55 · 임계 65/45 · 추세 추종)")}
{grade_table(agg["old"], "OLD 로직 (베이스 50 · 임계 70/50 · 평균회귀)")}

<h3>임계값 sweep (+10d 평균 수익률)</h3>
<table><thead><tr>
  <th>점수≥</th>
  <th>n (NEW)</th><th>+10d (NEW)</th><th>승률 (NEW)</th>
  <th>n (OLD)</th><th>+10d (OLD)</th><th>승률 (OLD)</th>
</tr></thead><tbody>{sweep_rows}</tbody></table>

</body></html>
"""
    out_path.write_text(html, encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────
# 7) 메인
# ─────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-us", type=int, default=100)
    ap.add_argument("--n-kr", type=int, default=100)
    ap.add_argument("--period", default="3y")
    args = ap.parse_args()

    us, kr = load_universe(args.n_us, args.n_kr)
    tickers = us + kr
    log.info("universe: %d US + %d KR = %d", len(us), len(kr), len(tickers))

    all_rows = []
    failed = []
    t0 = time.time()
    for i, t in enumerate(tickers, 1):
        h = fetch_history(t, period=args.period)
        if h is None:
            failed.append(t)
            continue
        try:
            r = backtest_ticker(t, h)
            if not r.empty:
                all_rows.append(r)
        except Exception as e:
            log.warning("backtest %s: %s", t, e)
            failed.append(t)
        if i % 20 == 0:
            log.info("[%d/%d] elapsed %.1fs", i, len(tickers), time.time() - t0)

    if not all_rows:
        log.error("no data")
        sys.exit(1)

    df = pd.concat(all_rows, ignore_index=True)
    log.info("total observations: %d (failed: %d)", len(df), len(failed))

    today = date.today().isoformat()
    csv_path = REPORT_DIR / f"entry_timing_{today}.csv"
    df.to_csv(csv_path, index=False)
    log.info("csv: %s", csv_path)

    agg = aggregate(df)
    html_path = REPORT_DIR / f"entry_timing_{today}.html"
    render_html(agg, len(df), html_path)
    log.info("html: %s", html_path)

    # 콘솔 요약
    print("\n=== NEW 로직 ===")
    for g in ("GREEN", "YELLOW", "RED", "ALL"):
        r = agg["new"].get(g, {})
        if r.get("n", 0):
            print(f"  {g:6s} n={r['n']:6d}  +10d={r['avg_fwd10']:+.2f}%  win={r['win10']:.1f}%  MDD={r['avg_mdd20']:.2f}%")
    print("\n=== OLD 로직 ===")
    for g in ("GREEN", "YELLOW", "RED", "ALL"):
        r = agg["old"].get(g, {})
        if r.get("n", 0):
            print(f"  {g:6s} n={r['n']:6d}  +10d={r['avg_fwd10']:+.2f}%  win={r['win10']:.1f}%  MDD={r['avg_mdd20']:.2f}%")


if __name__ == "__main__":
    main()
