"""MF-002: factor-orthogonalization — Score/Moat/StoryRisk 3축 상관 모니터.

월가 패널(GS QIS) 권고: 점수·해자·스토리리스크는 직교 팩터여야 한다.
|ρ| > 0.7 인 페어가 발견되면 stderr 경고 — 신호 누수·중복의 사전 경보.

사용:
  python -m web_app.tools.factor_correlation [snapshot.json]
  snapshot 인자 없으면 최신 스냅샷 자동 탐색.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

MOAT_ORDINAL = {
    "INTANGIBLE": 4, "SWITCHING": 4, "NETWORK": 3,
    "COST": 2, "EFFICIENT_SCALE": 2, "NONE": 0,
}


def _to_ordinal(cat: str | None) -> int:
    if not cat:
        return 0
    return MOAT_ORDINAL.get(str(cat).upper(), 0)


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


def _spearman(xs: list[float], ys: list[float]) -> float:
    def rank(vs: list[float]) -> list[float]:
        idx = sorted(range(len(vs)), key=lambda i: vs[i])
        r = [0.0] * len(vs)
        for new_rank, old_idx in enumerate(idx):
            r[old_idx] = float(new_rank)
        return r
    return _pearson(rank(xs), rank(ys))


def extract_factors(rows: list[dict[str, Any]]) -> dict[str, list[float]]:
    score, moat, story = [], [], []
    for r in rows:
        ts = r.get("TotalScore")
        if not isinstance(ts, (int, float)):
            continue
        score.append(float(ts))
        moat.append(float(_to_ordinal(r.get("MoatCategory"))))
        md = r.get("MoatData") or {}
        sr = md.get("story_risk")
        if sr is None:
            sr = r.get("IsSpeculativeTheme")
        story.append(1.0 if sr else 0.0)
    return {"Score": score, "Moat": moat, "StoryRisk": story}


def correlate(factors: dict[str, list[float]]) -> list[dict[str, Any]]:
    names = list(factors.keys())
    out = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            xs, ys = factors[a], factors[b]
            n = min(len(xs), len(ys))
            if n < 2:
                continue
            out.append({
                "pair": f"{a}~{b}",
                "n": n,
                "pearson": round(_pearson(xs[:n], ys[:n]), 3),
                "spearman": round(_spearman(xs[:n], ys[:n]), 3),
            })
    return out


def report(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    factors = extract_factors(rows)
    corr = correlate(factors)
    warnings = []
    for c in corr:
        if abs(c["pearson"]) > 0.7 or abs(c["spearman"]) > 0.7:
            warnings.append(f"[WARN] high correlation {c['pair']}: pearson={c['pearson']} spearman={c['spearman']}")
    return corr, warnings


def _find_latest_snapshot() -> Path | None:
    base = Path(__file__).resolve().parents[1] / "snapshots"
    if not base.exists():
        base = Path(__file__).resolve().parents[1] / "snapshots.bak"
    if not base.exists():
        return None
    snaps = sorted(base.glob("scanner_*.json"))
    return snaps[-1] if snaps else None


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else _find_latest_snapshot()
    if not path or not path.exists():
        print("snapshot not found", file=sys.stderr)
        return 2
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    rows = data if isinstance(data, list) else data.get("rows", [])
    corr, warns = report(rows)
    print(json.dumps({"snapshot": str(path), "correlations": corr}, ensure_ascii=False, indent=2))
    for w in warns:
        print(w, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
