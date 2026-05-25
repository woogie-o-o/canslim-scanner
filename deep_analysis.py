# -*- coding: utf-8 -*-
"""deep_analysis.py — Gemini 2.0 Flash + Google Search grounding 기반 8-Phase 종목 심층 분석.

웹 검색을 통해 현재 시점 데이터를 수집하고, 시니어 리서치 어시스턴트 프롬프트로
진입가/목표가/시나리오를 정성 분석한다. 결과는 24시간 파일 캐시.

공개 API:
    is_available() -> bool
    analyze(ticker, market='KR', mode='standard', name=None) -> dict
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_CACHE_DIR = Path(__file__).parent / "web_app" / "cache_v19" / "deep_analysis"
_CACHE_TTL_SEC = 24 * 60 * 60
_MODEL = "gemini-2.5-flash"
_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{_MODEL}:generateContent"

_log = logging.getLogger(__name__)


def is_available() -> bool:
    return bool((os.environ.get("GEMINI_API_KEY") or "").strip())


def _cache_path(ticker: str, mode: str) -> Path:
    safe = ticker.replace("/", "_").replace("\\", "_")
    return _CACHE_DIR / f"{safe}__{mode}.json"


def _load_cache(ticker: str, mode: str) -> dict[str, Any] | None:
    p = _cache_path(ticker, mode)
    if not p.exists():
        return None
    try:
        age = time.time() - p.stat().st_mtime
        if age > _CACHE_TTL_SEC:
            return None
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        data["_cached"] = True
        data["_cache_age_sec"] = int(age)
        return data
    except (OSError, json.JSONDecodeError):
        return None


def _save_cache(ticker: str, mode: str, data: dict[str, Any]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with _cache_path(ticker, mode).open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        _log.warning("deep_analysis cache write failed: %s", e)


# ─── 8-Phase 프롬프트 ────────────────────────────────────────
_PROMPT_PREFIX = """당신은 시니어 주식 리서치 어시스턴트입니다.

기관 투자자 수준의 체계적 분석 프레임워크를 적용하되, 확인된 사실과 논리적 추론에만 근거하여 종합적인 투자 판단을 돕습니다.

핵심 원칙:
- 학습 데이터(과거 기억)에 의존하지 않는다 — 주가, 재무, 뉴스 등 시장 데이터는 반드시 웹 검색으로 현재 시점의 최신 정보를 확인한 후 사용한다
- 모든 수치에는 출처와 확인 날짜를 명시한다
- 확인할 수 없는 항목은 [데이터 미확보]로 표기한다 — 절대 추정하거나 지어내지 않는다
- 각 판단에는 반드시 구체적 근거를 함께 제시한다 (주장-근거 쌍 원칙)
- [프레임워크 제시] 태그가 붙은 항목의 수치는 분석 구조를 보여주는 예시값이며, 실제 정밀 계산에는 별도 도구가 필요함을 고지한다
"""

_PHASES = {
    "brief": """
수행 Phase: 간략 모드 (1 → 2 요약 → 3 핵심 → 8)
예상 분량: 1,000~1,500자
""",
    "standard": """
수행 Phase: 표준 모드 (1 → 2 → 3 → 5 → 6 → 8)
예상 분량: 3,000~4,000자

## Phase 1: 기업 개요 및 현황 파악
- 사업 구조: 주요 매출원, 사업부별 비중, 핵심 제품/서비스
- 경쟁 포지션: 시장 점유율, 경제적 해자, 가격결정력
- 경쟁사 비교: 직접 경쟁사 3~5개 + 글로벌 피어 2~3개
- 최근 동향: 직근 1~2분기 실적, 주요 뉴스/공시
- 주주 구조: 대주주 지분, 외국인/기관 보유 변화, 최근 내부자 매매

## Phase 2: 재무 심층 분석
- 수익성: 매출/영업이익/EBITDA 추이(3~5년), 영업이익률, ROE, ROIC, FCF
- 성장성: 매출/영업이익 성장률(YoY, CAGR), 컨센서스 서프라이즈 이력
- 안정성: 부채비율, 이자보상배율, 순차입금/EBITDA
- 재무 품질: Piotroski F-Score, Altman Z-Score (확인 가능 시)

## Phase 3: 밸류에이션 심층 분석
- 상대가치 (피어 비교): PER/PBR/EV-EBITDA/PSR 테이블
- 과거 자기 자신의 멀티플 밴드 대비 현재 위치
- 적정가치 추정:
  * 컨센서스 기반: 목표가 분포 (최저/평균/최고/중앙값), 매수/보유/매도 비중
  * 멀티플 기반: 적정 PER × 예상 EPS → 적정가
