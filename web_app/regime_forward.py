"""regime_forward.py — RegimeEntryScore vs TotalScore forward-IC 비교(누적 검증).

history.save_snapshot 이 매일 기록하는 scanner_{MARKET}_{date}.json 에는
`score`(TotalScore)와 (레짐 모듈 활성 시) `regime_entry`(RegimeEntryScore)가 함께 담긴다.
이 모듈은 성숙한 스냅샷의 forward 수익에 대해 **두 점수의 IC를 나란히** 계산해,
RegimeEntryScore 가 기존 TotalScore 를 실제로 능가하는지 표본외로 추적한다.

→ `REGIME_RANK=1` 활성화의 최종 근거. 레짐 필드가 아직 스냅샷에 없으면(누적 전)
   정직하게 ACCUMULATING 으로 표시한다(거짓 0 금지).

CLI:
    python regime_forward.py KR --horizons 1,3,5,10
    python regime_forward.py US --save
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

_BASE = os.path.dirname(os.path.abspath(__file__))
_SNAP_DIR = os.path.join(_BASE, "snapshots")
_LOG = logging.getLogger("regime_forward")

_DEFAULT_HORIZONS = (1, 3, 5)
_MIN_NAMES = 30
_MIN_DATES = 3


def _spearman(a, b):
    import pandas as pd
    if len(a) < 5 or len(a) != len(b):
        return None
    ic = pd.Series(a).rank().corr(pd.Series(b).rank())
    if ic is None or (isinstance(ic, float) and math.isnan(ic)):
        return None
    return float(ic)


def load_dual_snapshots(market: str, snap_dir: str = _SNAP_DIR):
    """[(date, {ticker: {'score': float, 'regime': float|None}}), ...] 날짜 오름차순."""
    pat = os.path.join(snap_dir, f"scanner_{market}_*.json")
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
        rows = {}
        for t, v in data.items():
            if not isinstance(v, dict) or v.get("score") is None:
                continue
            rows[t] = {"score": float(v["score"]),
                       "regime": (float(v["regime_entry"])
                                  if v.get("regime_entry") is not None else None)}
        if rows:
            out.append((d, rows))
    out.sort(key=lambda x: x[0])
    return out


def _agg(ics):
    n = len(ics)
    if n < _MIN_DATES:
        return None
    m = sum(ics) / n
    var = sum((x - m) ** 2 for x in ics) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    t = (m / (sd / math.sqrt(n))) if sd > 0 else None
    return {"mean_ic": round(m, 4), "t_stat": round(t, 2) if t is not None else None,
            "hit_rate": round(sum(1 for x in ics if x > 0) / n, 2), "n_dates": n}


def evaluate_compare(market: str, horizons=_DEFAULT_HORIZONS, min_names: int = _MIN_NAMES,
                     snap_dir: str = _SNAP_DIR, closes=None):
    """두 점수(score=TotalScore, regime=RegimeEntryScore)의 forward IC 비교."""
    import pandas as pd
    from score_eval import fetch_closes, forward_returns

    snaps = load_dual_snapshots(market, snap_dir)
    if len(snaps) < 2:
        return {"market": market, "error": "스냅샷 2일 미만", "snapshots": len(snaps)}

    n_regime_snaps = sum(1 for _, rows in snaps
                         if any(r["regime"] is not None for r in rows.values()))

    universe = sorted({t for _, rows in snaps for t in rows})
    if closes is None:
        start = min(d for d, _ in snaps).isoformat()
        end = (pd.Timestamp(max(d for d, _ in snaps))
               + pd.Timedelta(days=max(horizons) * 2 + 7)).date().isoformat()
        closes = fetch_closes(universe, start, end)
    if closes is None or closes.empty:
        return {"market": market, "error": "가격 수집 실패", "snapshots": len(snaps)}

    per_h = {}
    for h in horizons:
        ic_tot, ic_reg, dates = [], [], []
        for d, rows in snaps:
            rets = forward_returns(closes, d, h)
            if rets is None or len(rets) == 0:
                continue
            sc = pd.Series({t: rows[t]["score"] for t in rows}, dtype="float64")
            common = sc.index.intersection(rets.index)
            if len(common) < min_names:
                continue
            it = _spearman(sc.loc[common].to_numpy(), rets.loc[common].rank().to_numpy())
            if it is None:
                continue
            ic_tot.append(it)
            dates.append(d.isoformat())
            # regime: 같은 일자/공통종목에서 regime 점수 있는 것만
            rg = pd.Series({t: rows[t]["regime"] for t in rows
                            if rows[t]["regime"] is not None}, dtype="float64")
            cr = rg.index.intersection(rets.index)
            if len(cr) >= min_names:
                ir = _spearman(rg.loc[cr].to_numpy(), rets.loc[cr].rank().to_numpy())
                if ir is not None:
                    ic_reg.append(ir)

        agg_t = _agg(ic_tot)
        agg_r = _agg(ic_reg)
        if agg_t is None:
            per_h[h] = {"status": "INSUFFICIENT", "n_dates": len(ic_tot)}
            continue
        entry = {"status": "OK", "total": agg_t}
        if agg_r is None:
            entry["regime"] = {"status": "ACCUMULATING", "n_dates": len(ic_reg)}
            entry["verdict"] = "레짐 점수 누적 중 — 비교 불가"
        else:
            delta = round(agg_r["mean_ic"] - agg_t["mean_ic"], 4)
            entry["regime"] = agg_r
            entry["delta_ic"] = delta
            entry["verdict"] = ("레짐 우월" if delta > 0 else "기존 우월" if delta < 0 else "동률")
        per_h[h] = entry

    return {"market": market, "snapshots": len(snaps),
            "regime_snapshots": n_regime_snaps, "universe": len(universe),
            "date_range": [snaps[0][0].isoformat(), snaps[-1][0].isoformat()],
            "horizons": dict(sorted(per_h.items()))}


def format_report(rep: dict) -> str:
    if rep.get("error"):
        return f"[{rep['market']}] 비교 불가: {rep['error']} (스냅샷 {rep.get('snapshots')}일)"
    L = [f"━━ RegimeEntryScore vs TotalScore forward-IC — {rep['market']} ━━",
         f"스냅샷 {rep['snapshots']}일 (레짐필드 {rep['regime_snapshots']}일) · "
         f"{rep['date_range'][0]}~{rep['date_range'][1]} · 유니버스 {rep['universe']}",
         ""]
    if rep["regime_snapshots"] == 0:
        L.append("⚠ 아직 레짐 필드가 담긴 스냅샷이 없음 — REGIME 모듈 활성 상태로 스캔이")
        L.append("  하루 이상 누적되면 비교가 시작됩니다(거짓 결과 대신 정직하게 대기).")
    L.append(f"{'지평':>5} | {'기존 IC':>8} | {'레짐 IC':>8} | {'ΔIC':>8} | {'판정':<14} | 일자")
    L.append("-" * 64)
    for h, r in rep["horizons"].items():
        if r["status"] != "OK":
            L.append(f"{h:>3}d  | {'—':>8} | {'—':>8} | {'—':>8} | INSUFFICIENT   | {r.get('n_dates',0)}")
            continue
        ti = f"{r['total']['mean_ic']:+.4f}"
        rg = r["regime"]
        if rg.get("status") == "ACCUMULATING":
            L.append(f"{h:>3}d  | {ti:>8} | {'누적중':>8} | {'—':>8} | {r['verdict']:<14} | {r['total']['n_dates']}")
        else:
            ri = f"{rg['mean_ic']:+.4f}"; di = f"{r['delta_ic']:+.4f}"
            L.append(f"{h:>3}d  | {ti:>8} | {ri:>8} | {di:>8} | {r['verdict']:<14} | {r['total']['n_dates']}")
    L.append("-" * 64)
    L.append("ΔIC>0 이고 누적 일자가 충분(수십)하며 |t|>2 면 레짐 점수 활성화(REGIME_RANK=1) 근거.")
    L.append("⚠ 누적 초기엔 잡음 큼 — 일자가 쌓일수록 신뢰도↑.")
    return "\n".join(L)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="RegimeEntryScore vs TotalScore forward-IC")
    ap.add_argument("market", nargs="?", default="KR")
    ap.add_argument("--horizons", default="1,3,5")
    ap.add_argument("--min-names", type=int, default=_MIN_NAMES)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()
    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())
    rep = evaluate_compare(args.market.upper(), horizons=horizons, min_names=args.min_names)
    if args.save and not rep.get("error"):
        rep["generated"] = datetime.now().isoformat(timespec="seconds")
        try:
            with open(os.path.join(_BASE, f"regime_forward_{args.market.upper()}.json"),
                      "w", encoding="utf-8") as f:
                json.dump(rep, f, ensure_ascii=False)
        except Exception as e:
            _LOG.warning("save failed: %s", e)
    print()
    print(format_report(rep))


if __name__ == "__main__":
    main()
