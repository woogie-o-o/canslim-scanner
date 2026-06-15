# -*- coding: utf-8 -*-
"""bottleneck_screen.py — 공급망 병목(scarce-layer) 키워드 근접도 스크리너.

공개 방법론(공급망 병목/핵심노드 헌팅)의 **초벌 패스 프록시**:
사업 설명·섹터 텍스트를 '희소층(scarce layer)' 키워드 택소노미와 매칭해
병목 근접도(0~100)와 매칭 레이어를 산출한다.

⚠ 이것은 증거기반 심층 판단이 아니라 **후보 발굴(initial pass)** 용이다.
   "시스템 확장 시 우회하기 가장 어려운 상류 환경에 가까운가"를 키워드로 근사할 뿐,
   공급사 수·인증 장벽·증설 난이도 같은 실제 병목 판단은 종목별 심층 분석(LLM+공시)에서 한다.

설계 원칙:
   - 상류 희소층(장비·소재·소모품·후공정·HBM)일수록 가중치↑.
   - 다운스트림/스토리(플랫폼·완성품·서비스)는 키워드 미매칭 → 0점으로 자연 강등.
   - 한/영 키워드 모두 지원(국내 종목 설명 + 영문 사업요약).

순수 함수 — 네트워크/IO 없음.
"""
from __future__ import annotations

import re
from typing import Any

# 짧은 영문 키워드(gan/sic/inp 등)가 단어 내부(Morgan·ASIC·input)에 박혀 생기는
# 오탐 방지를 위해, ASCII 키워드는 영숫자 경계 매칭한다. 한글 키워드는 부분일치 유지
# (한글은 단어 경계 개념이 약하고 복합어 매칭이 필요 — 예: '패키지기판'의 '기판').
_KW_PAT_CACHE: dict[str, "re.Pattern[str]"] = {}


def _kw_in(kw: str, blob: str) -> bool:
    """키워드가 텍스트에 의미있게 등장하는가. ASCII는 영숫자 경계, 한글은 부분일치."""
    if kw.isascii():
        pat = _KW_PAT_CACHE.get(kw)
        if pat is None:
            pat = re.compile(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])")
            _KW_PAT_CACHE[kw] = pat
        return pat.search(blob) is not None
    return kw in blob

# 레이어명 → {weight(1~5, 클수록 희소·상류), keywords(소문자/한글)}
SCARCE_LAYERS: dict[str, dict[str, Any]] = {
    "메모리/HBM·인터커넥트": {
        "weight": 5,
        "keywords": ["hbm", "고대역폭메모리", "고대역폭", "인터커넥트", "interconnect",
                     "tsv", "실리콘관통전극"],
    },
    "후공정/어드밴스드 패키징": {
        "weight": 5,
        "keywords": ["advanced packaging", "어드밴스드 패키징", "후공정", "패키징",
                     "하이브리드 본딩", "hybrid bonding", "본딩", "재배선", "rdl",
                     "범프", "bump", "2.5d", "3d 적층", "칩렛", "chiplet"],
    },
    "전공정 소재·소모품": {
        "weight": 5,
        "keywords": ["cmp", "슬러리", "slurry", "포토레지스트", "photoresist", "전구체",
                     "precursor", "특수가스", "식각액", "박리액", "감광액", "소모품"],
    },
    "식각/증착·공정장비": {
        "weight": 5,
        "keywords": ["식각", "etch", "증착", "deposition", "cvd", "ald", "노광",
                     "litho", "리소그래피", "이온주입", "감박", "thinning", "세정장비"],
    },
    "반도체 장비/검사·테스트": {
        "weight": 5,
        "keywords": ["반도체 장비", "검사 장비", "테스트 핸들러", "핸들러", "프로버",
                     "probe", "metrology", "inspection", "테스트 소켓", "번인",
                     "burn-in", "후공정 장비"],
    },
    "기판/substrate·PCB·CCL": {
        "weight": 4,
        "keywords": ["substrate", "패키지기판", "패키지 기판", "fc-bga", "ccl",
                     "동박적층판", "유리기판", "glass substrate", "pcb", "기판"],
    },
    "광통신/CPO·실리콘포토닉스": {
        "weight": 4,
        "keywords": ["cpo", "co-packaged", "silicon photonics", "실리콘 포토닉스",
                     "광트랜시버", "transceiver", "optical", "광모듈", "광통신", "광소자"],
    },
    "화합물반도체/기판·에피·레이저": {
        "weight": 5,
        "keywords": ["inp", "gaas", "화합물반도체", "화합물 반도체", "compound semi",
                     "epiwafer", "에피웨이퍼", "에피택시", "mocvd", "dfb", "eml",
                     "광원", "cw 레이저", "laser diode", "사파이어 기판", "soi 웨이퍼"],
    },
    "전력/냉각 인프라": {
        "weight": 4,
        "keywords": ["전력반도체", "sic", "gan", "전력기기", "변압기", "액침냉각",
                     "liquid cooling", "전력변환", "전력 변환", "전력 인프라"],
    },
    "로봇 핵심부품": {
        "weight": 4,
        "keywords": ["감속기", "하모닉", "harmonic", "액추에이터", "actuator", "서보",
                     "토크센서", "로봇 구동", "정밀감속기"],
    },
    "소재/원재료(일반)": {
        "weight": 3,
        "keywords": ["특수소재", "화학소재", "기능성소재", "원재료", "신소재",
                     "advanced material", "소재"],
    },
}