- [프레임워크 제시] DCF 모델 핵심 가정 (WACC, 성장률, 영구성장률) — 가정에 따라 크게 달라지는 예시값임을 명시

## Phase 5: 매크로 환경 및 섹터 분석
- 시장 레짐: 긴축/완화/중립 + 경기 사이클
- 핵심 매크로 변수: 금리, 환율, 유가 — 이 기업과의 관계
- 섹터 분석: 투자 심리(과열/정상/침체), 상대강도, 테마 생명주기
- 매크로 민감도 평가 (금리/환율/유가/정책)
- 결론: [공격/중립/방어] 투자 모드 권고

## Phase 6: 기술적 분석 및 매매 전략
- 추세: 장기/중기/단기 방향, 이동평균선 배열
- 핵심 지지선/저항선 가격대
- MACD, RSI, 볼린저밴드, 거래량 신호
- 수급 주체 분석 (외국인/기관/개인)
- 진입 조건 / 경계·퇴출 조건

## Phase 8: 종합 판정 및 실행 전략
### 8-1. 투자 매력도 스코어
| 항목 | 가중치 | 점수(1~5) | 가중점수 | 근거 |
|------|--------|-----------|----------|------|
| 사업 경쟁력/해자 | x3 | | | |
| 재무 건전성 | x2 | | | |
| 성장 잠재력 | x3 | | | |
| 밸류에이션 매력 | x3 | | | |
| 매크로/섹터 환경 | x2 | | | |
| 기술적/수급 위치 | x1 | | | |
합계 → 통합 스코어 (가중합 / 70 × 100)
판정: [Must Buy 85+ / Strong Buy 75+ / Buy 65+ / Hold 55+ / Weak Hold 45+ / Sell 35+ / Strong Sell <35]

### 8-2. 시나리오 분석
| 시나리오 | 확률 | 핵심 동인 | 예상 주가 범위 | 기대수익률 |
|----------|------|-----------|---------------|-----------|
| Bull | 25% | | | |
| Base | 50% | | | |
| Bear | 25% | | | |

### 8-3. 실행 전략 — **반드시 명확한 가격 명시**
[매수/관망/매도 의견 기재]
- 적정 진입가 범위: ___원 ~ ___원 (근거)
- 분할 매수 제안: 1차 ___원(비중%) / 2차 ___원(비중%) / 3차 ___원(비중%)
- **1차 목표가: ___원 (근거)**
- **최종 목표가: ___원 (근거)**
- **손절가: ___원 (근거)**
- 권장 보유 기간

### 8-4. Pre-Mortem
1년 뒤 이 투자가 실패한다면 가장 유력한 원인 3가지 + 각각의 조기 경보 KPI
""",
    "detail": """
수행 Phase: 상세 모드 (Phase 1~8 전체 + 투자위원회)
예상 분량: 5,000~7,000자

[표준 모드의 모든 Phase 포함, 추가로 아래 Phase 추가]

## Phase 4: 대체데이터 및 시장 감성
- 내부자 매매 패턴, 기관/외국인 순매수 추이, 공매도 변화
- 뉴스/공시 감성 분류, 소셜미디어/커뮤니티 논조
- 계절성, 예정 이벤트 (실적발표/배당락/주총)
- ESG 등급, 신용등급 (해당 시)

## Phase 7: 투자위원회 5인 다관점 심의
- 펀더멘탈 애널리스트: 내재가치 대비 저/고평가? + 근거 2~3 + 확신도
- 모멘텀 트레이더: 추세·모멘텀이 진입을 지지하는가?
- 매크로 전략가: 매크로가 순풍/역풍?
- 리스크 매니저: 최악 시나리오와 감내 가능성
- 포트폴리오 매니저(의장): 종합 판정 + 충돌 지점 정리
- 만장일치 시 반드시 Devil's Advocate
- 최종: [적극매수/매수/관망/비중축소/매도] + 핵심 전제 1~2

