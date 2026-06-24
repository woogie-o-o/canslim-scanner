"""
패널 진단 스크립트
==================
월가 퀀트 패널이 요청한 진단을 한 번에 수행:

#1 신호 상관관계 분석 (entry_timing 서브신호 pairwise corr)
#2 데이터 시작 시점 / 레짐 점검 (bear regime 비중)
#4 섹터/시총 집중도 (캐시 종목 기준, 가능 범위)
#5 Base rate 비교 (조건부 GREEN 시 +5d/+10d/+20d 수익률 vs unconditional)

산출: backtest/reports/panel_diagnostic.txt
"""
from __future__ import annotations
import sys
import io
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd

# Windows cp949 → utf-8 강제
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
REPORT = ROOT / "reports"
REPORT.mkdir(exist_ok=True)


def load_all_parquets(market: str = "ALL") -> dict[str, pd.DataFrame]:
    """캐시된 3년 일봉을 로드."""
    out = {}
    for p in CACHE.glob("*_3y.parquet"):
        ticker = p.stem.replace("_3y", "")
        if market == "KR" and not ("_KS" in ticker or "_KQ" in ticker):
            continue
        if market == "US" and ("_KS" in ticker or "_KQ" in ticker):
            continue
        try:
            df = pd.read_parquet(p)
            if df is None or df.empty or len(df) < 60:
                continue
            # tz 제거 (혹시 모를 비교 안정성)
            if hasattr(df.index, "tz") and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            out[ticker] = df
        except Exception:
            continue
    return out