def bottleneck_proximity(text: str, sector: str = "") -> dict[str, Any]:
    """사업설명·섹터 텍스트의 병목 근접도(0~100)와 매칭 레이어.

    Args:
        text: 사업 설명/업종/회사 개요 등 자유 텍스트.
        sector: 섹터명(현재는 text 에 합산해 매칭에만 사용).

    Returns:
        ``{"score": int 0~100, "layers": [레이어명...(가중 내림차순)], "top_layer": str|None}``
    """
    blob = f"{text or ''} {sector or ''}".lower()
    if not blob.strip():
        return {"score": 0, "layers": [], "top_layer": None}

    matched: list[tuple[str, int]] = []
    for layer, spec in SCARCE_LAYERS.items():
        if any(_kw_in(kw, blob) for kw in spec["keywords"]):
            matched.append((layer, int(spec["weight"])))

    if not matched:
        return {"score": 0, "layers": [], "top_layer": None}

    max_w = max(w for _, w in matched)
    n = len(matched)
    # 최상위 희소층 가중(80%) + 다층 병목 보너스(층당 10%, 최대 +20)
    score = min(100, round((max_w / 5.0) * 80 + min(n - 1, 2) * 10))
    layers = [l for l, _ in sorted(matched, key=lambda x: -x[1])]
    return {"score": int(score), "layers": layers, "top_layer": layers[0]}


# ──────────────────────────────────────────────────────────────────────────
# 종목별 심층 브리프 (외부 병목 스킬 스코어카드 핸드오프)
# ──────────────────────────────────────────────────────────────────────────
#
# 초벌 패스(bottleneck_proximity)는 "후보"만 거른다. 실제 8팩터 판단은 공시·증거가
# 필요한 리서치 작업이므로, 여기서는 외부 병목 방법론 스킬에 바로 넣을 수 있는
# prefilled 스코어카드 스켈레톤 + 리서치 프롬프트를 만들어 핸드오프한다.
# (스코어카드 키는 외부 스킬의 scripts/serenity_scorecard.py 입력과 호환되도록 일치시킴.)

# 8개 팩터 (각 0~5, 리서치로 채움)
SCORECARD_FACTORS: tuple[str, ...] = (
    "demand_inflection", "architecture_coupling", "chokepoint_severity",
    "supplier_concentration", "expansion_difficulty", "evidence_quality",
    "valuation_disconnect", "catalyst_timing",
)

