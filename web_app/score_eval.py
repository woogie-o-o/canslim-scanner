"""
score_eval.py — 점수 신호 표본외(out-of-sample) 검증 하니스

snapshots/scanner_{MARKET}_{YYYY-MM-DD}.json 에 매일 저장되는 종목 점수를,
yfinance 의 실제 포워드 수익과 대조해 점수의 예측력을 측정한다.

핵심 지표
  - IC (Information Coefficient): 각 스냅샷 일자에서 Spearman(점수, 포워드수익)의
    순위상관. 점수가 미래수익을 얼마나 잘 줄세우는지. 평균 IC·표준편차·t-stat·
    적중률(IC>0 비율)을 집계.
  - 분위 스프레드: 상위 quintile − 하위 quintile 평균 포워드수익(일자별 후 평균).

설계 원칙 (월가 퀀트 패널 권고)
  - 절대 과대평가 금지: 표본(스냅샷 일수)이 적으면 'INSUFFICIENT' 로 명시.
  - 출처 없는 수치 인용 금지 — 실제 가격/점수에서만 계산.
  - 포워드 지평이 아직 미래라 계산 불가하면 조용히 건너뛴다(거짓 0 금지).

스냅샷이 ~12일치뿐이면 단기(1·3·5 거래일) 지평만 의미가 있고, 스냅샷이
쌓일수록 21·63 거래일(1·3개월) 지평이 자동으로 활성화된다.

사용:
    python score_eval.py US
    python score_eval.py KR --horizons 1,3,5,10
    python score_eval.py US --min-names 50
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

_LOG = logging.getLogger("score_eval")
_BASE = os.path.dirname(os.path.abspath(__file__))
_SNAP_DIR = os.path.join(_BASE, "snapshots")

_DEFAULT_HORIZONS = (1, 3, 5)
_MIN_NAMES = 30        # 일자별 최소 종목 수 (이하면 그 일자 IC 신뢰 불가)
_MIN_DATES = 3         # 지평별 최소 평가 일자 수 (이하면 INSUFFICIENT)
_FETCH_CHUNK = 200     # yfinance 배치 크기


# ── 스냅샷 로드 ───────────────────────────────────────────────────────
def load_snapshots(market: str) -> list[tuple]:
    """[(date, {ticker: score}), ...] 날짜 오름차순. score 없는 항목은 제외."""
    pat = os.path.join(_SNAP_DIR, f"scanner_{market}_*.json")
    out = []
    for p in sorted(glob.glob(pat)):
        m = re.search(r"_(\d{4}-\d{2}-\d{2})\.json$", p)
        if not m:
            continue
        d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            _LOG.warning("snapshot load failed %s: %s", p, e)
            continue
        scores = {
            t: float(v["score"])
            for t, v in data.items()
            if isinstance(v, dict) and v.get("score") is not None
        }
        if scores:
            out.append((d, scores))
    out.sort(key=lambda x: x[0])
    return out


# ── 가격 수집 ─────────────────────────────────────────────────────────
def fetch_closes(symbols: list[str], start, end):
    """yfinance 일봉 종가(배당 조정) → DataFrame(index=거래일, cols=symbol)."""
    import pandas as pd
    import yfinance as yf

    closes: dict = {}
    syms = sorted(set(symbols))
    for i in range(0, len(syms), _FETCH_CHUNK):
        chunk = syms[i:i + _FETCH_CHUNK]
        try:
            df = yf.download(
                chunk, start=start, end=end, interval="1d",
                progress=False, group_by="ticker", threads=True, auto_adjust=True,
            )
        except Exception as e:
            _LOG.warning("yf chunk %d failed: %s", i, e)
            continue
        if df is None or len(df) == 0:
            continue
        if len(chunk) == 1:
            sym = chunk[0]
            try:
                s = df["Close"].dropna()
                if len(s):
                    closes[sym] = s
            except Exception:
                pass
        else:
            lv = set(df.columns.get_level_values(0))
            for sym in chunk:
                if sym not in lv:
                    continue
                try:
                    s = df[sym]["Close"].dropna()
                    if len(s):
                        closes[sym] = s
                except Exception:
                    pass
    if not closes:
        return pd.DataFrame()
    return pd.DataFrame(closes).sort_index()


# ── 포워드 수익 ───────────────────────────────────────────────────────
def forward_returns(closes, snap_date, horizon: int):
    """snap_date 종가 대비 horizon 거래일 후 수익률(Series, index=symbol).
    포워드 거래일이 아직 없으면 None."""
    import pandas as pd

    idx = closes.index
    ts = pd.Timestamp(snap_date)
    pos = idx.searchsorted(ts, side="right") - 1   # snap_date 이하 마지막 거래일
    if pos < 0:
        return None
    fpos = pos + horizon
    if fpos >= len(idx):
        return None                                  # 포워드 데이터 아직 미래
    entry = closes.iloc[pos]
    fwd = closes.iloc[fpos]
    ret = fwd / entry - 1.0
    return ret[entry > 0].dropna()


# ── 평가 ──────────────────────────────────────────────────────────────
def evaluate(market: str, horizons=_DEFAULT_HORIZONS, min_names: int = _MIN_NAMES) -> dict:
    import pandas as pd

    snaps = load_snapshots(market)
    if len(snaps) < 2:
        return {"market": market, "error": "스냅샷이 2일 미만 — 평가 불가", "snapshots": len(snaps)}

    universe = sorted({t for _, sc in snaps for t in sc})
    start = (min(d for d, _ in snaps)).isoformat()
    # end 는 충분히 미래로 — 최신 스냅샷 + 최대 지평 커버
    end_dt = max(d for d, _ in snaps)
    end = (pd.Timestamp(end_dt) + pd.Timedelta(days=max(horizons) * 2 + 7)).date().isoformat()

    _LOG.info("fetching %d symbols %s..%s", len(universe), start, end)
    closes = fetch_closes(universe, start, end)
    if closes.empty:
        return {"market": market, "error": "가격 수집 실패", "snapshots": len(snaps)}

    per_h = {}
    for h in horizons:
        ics, spreads, dates_used, names_used = [], [], [], []
        for d, scores in snaps:
            rets = forward_returns(closes, d, h)
            if rets is None or len(rets) == 0:
                continue
            sc = pd.Series(scores, dtype="float64")
            common = sc.index.intersection(rets.index)
            if len(common) < min_names:
                continue
            a = sc.loc[common]
            b = rets.loc[common]
            # Spearman = 순위(rank) 후 Pearson. scipy 의존 회피(미설치 환경).
            ic = a.rank().corr(b.rank())
            if ic is None or (isinstance(ic, float) and math.isnan(ic)):
                continue
            ics.append(float(ic))
            dates_used.append(d.isoformat())
            names_used.append(len(common))
            # 분위 스프레드 (상위 - 하위 quintile)
            try:
                q = pd.qcut(a.rank(method="first"), 5, labels=False)
                top = b[q == 4].mean()
                bot = b[q == 0].mean()
                if not (math.isnan(top) or math.isnan(bot)):
                    spreads.append(float(top - bot))
            except Exception:
                pass

        n = len(ics)
        if n < _MIN_DATES:
            per_h[h] = {"status": "INSUFFICIENT", "n_dates": n,
                        "note": f"평가 일자 {n}개 < 최소 {_MIN_DATES} — 포워드 지평이 아직 미래이거나 표본 부족"}
            continue
        mean_ic = sum(ics) / n
        var = sum((x - mean_ic) ** 2 for x in ics) / (n - 1) if n > 1 else 0.0
        std_ic = math.sqrt(var)
        t_stat = (mean_ic / (std_ic / math.sqrt(n))) if std_ic > 0 else float("nan")
        hit = sum(1 for x in ics if x > 0) / n
        mean_spread = (sum(spreads) / len(spreads)) if spreads else float("nan")
        per_h[h] = {
            "status": "OK",
            "n_dates": n,
            "avg_names": round(sum(names_used) / len(names_used)),
            "mean_ic": round(mean_ic, 4),
            "std_ic": round(std_ic, 4),
            "t_stat": round(t_stat, 2) if not math.isnan(t_stat) else None,
            "hit_rate": round(hit, 2),
            "quintile_spread": round(mean_spread, 4) if not math.isnan(mean_spread) else None,
            "dates": dates_used,
        }

    return {
        "market": market,
        "snapshots": len(snaps),
        "universe": len(universe),
        "date_range": [snaps[0][0].isoformat(), snaps[-1][0].isoformat()],
        "horizons": dict(sorted(per_h.items())),
    }


# ── 리포트 출력 ───────────────────────────────────────────────────────
def format_report(rep: dict) -> str:
    if rep.get("error"):
        return f"[{rep['market']}] 평가 불가: {rep['error']} (스냅샷 {rep.get('snapshots')}일)"
    lines = []
    lines.append(f"━━ 점수 신호 표본외 검증 — {rep['market']} ━━")
    lines.append(f"스냅샷 {rep['snapshots']}일 ({rep['date_range'][0]} ~ {rep['date_range'][1]}) · 유니버스 {rep['universe']}종목")
    lines.append("")
    lines.append(f"{'지평':>6} | {'상태':<12} | {'일자':>4} | {'평균IC':>8} | {'t-stat':>7} | {'적중률':>6} | {'분위스프레드':>10}")
    lines.append("-" * 78)
    for h, r in rep["horizons"].items():
        if r["status"] != "OK":
            lines.append(f"{h:>4}d  | {'INSUFFICIENT':<12} | {r['n_dates']:>4} | {'—':>8} | {'—':>7} | {'—':>6} | {'—':>10}")
            continue
        sp = f"{r['quintile_spread']*100:+.2f}%" if r["quintile_spread"] is not None else "—"
        t = f"{r['t_stat']:+.2f}" if r["t_stat"] is not None else "—"
        lines.append(
            f"{h:>4}d  | {'OK':<12} | {r['n_dates']:>4} | {r['mean_ic']:>+8.4f} | {t:>7} | {r['hit_rate']*100:>5.0f}% | {sp:>10}"
        )
    lines.append("-" * 78)
    lines.append("해석: IC>0 이면 점수가 미래수익을 양(+)의 방향으로 줄세움. |t|>2 면 통계적으로 유의(표본 작으면 신뢰 낮음).")
    lines.append("       분위스프레드 = 상위20% − 하위20% 평균 포워드수익. 양수면 고점수가 실제로 더 올랐다는 뜻.")
    lines.append("⚠ 스냅샷 누적 기간이 짧으면 추정 잡음이 크다. 일자 수가 늘수록 신뢰도 상승.")
    return "\n".join(lines)


def _cache_path(market: str) -> str:
    return os.path.join(_BASE, f"score_eval_{market.upper()}.json")


def save_cache(market: str, horizons=_DEFAULT_HORIZONS) -> dict:
    """평가 후 결과를 score_eval_{MARKET}.json 에 저장(UI 배지/라우트가 읽음)."""
    rep = evaluate(market, horizons=horizons)
    rep["generated"] = datetime.now().isoformat(timespec="seconds")
    # UI 배지용 요약: 가장 긴 OK 지평의 IC/상태를 뽑아둔다.
    rep["badge"] = _summarize_for_badge(rep)
    try:
        with open(_cache_path(market), "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)
    except Exception as e:
        _LOG.warning("score_eval cache write failed: %s", e)
    return rep


def _summarize_for_badge(rep: dict) -> dict:
    """배지 1줄 요약: 가장 긴 OK 지평 기준 IC·라벨·신호등."""
    hz = rep.get("horizons") or {}
    best = None
    for h, r in hz.items():
        if r.get("status") == "OK":
            if best is None or int(h) > int(best[0]):
                best = (h, r)
    if best is None:
        return {"level": "none", "label": "검증 데이터 부족", "ic": None, "horizon": None}
    h, r = best
    ic = r.get("mean_ic")
    t = r.get("t_stat")
    sig = t is not None and abs(t) >= 2.0
    if ic is not None and ic > 0 and sig:
        level, label = "valid", "예측력 유효"
    elif ic is not None and ic < 0 and sig:
        level, label = "negative", "역방향 주의"
    else:
        level, label = "testing", "검증 중"
    return {"level": level, "label": label, "ic": ic, "t_stat": t,
            "horizon": int(h), "n_dates": r.get("n_dates")}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # cp949 콘솔에서도 한글/기호 출력
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="점수 신호 표본외 검증")
    ap.add_argument("market", nargs="?", default="US", help="US 또는 KR")
    ap.add_argument("--horizons", default="1,3,5", help="콤마 구분 거래일 지평 (예: 1,3,5,10,21)")
    ap.add_argument("--min-names", type=int, default=_MIN_NAMES, help="일자별 최소 종목 수")
    ap.add_argument("--save", action="store_true", help="결과를 score_eval_{MARKET}.json 캐시로 저장")
    args = ap.parse_args()
    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())
    if args.save:
        rep = save_cache(args.market.upper(), horizons=horizons)
        print(f"[저장] {_cache_path(args.market.upper())}")
    else:
        rep = evaluate(args.market.upper(), horizons=horizons, min_names=args.min_names)
    print()
    print(format_report(rep))


if __name__ == "__main__":
    main()
