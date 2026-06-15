# -*- coding: utf-8 -*-
"""bottleneck_consensus.py — 6렌즈 공급망 병목 합의 패널 (코드화).

스캐너 유니버스에서 병목 후보를 추출(결정적)하고, 6개 클론 스킬이 토론→합의하도록
패널 브리프를 생성한다. 실제 토론은 LLM 멀티에이전트(Workflow)로 on-demand 실행하며,
이 모듈은 그 입력(후보 + 렌즈별 프롬프트 + 의장 프롬프트)을 재현 가능하게 만든다.

흐름:
    스캐너 유니버스 설명 → top_bottleneck_candidates() → build_consensus_panel()
    → (Workflow) 6렌즈 1라운드 + 의장 2라운드 → 합의 선별

CLI:
    python bottleneck_consensus.py            # 유니버스에서 후보 추출 + 패널 브리프 출력
    python bottleneck_consensus.py --json     # JSON 출력
"""
from __future__ import annotations

import os
from typing import Any

import bottleneck_screen as _bs

# 클론된 6개 스킬 팩 기본 위치 (.kkirikkiri/serenity-pack/<dir>)
DEFAULT_PACK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".kkirikkiri", "serenity-pack")

# 6렌즈 — 각 저장소의 실제 차별점(README/파일 확인 기반)
PANEL_LENSES: list[dict[str, str]] = [
    {"label": "yan-labs (원칙·실적기록 라이브러리)", "dir": "serenity-aleabitoreddit",
     "focus": "12개 전이 원칙 + 종목별 thesis + conviction tier + 실적기록 대조"},
    {"label": "0xagata (기관 진입 전 스몰캡)", "dir": "serenity-skill",
     "focus": "컨센서스 형성 전 발굴, 기관 진입 비대칭성"},
    {"label": "W-Y-P (BOM/OSINT 심층 + 재무·GAAP)", "dir": "Serenity-aleabitoreddit-skill",
     "focus": "substrate·epiwafer·laser 다단계 BOM 분해 + 재무 번역 + 희석/GAAP 품질"},
    {"label": "xvhaoran (베이지안 갱신)", "dir": "Serenity.SKILL",
     "focus": "prior→증거→posterior, 확정 공시 vs 의견 구분, 크로스마켓"},
    {"label": "fadewalk (마이크로캡 탄력성)", "dir": "serenity-stock-choke",
     "focus": "호르무즈식 정가권, 마이크로캡 탄력성, 롱/숏 신호"},
    {"label": "destiny (세레니티+매크로+기술 융합)", "dir": "stock-skill",
     "focus": "무엇(병목)+언제(매크로 타이밍)+어떻게(기술적 실행) 3중 융합"},
]


