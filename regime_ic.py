# -*- coding: utf-8 -*-
"""regime_ic.py — 레짐/OFI 신호의 표본외 예측력(IC) 워크포워드 백테스트 (정확도 보정판).

기존 score_eval.py/bottleneck_ic.py는 '오늘 스냅샷 → 수 주 후 측정'하는 forward
트래커라, 과거 RegimeEntryScore 패널이 없어 소급 평가가 불가하다. 그러나
regime_classifier / order_flow 신호는 일봉 가격의 **누수 없는 결정적 함수**이므로
과거 시점(point-in-time)으로 재구성해 forward 수익과의 IC를 정직하게 측정할 수 있다.

정확도 보정(v2):
  - **Newey-West(HAC) t-stat**: 겹치는 forward 윈도우의 자기상관을 보정 → 유의성 정직화.
  - **블록 부트스트랩 CI**: 자기상관 보존 블록 재표본으로 평균 IC의 신뢰구간.
  - **비겹침 옵션**(--non-overlap): step≥horizon 강제 → 독립 표본.
  - **분위 롱숏 스프레드 + 거래비용**: IC보다 경제적으로 직접적인 상/하위 분위 수익차(비용 차감).
  - **유니버스 확대**: theme_stocks.txt(~400 KR 종목) → 횡단면 두께 증가(IC 정밀도↑).
  - **벡터화 OFI**: rolling 시계열 1회 산출 후 샘플링(O(T²)→O(T)).

원칙(월가 퀀트 패널): 과대평가 금지 · 표본 적으면 INSUFFICIENT 명시 · 거짓 0 금지 ·
실제 가격에서만 산출 · 룩어헤드 없이 신호는 과거만 사용.

사용:
    py -3.13 regime_ic.py --market KR --universe themes --horizons 5,21 --non-overlap --cost-bps 25 --save
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import math
import os
import re
import sys
from datetime import datetime

import numpy as np
import pandas as pd

import order_flow as _of
import regime_classifier as _rc

_LOG = logging.getLogger("regime_ic")
_BASE = os.path.dirname(os.path.abspath(__file__))
_RNG = np.random.default_rng(42)   # 재현성

# ── 기본 바스켓 (유니버스 파일 없을 때 폴백) ────────────────────────────────
_OFI_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMD", "AVGO", "TSM", "INTC", "MU", "QCOM", "TXN",
    "GOOGL", "META", "AMZN", "NFLX", "CRM", "ADBE", "ORCL", "CSCO", "IBM", "NOW",
    "TSLA", "GM", "F", "RIVN", "JPM", "BAC", "GS", "MS", "WFC", "C",
    "XOM", "CVX", "COP", "SLB", "LMT", "RTX", "NOC", "GD", "BA", "CAT",
    "LLY", "JNJ", "PFE", "MRK", "ABBV", "UNH", "WMT", "COST", "HD", "MCD",
]
_REGIME_UNIVERSE = ["^GSPC", "^NDX", "SMH", "XLK", "XLE", "XLF", "XBI", "ITA"]
_KR_OFI_BASKET = [
    "005930.KS", "000660.KS", "373220.KS", "207940.KS", "005380.KS", "000270.KS",
    "068270.KS", "035420.KS", "035720.KS", "051910.KS", "006400.KS", "028260.KS",
    "247540.KQ", "086520.KQ", "196170.KQ", "058470.KQ", "028300.KQ", "240810.KQ",
]
_KR_REGIME_UNIVERSE = [
    "^KS11", "^KQ11", "005930.KS", "000660.KS", "035420.KS", "373220.KS",
    "207940.KS", "005380.KS", "247540.KQ", "068270.KS", "012450.KS", "051910.KS",
]


def load_us_universe_from_index(keys=("SP400", "SP600"), max_names: int = 0) -> list[str]:
    """index_membership.json 에서 US 지수 구성종목(중소형 기본). 없으면 바스켓 폴백."""
    path = os.path.join(_BASE, "web_app", "index_membership.json")
    if not os.path.exists(path):
        return list(_OFI_UNIVERSE)
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        syms = set()
        for k in keys:
            for s in d.get(k, []):
                syms.add(str(s).upper())
        out = sorted(syms)
        if max_names and len(out) > max_names:
            out = out[:max_names]
        return out or list(_OFI_UNIVERSE)
    except Exception as e:
        _LOG.warning("index 파싱 실패: %s", e)
        return list(_OFI_UNIVERSE)


def load_kr_universe_from_themes(max_names: int = 0) -> list[str]:
    """theme_stocks.txt 에서 KR 티커(NNNNNN.KS/.KQ)를 파싱. 없으면 바스켓 폴백."""
    path = os.path.join(_BASE, "theme_stocks.txt")
    if not os.path.exists(path):
        return list(_KR_OFI_BASKET)
    syms = set()
    pat = re.compile(r"\b(\d{6}\.(?:KS|KQ))\b")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = pat.search(line)
                if m:
                    syms.add(m.group(1))
    except Exception as e:
        _LOG.warning("theme 파싱 실패: %s", e)
        return list(_KR_OFI_BASKET)
    out = sorted(syms)
    if max_names and len(out) > max_names:
        out = out[:max_names]
    return out or list(_KR_OFI_BASKET)


# ── 가격 수집 (청크 + 머지) ──────────────────────────────────────────────────
def _fetch(symbols, years=6, chunk=120):
    import yfinance as yf
    syms = sorted({s.strip() for s in symbols if s and s.strip()})
    out: dict = {}
    for i in range(0, len(syms), chunk):
        part = syms[i:i + chunk]
        try:
            df = yf.download(part, period=f"{years}y", interval="1d", progress=False,
                             group_by="ticker", auto_adjust=True, threads=True)
        except Exception as e:
            _LOG.warning("fetch chunk %d 실패: %s", i, e)
            continue
        if df is None or len(df) == 0:
            continue
        if len(part) == 1:
            try:
                sub = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
                if len(sub) > 120:
                    out[part[0]] = sub
            except Exception:
                pass
            continue
        lv = set(df.columns.get_level_values(0))
        for s in part:
            if s not in lv:
                continue
            try:
                sub = df[s][["Open", "High", "Low", "Close", "Volume"]].dropna()
                if len(sub) > 120:
                    out[s] = sub
            except Exception:
                pass
    return out


# ── 벡터화 OFI 시계열 (order_flow.compute_ofi 의 ofi 필드와 동치, 후행/누수無) ──
def _ofi_series(df: pd.DataFrame, window: int = 20) -> pd.Series:
    H, L, C, V = df["High"], df["Low"], df["Close"], df["Volume"]
    rng = (H - L).replace(0.0, np.nan)
    clv = (((C - L) - (H - C)) / rng).fillna(0.0)
    mfv = clv * V
    mp = max(2, window // 2)
    num = mfv.rolling(window, min_periods=mp).sum()
    den = V.rolling(window, min_periods=mp).sum().replace(0.0, np.nan)
    return (num / den).clip(-1.0, 1.0)


# ── 통계 헬퍼 ────────────────────────────────────────────────────────────────
def _spearman(a: np.ndarray, b: np.ndarray):
    n = len(a)
    if n < 5:
        return None
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    ra = ra - ra.mean(); rb = rb - rb.mean()
    den = math.sqrt((ra @ ra) * (rb @ rb))
    return float((ra @ rb) / den) if den > 0 else None


def _nw_tstat(xs: list[float], lag: int):
    """Newey-West(HAC) t-stat of the mean — 겹침 자기상관 보정."""
    n = len(xs)
    if n < 3:
        return None
    a = np.asarray(xs, dtype=float)
    m = a.mean()
    e = a - m
    g0 = float(e @ e) / n
    s = g0
    for l in range(1, min(lag, n - 1) + 1):
        gl = float(e[l:] @ e[:-l]) / n
        w = 1.0 - l / (lag + 1.0)        # Bartlett kernel
        s += 2.0 * w * gl
    s = max(s, 1e-12)
    se = math.sqrt(s / n)                 # 평균의 HAC 표준오차
    return float(m / se) if se > 0 else None


def _block_bootstrap_ci(xs: list[float], block: int, n_boot: int = 2000, alpha: float = 0.05):
    """이동블록 부트스트랩 — 자기상관 보존 평균 CI. (low, high) 반환."""
    n = len(xs)
    block = max(1, min(block, n))
    if n < 3:
        return (None, None)
    a = np.asarray(xs, dtype=float)
    n_blocks = int(math.ceil(n / block))
    means = np.empty(n_boot)
    max_start = n - block
    for b in range(n_boot):
        starts = _RNG.integers(0, max_start + 1, size=n_blocks)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n]
        means[b] = a[idx].mean()
    lo = float(np.percentile(means, 100 * alpha / 2))
    hi = float(np.percentile(means, 100 * (1 - alpha / 2)))
    return (round(lo, 4), round(hi, 4))


# ── 1) OFI 횡단면 IC (HAC t + 부트스트랩 CI + 분위 롱숏 + 비용) ───────────────
def ofi_cross_sectional_ic(data: dict, horizons, step: int, *, non_overlap: bool,
                           cost_bps: float, warmup: int = 60, min_names: int = 30,
                           n_boot: int = 2000):
    # 종목별 OFI 시계열 + forward 수익(label) 사전 산출 — 벡터화
    ofi_map = {s: _ofi_series(df) for s, df in data.items()}
    close_map = {s: df["Close"] for s, df in data.items()}
    all_dates = sorted({d for df in data.values() for d in df.index})

    results = {}
    for h in horizons:
        # forward 수익 label (close[t+h]/close[t]-1) — t에서 신호는 과거만, label은 실현 미래
        fwd_map = {s: (c.shift(-h) / c - 1.0) for s, c in close_map.items()}
        eff_step = max(step, h) if non_overlap else step
        cost = cost_bps / 1e4

        ics, spreads, names_used, ic_dates = [], [], [], []
        for i in range(warmup, len(all_dates), eff_step):
            t = all_dates[i]
            o_vals, f_vals = [], []
            for s in data:
                ov = ofi_map[s].get(t, np.nan)
                fv = fwd_map[s].get(t, np.nan)
                if np.isfinite(ov) and np.isfinite(fv):
                    o_vals.append(float(ov)); f_vals.append(float(fv))
            if len(o_vals) < min_names:
                continue
            o = np.array(o_vals); f = np.array(f_vals)
            ic = _spearman(o, f)
            if ic is None:
                continue
            ics.append(ic)
            ic_dates.append(t)
            names_used.append(len(o))
            # 분위 롱숏 스프레드(상위20% − 하위20%), 롱숏 라운드트립 비용 4×one-way 차감
            try:
                q = pd.qcut(pd.Series(o).rank(method="first"), 5, labels=False)
                top = f[q.to_numpy() == 4].mean(); bot = f[q.to_numpy() == 0].mean()
                if np.isfinite(top) and np.isfinite(bot):
                    spreads.append(float(top - bot) - 4.0 * cost)
            except Exception:
                pass

        n = len(ics)
        if n < 5:
            results[h] = {"status": "INSUFFICIENT", "n_dates": n,
                          "non_overlap": non_overlap, "eff_step": eff_step}
            continue
        mean_ic = sum(ics) / n
        lag = 0 if non_overlap else (h - 1)
        t_nw = _nw_tstat(ics, lag)
        t_naive = (mean_ic / (np.std(ics, ddof=1) / math.sqrt(n))) if np.std(ics, ddof=1) > 0 else None
        ci = _block_bootstrap_ci(ics, block=max(1, h if not non_overlap else 1), n_boot=n_boot)
        ms = (sum(spreads) / len(spreads)) if spreads else None
        results[h] = {
            "status": "OK", "n_dates": n, "avg_names": round(sum(names_used) / len(names_used)),
            "eff_step": eff_step, "non_overlap": non_overlap,
            "mean_ic": round(mean_ic, 4),
            "t_nw": round(t_nw, 2) if t_nw is not None else None,
            "t_naive": round(t_naive, 2) if t_naive is not None else None,
            "ic_ci95": ci,
            "hit_rate": round(sum(1 for x in ics if x > 0) / n, 2),
            "ls_spread_net": round(ms, 4) if ms is not None else None,
            "cost_bps": cost_bps,
            "yearly": _yearly_ic(ic_dates, ics),
        }
    return results


def _agg_ic(ics: list[float], lag: int, n_boot: int = 1500) -> dict:
    n = len(ics)
    if n < 5:
        return {"status": "INSUFFICIENT", "n": n}
    m = sum(ics) / n
    t = _nw_tstat(ics, lag)
    ci = _block_bootstrap_ci(ics, block=max(1, lag + 1), n_boot=n_boot)
    return {"mean_ic": round(m, 4), "t_hac": round(t, 2) if t is not None else None,
            "ci95": ci, "hit": round(sum(1 for x in ics if x > 0) / n, 2), "n": n}


def _rank(a: np.ndarray) -> np.ndarray:
    return pd.Series(a).rank().to_numpy()


def confound_diagnostics(data: dict, horizons, step: int, *, non_overlap: bool,
                         warmup: int = 60, min_names: int = 30):
    """선택/모멘텀/사이즈 편향 진단 — OFI 엣지가 진짜 독립 신호인지 검정.

    각 일자 횡단면에서:
      - IC(OFI)            : 원 신호
      - IC(MOM20)          : 모멘텀 baseline (선택편향이 모든 신호를 부풀리는지 비교)
      - IC(OFI⊥MOM)        : 모멘텀 직교화 OFI 잔차 (OFI가 모멘텀 변장인지)
      - IC(OFI size-neutral): 거래대금 데실 내 demean (소형주 틸트 artifact인지)
    """
    ofi_map = {s: _ofi_series(df) for s, df in data.items()}
    close_map = {s: df["Close"] for s, df in data.items()}
    mom_map = {s: (c / c.shift(20) - 1.0) for s, c in close_map.items()}
    dv_map = {s: (df["Close"] * df["Volume"]).rolling(20, min_periods=10).mean()
              for s, df in data.items()}
    all_dates = sorted({d for df in data.values() for d in df.index})

    out = {}
    for h in horizons:
        fwd_map = {s: (c.shift(-h) / c - 1.0) for s, c in close_map.items()}
        eff_step = max(step, h) if non_overlap else step
        S = {"ofi": [], "mom": [], "resid": [], "size": []}
        for i in range(warmup, len(all_dates), eff_step):
            t = all_dates[i]
            O, M, DV, F = [], [], [], []
            for s in data:
                o = ofi_map[s].get(t, np.nan); m = mom_map[s].get(t, np.nan)
                dv = dv_map[s].get(t, np.nan); f = fwd_map[s].get(t, np.nan)
                if np.isfinite(o) and np.isfinite(m) and np.isfinite(dv) and np.isfinite(f):
                    O.append(o); M.append(m); DV.append(dv); F.append(f)
            if len(O) < min_names:
                continue
            O = np.array(O); M = np.array(M); DV = np.array(DV); F = np.array(F)
            S["ofi"].append(_spearman(O, F))
            S["mom"].append(_spearman(M, F))
            # OFI ⊥ MOM : rank-OLS 잔차
            ro = _rank(O) - _rank(O).mean(); rm = _rank(M) - _rank(M).mean()
            denom = float(rm @ rm)
            resid = ro - (float(ro @ rm) / denom) * rm if denom > 0 else ro
            S["resid"].append(_spearman(resid, F))
            # 사이즈 중립 : 거래대금 데실 내 OFI demean
            try:
                dec = pd.qcut(pd.Series(DV).rank(method="first"), 10, labels=False).to_numpy()
                o_sn = O.copy().astype(float)
                for g in range(10):
                    msk = dec == g
                    if msk.sum() > 0:
                        o_sn[msk] = O[msk] - O[msk].mean()
                S["size"].append(_spearman(o_sn, F))
            except Exception:
                pass
        lag = 0 if non_overlap else (h - 1)
        out[h] = {k: _agg_ic([x for x in v if x is not None], lag) for k, v in S.items()}
    return out


def _yearly_ic(dates: list, ics: list[float]) -> dict:
    """연도별 평균 IC + 일자수 — 단일기간 우연 여부 판별(부호 안정성)."""
    by_year: dict[int, list[float]] = {}
    for d, ic in zip(dates, ics):
        y = pd.Timestamp(d).year
        by_year.setdefault(y, []).append(ic)
    out = {}
    for y in sorted(by_year):
        v = by_year[y]
        out[str(y)] = {"mean_ic": round(sum(v) / len(v), 4), "n": len(v)}
    pos_years = sum(1 for y in out.values() if y["mean_ic"] > 0)
    out["_summary"] = {"n_years": len(by_year), "pos_years": pos_years,
                       "sign_stable": pos_years == len(by_year) or pos_years == 0}
    return out


# ── 2) 레짐 전환신호 이벤트 스터디 ──────────────────────────────────────────
def regime_event_study(data: dict, horizons, step: int, *, cost_bps: float,
                       warmup: int = 520, config: dict = None):
    cfg = config or _rc.REGIME_CONFIG
    cost = cost_bps / 1e4
    out = {}
    for h in horizons:
        fired_rets, base_rets = [], []
        n_fired = 0
        for sym, df in data.items():
            idx = df.index
            for i in range(warmup, len(df) - h, step):
                hist = df.iloc[:i + 1]
                if len(hist) < warmup:
                    continue
                try:
                    res = _rc.classify_regime(hist, config=cfg)
                except Exception:
                    continue
                entry = float(df["Close"].iloc[i]); fwd = float(df["Close"].iloc[i + h])
                if entry <= 0:
                    continue
                ret = fwd / entry - 1.0
                base_rets.append(ret)
                if (res.transition_signal or {}).get("early_long"):
                    fired_rets.append(ret - 2.0 * cost)   # 롱 진입+청산 비용
                    n_fired += 1
        if len(base_rets) < 10:
            out[h] = {"status": "INSUFFICIENT", "n_obs": len(base_rets)}
            continue
        base_mean = sum(base_rets) / len(base_rets)
        if n_fired < 5:
            out[h] = {"status": "INSUFFICIENT_FIRES", "n_fired": n_fired,
                      "base_mean_ret": round(base_mean, 4), "n_obs": len(base_rets),
                      "note": "발화 표본 부족 — 소급 백테스트로 검증 불가, forward 누적 필요"}
            continue
        fired_mean = sum(fired_rets) / len(fired_rets)
        # 발화군 평균이 기준을 초과하는지 부트스트랩 CI
        ci = _block_bootstrap_ci(fired_rets, block=1, n_boot=2000)
        out[h] = {
            "status": "OK", "n_obs": len(base_rets), "n_fired": n_fired,
            "fired_mean_ret": round(fired_mean, 4), "base_mean_ret": round(base_mean, 4),
            "edge": round(fired_mean - base_mean, 4), "fired_ci95": ci, "cost_bps": cost_bps,
        }
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("hmmlearn").setLevel(logging.ERROR)
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizons", default="5,21")
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--years", type=int, default=6)
    ap.add_argument("--market", default="KR", choices=["US", "KR"])
    ap.add_argument("--universe", default="themes", choices=["themes", "basket", "index"],
                    help="KR: themes=theme_stocks.txt(~400)/basket / US: index=SP400+SP600/basket")
    ap.add_argument("--max-names", type=int, default=0, help="유니버스 상한(0=전체)")
    ap.add_argument("--non-overlap", action="store_true", help="step≥horizon 강제(독립 표본)")
    ap.add_argument("--cost-bps", type=float, default=25.0, help="one-way 거래비용(bp)")
    ap.add_argument("--restarts", type=int, default=0, help="레짐 HMM 멀티스타트 override")
    ap.add_argument("--boot", type=int, default=2000, help="부트스트랩 반복")
    ap.add_argument("--skip-regime", action="store_true", help="레짐 이벤트 스터디 생략(OFI 강건성 집중)")
    ap.add_argument("--diagnostics", action="store_true",
                    help="선택/모멘텀/사이즈 편향 진단(OFI⊥MOM, size-neutral)")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()
    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())
    mkt = args.market.upper()

    if mkt == "KR":
        ofi_uni = (load_kr_universe_from_themes(args.max_names)
                   if args.universe == "themes" else _KR_OFI_BASKET)
        reg_uni = _KR_REGIME_UNIVERSE
    else:
        ofi_uni = (load_us_universe_from_index(max_names=args.max_names)
                   if args.universe == "index" else _OFI_UNIVERSE)
        reg_uni = _REGIME_UNIVERSE

    reg_cfg = dict(_rc.REGIME_CONFIG)
    if args.restarts and args.restarts > 0:
        reg_cfg["n_init_restarts"] = args.restarts

    print(f"[1/3] [{mkt}/{args.universe}] OFI 유니버스 수집 ({len(ofi_uni)} 종목, {args.years}y)...")
    ofi_data = _fetch(ofi_uni, years=args.years)
    print(f"      수집 완료: {len(ofi_data)} 종목")

    print(f"[2/3] OFI 횡단면 IC (step={args.step}, non_overlap={args.non_overlap}, cost={args.cost_bps}bp)...")
    ofi_ic = ofi_cross_sectional_ic(ofi_data, horizons, args.step,
                                    non_overlap=args.non_overlap, cost_bps=args.cost_bps,
                                    n_boot=args.boot)
    diag = (confound_diagnostics(ofi_data, horizons, args.step, non_overlap=args.non_overlap)
            if args.diagnostics else {})

    if args.skip_regime:
        print("[3/3] 레짐 이벤트 스터디 생략(--skip-regime)")
        reg_data, reg_ev = {}, {}
    else:
        print(f"[3/3] 레짐 이벤트 스터디 ({len(reg_uni)} 종목, restarts={reg_cfg['n_init_restarts']})...")
        reg_data = _fetch(reg_uni, years=args.years)
        reg_ev = regime_event_study(reg_data, horizons, step=max(args.step, 3),
                                    cost_bps=args.cost_bps, config=reg_cfg)

    rep = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "market": mkt, "universe": args.universe, "years": args.years,
        "non_overlap": args.non_overlap, "cost_bps": args.cost_bps,
        "ofi_universe": len(ofi_data), "regime_universe": len(reg_data),
        "ofi_cross_sectional_ic": ofi_ic, "regime_event_study": reg_ev,
        "confound_diagnostics": diag,
        "hmm_available": _rc._HAS_HMM,
    }

    print("\n" + "=" * 72)
    print(f"레짐/OFI 표본외 예측력 — {mkt}/{args.universe} ({len(ofi_data)}종목, {args.years}y)")
    print(f"겹침보정: {'비겹침' if args.non_overlap else 'HAC(Newey-West)'} · 비용 {args.cost_bps}bp/leg")
    print("=" * 72)
    print(f"\n● OFI 횡단면 IC")
    for h, r in ofi_ic.items():
        if r["status"] == "OK":
            ci = r["ic_ci95"]
            cis = f"[{ci[0]:+.3f},{ci[1]:+.3f}]" if ci and ci[0] is not None else "—"
            sp = f"{r['ls_spread_net']*100:+.2f}%" if r["ls_spread_net"] is not None else "—"
            print(f"  {h:>3}일 | IC {r['mean_ic']:+.4f} | t_HAC {r['t_nw']} (naive {r['t_naive']}) | "
                  f"CI95 {cis} | 적중 {r['hit_rate']*100:.0f}% | 롱숏(비용後) {sp} | "
                  f"일자 {r['n_dates']}×{r['avg_names']}종목")
            yr = r.get("yearly", {})
            ys = " ".join(f"{k}:{v['mean_ic']:+.3f}" for k, v in yr.items() if k != "_summary")
            sm = yr.get("_summary", {})
            print(f"        └ 연도별 IC: {ys}  → {sm.get('pos_years')}/{sm.get('n_years')}년 양수"
                  f"{' (부호 안정)' if sm.get('sign_stable') else ' (혼재)'}")
        else:
            print(f"  {h:>3}일 | {r['status']} (일자 {r.get('n_dates','?')})")
    if diag:
        print(f"\n● 편향 진단 (OFI가 진짜 독립 신호인가)")
        _nm = {"ofi": "OFI 원본", "mom": "MOM20 baseline",
               "resid": "OFI⊥MOM(잔차)", "size": "OFI size-neutral"}
        for h, dd in diag.items():
            print(f"  [{h}일]")
            for k in ("ofi", "mom", "resid", "size"):
                r = dd.get(k, {})
                if r.get("status") == "INSUFFICIENT" or "mean_ic" not in r:
                    print(f"     {_nm[k]:<18} | INSUFFICIENT")
                    continue
                ci = r["ci95"]
                cis = f"[{ci[0]:+.3f},{ci[1]:+.3f}]" if ci and ci[0] is not None else "—"
                sig = "유의" if (ci and ci[0] is not None and (ci[0] > 0 or ci[1] < 0)) else "n.s."
                print(f"     {_nm[k]:<18} | IC {r['mean_ic']:+.4f} | t_HAC {r['t_hac']} | CI95 {cis} | {sig}")

    print(f"\n● 레짐 early_long 이벤트 스터디 (HMM={_rc._HAS_HMM})")
    for h, r in reg_ev.items():
        if r["status"] == "OK":
            print(f"  {h:>3}일 | 발화군 {r['fired_mean_ret']*100:+.2f}% vs 기준 {r['base_mean_ret']*100:+.2f}% "
                  f"| 엣지 {r['edge']*100:+.2f}%p | 발화 {r['n_fired']}/{r['n_obs']} | CI95 {r['fired_ci95']}")
        else:
            print(f"  {h:>3}일 | {r['status']} (발화 {r.get('n_fired','?')})")
    print("\n해석: t_HAC>2 & CI95가 0을 안 걸치면 유의. naive t는 겹침으로 과대평가됨(비교용).")
    print("      롱숏(비용後)>0 이어야 실거래 가치. 발화 표본<5면 레짐은 forward 누적 필요.")

    if args.save:
        p = os.path.join(_BASE, f"regime_ic_result_{mkt}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print(f"\n[저장] {p}")


if __name__ == "__main__":
    main()