# 페널티 (각 0~5)
SCORECARD_PENALTIES: tuple[str, ...] = (
    "dilution_financing", "governance", "geopolitics", "liquidity",
    "hype_risk", "accounting_quality", "cyclicality", "alternative_design_risk",
)


def _infer_market(ticker: str) -> str:
    t = (ticker or "").upper()
    if t.endswith(".KS") or t.endswith(".KQ"):
        return "Korea"
    if t.endswith(".HK"):
        return "Hong Kong"
    if t.endswith(".T"):
        return "Japan"
    if t.endswith(".TW"):
        return "Taiwan"
    return "US"


def build_bottleneck_brief(result: dict[str, Any]) -> dict[str, Any]:
    """스캔 결과 1건 → 외부 병목 방법론 스킬용 prefilled 브리프.

    Args:
        result: 스캐너 종목 dict. 사용 키: Ticker, Name, Sector, Desc,
            BottleneckLayers, _PER, _PBR, _ROE.

    Returns:
        ``{"ticker", "scorecard_skeleton": {...}, "research_prompt": str}``
        - scorecard_skeleton: 8팩터/페널티 0 초기화(JSON 그대로 스킬 스코어카드에 입력 가능).
        - research_prompt: 해당 종목에 병목 방법론을 적용하라는 시드 프롬프트.
    """
    ticker = str(result.get("Ticker", "") or "")
    name = str(result.get("Name", "") or "")
    sector = str(result.get("Sector", "") or "")
    desc = str(result.get("Desc", "") or "")
    layers = result.get("BottleneckLayers") or []
    market = _infer_market(ticker)

    skeleton: dict[str, Any] = {
        "ticker": ticker,
        "company": name,
        "market": market,
        "notes": "factors/penalties 는 공시·증거 리서치로 0~5 를 채운다. 0=없음, 5=매우 강함.",
        "factors": {k: 0 for k in SCORECARD_FACTORS},
        "penalties": {k: 0 for k in SCORECARD_PENALTIES},
        "evidence": [{"claim": "", "source": "", "strength": "primary/media/analysis/social/rumor"}],
        "what_could_weaken_view": ["", "", ""],
        "_hint_detected_layers": list(layers),
    }

    layer_hint = ", ".join(layers) if layers else "(키워드 초벌 매칭 없음 — 직접 산업체인 매핑 필요)"
    val_bits = []
    for label, key, fmt in (("PER", "_PER", "{:.1f}"), ("PBR", "_PBR", "{:.2f}"), ("ROE", "_ROE", "{:.2%}")):
        v = result.get(key)
        if isinstance(v, (int, float)) and v:
            try:
                val_bits.append(f"{label} {fmt.format(v)}")
            except (ValueError, TypeError):
                pass
    val_line = " · ".join(val_bits) if val_bits else "밸류에이션 데이터 미상"

    research_prompt = (
        f"공급망 병목(scarce layer) 방법론으로 다음 종목을 심층 분석해줘.\n"
        f"- 종목: {name or ticker} ({ticker}) / 시장: {market} / 섹터: {sector}\n"
        f"- 사업 요약: {desc or '(설명 없음)'}\n"
        f"- 초벌 매칭 희소층: {layer_hint}\n"
        f"- 현재 밸류에이션(참고): {val_line}\n\n"
        f"요구사항:\n"
        f"1) 이 회사가 산업체인에서 정확히 '무엇을 우회 불가능하게 만드는지(卡住的 환경)' 한 줄로.\n"
        f"2) 8팩터 스코어카드(수요변곡·아키텍처결합·병목심각도·공급사집중도·증설난이도·"
        f"증거품질·밸류갭·촉매타이밍)를 0~5로 채우고 근거 증거를 달 것 — 공시/IR/필링 우선.\n"
        f"3) 페널티(희석·거버넌스·지정학·유동성·과열·회계·경기민감·대체설계) 평가.\n"
        f"4) '무엇이 이 판단을 약화/오류로 만드는가' 3가지.\n"
        f"5) 다음 검증 액션(확인할 공시·지표·고객 교차검증).\n"
        f"매수/매도 지시는 하지 말 것 — 리서치 우선순위만. 최종 판단은 사용자 몫."
    )

    return {"ticker": ticker, "scorecard_skeleton": skeleton, "research_prompt": research_prompt}