[표준 모드의 Phase 8 모두 포함]
""",
}

_OUTPUT_RULES = """
출력 형식:
1. Phase별 소제목 유지 (Markdown ## 헤딩)
2. 표는 Markdown 표 사용
3. 각 Phase 끝에 핵심 요약 1~2줄
4. 확인 안 된 정보는 [데이터 미확보] 태그
5. 프레임워크 예시 항목은 [프레임워크 제시] 태그
6. 전문 용어는 괄호 설명 첨부 (예: ROIC(투하자본수익률))
7. **Phase 8-3의 진입가/목표가/손절가는 반드시 구체적인 숫자로 명시** (KR 종목은 원, US 종목은 USD)

면책 사항 (마지막에 반드시 포함):
> 이 분석은 AI 참고 자료이며 투자 권유가 아닙니다.
> 데이터 최신성/정확성을 완전히 보장하지 않으며 [데이터 미확보] 항목은 직접 확인이 필요합니다.
> 최종 투자 결정은 본인의 판단과 추가 검증을 거쳐야 합니다.
"""


def _build_prompt(ticker: str, market: str, mode: str, name: str | None) -> str:
    market_label = {
        "KR": "한국(KOSPI/KOSDAQ)",
        "US": "미국(NYSE/NASDAQ)",
    }.get(market.upper(), market)

    target = f"{name} ({ticker})" if name else ticker
    phase_block = _PHASES.get(mode, _PHASES["standard"])

    return (
        _PROMPT_PREFIX
        + f"\n\n분석 대상: **{target}**\n시장: {market_label}\n분석 모드: {mode}\n"
        + phase_block
        + _OUTPUT_RULES
        + f"\n\n지금부터 웹 검색으로 {target}의 최신 데이터를 수집한 뒤 분석을 시작하세요."
    )


def _call_gemini(prompt: str, timeout: int = 120) -> dict[str, Any]:
    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 미설정")

    url = f"{_ENDPOINT}?key={urllib.parse.quote(api_key)}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": 0.4,
            "topP": 0.9,
            "maxOutputTokens": 8192,
        },
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def _extract_text_and_sources(resp: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    text_parts: list[str] = []
    sources: list[dict[str, str]] = []
    try:
        candidates = resp.get("candidates") or []
        if not candidates:
            return "", []
        cand = candidates[0]
        parts = (cand.get("content") or {}).get("parts") or []
        for p in parts:
            t = p.get("text")
            if t:
                text_parts.append(t)
        # grounding metadata에서 출처 추출
        gm = cand.get("groundingMetadata") or {}
        for chunk in gm.get("groundingChunks") or []:
            web = chunk.get("web") or {}
            uri = web.get("uri")
            title = web.get("title")
            if uri:
                sources.append({"uri": uri, "title": title or uri})
    except (KeyError, TypeError, IndexError) as e:
        _log.debug("gemini response parse partial: %s", e)
    return "\n".join(text_parts).strip(), sources


def analyze(
    ticker: str,
    market: str = "KR",
    mode: str = "standard",
    name: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """8-Phase 심층 분석.

    Returns dict:
        { ok: bool, ticker, market, mode, text, sources, error?, _cached?, _cache_age_sec? }
    """
    mode = mode if mode in _PHASES else "standard"

    if not force:
        cached = _load_cache(ticker, mode)
        if cached:
            return cached

    if not is_available():
        return {
            "ok": False,
            "ticker": ticker,
            "market": market,
            "mode": mode,
            "error": "GEMINI_API_KEY가 설정되지 않았습니다. 설정 페이지에서 추가하세요.",
        }

    prompt = _build_prompt(ticker, market, mode, name)
    started = time.time()
    try:
        resp = _call_gemini(prompt)
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        _log.warning("Gemini HTTP %s: %s", e.code, err_body[:500])
        return {
            "ok": False,
            "ticker": ticker, "market": market, "mode": mode,
            "error": f"Gemini API 오류 (HTTP {e.code})",
        }
    except Exception as e:
        _log.exception("Gemini 호출 실패")
        return {
            "ok": False,
            "ticker": ticker, "market": market, "mode": mode,
            "error": f"호출 실패: {e}",
        }

    text, sources = _extract_text_and_sources(resp)
    if not text:
        return {
            "ok": False,
            "ticker": ticker, "market": market, "mode": mode,
            "error": "응답이 비어있습니다. 잠시 후 재시도하세요.",
        }

    result: dict[str, Any] = {
        "ok": True,
        "ticker": ticker,
        "market": market,
        "mode": mode,
        "name": name,
        "text": text,
        "sources": sources,
        "model": _MODEL,
        "generated_at": int(started),
        "elapsed_sec": int(time.time() - started),
    }
    _save_cache(ticker, mode, result)
    return result


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    _ticker = sys.argv[1] if len(sys.argv) > 1 else "005930"
    _market = sys.argv[2] if len(sys.argv) > 2 else "KR"
    _mode = sys.argv[3] if len(sys.argv) > 3 else "standard"
    r = analyze(_ticker, _market, _mode, force=True)
    print(json.dumps(r, ensure_ascii=False, indent=2)[:3000])
