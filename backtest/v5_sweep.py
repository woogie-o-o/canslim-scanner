"""
V5_DECORRELATED 파라미터 스윕
=============================
목적: STRONG 임계, base, 가중치를 흔들어 최적 entry 신호 찾기.

평가 척도:
  - edge20  = 신호평균 20d ret − base20  (커야 좋음)
  - win20   = 20일 승률 (>=55% 목표)
  - fire    = 발화 빈도 (5~15% 목표; 너무 적으면 통계 불안, 너무 많으면 필터 무용)
  - sharpe  = edge20 / std(fwd20)  (정보비율)

종합 점수: composite = edge20_pp × min(fire/5, 1) × min(fire/30, 1) × (1 if win>=53 else 0.5)
"""
from __future__ import annotations
import sys, io, json, os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
REPORT = ROOT / "reports"
REPORT.mkdir(exist_ok=True)

# signal_diagnostic 에서 compute_signals 재사용 (이쪽 import 가 stdout wrap 함)
sys.path.insert(0, str(ROOT))
from signal_diagnostic import compute_signals, load_all_parquets  # noqa


def score_v5(sig: pd.DataFrame, *,
             base: int = 30,
             # MeanRev 가중치
             rsi_low_pts: int = 16, rsi_mid_pts: int = 9, rsi_hi_pts: int = -14,
             bb_low_pts: int = 14, bb_hi_pts: int = -10,
             vwap_bonus: int = 4, vwap_pen: int = -6,
             # Trend
             trend_strong: int = 10, trend_align: int = 6, trend_bear: int = -8,
             # Volume
             voljump_pts: int = 12,
             # MACD
             macd_bull: int = 3, macd_bear: int = -4,
             # ATR / DayChg
             atr_pen: int = -10, chase_pen: int = -10, dip_bonus: int = 4,
             ) -> pd.Series:
    s = pd.Series(float(base), index=sig.index)

    rsi = sig["RSI"].values
    bbp = sig["BBPos"].values
    vd  = sig["VWAPDist"].values

    # MeanRev composite
    rsi_pts = np.where(rsi < 30, rsi_low_pts,
              np.where(rsi < 40, rsi_mid_pts,
              np.where(rsi >= 70, rsi_hi_pts, 0)))
    bb_pts  = np.where(bbp < -0.7, bb_low_pts,
              np.where(bbp > 0.95, bb_hi_pts, 0))
    vwap_pos = np.where((vd >= -0.03) & (vd <= 0.02), vwap_bonus, 0)
    vwap_neg = np.where(vd > 0.07, vwap_pen, 0)

    pos_mr = np.maximum(np.maximum(rsi_pts, bb_pts), 0)
    neg_mr = np.minimum(np.minimum(rsi_pts, bb_pts), vwap_neg)
    neg_mr = np.minimum(neg_mr, 0)
    vwap_b = np.where((pos_mr == 0) & (neg_mr == 0), vwap_pos, 0)
    s += pos_mr + neg_mr + vwap_b

    # Trend
    ma  = sig["MAAlign"].astype(bool).values
    reg = sig["RegimeBull"].astype(bool).values
    s += np.where(ma & reg, trend_strong,
         np.where(ma, trend_align,
         np.where(~reg, trend_bear, 0)))

    # Volume jump
    s += np.where(sig["VolJump"].astype(bool).values, voljump_pts, 0)

    # MACD
    s += np.where(sig["MACDBull"].astype(bool).values, macd_bull, macd_bear)

    # ATR
    s += np.where(sig["ATRPct"].values > 8.0, atr_pen, 0)

    # DayChg
    dc = sig["DayChg"].values
    s += np.where(dc > 0.07, chase_pen,
         np.where(dc < -0.05, dip_bonus, 0))

    return pd.Series(np.clip(s.values, 0, 100), index=sig.index)


def assemble_universe(market: str):
    data = load_all_parquets(market)
    frames = []
    for tk, df in data.items():
        try:
            sig = compute_signals(df)
            fwd5  = df["Close"].pct_change(5).shift(-5)
            fwd10 = df["Close"].pct_change(10).shift(-10)
            fwd20 = df["Close"].pct_change(20).shift(-20)
            sig["Fwd5"]  = fwd5
            sig["Fwd10"] = fwd10
            sig["Fwd20"] = fwd20
            sig = sig.dropna(subset=["RSI", "MAAlign", "Fwd20"])
            frames.append(sig)
        except Exception:
            continue
    if not frames:
        return None
    return pd.concat(frames, axis=0)


def evaluate(sig_all: pd.DataFrame, score: pd.Series, threshold: int):
    fwd20 = sig_all["Fwd20"]
    base20 = fwd20.mean() * 100
    mask = score >= threshold
    n = int(mask.sum())
    if n < 100:
        return None
    sub = fwd20[mask]
    avg = sub.mean() * 100
    win = (sub > 0).mean() * 100
    fire = n / len(score) * 100
    edge = avg - base20
    sd = sub.std() * 100
    sharpe = edge / sd if sd > 0 else 0.0
    fire_score = min(fire / 5, 1.0) * min(15 / max(fire, 1), 1.0)
    win_factor = 1.0 if win >= 53 else 0.5
    composite = edge * fire_score * win_factor
    return dict(threshold=threshold, n=n, fire=fire, edge=edge, win=win,
                avg=avg, base=base20, sharpe=sharpe, composite=composite)