def top_bottleneck_candidates(
    desc_map: dict[str, str],
    *,
    n: int = 20,
    min_score: int = 60,
    sectors: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """유니버스 설명 맵에서 병목 근접도 상위 후보 추출.

    Args:
        desc_map: ``{ticker: 사업설명}``.
        n: 최대 후보 수.
        min_score: 이 점수 미만은 제외(0점=무매칭은 항상 제외).
        sectors: ``{ticker: 섹터}`` (선택, 매칭 보강).

    Returns:
        score 내림차순 ``[{ticker, desc, score, top_layer, layers}]``.
    """
    sectors = sectors or {}
    out: list[dict[str, Any]] = []
    for tk, desc in desc_map.items():
        bp = _bs.bottleneck_proximity(desc or "", sectors.get(tk, ""))
        if bp["score"] >= max(1, min_score):
            out.append({
                "ticker": tk,
                "desc": desc or "",
                "score": bp["score"],
                "top_layer": bp["top_layer"],
                "layers": bp["layers"],
            })
    out.sort(key=lambda c: c["score"], reverse=True)
    return out[:n]


def _candidate_block(candidates: list[dict[str, Any]]) -> str:
    lines = []
    for c in candidates:
        lines.append(f"{c['ticker']} — {c['desc']} "
                     f"(병목근접 {c['score']}, {c.get('top_layer') or '-'})")
    return "\n".join(lines)


def _round1_prompt(lens: dict[str, str], skill_path: str, block: str) -> str:
    return (
        f'너는 "{lens["label"]}" 방법론을 그대로 적용하는 공급망 병목(scarce layer) 애널리스트다.\n'
        f"고유 초점: {lens['focus']}\n"
        f"1) 먼저 클론된 스킬을 읽어 방법론을 정확히 내재화해라(SKILL.md + references/ 핵심):\n"
        f"   경로: {skill_path}\n"
        f"2) 그 방법론으로 아래 후보를 평가해라. 후보는 스캐너 유니버스에서 '병목 키워드'로 "
        f"1차 추출된 것이라 거짓양성(반도체 무관 '패키징'·소모품)과 다운스트림(완성 GPU/메모리)도 섞여 있다.\n"
        f"3) 진짜 우회 불가능한 병목 노드만 conviction 1~5로 랭킹하고, 무관/다운스트림/근거부족은 reject 해라.\n\n"
        f"후보:\n{block}\n\n"
        f"규칙: 정성 평가만. 없는 수치·필링·계약 날조 금지. 매수/매도 지시 금지. 한국어."
    )


CHAIR_PROMPT = (
    "너는 6개 공급망 병목 방법론 패널의 의장이다. 6개 렌즈의 독립 평가(JSON)를 받아 토론·합의시켜라.\n"
    "- consensus_selection: 여러 렌즈가 공통으로 고른 종목을 lens_votes(동의 렌즈 수) 내림차순으로. "
    "결합 근거 + 핵심 반대(어느 렌즈가 왜 의심) 포함.\n"
    "- dissent_cards: 렌즈 간 불일치는 다수결로 뭉개지 말고 보존. 판가름 데이터 명시.\n"
    "- rejected_false_positives: 대다수가 거른 거짓양성(반도체 무관/다운스트림)과 이유.\n"
    "- summary: 합의 핵심 + 스캐너 유니버스 한계 한 줄.\n"
    "매수/매도 지시 금지. 한국어."
)


def build_consensus_panel(
    candidates: list[dict[str, Any]],
    *,
    pack_dir: str = DEFAULT_PACK_DIR,
) -> dict[str, Any]:
    """6렌즈 합의 패널 브리프(토론 입력) 생성.

    Returns:
        ``{"candidate_block", "lenses": [{label,dir,focus,skill_path,round1_prompt}],
           "chair_prompt"}``
    """
    block = _candidate_block(candidates)
    lenses = []
    for L in PANEL_LENSES:
        skill_path = f"{pack_dir}/{L['dir']}"
        lenses.append({
            **L,
            "skill_path": skill_path,
            "round1_prompt": _round1_prompt(L, skill_path, block),
        })
    return {"candidate_block": block, "lenses": lenses, "chair_prompt": CHAIR_PROMPT}


def _load_universe() -> dict[str, str]:
    """스캐너 유니버스 설명 맵(US_DESC + KR_DESC)을 합쳐 로드."""
    import quant_nexus_v20 as qn
    App = qn.QuantNexusApp
    merged: dict[str, str] = {}
    merged.update(getattr(App, "US_DESC", {}) or {})
    merged.update(getattr(App, "KR_DESC", {}) or {})
    return merged


def main() -> None:
    import argparse
    import json
    try:  # Windows cp949 콘솔에서도 한글/em-dash 출력 안전
        import sys as _sys
        _sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="공급망 병목 합의 패널 브리프 생성")
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--min-score", type=int, default=60)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    universe = _load_universe()
    cands = top_bottleneck_candidates(universe, n=args.n, min_score=args.min_score)
    panel = build_consensus_panel(cands)

    if args.json:
        print(json.dumps({"candidates": cands, "panel": panel}, ensure_ascii=False, indent=2))
        return

    print(f"=== 병목 후보 {len(cands)} (유니버스 {len(universe)}종목, min={args.min_score}) ===")
    print(panel["candidate_block"])
    print("\n=== 패널 렌즈 ===")
    for L in panel["lenses"]:
        print(f"- {L['label']} → {L['skill_path']}")
    print("\n6렌즈 1라운드(병렬) + 의장 2라운드(합의)로 Workflow 실행하면 합의 선별이 나온다.")


if __name__ == "__main__":
    main()
