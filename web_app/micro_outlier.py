"""MF-004: microstructure-flag — 갭/유동성/스프레드 이상치 격리.

월가 패널(Citadel) 권고: 마이크로구조 이상치는 진입 alpha 평가에 노이즈로 들어간다.
- gap_pct > 10%: 갭 폭주
- volume / avg20 < 0.2: 거래량 위축
- spread% / atr% > 0.5: 스프레드 비대

플래그만 부착, 정렬 시 -5 페널티. 유니버스에서 제거하지는 않음 (사용자가 결정).
"""

from __future__ import annotations

from typing import Any

PENALTY = -5.0


def is_micro_outlier(row: dict[str, Any]) -> tuple[bool, str]:
    """row 로부터 (이상치 여부, 사유) 반환."""
    if not row:
        return (False, "")

    reasons = []

    gap = row.get("GapPct") or row.get("gap_pct")
    if isinstance(gap, (int, float)) and abs(gap) > 10:
        reasons.append(f"gap={gap:.1f}%")

    vol = row.get("Volume") or row.get("volume")
    avg20 = row.get("AvgVol20") or row.get("avg_volume_20")
    if isinstance(vol, (int, float)) and isinstance(avg20, (int, float)) and avg20 > 0:
        ratio = vol / avg20
        if ratio < 0.2:
            reasons.append(f"vol/avg20={ratio:.2f}<0.2")

    spread_pct = row.get("SpreadPct") or row.get("spread_pct")
    atr_pct = row.get("ATR_Pct") or row.get("atr_pct")
    if isinstance(spread_pct, (int, float)) and isinstance(atr_pct, (int, float)) and atr_pct > 0:
        ratio = spread_pct / atr_pct
        if ratio > 0.5:
            reasons.append(f"spread/atr={ratio:.2f}>0.5")

    if reasons:
        return (True, " / ".join(reasons))
    return (False, "")


def annotate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """rows 각 dict 에 MicroOutlier/MicroOutlierReason 필드 + 정렬 페널티 적용."""
    if not rows:
        return rows
    for row in rows:
        if not isinstance(row, dict):
            continue
        flag, reason = is_micro_outlier(row)
        row["MicroOutlier"] = flag
        row["MicroOutlierReason"] = reason if flag else ""
        if flag:
            ts = row.get("TotalScore")
            if isinstance(ts, (int, float)):
                row.setdefault("_RawTotalScoreMicro", float(ts))
                row["TotalScore"] = max(0.0, float(ts) + PENALTY)
    return rows
