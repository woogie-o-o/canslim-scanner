#!/usr/bin/env python3
"""
레짐 분류기 백테스트 — HMM 우선, rule-based 폴백
KOSPI 5년치 워크포워드 검증
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import os, logging
import numpy as np
import pandas as pd

logging.basicConfig(level=logging.WARNING)

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import yfinance as yf
from regime_classifier import (
    compute_features, standardize_features, REGIME_CONFIG,
    R_BULL, R_BEAR, R_CHOP,
    _fit_hmm, _is_degenerate, _build_state_map,
    _forward_posteriors, _alpha_to_regime_df, _compute_transition_signal,
    _HAS_HMM,
)

SEP = "=" * 57

def pct(v): return f"{v:+.2%}" if np.isfinite(v) else "   N/A"

# ── rule-based 단일 bar 분류 ──────────────────────────────────────────────────
def rule_classify(row, rvol_hist):
    rvol_pct = float((rvol_hist <= row["rvol_20"]).mean())
    dd  = float(row["dd"])
    eff = float(row["eff"]) if np.isfinite(row["eff"]) else 0.0
    mom = float(row["mom"]) if np.isfinite(row["mom"]) else 0.0
    if rvol_pct >= 0.70 and dd <= -0.05:   return R_BEAR
    elif eff < 0.30 and rvol_pct < 0.70:   return R_CHOP
    elif mom > 0 and rvol_pct < 0.60 and dd > -0.10: return R_BULL
    elif dd <= -0.08:                       return R_BEAR
    else:                                   return R_CHOP

# ── HMM 전체 기간 forward-filter → prob_hist 반환 ────────────────────────────
def build_hmm_prob_hist(feat, z):
    cols = list(REGIME_CONFIG["features"])
    Z    = z[cols].dropna()
    X    = Z.to_numpy()

    seeds = [42 + i for i in range(8)]
    model = _fit_hmm(X, 3, REGIME_CONFIG, seeds)
    if model is None or _is_degenerate(model, X, 3):
        model = _fit_hmm(X, 2, REGIME_CONFIG, seeds)
        if model is None:
            return None, None, None

    n_states  = model.n_components
    state_map = _build_state_map(model, feat.loc[Z.index], X, n_states)
    alpha     = _forward_posteriors(model, X)
    prob_hist = _alpha_to_regime_df(alpha, state_map, Z.index)

    A             = np.asarray(model.transmat_)
    pnext_alpha   = alpha @ A   # (T, K)

    return prob_hist, pnext_alpha, state_map

def main():
    print(SEP)
    mode = "HMM" if _HAS_HMM else "rule-based"
    print(f"  레짐 분류기 백테스트 ({mode}) — KOSPI 5년")
    print(SEP)

    # ── 1. 데이터 ─────────────────────────────────────────
    print("▶ 데이터 다운로드 (^KS11, 5y)...")
    ohlcv = yf.Ticker("^KS11").history(period="5y", interval="1d")
    print(f"  총 {len(ohlcv)}거래일\n")

    feat  = compute_features(ohlcv, REGIME_CONFIG)
    z     = standardize_features(feat, REGIME_CONFIG)
    close = ohlcv["Close"].reindex(feat.index).astype(float)

    # ── 2. 레짐 분류 ──────────────────────────────────────
    use_hmm = False
    if _HAS_HMM:
        print("▶ HMM 학습 중 (8-restart)...")
        prob_hist, pnext_alpha, state_map = build_hmm_prob_hist(feat, z)
        if prob_hist is not None:
            use_hmm = True
            print(f"  완료 (HMM, {len(prob_hist)}일)\n")
        else:
            print("  HMM 수렴 실패 → rule-based 폴백\n")

    if not use_hmm:
        print("▶ rule-based 워크포워드 분류 중...")
        feat2   = feat.dropna(subset=["rvol_20", "dd", "eff", "mom"])
        close   = close.reindex(feat2.index)
        WARMUP  = 63
        labels  = []
        for i in range(len(feat2)):
            if i < WARMUP:
                labels.append(R_CHOP); continue
            hist = feat2["rvol_20"].iloc[:i+1].dropna().to_numpy()
            labels.append(rule_classify(feat2.iloc[i], hist))
        feat2["regime"] = labels
        print(f"  완료: {feat2['regime'].value_counts().to_dict()}\n")

    # ── 3. 레짐별 수익률 ─────────────────────────────────
    print(SEP)
    print("  레짐별 평균 수익률")
    print(SEP)
    print(f"  {'레짐':<8} {'1일':>8} {'5일':>8} {'20일':>9} {'일수':>6}  {'vs 전체'}")
    print(f"  {'-'*8} {'-'*8} {'-'*8} {'-'*9} {'-'*6}  {'-'*8}")

    bm20 = (close.shift(-20) / close - 1).mean()
    bm5  = (close.shift(-5)  / close - 1).mean()
    bm1  = (close.shift(-1)  / close - 1).mean()

    for r, short in [(R_BULL,"Bull"), (R_BEAR,"Bear"), (R_CHOP,"Chop")]:
        if use_hmm:
            mask = prob_hist[r].reindex(close.index) > 0.5
        else:
            mask = feat2["regime"] == r
        n   = int(mask.sum())
        r1  = (close.shift(-1)  / close - 1)[mask].mean()
        r5  = (close.shift(-5)  / close - 1)[mask].mean()
        r20 = (close.shift(-20) / close - 1)[mask].mean()
        edge = f"{(r20-bm20)*100:+.2f}%p" if np.isfinite(r20) else ""
        print(f"  {short:<8} {pct(r1):>8} {pct(r5):>8} {pct(r20):>9} {n:>6}  {edge}")
    print(f"  {'(전체)':<8} {pct(bm1):>8} {pct(bm5):>8} {pct(bm20):>9}")

    # ── 4. early 신호 (HMM 전용) ──────────────────────────
    if use_hmm:
        print(f"\n{SEP}")
        print("  early 신호 적중률 (HMM forward-filter)")
        print(SEP)

        WARMUP = 252
        records, prev_type, prev_idx = [], None, -999

        for i in range(WARMUP, len(prob_hist)):
            sub = prob_hist.iloc[:i+1]

            pnext_vec = pnext_alpha[
                list(prob_hist.index).index(prob_hist.index[i])
                if prob_hist.index[i] in prob_hist.index else i
            ] if i < len(pnext_alpha) else pnext_alpha[-1]

            from regime_classifier import _state_vec_to_regime
            pnext_map = {}
            for s, v in enumerate(pnext_vec):
                r = state_map.get(s, R_CHOP)
                pnext_map[r] = pnext_map.get(r, 0.0) + float(v)

            sig = _compute_transition_signal(sub, pnext_map, REGIME_CONFIG)

            if sig["early_long"] or sig["early_exit"]:
                stype = "early_long" if sig["early_long"] else "early_exit"
                if stype == prev_type and (i - prev_idx) <= 3:
                    continue
                cl = float(close.iloc[close.index.get_loc(prob_hist.index[i])] \
                     if prob_hist.index[i] in close.index else np.nan)
                records.append({"idx": i, "date": prob_hist.index[i],
                                "type": stype, "close": cl})
                prev_type, prev_idx = stype, i

        sig_df  = pd.DataFrame(records)
        total_y = len(prob_hist) / 252

        if sig_df.empty:
            print("  신호 발생 없음 (기간 내)")
        else:
            for stype, direction, label in [
                ("early_long",  1, "풀시드 진입"),
                ("early_exit", -1, "전액 현금화"),
            ]:
                sub = sig_df[sig_df["type"] == stype]
                print(f"\n  [{label}]  총 {len(sub)}회  ({len(sub)/total_y:.1f}회/년)")
                if sub.empty: continue

                print(f"  {'기간':<5} {'평균수익':>9} {'적중률':>8} {'최악':>9} {'최선':>9}")
                print(f"  {'-'*5} {'-'*9} {'-'*8} {'-'*9} {'-'*9}")

                for d in [1, 5, 20]:
                    rets = []
                    for _, row in sub.iterrows():
                        date = row["date"]
                        if date not in close.index: continue
                        ix = close.index.get_loc(date)
                        if ix + d < len(close):
                            rets.append(float(close.iloc[ix+d] / close.iloc[ix] - 1))
                    if not rets: continue
                    rets = np.array(rets)
                    print(f"  {d:2d}일  {pct(rets.mean()):>9} {(rets*direction>0).mean():>7.0%}"
                          f"  {pct(rets.min()):>9} {pct(rets.max()):>9}")

            # whipsaw
            print(f"\n{SEP}")
            print("  허수 신호(whipsaw) 분석")
            print(SEP)
            ws = sum(
                1 for i in range(1, len(sig_df))
                if sig_df.iloc[i]["type"] != sig_df.iloc[i-1]["type"]
                and (sig_df.iloc[i]["date"] - sig_df.iloc[i-1]["date"]).days <= 10
            )
            print(f"  전체 신호: {len(sig_df)}회  |  10일 내 역전환: {ws}회 ({ws/len(sig_df):.0%})")

            # 최근 이력
            print(f"\n{SEP}")
            print("  최근 신호 이력 (최대 10개)")
            print(SEP)
            for _, r in sig_df.tail(10).iterrows():
                arrow = "🚀" if r["type"] == "early_long" else "⚠️ "
                print(f"  {arrow} {r['date'].date()}  {r['type']:<12}  KOSPI={r['close']:.0f}")

    # ── 5. rule-based 전환 신호 (HMM 없을 때) ────────────────────────────────
    else:
        print(f"\n{SEP}")
        print("  레짐 전환 신호 적중률 (rule-based)")
        print(SEP)
        reg_arr = np.array(feat2["regime"].tolist())
        transitions = []
        for i in range(3, len(reg_arr)):
            prev3 = reg_arr[i-3:i]
            cur   = reg_arr[i]
            if len(set(prev3)) == 1 and prev3[0] != cur:
                old = prev3[0]
                if old in (R_BEAR, R_CHOP) and cur == R_BULL:
                    transitions.append({"idx":i,"date":feat2.index[i],"type":"entry","close":float(close.iloc[i])})
                elif old == R_BULL and cur in (R_BEAR, R_CHOP):
                    transitions.append({"idx":i,"date":feat2.index[i],"type":"exit","close":float(close.iloc[i])})
        tr_df = pd.DataFrame(transitions)
        total_y = len(feat2) / 252
        for stype, direction, label in [("entry",1,"매수"),("exit",-1,"매도")]:
            sub = tr_df[tr_df["type"]==stype] if not tr_df.empty else pd.DataFrame()
            print(f"\n  [{label}]  총 {len(sub)}회 ({len(sub)/total_y:.1f}회/년)")
            for d in [1,5,20]:
                rets=[float(close.iloc[int(r["idx"])+d]/close.iloc[int(r["idx"])]-1)
                      for _,r in sub.iterrows() if int(r["idx"])+d<len(close)]
                if not rets: continue
                rets=np.array(rets)
                print(f"  {d:2d}일  {pct(rets.mean()):>9} {(rets*direction>0).mean():>7.0%}"
                      f"  {pct(rets.min()):>9} {pct(rets.max()):>9}")

    print(f"\n{SEP}")
    print(f"  완료  |  모드: {'HMM ✅' if use_hmm else 'rule-based ⚠️'}")
    print(SEP)

if __name__ == "__main__":
    main()
