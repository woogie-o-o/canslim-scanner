# -*- coding: utf-8 -*-
"""moat.py — 종목별 경제적 해자(Economic Moat) 표기 생성기.

Morningstar 5종 분류 + 한줄 부연을 dict row에 주입한다.

- 기본은 섹터 기반 규칙 매핑 (LLM 키 없어도 동작)
- ANTHROPIC_API_KEY 또는 OPENAI_API_KEY 가 있으면 LLM으로 한줄 부연을 풍부화
- 결과는 cache_v19/moat/{ticker}.json (TTL 30일) 에 저장

주입 필드 (one_liner.py 패턴과 동일):
  Moat          — 한줄 표기 (배지 텍스트, 예: "브랜드·생태계")
  MoatCategory  — 5종 enum (INTANGIBLE/SWITCHING/NETWORK/COST/EFFICIENT_SCALE/NONE)
  MoatData      — {category, label, detail, source}  (툴팁/카드용)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Optional

_log = logging.getLogger(__name__)

# ── Morningstar 5종 분류 ─────────────────────────────────────────────
INTANGIBLE = "INTANGIBLE"        # 무형자산 (브랜드, 특허, 면허)
SWITCHING = "SWITCHING"          # 전환비용
NETWORK = "NETWORK"              # 네트워크 효과
COST = "COST"                    # 원가우위
EFFICIENT_SCALE = "EFFICIENT_SCALE"  # 효율적 규모
NONE = "NONE"                    # 해자 약함/불명

_CATEGORY_LABEL = {
    INTANGIBLE: "브랜드·IP",
    SWITCHING: "전환비용",
    NETWORK: "네트워크 효과",
    COST: "원가우위",
    EFFICIENT_SCALE: "효율적 규모",
    NONE: "해자 약함",
}

_CATEGORY_DETAIL = {
    INTANGIBLE: "브랜드·특허·면허 등 무형자산이 가격결정력을 지킨다.",
    SWITCHING: "고객이 경쟁사로 옮기는 비용이 커 락인 효과가 강하다.",
    NETWORK: "사용자가 늘수록 서비스 가치가 비선형으로 커진다.",
    COST: "구조적 원가우위로 경쟁사 대비 마진을 지킨다.",
    EFFICIENT_SCALE: "시장 규모가 제한돼 신규 진입이 비효율적이다.",
    NONE: "뚜렷한 구조적 해자가 보이지 않는다.",
}

# ── 섹터 → 기본 카테고리 매핑 ────────────────────────────────────────
# yfinance/scanner 섹터 이름 기준. 대소문자 무시 매칭.
_SECTOR_MOAT = {
    # Tech / Communication
    "technology": (NETWORK, "플랫폼·생태계 락인"),
    "communication services": (NETWORK, "사용자 네트워크"),
    "software": (SWITCHING, "워크플로우 락인"),
    "semiconductors": (INTANGIBLE, "공정·특허 우위"),

    # Consumer
    "consumer cyclical": (INTANGIBLE, "브랜드 충성도"),
    "consumer defensive": (INTANGIBLE, "브랜드·유통망"),
    "consumer staples": (INTANGIBLE, "브랜드·유통망"),

    # Healthcare
    "healthcare": (INTANGIBLE, "특허·임상 데이터"),
    "biotechnology": (INTANGIBLE, "파이프라인·특허"),
    "pharmaceuticals": (INTANGIBLE, "특허·규제 장벽"),

    # Financial
    "financial services": (SWITCHING, "계좌 락인·규제"),
    "financials": (SWITCHING, "계좌 락인·규제"),
    "banks": (COST, "조달비용 우위"),
    "insurance": (COST, "언더라이팅 규모"),

    # Industrials / Materials
    "industrials": (COST, "규모의 경제"),
    "basic materials": (COST, "원가·매장량"),
    "materials": (COST, "원가·매장량"),

    # Energy / Utilities / Real Estate
    "energy": (COST, "매장량·정제 규모"),
    "utilities": (EFFICIENT_SCALE, "독과점 인프라"),
    "real estate": (EFFICIENT_SCALE, "입지·자산"),

    # Korea-specific scanner labels (한글 섹터명도 지원)
    "반도체": (INTANGIBLE, "공정·특허 우위"),
    "전기·전자": (NETWORK, "공급망 통합"),
    "운수·창고업": (EFFICIENT_SCALE, "허브·물류망"),
    "금융업": (SWITCHING, "계좌 락인·규제"),
    "건설업": (COST, "규모의 경제"),
    "유통업": (INTANGIBLE, "브랜드·유통망"),
    "의약품": (INTANGIBLE, "특허·규제 장벽"),
    "전기가스업": (EFFICIENT_SCALE, "독과점 인프라"),
    "통신업": (NETWORK, "주파수·가입자망"),
}

# 딥테크/투기 테마는 보통 해자가 약하거나 검증되지 않음
_SPECULATIVE_HINTS = {
    "드론·우주", "위성·발사체", "양자보안·암호", "양자센서·하드웨어",
    "원전·SMR", "신재생·ESS", "자율주행·전장", "바이오 신약",
}

# ── 큐레이션 사전 (moat_data.json) — 1순위 ──────────────────────────
_DATA_PATH = os.path.join(os.path.dirname(__file__), "moat_data.json")
_OVERRIDES: dict[str, dict] = {}
_overrides_mtime = 0.0
_overrides_lock = threading.Lock()


def _load_overrides(force: bool = False) -> dict[str, dict]:
    """moat_data.json을 핫리로드. 파일 mtime이 바뀌면 다시 읽는다."""
    global _OVERRIDES, _overrides_mtime
    try:
        mt = os.path.getmtime(_DATA_PATH)
    except OSError:
        return _OVERRIDES
    if not force and mt == _overrides_mtime and _OVERRIDES:
        return _OVERRIDES
    with _overrides_lock:
        try:
            with open(_DATA_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            _OVERRIDES = {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, dict)}
            _overrides_mtime = mt
            _log.info("moat overrides loaded: %d tickers", len(_OVERRIDES))
        except (OSError, json.JSONDecodeError) as e:
            _log.warning("moat_data.json load failed: %s", e)
    return _OVERRIDES


# ── 캐시 ─────────────────────────────────────────────────────────────
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache_v19", "moat")
_CACHE_TTL = int(os.environ.get("MOAT_CACHE_TTL", str(30 * 86400)))  # 30일
_cache_lock = threading.Lock()

# 인메모리 LRU (프로세스 수명 동안)
_mem_cache: dict[str, dict] = {}


def _ensure_dir() -> None:
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
    except OSError as e:
        _log.warning("moat cache dir create failed: %s", e)


def _cache_path(ticker: str) -> str:
    safe = ticker.replace("/", "_").replace("\\", "_").replace(":", "_")
    return os.path.join(_CACHE_DIR, f"{safe}.json")


def _cache_read(ticker: str) -> Optional[dict]:
    if ticker in _mem_cache:
        return _mem_cache[ticker]
    path = _cache_path(ticker)
    if not os.path.exists(path):
        return None
    try:
        st = os.stat(path)
        if (time.time() - st.st_mtime) > _CACHE_TTL:
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        _mem_cache[ticker] = data
        return data
    except (OSError, json.JSONDecodeError) as e:
        _log.debug("moat cache read failed %s: %s", ticker, e)
        return None


def _cache_write(ticker: str, data: dict) -> None:
    _ensure_dir()
    path = _cache_path(ticker)
    try:
        with _cache_lock, open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        _mem_cache[ticker] = data
    except OSError as e:
        _log.debug("moat cache write failed %s: %s", ticker, e)


# ── 규칙 기반 생성 ───────────────────────────────────────────────────
def _rule_based(sector: str, theme: str | None, mcap: float | None) -> dict:
    s = (sector or "").strip().lower()
    cat, label = _SECTOR_MOAT.get(s, (NONE, "해자 약함"))

    # 한글 섹터명은 lowercase가 의미 없으므로 원문도 확인
    if cat == NONE and sector and sector in _SECTOR_MOAT:
        cat, label = _SECTOR_MOAT[sector]

    # 시가총액이 매우 작으면 해자 신뢰도 낮춤
    if mcap is not None and mcap < 5e10 and cat != NONE:
        # 중소형은 라벨은 유지하되 detail만 약화
        pass

    # MF-000: 투기 테마는 'story_risk' 별도 축으로만 표시 — 해자 카테고리는 덮지 않음.
    # 라이선스·규제·특허 등 진짜 해자가 있는 스토리 종목(ASTS, OKLO 등) false negative 방지.
    story_risk = bool(theme and theme in _SPECULATIVE_HINTS)

    return {
        "category": cat,
        "label": label,
        "detail": _CATEGORY_DETAIL.get(cat, ""),
        "source": "rule",
        "story_risk": story_risk,
    }


# ── LLM 증강 (선택, 기본 OFF) ────────────────────────────────────────
# 큐레이션 사전(moat_data.json) + 섹터 규칙으로 대부분 커버되므로 LLM은 opt-in.
# MOAT_ENABLE_LLM=1 로 활성화. (기존 MOAT_DISABLE_LLM 변수는 무시됨)
_LLM_AVAILABLE: Optional[bool] = None
_LLM_PROVIDER: Optional[str] = None  # "anthropic" or "openai"
_LLM_CLIENT = None


def _init_llm() -> bool:
    global _LLM_AVAILABLE, _LLM_PROVIDER, _LLM_CLIENT
    if _LLM_AVAILABLE is not None:
        return _LLM_AVAILABLE

    # 기본 OFF — opt-in 방식
    if not os.environ.get("MOAT_ENABLE_LLM"):
        _LLM_AVAILABLE = False
        return False

    # Anthropic 우선
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            _LLM_CLIENT = anthropic.Anthropic()
            _LLM_PROVIDER = "anthropic"
            _LLM_AVAILABLE = True
            _log.info("moat LLM enabled: anthropic")
            return True
        except Exception as e:
            _log.warning("moat anthropic init failed: %s", e)

    if os.environ.get("OPENAI_API_KEY"):
        try:
            import openai
            _LLM_CLIENT = openai.OpenAI()
            _LLM_PROVIDER = "openai"
            _LLM_AVAILABLE = True
            _log.info("moat LLM enabled: openai")
            return True
        except Exception as e:
            _log.warning("moat openai init failed: %s", e)

    _LLM_AVAILABLE = False
    return False


_LLM_PROMPT = """다음 종목의 경제적 해자(economic moat)를 분석하라.