# ──────────────────────────────────────────────────────────────────────────
# 병목 ∩ 진입타이밍 게이트
# ──────────────────────────────────────────────────────────────────────────
#
# 병목 방법론은 '무엇이 희소한가'만 알 뿐 '언제 사야 하나/이미 비싼가'를 모른다.
# 그래서 병목성이 높아도 이미 폭등(파라볼릭)했거나 과매수면 추격 매수가 된다.
# 이 게이트는 병목성과 스캐너의 타이밍 신호(진입점수·RS·과매수·최근 급등)를 결합해
# "병목 + 진입 자리"만 통과시키고, 폭등 꼭대기/과매수는 '조정대기'로 분리한다.

BOTTLENECK_GATE_MIN = 60      # 병목 근접도 하한
PARABOLA_3M_PCT = 80.0        # 3개월 급등 임계 — 이상이면 이미 폭등(추격 금지)
OVERBOUGHT_RSI = 72.0         # 과매수 임계
LAGGARD_RS = 40               # 약세 RS 임계
ENTRY_OK = 50                 # 진입점수 양호 하한


def bottleneck_entry_signal(
    *,
    bottleneck_score: float | None,
    entry_score: float | None = None,
    rsi: float | None = None,
    rs_rating: float | None = None,
    mom_3m: float | None = None,
    regime: str | None = None,
) -> dict[str, Any]:
    """병목성 + 진입 타이밍을 결합한 신호.

    Args:
        bottleneck_score: 0~100 병목 근접도.
        entry_score: 스캐너 진입타이밍 점수(0~100).
        rsi: RSI(0~100).
        rs_rating: 상대강도 등급(1~99).
        mom_3m: 최근 3개월 수익률(%) — 파라볼릭(추격) 판별용.
        regime: 시장 국면 문자열(선택).

    Returns:
        ``{"applicable": bool, "pass_gate": bool, "label": str|None, "reasons": [str]}``
        - 🟢 병목+진입: 통과(병목 + 들어갈 자리)
        - 🟡 조정대기: 병목이나 이미 폭등/과매수 → 추격 금지, 대기
        - ⚪ 병목-약세: 병목이나 모멘텀/진입 신호 부재 → 관망
    """
    bs = bottleneck_score or 0
    if bs < BOTTLENECK_GATE_MIN:
        return {"applicable": False, "pass_gate": False, "label": None, "reasons": []}

    reasons: list[str] = []
    parabola = mom_3m is not None and mom_3m >= PARABOLA_3M_PCT
    overbought = rsi is not None and rsi >= OVERBOUGHT_RSI
    laggard = rs_rating is not None and rs_rating < LAGGARD_RS
    bearish = bool(regime) and any(k in str(regime).upper() for k in ("BEAR", "DOWN", "약세", "하락"))
    timing_ok = entry_score is not None and entry_score >= ENTRY_OK

    if parabola or overbought:
        if parabola:
            reasons.append(f"3M +{mom_3m:.0f}% 폭등 — 추격 금지")
        if overbought:
            reasons.append(f"RSI {rsi:.0f} 과매수")
        return {"applicable": True, "pass_gate": False, "label": "🟡 조정대기", "reasons": reasons}

    if laggard or bearish or not timing_ok:
        if laggard:
            reasons.append(f"RS {rs_rating} 약세")
        if bearish:
            reasons.append("시장 국면 비우호")
        if not timing_ok:
            reasons.append("진입점수 부족")
        return {"applicable": True, "pass_gate": False, "label": "⚪ 병목-약세", "reasons": reasons}

    reasons.append("병목성 + 진입 자리(과열/약세 아님)")
    return {"applicable": True, "pass_gate": True, "label": "🟢 병목+진입", "reasons": reasons}
