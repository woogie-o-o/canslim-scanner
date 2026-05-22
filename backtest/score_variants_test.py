"""
Score 함수 변종 비교 테스트
=========================

캐시된 일봉으로 여러 점수 함수 후보를 동시에 평가하고,
단조 신호(GREEN > YELLOW > RED)와 edge(GREEN +10d - RED +10d)를 비교한다.

후보:
  V1 = OLD          (베이스 50, 평균회귀)
  V2 = NEW          (현재 - 베이스 55, 추세 추종)
  V3 = OLD_pure     (OLD에서 추세 가점 제거 - 더 순수한 평균회귀)
  V4 = HYBRID       (평균회귀 베이스 + MA정배열·거래량점프·변동성수축 가점)
  V5 = MOMENTUM_PIVOT (돌파 + 거래량 동반만 보상, 나머진 보수적)
  V6 = PULLBACK_BUY (SMA20 위 1~3% 눌림 + 추세 정배열에 큰 가점)
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
CACHE = _HERE / "cache"
sys.path.insert(0, str(_HERE))

# entry_timing_backtest 모듈에서 지표 함수 재사용
from entry_timing_backtest import (  # type: ignore
    _rsi, _bb_position, _atr_percent, _vwap_distance,
    _macd_div, _regime, _high_52w, _pivot_breakout, _s_confirmed,
)


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """모든 후보 점수 함수에 필요한 피처를 한 번에 계산."""
    c = df["Close"]; v = df["Volume"]
    out = pd.DataFrame(index=df.index)
    out["close"] = c
    out["rsi"] = _rsi(c)
    out["bb"] = _bb_position(c)
    out["atr_p"] = _atr_percent(df)
    out["vwap_d"] = _vwap_distance(df)
    out["macd_div"] = _macd_div(c)
    out["reg"] = _regime(c)
    near52, dist52 = _high_52w(c)
    out["near52"] = near52
    out["pivot"] = _pivot_breakout(c, v)
    out["s_conf"] = _s_confirmed(v)
    out["day_chg"] = c.pct_change(fill_method=None)

    # 신규 피처
    sma20 = c.rolling(20).mean()
    sma50 = c.rolling(50).mean()
    sma200 = c.rolling(200).mean()
    out["ma_aligned"] = (c > sma50) & (sma50 > sma200)  # 정배열
    out["sma20_pullback"] = ((c - sma20) / sma20).between(0.0, 0.03)  # MA20 위 0~3%
    vol_avg20 = v.rolling(20).mean()
    out["vol_jump_up"] = (v > vol_avg20 * 2.0) & (c > df["Open"])  # 거래량 2배 + 양봉
    atr_now = out["atr_p"]
    atr_avg = atr_now.rolling(30).mean()
    out["atr_squeeze"] = atr_now < atr_avg * 0.8  # 변동성 수축

    # 전방 수익률 + MDD
    out["fwd10"] = c.pct_change(10).shift(-10)
    # MDD20
    low = df["Low"]
    fwd_mdd = pd.Series(np.nan, index=df.index)
    c_v = c.values; l_v = low.values
    for i in range(len(df) - 20):
        wmin = l_v[i+1:i+21].min()
        fwd_mdd.iat[i] = (wmin - c_v[i]) / c_v[i]
    out["mdd20"] = fwd_mdd
    return out


# ─────────────────────────────────────────────────────────────────────
# 후보 점수 함수들
# ─────────────────────────────────────────────────────────────────────
def score_V1_old(r) -> int:
    s = 50
    if r.rsi < 30: s += 15
    elif r.rsi < 40: s += 10
    elif r.rsi < 55: s += 3
    elif r.rsi < 70: s -= 3
    else: s -= 12
    if r.bb < -0.7: s += 10
    elif r.bb < -0.3: s += 5
    elif r.bb > 0.85: s -= 6
    elif r.bb > 0.5: s -= 2
    if r.vwap_d > 0.05: s -= 5
    elif r.vwap_d >= 0.0: s += 2
    elif r.vwap_d >= -0.03: s += 5
    else: s -= 2
    if r.atr_p > 8.0: s -= 10
    elif r.atr_p > 5.0: s -= 3
    if r.pivot and r.s_conf: s += 12
    elif r.near52 and r.s_conf: s += 6
    if r.macd_div == "BULLISH": s += 8
    elif r.macd_div == "BEARISH": s -= 8
    if r.reg == "STRONG_BULL": s += 5
    elif r.reg == "BULL": s += 3
    elif r.reg == "BEAR": s -= 8
    elif r.reg == "STRONG_BEAR": s -= 15
    if r.day_chg > 0.07: s -= 10
    elif r.day_chg > 0.04: s -= 5
    elif r.day_chg < -0.05: s += 5
    return max(0, min(100, s))


def score_V2_new(r) -> int:
    """현재 NEW 로직 (회귀 검증용)."""
    s = 55
    if r.rsi < 30: s += 15
    elif r.rsi < 40: s += 10
    elif r.rsi < 55: s += 4
    elif r.rsi < 70: s += 5
    elif r.rsi < 80: s -= 3
    else: s -= 12
    if r.bb < -0.7: s += 10
    elif r.bb < -0.3: s += 5
    elif r.bb < 0.5: s += 2
    elif r.bb < 0.85: s += 0
    else: s -= 6
    if r.vwap_d > 0.08: s -= 5
    elif r.vwap_d >= 0.0: s += 3
    elif r.vwap_d >= -0.03: s += 6
    else: s -= 2
    if r.atr_p < 2.0: s += 2
    elif r.atr_p < 5.0: s += 4
    elif r.atr_p < 8.0: s -= 2
    else: s -= 8
    if r.pivot and r.s_conf: s += 16
    elif r.near52 and r.s_conf: s += 10
    elif r.near52: s += 3
    if r.macd_div == "BULLISH": s += 10
    elif r.macd_div == "BEARISH": s -= 8
    if r.reg == "STRONG_BULL": s += 8
    elif r.reg == "BULL": s += 5
    elif r.reg == "BEAR": s -= 10
    elif r.reg == "STRONG_BEAR": s -= 18
    if r.day_chg > 0.10: s -= 8
    elif r.day_chg > 0.07: s -= 4
    elif r.day_chg < -0.05: s += 4
    return max(0, min(100, s))


def score_V3_pure_reversion(r) -> int:
    """V1보다 더 강한 평균회귀 — 신고가/돌파 가점 없음."""
    s = 50
    if r.rsi < 30: s += 18
    elif r.rsi < 40: s += 12
    elif r.rsi < 50: s += 5
    elif r.rsi < 65: s -= 2
    else: s -= 12
    if r.bb < -0.7: s += 14
    elif r.bb < -0.3: s += 7
    elif r.bb > 0.7: s -= 8
    if r.vwap_d > 0.05: s -= 6
    elif r.vwap_d >= -0.03: s += 4
    else: s -= 2
    if r.atr_p > 8.0: s -= 10
    elif r.atr_p > 5.0: s -= 3
    if r.macd_div == "BULLISH": s += 8
    elif r.macd_div == "BEARISH": s -= 8
    if r.reg == "STRONG_BULL": s += 3
    elif r.reg == "BEAR": s -= 8
    elif r.reg == "STRONG_BEAR": s -= 15
    if r.day_chg > 0.05: s -= 8
    elif r.day_chg < -0.05: s += 6
    return max(0, min(100, s))


def score_V4_hybrid(r) -> int:
    """평균회귀 베이스 + 정배열/거래량점프/변동성수축 가점."""
    s = 50
    # 회귀 신호
    if r.rsi < 30: s += 14
    elif r.rsi < 40: s += 8
    elif r.rsi < 55: s += 2
    elif r.rsi < 70: s -= 3
    else: s -= 12
    if r.bb < -0.7: s += 10
    elif r.bb < -0.3: s += 5
    elif r.bb > 0.85: s -= 6
    if r.vwap_d >= -0.03 and r.vwap_d <= 0.02: s += 5
    elif r.vwap_d > 0.05: s -= 5
    if r.atr_p > 8.0: s -= 10
    # 추세 정배열 + 거래량 점프 = 큰 보너스
    if r.ma_aligned and r.vol_jump_up: s += 14
    elif r.ma_aligned and r.atr_squeeze: s += 6  # 정배열 + 변동성 수축
    elif r.ma_aligned: s += 3
    # 신고가 + 거래량
    if r.pivot and r.s_conf: s += 10
    if r.macd_div == "BULLISH": s += 6
    elif r.macd_div == "BEARISH": s -= 8
    # 레짐
    if r.reg == "STRONG_BULL": s += 4
    elif r.reg == "BEAR": s -= 10
    elif r.reg == "STRONG_BEAR": s -= 18
    if r.day_chg > 0.07: s -= 10
    elif r.day_chg < -0.05: s += 5
    return max(0, min(100, s))


def score_V5_momentum_pivot(r) -> int:
    """돌파+거래량에만 강하게 보상, 나머진 보수적."""
    s = 45
    # pivot + 거래량 → 메인 신호
    if r.pivot and r.s_conf:
        s += 25
        if r.ma_aligned: s += 8
    elif r.near52 and r.s_conf: s += 12
    elif r.vol_jump_up and r.ma_aligned: s += 14
    elif r.s_conf and r.ma_aligned: s += 6
    # 회귀 신호도 약하게 인정
    if r.rsi < 30: s += 8
    elif r.rsi > 75: s -= 6
    if r.bb > 0.9: s -= 6
    # 안전장치
    if r.atr_p > 8.0: s -= 8
    if r.macd_div == "BEARISH": s -= 8
    if r.reg == "BEAR": s -= 12
    elif r.reg == "STRONG_BEAR": s -= 20
    elif r.reg == "STRONG_BULL": s += 4
    if r.day_chg > 0.08: s -= 8
    return max(0, min(100, s))


def score_V6_pullback(r) -> int:
    """SMA20 눌림 + 정배열 = 핵심. 신고가 추격 안 함."""
    s = 50
    # 핵심: 정배열 상태에서 SMA20 위 0~3% 눌림
    if r.ma_aligned and r.sma20_pullback:
        s += 18
    elif r.ma_aligned and r.rsi < 50:
        s += 10  # 정배열 + RSI 눌림
    elif r.ma_aligned:
        s += 3
    # 회귀
    if r.rsi < 30: s += 12
    elif r.rsi < 40: s += 6
    elif r.rsi > 75: s -= 8
    if r.bb < -0.5: s += 6
    elif r.bb > 0.85: s -= 6
    # MACD
    if r.macd_div == "BULLISH": s += 8
    elif r.macd_div == "BEARISH": s -= 8
    # 변동성
    if r.atr_p > 8.0: s -= 10
    if r.atr_squeeze and r.ma_aligned: s += 5
    # 레짐
    if r.reg == "STRONG_BULL": s += 5
    elif r.reg == "BEAR": s -= 10
    elif r.reg == "STRONG_BEAR": s -= 18
    # 당일
    if r.day_chg > 0.07: s -= 10
    elif r.day_chg < -0.05: s += 6
    return max(0, min(100, s))


VARIANTS: dict[str, Callable] = {
    "V1_OLD":            score_V1_old,
    "V2_NEW":            score_V2_new,
    "V3_PURE_REVERSION": score_V3_pure_reversion,
    "V4_HYBRID":         score_V4_hybrid,
    "V5_MOMENTUM_PIVOT": score_V5_momentum_pivot,
    "V6_PULLBACK":       score_V6_pullback,
}


def main():
    all_rows = []
    files = sorted(CACHE.glob("*_3y.parquet"))
    print(f"loading {len(files)} cached parquet files...")
    for f in files:
        try:
            df = pd.read_parquet(f)
            if len(df) < 260:
                continue
            feats = compute_features(df)
            # 220일부터 (SMA200 + 전방 20 확보)
            feats = feats.iloc[220:-20]
            feats = feats.dropna(subset=["fwd10", "mdd20"])
            if feats.empty:
                continue
            # 각 변종 점수 계산
            score_cols = {}
            for name, fn in VARIANTS.items():
                col = []
                for _, row in feats.iterrows():
                    col.append(fn(row))
                score_cols[name] = col
            for name, col in score_cols.items():
                feats[name] = col
            all_rows.append(feats[["fwd10", "mdd20"] + list(VARIANTS.keys())])
        except Exception as e:
            print(f"  skip {f.name}: {e}")
    if not all_rows:
        print("no data")
        return
    big = pd.concat(all_rows, ignore_index=True)
    print(f"total obs: {len(big):,}\n")

    base_avg = big["fwd10"].mean() * 100
    print(f"Baseline ALL +10d = {base_avg:+.2f}%, win = {(big['fwd10']>0).mean()*100:.1f}%\n")

    # 각 변종에 대해 최선의 단조 임계 찾기
    summary = []
    for name in VARIANTS:
        best = None
        for hi in range(50, 91, 5):
            for lo in range(30, hi, 5):
                g = big[big[name] >= hi]
                y = big[(big[name] >= lo) & (big[name] < hi)]
                r = big[big[name] < lo]
                if min(len(g), len(y), len(r)) < 500:
                    continue
                ga = g["fwd10"].mean() * 100
                ya = y["fwd10"].mean() * 100
                ra = r["fwd10"].mean() * 100
                if ga > ya > ra:
                    edge = ga - ra
                    if best is None or edge > best["edge"]:
                        best = dict(
                            hi=hi, lo=lo, edge=edge,
                            g_share=len(g) / len(big) * 100,
                            g_avg=ga, y_avg=ya, r_avg=ra,
                            g_win=(g["fwd10"] > 0).mean() * 100,
                            g_mdd=g["mdd20"].mean() * 100,
                        )
        summary.append((name, best))

    print("=== 각 변종의 최선 단조 임계 ===")
    print(f"{'변종':<22} {'hi':>3} {'lo':>3} {'G비중':>7} {'G+10d':>7} {'G승률':>6} {'G_MDD':>7} {'edge':>7}")
    for name, b in summary:
        if b is None:
            print(f"{name:<22} 단조 임계 없음")
            continue
        print(f"{name:<22} {b['hi']:>3} {b['lo']:>3}  {b['g_share']:>5.1f}%  {b['g_avg']:>+5.2f}%  {b['g_win']:>4.1f}%  {b['g_mdd']:>+5.2f}%  {b['edge']:>+5.2f}%")

    # 추가: 각 변종의 top 1% 시점 +10d (점수 함수가 진짜 좋은 시점 골라내는지)
    print("\n=== 각 변종의 상위 1% 점수 시점 ===")
    print(f"{'변종':<22} {'cutoff':>7} {'n':>6} {'+10d':>7} {'승률':>5}")
    for name in VARIANTS:
        cutoff = big[name].quantile(0.99)
        sub = big[big[name] >= cutoff]
        avg = sub["fwd10"].mean() * 100
        win = (sub["fwd10"] > 0).mean() * 100
        print(f"{name:<22} {cutoff:>6.1f}  {len(sub):>6,}  {avg:>+5.2f}%  {win:>4.1f}%")


if __name__ == "__main__":
    main()
