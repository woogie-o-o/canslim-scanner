"""analyst_consensus.py — yfinance upgrades_downgrades 단일 정규화기.

증권사 목표가(헤드라인 숫자)와 투자의견 변경 이력(화면 리스트)이
서로 다른 슬라이스를 쓰면 사용자에겐 '괴리'로 보인다. 이 모듈은 두
화면이 **반드시 같은 집합**을 쓰도록 단일 진실원천을 제공한다.

핵심 규칙:
  • 최신순 정렬을 신뢰하지 않고 GradeDate 로 내림차순 강제.
  • 증권사(Firm)당 1건 — 가장 최근 의견만. (한 하우스가 12행 중 5번
    나오면 평균이 그 하우스로 쏠리던 문제 제거.)
  • 목표가는 해당 증권사의 '가장 최근 유효(>0) currentPriceTarget'.
    최신 행이 등급만 재확인(목표가 공란)이어도 그 하우스의 직전
    목표가를 살려 평균에 반영 → 표시 리스트와 평균이 일치.
  • 최근성 우선: recency_days 이내 우선, 부족하면(min_firms 미만)
    날짜 무관 최근 max_firms 로 폴백(커버리지 얇은 종목 보호).

헤드라인 평균은 반환 rows 중 target>0 인 항목만의 평균이라,
'리스트에 보이는 목표가들의 평균 = 표시되는 증권사 목표가'가 성립한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def _to_float(v: Any) -> float:
    if v is None:
        return 0.0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f or f <= 0.0:  # NaN 또는 비양수
        return 0.0
    return f


def _row_date(idx_val: Any) -> datetime | None:
    try:
        if hasattr(idx_val, "to_pydatetime"):
            return idx_val.to_pydatetime().replace(tzinfo=None)
        return datetime.fromisoformat(str(idx_val).replace("Z", "")[:19])
    except (ValueError, TypeError):
        return None


def summarize_upgrades_downgrades(
    ud: Any,
    *,
    max_firms: int = 12,
    recency_days: int = 180,
    min_firms: int = 5,
) -> dict[str, Any]:
    """yfinance Ticker.upgrades_downgrades → 증권사별 최신 의견 컨센서스.

    Returns:
        {
          "rows": [ {firm, grade, from_grade, action,
                     target, prior_target, date}, ... ],  # 최신순, 증권사당 1건
          "mean_target": float,   # rows 중 target>0 평균 (없으면 0.0)
          "count": int,           # rows 길이(증권사 수)
          "target_count": int,    # rows 중 target>0 인 증권사 수
        }
    빈/유효치 없음 → 모든 값 0/빈 리스트.
    """
    empty = {"rows": [], "mean_target": 0.0, "count": 0, "target_count": 0}
    if ud is None:
        return empty
    try:
        if len(ud) == 0:
            return empty
    except TypeError:
        return empty

    cols = getattr(ud, "columns", [])
    has_cur = "currentPriceTarget" in cols
    has_prior = "priorPriceTarget" in cols

    # GradeDate(index) 내림차순 강제 — yfinance 정렬을 신뢰하지 않는다.
    try:
        if not ud.index.is_monotonic_decreasing:
            ud = ud.sort_index(ascending=False)
    except (AttributeError, TypeError):
        pass

    cutoff = datetime.now() - timedelta(days=recency_days)

    # 증권사별 최신 메타 + '가장 최근 유효 목표가'(직전 행에서라도 회수)
    by_firm: dict[str, dict] = {}
    order: list[str] = []
    for idx_val, row in ud.iterrows():
        firm_raw = str(row.get("Firm", "") or "").strip()
        if not firm_raw:
            continue
        key = firm_raw.lower()
        dt = _row_date(idx_val)
        tgt = _to_float(row.get("currentPriceTarget")) if has_cur else 0.0
        if key not in by_firm:
            by_firm[key] = {
                "firm": firm_raw,
                "grade": str(row.get("ToGrade", "") or ""),
                "from_grade": str(row.get("FromGrade", "") or ""),
                "action": str(row.get("Action", "") or ""),
                "target": tgt,  # 최신 행 목표가(없으면 아래서 보강)
                "prior_target": _to_float(row.get("priorPriceTarget")) if has_prior else 0.0,
                "date": dt.strftime("%Y-%m-%d") if dt else "",
                "_dt": dt,
            }
            order.append(key)
        else:
            # 이미 최신 메타 확보 — 목표가만 공란이면 직전 유효치로 보강
            if by_firm[key]["target"] <= 0.0 and tgt > 0.0:
                by_firm[key]["target"] = tgt

    ranked = [by_firm[k] for k in order]  # 이미 최신순

    # 최근성 우선, 부족하면 날짜 무관 폴백
    recent = [r for r in ranked if r["_dt"] is not None and r["_dt"] >= cutoff]
    chosen = recent if len(recent) >= min_firms else ranked
    chosen = chosen[:max_firms]

    tgts = [r["target"] for r in chosen if r["target"] > 0.0]
    mean_t = round(sum(tgts) / len(tgts), 2) if tgts else 0.0

    rows = []
    for r in chosen:
        rows.append({
            "firm": r["firm"],
            "grade": r["grade"],
            "from_grade": r["from_grade"],
            "action": r["action"],
            "target": r["target"] if r["target"] > 0.0 else None,
            "prior_target": r["prior_target"] if r["prior_target"] > 0.0 else None,
            "date": r["date"],
        })

    return {
        "rows": rows,
        "mean_target": mean_t,
        "count": len(rows),
        "target_count": len(tgts),
    }


if __name__ == "__main__":
    import yfinance as yf

    for tk in ("AAPL", "NVDA"):
        s = summarize_upgrades_downgrades(yf.Ticker(tk).upgrades_downgrades)
        print(f"{tk}: firms={s['count']} w/target={s['target_count']} "
              f"mean={s['mean_target']}")
        for r in s["rows"][:5]:
            print("  ", r["date"], r["firm"], r["grade"], r["target"])
    print("ANALYST_CONSENSUS OK")
