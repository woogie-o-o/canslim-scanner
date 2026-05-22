"""
#4 섹터/시총 집중도 진단
========================
오늘 스캐너 GREEN/STRONG 종목들의 섹터 분포 확인.
한 섹터 >=40%면 stock-picking이 아니라 factor bet.

AST 기반: quant_nexus_v20.QuantNexusApp.__init__ 를 실행하지 않고
self.kr_sectors / self.us_sectors 의 dict 리터럴을 직접 파싱.
"""
from __future__ import annotations
import sys
import io
import ast
import json
from pathlib import Path
from collections import Counter

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "web_app" / "snapshots"
SRC  = ROOT / "quant_nexus_v20.py"


def extract_sector_dicts(src_path: Path) -> dict[str, dict]:
    """Find `self.kr_sectors = {...}` and `self.us_sectors = {...}` assignments
    via AST, eval their literal RHS to native dicts."""
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (isinstance(tgt, ast.Attribute)
                    and isinstance(tgt.value, ast.Name)
                    and tgt.value.id == "self"
                    and tgt.attr in ("kr_sectors", "us_sectors", "eu_sectors")):
                    try:
                        out[tgt.attr] = ast.literal_eval(node.value)
                    except Exception as e:
                        print(f"  [warn] {tgt.attr} literal_eval 실패: {e}")
    return out


def build_ticker_map(sector_dict: dict) -> dict[str, str]:
    """{TopCat: {SubCat: [tickers]}} -> {ticker: TopCat}"""
    out = {}
    for top, subs in sector_dict.items():
        if not isinstance(subs, dict):
            continue
        for sub, tickers in subs.items():
            if not isinstance(tickers, (list, tuple)):
                continue
            for t in tickers:
                out.setdefault(t, top)
    return out


def normalize_ticker(tk: str, market: str) -> list[str]:
    """스냅샷 ticker 와 sector map ticker 의 표기 차이를 흡수해 후보 키 리스트 반환."""
    candidates = [tk]
    if market == "KR":
        # 005930.KS -> 005930
        if tk.endswith(".KS"):
            candidates.append(tk[:-3])
        if tk.endswith(".KQ"):
            candidates.append(tk[:-3])
        # 005930 -> 005930.KS
        if tk.isdigit():
            candidates += [f"{tk}.KS", f"{tk}.KQ"]
    return candidates


def analyze_snapshot(path: Path, ticker_map: dict, market: str):
    if not path.exists():
        print(f"[{market}] no snapshot: {path}")
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = [(tk, info.get("score", 0), info.get("rank", 9999)) for tk, info in data.items()]
    rows.sort(key=lambda x: x[2])

    total = len(rows)
    print(f"\n[{market}] 스냅샷: {path.name}  종목 {total}개  매핑소스 {len(ticker_map)} tickers")

    for cutoff_label, top_n in [("TOP20", 20), ("TOP50", 50), ("TOP100", 100)]:
        head = rows[:top_n]
        sec_count: Counter = Counter()
        unmatched = []
        for tk, sc, rk in head:
            sec = None
            for cand in normalize_ticker(tk, market):
                if cand in ticker_map:
                    sec = ticker_map[cand]
                    break
            if sec is None:
                unmatched.append(tk)
            else:
                sec_count[sec] += 1
        print(f"\n  {cutoff_label} ({top_n}개) 섹터 분포:")
        for sec, n in sec_count.most_common(10):
            pct = n / top_n * 100
            flag = " [집중!]" if pct >= 40 else (" [주의]" if pct >= 25 else "")
            print(f"    {sec:30s}: {n:3d} ({pct:5.1f}%){flag}")
        if unmatched:
            sample = ", ".join(unmatched[:5])
            print(f"    (섹터 미매칭: {len(unmatched)}개 — 예: {sample})")

        if sec_count:
            n_total = sum(sec_count.values())
            hhi = sum((c / n_total) ** 2 for c in sec_count.values())
            if hhi < 0.15:    diag = "매우 분산 OK"
            elif hhi < 0.25:  diag = "분산 OK"
            elif hhi < 0.40:  diag = "집중 주의"
            else:             diag = "극도 집중 RISK"
            print(f"    HHI (집중도): {hhi:.3f}  ({diag})")


def main():
    print(f"AST 파싱: {SRC.name}")
    raw = extract_sector_dicts(SRC)
    kr_map = build_ticker_map(raw.get("kr_sectors", {}))
    us_map = build_ticker_map(raw.get("us_sectors", {}))
    print(f"섹터 매핑: KR {len(kr_map)} tickers, US {len(us_map)} tickers")
    if not kr_map and not us_map:
        print("[FATAL] 섹터 dict 추출 실패")
        return

    today_kr = SNAP / "scanner_KR_2026-05-15.json"
    today_us = SNAP / "scanner_US_2026-05-15.json"
    analyze_snapshot(today_kr, kr_map, "KR")
    analyze_snapshot(today_us, us_map, "US")


if __name__ == "__main__":
    main()