티커: {ticker}
종목명: {name}
섹터: {sector}
시가총액: {mcap_str}

Morningstar 5분류 중 가장 적합한 것 하나만 선택:
- INTANGIBLE: 브랜드·특허·면허 등 무형자산
- SWITCHING: 전환비용으로 인한 락인
- NETWORK: 네트워크 효과 (사용자가 늘수록 가치↑)
- COST: 구조적 원가우위
- EFFICIENT_SCALE: 시장 규모 제한으로 인한 독과점
- NONE: 뚜렷한 해자 없음

응답 형식 (JSON 한 줄만, 다른 설명 금지):
{{"category":"...","label":"한국어 6자 이내","detail":"한국어 25자 이내"}}

label 예시: "브랜드 충성도", "iOS 생태계", "TSMC 공정 우위", "AWS 시장지배"
detail은 왜 그런 해자가 있는지 한 문장.
"""


def _llm_generate(ticker: str, name: str, sector: str, mcap: float | None) -> Optional[dict]:
    if not _init_llm():
        return None
    mcap_str = f"{mcap/1e12:.1f}조" if mcap and mcap >= 1e12 else (f"{mcap/1e8:.0f}억" if mcap else "불명")
    prompt = _LLM_PROMPT.format(
        ticker=ticker, name=name or ticker, sector=sector or "불명", mcap_str=mcap_str
    )
    try:
        if _LLM_PROVIDER == "anthropic":
            resp = _LLM_CLIENT.messages.create(
                model=os.environ.get("MOAT_LLM_MODEL", "claude-haiku-4-5-20251001"),
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip()
        else:  # openai
            resp = _LLM_CLIENT.chat.completions.create(
                model=os.environ.get("MOAT_LLM_MODEL", "gpt-4o-mini"),
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content.strip()

        # JSON 추출 (마크다운 코드펜스 제거)
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        data = json.loads(raw)
        cat = str(data.get("category", "")).upper()
        if cat not in (INTANGIBLE, SWITCHING, NETWORK, COST, EFFICIENT_SCALE, NONE):
            return None
        label = str(data.get("label", "")).strip()[:12] or _CATEGORY_LABEL.get(cat, "")
        detail = str(data.get("detail", "")).strip()[:60] or _CATEGORY_DETAIL.get(cat, "")
        return {"category": cat, "label": label, "detail": detail, "source": f"llm:{_LLM_PROVIDER}"}
    except Exception as e:
        _log.debug("moat LLM call failed %s: %s", ticker, e)
        return None


# ── 공개 API ─────────────────────────────────────────────────────────
def _resolve_one(row: dict) -> dict:
    ticker = str(row.get("Ticker") or "").strip()
    if not ticker:
        return {"category": NONE, "label": "—", "detail": "", "source": "noop"}

    # 1순위: 큐레이션된 사전 — moat_data.json. 캐시 무시 (실시간 편집 반영).
    sector = row.get("Sector") or ""
    name = row.get("Name") or ticker
    mcap = row.get("_MarketCap") or row.get("MarketCap")
    theme = row.get("Theme") or row.get("SpeculativeTheme")
    story_risk = bool(theme and theme in _SPECULATIVE_HINTS)

    overrides = _load_overrides()
    ov = overrides.get(ticker)
    if ov:
        cat = str(ov.get("category", NONE)).upper()
        # MF-001: curated 가 speculative 룰을 이긴다. story_risk 는 별도 축으로 함께 노출.
        return {
            "category": cat if cat in (INTANGIBLE, SWITCHING, NETWORK, COST, EFFICIENT_SCALE, NONE) else NONE,
            "label": str(ov.get("label", _CATEGORY_LABEL.get(cat, ""))),
            "detail": str(ov.get("detail", _CATEGORY_DETAIL.get(cat, ""))),
            "source": "curated",
            "confidence": str(ov.get("confidence", "heuristic")),
            "evidence_source": str(ov.get("source", "")),
            "story_risk": story_risk,
        }

    cached = _cache_read(ticker)
    if cached:
        cached.setdefault("story_risk", story_risk)
        return cached

    # 2순위: LLM (opt-in). 3순위: 섹터 규칙 폴백.
    data = _llm_generate(ticker, name, sector, mcap) or _rule_based(sector, theme, mcap)
    data.setdefault("story_risk", story_risk)
    _cache_write(ticker, data)
    return data


def annotate(results: list) -> list:
    """results 각 dict에 Moat/MoatCategory/MoatData 필드를 주입한다.

    one_liner.annotate와 동일하게 in-place mutate + return.
    이미 Moat가 채워진 행은 스킵 (force는 인자 없음 — 캐시 TTL로 통제).
    """
    if not results:
        return results
    for row in results:
        if not isinstance(row, dict):
            continue
        if row.get("Moat"):
            continue
        try:
            data = _resolve_one(row)
            row["Moat"] = data["label"]
            row["MoatCategory"] = data["category"]
            row["MoatConfidence"] = data.get("confidence", "heuristic")
            row["MoatData"] = data
        except Exception as e:
            _log.debug("moat annotate row failed: %s", e)
            row["Moat"] = ""
            row["MoatCategory"] = NONE
            row["MoatData"] = {"category": NONE, "label": "", "detail": "", "source": "error"}
    return results