def compute_signals(df: pd.DataFrame) -> pd.DataFrame:
    """entry_timing 로직의 서브신호를 시계열로 재현."""
    c = df["Close"]
    h = df["High"]
    l = df["Low"]
    v = df["Volume"]
    o = df["Open"]

    # RSI (14)
    delta = c.diff()
    up = delta.clip(lower=0).rolling(14).mean()
    dn = (-delta.clip(upper=0)).rolling(14).mean()
    rs = up / (dn.replace(0, np.nan))
    rsi = 100 - 100 / (1 + rs)

    # Bollinger position
    ma20 = c.rolling(20).mean()
    sd20 = c.rolling(20).std()
    bb_pos = (c - ma20) / (2 * sd20.replace(0, np.nan))  # -1..+1 대략

    # MA align (50, 200)
    sma50 = c.rolling(50).mean()
    sma200 = c.rolling(200).mean()
    ma_align = (c > sma50) & (sma50 > sma200)

    # Volume jump (vs 20MA, with up bar)
    vavg20 = v.rolling(20).mean()
    vol_jump = (v > 2.0 * vavg20) & (c > o)

    # ATR%
    tr = pd.concat([(h - l).abs(),
                    (h - c.shift()).abs(),
                    (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_pct = (tr.rolling(14).mean() / c) * 100

    # ATR squeeze
    atr_avg30 = atr_pct.rolling(30).mean()
    atr_squeeze = atr_pct < atr_avg30 * 0.8

    # Near 52w high
    high52 = c.rolling(252).max()
    near52 = (high52 - c) / high52 <= 0.05

    # Regime (간이): 200ma 상회 + ADX-like (변동성)
    # 간이 regime: c>sma200 이면 BULL, 아니면 BEAR
    regime_bull = c > sma200

    # day change
    day_chg = c.pct_change(fill_method=None)

    # MACD divergence (간이): MACD hist sign change
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd - signal
    macd_bull = macd_hist > 0

    # VWAP (rolling 20d 근사)
    typical = (h + l + c) / 3
    vwap20 = (typical * v).rolling(20).sum() / v.rolling(20).sum().replace(0, np.nan)
    vwap_d = (c - vwap20) / vwap20

    sig = pd.DataFrame({
        "RSI": rsi,
        "BBPos": bb_pos,
        "MAAlign": ma_align.astype(float),
        "VolJump": vol_jump.astype(float),
        "ATRPct": atr_pct,
        "ATRSqueeze": atr_squeeze.astype(float),
        "Near52H": near52.astype(float),
        "RegimeBull": regime_bull.astype(float),
        "MACDBull": macd_bull.astype(float),
        "VWAPDist": vwap_d,
        "DayChg": day_chg,
    }, index=df.index)
    return sig


def entry_score_vec(sig: pd.DataFrame) -> pd.Series:
    """quant_nexus_v20 의 entry_timing V5_DECORRELATED 로직을 벡터로 재현.
    - base 30
    - RSI+BB+VWAP 통합 MeanRev (max-only)
    - MA+Regime 통합 TrendAlign
    - MACD 가중치 절반
    - 임계: STRONG>=65, NEUTRAL>=35
    """
    s = pd.Series(30.0, index=sig.index)

    rsi = sig["RSI"]
    bbp = sig["BBPos"]
    vd  = sig["VWAPDist"]

    # ── MeanRev 컴포지트 (RSI+BB+VWAP max-only) ──
    rsi_pts = np.where(rsi < 30, 16,
              np.where(rsi < 40, 9,
              np.where(rsi >= 70, -14, 0)))
    bb_pts  = np.where(bbp < -0.7, 14,
              np.where(bbp > 0.95, -10, 0))
    # VWAP 만 단독으로 양/음 신호 후보
    vwap_pos = np.where((vd >= -0.03) & (vd <= 0.02), 4, 0)
    vwap_neg = np.where(vd > 0.07, -6, 0)

    # 양수 측: RSI, BB 의 최대 → 그 다음 VWAP 보너스는 둘 다 0 일때만
    pos_mr = np.maximum(np.maximum(rsi_pts, bb_pts), 0)
    # 음수 측: RSI 과열, BB 과확장, VWAP 과확장 중 가장 강한 페널티
    neg_mr = np.minimum(np.minimum(rsi_pts, bb_pts), vwap_neg)
    neg_mr = np.minimum(neg_mr, 0)
    # 둘 다 0 인 경우만 VWAP 눌림 보너스
    vwap_bonus = np.where((pos_mr == 0) & (neg_mr == 0), vwap_pos, 0)
    mr_pts = pos_mr + neg_mr + vwap_bonus
    s += mr_pts

    # ── TrendAlign 컴포지트 (MA + Regime) ──
    ma  = sig["MAAlign"].astype(bool).values
    reg = sig["RegimeBull"].astype(bool).values
    trend_pts = np.where(ma & reg, 10,
                np.where(ma, 6,
                np.where(~reg, -8, 0)))
    s += trend_pts

    # ── 독립 가점 ──
    s += np.where(sig["VolJump"].astype(bool), 12, 0)
    # 신고가 돌파 + 거래량 동반은 데이터 한계로 Near52H 단독으로 근사 안 함

    # ATR squeeze + MA 정배열 (보조)
    s += np.where(sig["ATRSqueeze"].astype(bool) & sig["MAAlign"].astype(bool) & ~sig["VolJump"].astype(bool), 4, 0)

    # MACD 가중치 절반
    s += np.where(sig["MACDBull"].astype(bool), 3, -4)

    # ATR 과대
    s += np.where(sig["ATRPct"] > 8.0, -10, 0)

    # 당일 등락
    dc = sig["DayChg"]
    s += np.where(dc > 0.07, -10,
         np.where(dc < -0.05, 4, 0))

    return s.clip(0, 100)


def main():
    out_lines = []
    def p(msg=""):
        print(msg)
        out_lines.append(str(msg))

    p("=" * 70)
    p(f"패널 진단 리포트 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    p("=" * 70)

    # KR + US 분리
    for market in ("US", "KR"):
        data = load_all_parquets(market)
        p(f"\n[{market}] 캐시된 종목 수: {len(data)}")
        if not data:
            continue

        # 데이터 시작/종료 시점 통계
        starts = [df.index.min() for df in data.values()]
        ends = [df.index.max() for df in data.values()]
        p(f"  데이터 시작: 최소 {min(starts).date()}, 최대 {max(starts).date()}")
        p(f"  데이터 종료: 최소 {min(ends).date()}, 최대 {max(ends).date()}")

        # 모든 종목의 신호 + entry_score 계산
        all_signals = []
        all_scores = []
        all_fwd = []  # forward returns
        all_regime = []

        for tk, df in data.items():
            try:
                sig = compute_signals(df)
                sc = entry_score_vec(sig)
                # 미래 수익률
                fwd5 = df["Close"].pct_change(5).shift(-5)
                fwd10 = df["Close"].pct_change(10).shift(-10)
                fwd20 = df["Close"].pct_change(20).shift(-20)
                joined = sig.copy()
                joined["EntryScore"] = sc
                joined["Fwd5"] = fwd5
                joined["Fwd10"] = fwd10
                joined["Fwd20"] = fwd20
                joined["Ticker"] = tk
                joined = joined.dropna(subset=["RSI", "MAAlign", "EntryScore"])
                all_signals.append(joined)
            except Exception as e:
                continue

        if not all_signals:
            continue
        big = pd.concat(all_signals, axis=0)
        p(f"  유효 관측치: {len(big):,}")

        # ===== #2 레짐 분포 =====
        p(f"\n  [#2 레짐 점검]")
        bull_pct = big["RegimeBull"].mean() * 100
        p(f"    BULL (>200MA) 비율: {bull_pct:.1f}%")
        p(f"    BEAR (<200MA) 비율: {100 - bull_pct:.1f}%")
        if bull_pct > 75:
            p(f"    ⚠️ BULL regime이 {bull_pct:.0f}% — 모델은 강세장만 학습")

        # 연도별 분포
        big["Year"] = big.index.year
        yr_regime = big.groupby("Year")["RegimeBull"].mean() * 100
        p(f"    연도별 BULL%:")
        for y, v in yr_regime.items():
            p(f"      {y}: {v:.1f}%")

        # ===== #1 신호 상관관계 =====
        p(f"\n  [#1 신호 pairwise 상관관계]")
        sig_cols = ["RSI", "BBPos", "MAAlign", "VolJump", "Near52H",
                    "RegimeBull", "MACDBull", "VWAPDist", "DayChg", "EntryScore"]
        corr = big[sig_cols].corr()
        # 0.5 이상만 출력
        high_corr = []
        for i, ci in enumerate(sig_cols):
            for cj in sig_cols[i+1:]:
                v = corr.loc[ci, cj]
                if abs(v) >= 0.30:
                    high_corr.append((ci, cj, v))
        high_corr.sort(key=lambda x: -abs(x[2]))
        p("    |corr| >= 0.30 (강한 동조 신호):")
        for ci, cj, v in high_corr[:15]:
            flag = " 🚨" if abs(v) >= 0.7 else (" ⚠️" if abs(v) >= 0.5 else "")
            p(f"      {ci:12s} ↔ {cj:12s}: {v:+.2f}{flag}")

        # ===== #5 Base rate 비교 =====
        p(f"\n  [#5 Base rate (조건부 vs unconditional)]")
        # unconditional
        base5 = big["Fwd5"].mean() * 100
        base10 = big["Fwd10"].mean() * 100
        base20 = big["Fwd20"].mean() * 100
        win5 = (big["Fwd5"] > 0).mean() * 100
        win20 = (big["Fwd20"] > 0).mean() * 100
        p(f"    Unconditional:")
        p(f"      평균 5d/10d/20d: {base5:+.2f}% / {base10:+.2f}% / {base20:+.2f}%")
        p(f"      승률 5d/20d: {win5:.1f}% / {win20:.1f}%")

        # NEUTRAL+ (>=35) 조건부 — V5 라벨
        neutral = big[big["EntryScore"] >= 35]
        if len(neutral) > 0:
            g5 = neutral["Fwd5"].mean() * 100
            g10 = neutral["Fwd10"].mean() * 100
            g20 = neutral["Fwd20"].mean() * 100
            gw5 = (neutral["Fwd5"] > 0).mean() * 100
            gw20 = (neutral["Fwd20"] > 0).mean() * 100
            pct_fire = len(neutral) / len(big) * 100
            p(f"    NEUTRAL+ (EntryScore >= 35, n={len(neutral):,}, 발화 {pct_fire:.1f}%):")
            p(f"      평균 5d/10d/20d: {g5:+.2f}% / {g10:+.2f}% / {g20:+.2f}%")
            p(f"      승률 5d/20d: {gw5:.1f}% / {gw20:.1f}%")
            edge5 = g5 - base5
            edge20 = g20 - base20
            p(f"      EDGE 5d: {edge5:+.2f}%p   20d: {edge20:+.2f}%p")
            if abs(edge20) < 0.5:
                p(f"      ⚠️ 20d edge < 0.5%p — 노이즈 가능")

        # STRONG (>=65) — V5 임계
        strong = big[big["EntryScore"] >= 65]
        if len(strong) > 0:
            s5 = strong["Fwd5"].mean() * 100
            s20 = strong["Fwd20"].mean() * 100
            sw20 = (strong["Fwd20"] > 0).mean() * 100
            pct_strong = len(strong) / len(big) * 100
            p(f"    STRONG (EntryScore >= 65, n={len(strong):,}, 발화 {pct_strong:.1f}%):")
            p(f"      평균 5d/20d: {s5:+.2f}% / {s20:+.2f}%")
            p(f"      승률 20d: {sw20:.1f}%")
            p(f"      EDGE 20d: {s20 - base20:+.2f}%p")

        # 레짐별 분리 (#2 후속): BULL vs BEAR 에서 V5 등급별 성과
        p(f"\n  [#2 레짐별 V5 등급 성과 분리]")
        for reg_name, mask in [("BULL", big["RegimeBull"] == 1),
                                ("BEAR", big["RegimeBull"] == 0)]:
            sub = big[mask]
            if len(sub) < 100:
                continue
            base = sub["Fwd20"].mean() * 100
            for lbl, thr in [("NEUTRAL+", 35), ("STRONG", 65)]:
                g = sub[sub["EntryScore"] >= thr]
                if len(g) == 0:
                    continue
                gret = g["Fwd20"].mean() * 100
                gwin = (g["Fwd20"] > 0).mean() * 100
                p(f"    [{reg_name}] base20={base:+.2f}%, {lbl}(>={thr})={gret:+.2f}% "
                  f"(n={len(g):,}, win={gwin:.1f}%, edge={gret-base:+.2f}%p)")

        # GREEN 비율 (얼마나 자주 신호 켜지나)
        green_pct = (big["EntryScore"] >= 50).mean() * 100
        p(f"\n  [신호 발화 빈도]")
        p(f"    EntryScore >= 50 비율: {green_pct:.1f}%  (목표: 10~25%)")
        if green_pct > 50:
            p(f"    🚨 신호가 절반 이상 항상 켜져있음 — 필터 의미 없음")
        elif green_pct > 30:
            p(f"    ⚠️ 신호 빈도 과다 — 임계값 상향 검토 필요")

    # 저장
    out_path = REPORT / "panel_diagnostic.txt"
    out_path.write_text("\n".join(out_lines), encoding="utf-8")
    p(f"\n✅ 저장: {out_path}")


if __name__ == "__main__":
    main()