def sweep(market: str):
    print(f"\n{'='*70}\n[{market}] V5 그리드 스윕\n{'='*70}")
    big = assemble_universe(market)
    if big is None:
        print(f"  [skip] {market} 데이터 없음")
        return []
    print(f"  유효 관측치: {len(big):,}")

    # 그리드 정의
    base_grid     = [25, 30, 35, 40]
    voljump_grid  = [8, 12, 16]
    trend_grid    = [8, 10, 12]      # trend_strong
    macd_pen_grid = [-2, -4, -6]
    thresholds    = [50, 55, 58, 60, 62, 65, 68]

    results = []
    total = len(base_grid) * len(voljump_grid) * len(trend_grid) * len(macd_pen_grid)
    done = 0
    for base in base_grid:
        for vj in voljump_grid:
            for tr in trend_grid:
                for mp in macd_pen_grid:
                    sc = score_v5(big,
                                  base=base,
                                  voljump_pts=vj,
                                  trend_strong=tr,
                                  macd_bear=mp)
                    for th in thresholds:
                        ev = evaluate(big, sc, th)
                        if ev is None:
                            continue
                        ev["base"] = base
                        ev["voljump"] = vj
                        ev["trend_strong"] = tr
                        ev["macd_bear"] = mp
                        ev["mkt"] = market
                        results.append(ev)
                    done += 1
        print(f"  진행 base={base} 완료 ({done}/{total})")
    return results


def report(rows: list[dict], market: str):
    if not rows:
        return
    df = pd.DataFrame(rows)
    # 1) 발화 5~15% & win>=53 & edge>0 인 후보만
    cand = df[(df.fire >= 5) & (df.fire <= 20) & (df.win >= 53) & (df.edge > 0)]
    print(f"\n  [{market}] 후보 (fire 5-20%, win>=53%, edge>0): {len(cand)}건")
    if cand.empty:
        # 폴백: edge 만으로 top
        cand = df[df.edge > 0].sort_values("composite", ascending=False).head(20)
        print(f"  → 폴백 (edge>0 전체 상위 20):")
    top = cand.sort_values("composite", ascending=False).head(10)
    cols = ["threshold", "base", "voljump", "trend_strong", "macd_bear",
            "fire", "win", "edge", "sharpe", "composite"]
    print(top[cols].to_string(index=False,
                              formatters={"fire":  "{:.1f}%".format,
                                          "win":   "{:.1f}%".format,
                                          "edge":  "{:+.2f}%p".format,
                                          "sharpe":"{:+.3f}".format,
                                          "composite":"{:.3f}".format}))
    return top


def main():
    all_rows = []
    bests = {}
    for mkt in ("KR", "US"):
        rows = sweep(mkt)
        all_rows.extend(rows)
        top = report(rows, mkt)
        if top is not None and len(top) > 0:
            bests[mkt] = top.iloc[0].to_dict()

    # 저장
    out_path = REPORT / "v5_sweep_results.json"
    out_path.write_text(json.dumps({
        "all": all_rows,
        "best": {k: {kk: (float(vv) if isinstance(vv, (np.floating, np.integer)) else vv)
                     for kk, vv in v.items()} for k, v in bests.items()}
    }, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print(f"\n저장: {out_path}")

    # ── 통합 추천 (KR+US 동시 만족) ──
    print(f"\n{'='*70}\n[통합 추천] KR/US 둘 다 양호한 설정\n{'='*70}")
    if not all_rows:
        return
    df = pd.DataFrame(all_rows)
    pivot_keys = ["threshold", "base", "voljump", "trend_strong", "macd_bear"]
    # 그룹별로 KR/US 둘 다 있는 행만
    g = df.groupby(pivot_keys + ["mkt"]).agg(
        fire=("fire", "first"), win=("win", "first"),
        edge=("edge", "first"), composite=("composite", "first")).reset_index()
    kr = g[g.mkt == "KR"].drop(columns="mkt").rename(
        columns={"fire":"fire_KR","win":"win_KR","edge":"edge_KR","composite":"c_KR"})
    us = g[g.mkt == "US"].drop(columns="mkt").rename(
        columns={"fire":"fire_US","win":"win_US","edge":"edge_US","composite":"c_US"})
    merged = kr.merge(us, on=pivot_keys, how="inner")
    merged["c_sum"] = merged["c_KR"] + merged["c_US"]
    merged = merged[(merged.edge_KR > 0) & (merged.edge_US > 0)
                    & (merged.fire_KR >= 5) & (merged.fire_KR <= 20)
                    & (merged.fire_US >= 5) & (merged.fire_US <= 20)]
    if merged.empty:
        print("  KR+US 동시 만족 설정 없음 — 시장별 분리 권장")
        return
    out = merged.sort_values("c_sum", ascending=False).head(8)
    cols = pivot_keys + ["fire_KR","win_KR","edge_KR","fire_US","win_US","edge_US","c_sum"]
    print(out[cols].to_string(index=False,
                              formatters={"fire_KR":"{:.1f}%".format,
                                          "fire_US":"{:.1f}%".format,
                                          "win_KR":"{:.1f}%".format,
                                          "win_US":"{:.1f}%".format,
                                          "edge_KR":"{:+.2f}%p".format,
                                          "edge_US":"{:+.2f}%p".format,
                                          "c_sum":"{:.3f}".format}))


if __name__ == "__main__":
    main()
