# -*- coding: utf-8 -*-
"""bottleneck_ic.py — 병목 등급 forward IC 스냅샷 추적.

병목 방법론의 예측력은 과거 시점 패널이 없어 소급 백테스트가 불가하다. 그래서
**오늘의 병목 등급을 스냅샷**해 두고, 시간이 지난 뒤 실제 수익률과의 상관(IC)을 계산해
"병목 등급/진입 게이트가 미래 수익을 예측하는가"를 forward로 검증한다.

흐름:
    스캔 완료 → record_snapshot(results, date) → JSONL 누적
    수 주 후 → compute_forward_ic(snapshots, 현재가격) → Spearman IC + 게이트 통과군 수익 비교

CLI:
    python bottleneck_ic.py snapshot   # 캐시된 최근 스캔에서 스냅샷 1건 적재
    python bottleneck_ic.py report     # 성숙한 스냅샷으로 forward IC 리포트
"""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any, Callable

DEFAULT_STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             ".bottleneck_snapshots", "snapshots.jsonl")


def _as_date(d: Any) -> dt.date:
    if isinstance(d, dt.date):
        return d
    return dt.date.fromisoformat(str(d))


def record_snapshot(results: list[dict[str, Any]], *, date: Any, store_path: str = DEFAULT_STORE) -> int:
    """병목 종목(BottleneckScore>0)을 JSONL에 1행씩 누적. 적재 건수 반환.

    각 행: {date, ticker, price, bottleneck_score, entry_pass, finvalue}
    """
    d = _as_date(date).isoformat()
    os.makedirs(os.path.dirname(store_path), exist_ok=True)
    n = 0
    with open(store_path, "a", encoding="utf-8") as f:
        for r in results:
            score = r.get("BottleneckScore") or 0
            if score <= 0:
                continue
            price = r.get("Price")
            if not price or price <= 0:
                continue
            row = {
                "date": d,
                "ticker": str(r.get("Ticker", "")),
                "price": float(price),
                "bottleneck_score": int(score),
                "entry_pass": bool(r.get("BottleneckEntryPass", False)),
                "finvalue": r.get("FinValue"),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def load_snapshots(store_path: str = DEFAULT_STORE) -> list[dict[str, Any]]:
    if not os.path.exists(store_path):
        return []
    rows = []
    with open(store_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or len(ys) != n:
        return None

    def _ranks(vals: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: vals[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks

    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / (sxx ** 0.5 * syy ** 0.5)


def compute_forward_ic(
    snapshots: list[dict[str, Any]],
    current_price_fn: Callable[[str], float | None],
    *,
    asof: Any,
    min_days: int = 21,
) -> dict[str, Any]:
    """성숙한(≥min_days 경과) 스냅샷의 forward 수익률 vs 병목 등급 IC.

    Returns:
        ``{n_matured, ic_bottleneck, ic_finvalue, gate_pass_mean_ret,
           gate_fail_mean_ret, mean_ret}`` (수익률은 소수, 0.30=+30%).
    """
    asof_d = _as_date(asof)
    scores: list[float] = []
    fins: list[float] = []
    fin_rets: list[float] = []
    rets: list[float] = []
    pass_rets: list[float] = []
    fail_rets: list[float] = []

    for s in snapshots:
        try:
            age = (asof_d - _as_date(s["date"])).days
        except Exception:
            continue
        if age < min_days:
            continue
        cur = current_price_fn(s["ticker"])
        snap_px = s.get("price")
        if not cur or not snap_px or snap_px <= 0:
            continue
        ret = cur / snap_px - 1.0
        scores.append(float(s.get("bottleneck_score") or 0))
        rets.append(ret)
        if s.get("entry_pass"):
            pass_rets.append(ret)
        else:
            fail_rets.append(ret)
        fv = s.get("finvalue")
        if isinstance(fv, (int, float)):
            fins.append(float(fv))
            fin_rets.append(ret)

    def _mean(xs: list[float]) -> float | None:
        return sum(xs) / len(xs) if xs else None

    return {
        "n_matured": len(rets),
        "ic_bottleneck": _spearman(scores, rets),
        "ic_finvalue": _spearman(fins, fin_rets),
        "gate_pass_mean_ret": _mean(pass_rets),
        "gate_fail_mean_ret": _mean(fail_rets),
        "mean_ret": _mean(rets),
        "n_pass": len(pass_rets),
        "n_fail": len(fail_rets),
    }


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    try:
        import sys as _sys
        _sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="병목 등급 forward IC 추적")
    parser.add_argument("cmd", choices=["snapshot", "report"])
    parser.add_argument("--min-days", type=int, default=21)
    parser.add_argument("--store", default=DEFAULT_STORE)
    args = parser.parse_args()

    if args.cmd == "snapshot":
        # 캐시된 최근 전체 스캔 결과에서 스냅샷 (네트워크 최소화)
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_app"))
        import engine_adapter
        eng = engine_adapter.EngineAdapter() if hasattr(engine_adapter, "EngineAdapter") else None
        results = []
        try:
            results = eng.scan_all(prefer_cache=True, cache_only=True) if eng else []
        except Exception as e:
            print(f"스캔 캐시 로드 실패: {e}")
        today = dt.date.today().isoformat()
        n = record_snapshot(results, date=today, store_path=args.store)
        print(f"[snapshot] {today} · {n}개 병목 종목 적재 → {args.store}")
        return

    # report
    snaps = load_snapshots(args.store)
    if not snaps:
        print("스냅샷 없음. 먼저 `snapshot`을 며칠간 누적하세요.")
        return
    import yfinance as yf

    def price(t: str) -> float | None:
        try:
            h = yf.Ticker(t).history(period="5d")["Close"]
            return float(h.iloc[-1]) if len(h) else None
        except Exception:
            return None

    out = compute_forward_ic(snaps, price, asof=dt.date.today(), min_days=args.min_days)
    print(f"=== 병목 forward IC ({len(snaps)} 스냅샷, 성숙 {out['n_matured']}, ≥{args.min_days}일) ===")
    if not out["n_matured"]:
        print("아직 성숙한 스냅샷이 없음 — 시간이 더 필요합니다.")
        return
    def _f(x): return f"{x:+.3f}" if isinstance(x, float) else "N/A"
    def _p(x): return f"{x*100:+.1f}%" if isinstance(x, float) else "N/A"
    print(f"  병목점수 IC(Spearman): {_f(out['ic_bottleneck'])}")
    print(f"  FinValue IC          : {_f(out['ic_finvalue'])}")
    print(f"  게이트 통과군 평균수익: {_p(out['gate_pass_mean_ret'])} (n={out['n_pass']})")
    print(f"  게이트 탈락군 평균수익: {_p(out['gate_fail_mean_ret'])} (n={out['n_fail']})")
    print(f"  전체 평균수익        : {_p(out['mean_ret'])}")
    print("  IC>0 이면 고점수가 실제로 더 올랐다는 뜻. 스냅샷이 쌓일수록 신뢰도↑.")


if __name__ == "__main__":
    main()
