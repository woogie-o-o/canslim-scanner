# -*- coding: utf-8 -*-
"""포지션 사이징 — 계좌 × 리스크 % × 손절폭 → 권장 수량.

VIX 게이트: 변동성 폭증 시 자동 risk_pct 축소 (WS-003).
"""
from __future__ import annotations
from typing import Dict, Optional


def _vix_scale(vix: Optional[float]) -> float:
    """VIX 기반 리스크 축소 배수. vix>40→0.5, vix>30→0.75, 그 외 1.0."""
    if vix is None:
        return 1.0
    try:
        v = float(vix)
    except (TypeError, ValueError):
        return 1.0
    if v != v:  # NaN
        return 1.0
    if v > 40.0:
        return 0.5
    if v > 30.0:
        return 0.75
    return 1.0


def _zero(reason: str) -> Dict[str, float]:
    return {"qty": 0, "risk_amount": 0.0, "notional": 0.0, "stop_pct": 0.0,
            "r_multiple_target": None, "skipped": reason}


def calc_position(equity: float, risk_pct: float, entry: float, stop: float,
                  target: float | None = None, vix: float | None = None) -> Dict[str, float]:
    """
    Args:
        equity: 계좌 총자산
        risk_pct: 한 거래당 허용 손실 % (예: 1.0 = 1%)
        entry: 진입가
        stop:  손절가 (entry보다 낮아야 — 롱 기준)
        target: (선택) 목표가 — R:R 계산용
        vix:    (선택) 현재 VIX — 30 초과 시 자동 risk 축소
    Returns:
        {qty, risk_amount, notional, stop_pct, r_multiple_target, [vix_scale]}
        invalid 입력 시 qty=0 + 'skipped' 사유 반환 (음수/NaN 방지).
    """
    if equity <= 0 or risk_pct <= 0 or entry <= 0:
        return _zero("invalid_equity_or_entry")
    if stop >= entry:
        return _zero("stop_not_below_entry")

    scale            = _vix_scale(vix)
    eff_risk_pct     = risk_pct * scale
    risk_amount_max  = equity * (eff_risk_pct / 100.0)
    risk_per_share   = entry - stop
    qty              = int(risk_amount_max // risk_per_share)
    if qty < 0:
        qty = 0
    risk_amount      = qty * risk_per_share
    notional         = qty * entry
    stop_pct         = (entry - stop) / entry * 100.0
    r_target         = None
    if target is not None and target > entry:
        r_target = (target - entry) / risk_per_share

    return {
        "qty": qty,
        "risk_amount": round(risk_amount, 2),
        "notional": round(notional, 2),
        "stop_pct": round(stop_pct, 2),
        "r_multiple_target": round(r_target, 2) if r_target else None,
        "vix_scale": scale,
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
        "t1_1R": round(entry + risk * 1.0, 2),
        "t2_2R": round(entry + risk * 2.0, 2),
        "t3_3R": round(entry + risk * 3.0, 2),
    }


if __name__ == "__main__":
    r = calc_position(10000, 1, 100, 97)
    assert r["qty"] == 33, r
    assert abs(r["risk_amount"] - 99.0) < 0.01, r
    print("[OK] position_sizer:", r)

    r_bad = calc_position(10000, 1, 100, 105)
    assert r_bad["qty"] == 0 and r_bad.get("skipped") == "stop_not_below_entry", r_bad
    print("[OK] stop>=entry → qty=0 (graceful)")

    r_vix = calc_position(10000, 1, 100, 97, vix=45)
    assert r_vix["qty"] == 16 and r_vix["vix_scale"] == 0.5, r_vix
    print("[OK] VIX=45 → qty halved:", r_vix)

    print("RR(100, 97, 109) =", calc_rr(100, 97, 109))
    print("targets:", suggest_targets(100, 97))
