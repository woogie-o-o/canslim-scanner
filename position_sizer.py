# -*- coding: utf-8 -*-
"""포지션 사이징 — 계좌 × 리스크 % × 손절폭 → 권장 수량."""
from __future__ import annotations
from typing import Dict


def calc_position(equity: float, risk_pct: float, entry: float, stop: float,
                  target: float | None = None) -> Dict[str, float]:
    """
    Args:
        equity: 계좌 총자산
        risk_pct: 한 거래당 허용 손실 % (예: 1.0 = 1%)
        entry: 진입가
        stop:  손절가 (entry보다 낮아야 — 롱 기준)
        target: (선택) 목표가 — R:R 계산용
    Returns:
        {qty, risk_amount, notional, stop_pct, r_multiple_target}
    """
    if equity <= 0 or risk_pct <= 0 or entry <= 0:
        raise ValueError("equity/risk_pct/entry must be positive")
    if stop >= entry:
        raise ValueError(f"stop({stop}) must be < entry({entry}) for long position")

    risk_amount_max = equity * (risk_pct / 100.0)
    risk_per_share  = entry - stop
    qty             = int(risk_amount_max // risk_per_share)
    risk_amount     = qty * risk_per_share
    notional        = qty * entry
    stop_pct        = (entry - stop) / entry * 100.0
    r_target        = None
    if target is not None and target > entry:
        r_target = (target - entry) / risk_per_share

    return {
        "qty": qty,
        "risk_amount": round(risk_amount, 2),
        "notional": round(notional, 2),
        "stop_pct": round(stop_pct, 2),
        "r_multiple_target": round(r_target, 2) if r_target else None,
    }


def calc_rr(entry: float, stop: float, target: float) -> float:
    """R:R 비율 — 1.0 미만이면 진입 금지 권장."""
    if entry <= stop or target <= entry:
        return 0.0
    return round((target - entry) / (entry - stop), 2)


def suggest_targets(entry: float, stop: float, atr: float | None = None) -> Dict[str, float]:
    """ATR 기반 1차/2차 익절 제안."""
    risk = entry - stop
    return {
        "t1_1R": round(entry + risk * 1.0, 2),     # 1R = 본전 + 리스크
        "t2_2R": round(entry + risk * 2.0, 2),
        "t3_3R": round(entry + risk * 3.0, 2),
    }


if __name__ == "__main__":
    r = calc_position(10000, 1, 100, 97)
    assert r["qty"] == 33, r
    assert abs(r["risk_amount"] - 99.0) < 0.01, r
    print("[OK] position_sizer:", r)

    try:
        calc_position(10000, 1, 100, 105)
        raise AssertionError("should have raised")
    except ValueError:
        print("[OK] stop>=entry rejected")

    print("RR(100, 97, 109) =", calc_rr(100, 97, 109))  # 3.0
    print("targets:", suggest_targets(100, 97))
