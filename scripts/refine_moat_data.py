#!/usr/bin/env python3
"""
신규 US 종목 596개의 해자 데이터를 yfinance 실제 기업 정보 기반으로 정밀화.
- yfinance에서 industry, longBusinessSummary, marketCap, returnOnEquity 등 수집
- 실제 산업 분류 + 재무 지표 기반으로 moat category/scores 재산정
- 기업별 한글 설명 생성
"""
import json, time, sys, re
from pathlib import Path

MOAT_PATH = Path(__file__).resolve().parent.parent / "web_app" / "moat_data.json"
VALIDATED_PATH = Path(__file__).resolve().parent / "us_expansion_validated.json"
CACHE_PATH = Path(__file__).resolve().parent / "us_yfinance_cache.json"

# ── 1. 대상 티커 식별 ──
with open(MOAT_PATH, encoding="utf-8") as f:
    moat_data = json.load(f)

with open(VALIDATED_PATH, encoding="utf-8") as f:
    validated = json.load(f)

new_tickers = set()
for tickers in validated["valid"].values():
    new_tickers.update(tickers)

# sector_heuristic인 것만 대상
targets = sorted([t for t in new_tickers if t in moat_data
                  and isinstance(moat_data[t], dict)
                  and moat_data[t].get("confidence") == "sector_heuristic"])
# 미커버 추가
missing = sorted(new_tickers - set(moat_data.keys()))
targets.extend(missing)

print(f"[INFO] 정밀화 대상: {len(targets)}개 (sector_heuristic: {len(targets)-len(missing)}, 미커버: {len(missing)})")

# ── 2. yfinance 데이터 수집 (캐시 활용) ──
cache = {}
if CACHE_PATH.exists():
    with open(CACHE_PATH, encoding="utf-8") as f:
        cache = json.load(f)
    print(f"[INFO] 캐시 로드: {len(cache)}개")

uncached = [t for t in targets if t not in cache]
if uncached:
    print(f"[INFO] yfinance에서 {len(uncached)}개 조회 중...")
    import yfinance as yf
    for idx, ticker in enumerate(uncached):
        try:
            t = yf.Ticker(ticker)
            info = t.info or {}
            cache[ticker] = {
                "industry": info.get("industry", ""),
                "sector": info.get("sector", ""),
                "summary": (info.get("longBusinessSummary") or "")[:500],
                "name": info.get("longName") or info.get("shortName") or ticker,
                "marketCap": info.get("marketCap", 0),
                "roe": info.get("returnOnEquity"),
                "grossMargins": info.get("grossMargins"),
                "operatingMargins": info.get("operatingMargins"),
                "debtToEquity": info.get("debtToEquity"),
                "revenueGrowth": info.get("revenueGrowth"),
                "currentRatio": info.get("currentRatio"),
            }
        except Exception as e:
            cache[ticker] = {"industry": "", "sector": "", "summary": "", "name": ticker,
                             "marketCap": 0, "roe": None, "grossMargins": None,
                             "operatingMargins": None, "debtToEquity": None,
                             "revenueGrowth": None, "currentRatio": None}
        if (idx + 1) % 20 == 0 or idx == len(uncached) - 1:
            pct = (idx + 1) / len(uncached) * 100
            print(f"  ... {pct:.0f}% ({idx+1}/{len(uncached)})")
            # 중간 저장
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=1)
            time.sleep(0.2)

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    print(f"[INFO] 캐시 저장: {len(cache)}개")

# ── 3. 산업 → 해자 매핑 테이블 ──
# (industry keyword → moat category, label_kr, base_scores)
INDUSTRY_MOAT_MAP = {
    # 테크/소프트웨어
    "Software": ("SWITCHING", "SW 전환비용", {"switching_costs": 3, "network_effects": 2, "ip_efficiency": 3, "cost_advantage": 1, "roic_sustainability": 3}),
    "Internet": ("NETWORK", "플랫폼 네트워크", {"switching_costs": 2, "network_effects": 3, "ip_efficiency": 2, "cost_advantage": 1, "roic_sustainability": 2}),
    "Semiconductor": ("IP", "반도체 IP", {"switching_costs": 2, "network_effects": 1, "ip_efficiency": 4, "cost_advantage": 2, "roic_sustainability": 3}),
    "Electronic Component": ("IP", "전자부품 기술", {"switching_costs": 2, "network_effects": 0, "ip_efficiency": 3, "cost_advantage": 2, "roic_sustainability": 2}),
    "Computer Hardware": ("SWITCHING", "HW 생태계", {"switching_costs": 3, "network_effects": 1, "ip_efficiency": 3, "cost_advantage": 1, "roic_sustainability": 2}),
    "Communication Equipment": ("IP", "통신장비 기술", {"switching_costs": 2, "network_effects": 1, "ip_efficiency": 3, "cost_advantage": 1, "roic_sustainability": 2}),
    "Information Technology": ("SWITCHING", "IT 전환비용", {"switching_costs": 3, "network_effects": 1, "ip_efficiency": 2, "cost_advantage": 1, "roic_sustainability": 2}),

    # 금융
    "Banks": ("SWITCHING", "예금 전환비용", {"switching_costs": 3, "network_effects": 1, "ip_efficiency": 0, "cost_advantage": 2, "roic_sustainability": 3}),
    "Insurance": ("COST", "보험 규모경제", {"switching_costs": 2, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 3, "roic_sustainability": 3}),
    "Capital Markets": ("NETWORK", "거래소/중개", {"switching_costs": 2, "network_effects": 3, "ip_efficiency": 1, "cost_advantage": 1, "roic_sustainability": 2}),
    "Financial Services": ("SWITCHING", "금융 락인", {"switching_costs": 2, "network_effects": 1, "ip_efficiency": 0, "cost_advantage": 2, "roic_sustainability": 2}),
    "Credit Services": ("NETWORK", "결제 네트워크", {"switching_costs": 2, "network_effects": 3, "ip_efficiency": 0, "cost_advantage": 1, "roic_sustainability": 2}),
    "Asset Management": ("SWITCHING", "자산운용 락인", {"switching_costs": 3, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 1, "roic_sustainability": 3}),

    # 헬스케어
    "Biotechnology": ("IP", "바이오 파이프라인", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 4, "cost_advantage": 0, "roic_sustainability": 2}),
    "Drug Manufacturers": ("IP", "제약 특허", {"switching_costs": 2, "network_effects": 0, "ip_efficiency": 4, "cost_advantage": 2, "roic_sustainability": 3}),
    "Pharmaceuticals": ("IP", "제약 특허", {"switching_costs": 2, "network_effects": 0, "ip_efficiency": 4, "cost_advantage": 2, "roic_sustainability": 3}),
    "Medical Devices": ("SWITCHING", "의료기기 전환비용", {"switching_costs": 3, "network_effects": 0, "ip_efficiency": 3, "cost_advantage": 1, "roic_sustainability": 3}),
    "Medical Instruments": ("SWITCHING", "의료기기 전환비용", {"switching_costs": 3, "network_effects": 0, "ip_efficiency": 3, "cost_advantage": 1, "roic_sustainability": 3}),
    "Health Care": ("SWITCHING", "헬스케어 락인", {"switching_costs": 2, "network_effects": 0, "ip_efficiency": 1, "cost_advantage": 2, "roic_sustainability": 2}),
    "Diagnostics": ("IP", "진단 기술", {"switching_costs": 2, "network_effects": 0, "ip_efficiency": 3, "cost_advantage": 1, "roic_sustainability": 2}),

    # 에너지
    "Oil & Gas": ("COST", "에너지 자원 규모", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 3, "roic_sustainability": 2}),
    "Utilities": ("EFFICIENT_SCALE", "규제 독점", {"switching_costs": 3, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 2, "roic_sustainability": 3}),
    "Regulated Electric": ("EFFICIENT_SCALE", "전력 규제독점", {"switching_costs": 3, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 2, "roic_sustainability": 3}),
    "Renewable": ("COST", "재생에너지", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 1, "cost_advantage": 2, "roic_sustainability": 2}),
    "Solar": ("IP", "태양광 기술", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 2, "cost_advantage": 2, "roic_sustainability": 1}),
    "Uranium": ("EFFICIENT_SCALE", "우라늄 희소자원", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 3, "roic_sustainability": 2}),
    "Nuclear": ("EFFICIENT_SCALE", "원자력 규제장벽", {"switching_costs": 2, "network_effects": 0, "ip_efficiency": 2, "cost_advantage": 2, "roic_sustainability": 3}),
    "Pipeline": ("EFFICIENT_SCALE", "파이프라인 독점", {"switching_costs": 3, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 3, "roic_sustainability": 3}),

    # 산업재
    "Aerospace": ("IP", "항공우주 기술", {"switching_costs": 3, "network_effects": 0, "ip_efficiency": 4, "cost_advantage": 1, "roic_sustainability": 3}),
    "Defense": ("IP", "방산 기술", {"switching_costs": 3, "network_effects": 0, "ip_efficiency": 3, "cost_advantage": 1, "roic_sustainability": 3}),
    "Industrial": ("COST", "산업재 규모", {"switching_costs": 2, "network_effects": 0, "ip_efficiency": 1, "cost_advantage": 3, "roic_sustainability": 2}),
    "Machinery": ("SWITCHING", "장비 전환비용", {"switching_costs": 3, "network_effects": 0, "ip_efficiency": 2, "cost_advantage": 1, "roic_sustainability": 2}),
    "Electrical Equipment": ("IP", "전기장비 기술", {"switching_costs": 2, "network_effects": 0, "ip_efficiency": 3, "cost_advantage": 1, "roic_sustainability": 2}),
    "Engineering": ("SWITCHING", "엔지니어링 전환비용", {"switching_costs": 3, "network_effects": 0, "ip_efficiency": 2, "cost_advantage": 1, "roic_sustainability": 2}),
    "Building": ("COST", "건자재 규모", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 3, "roic_sustainability": 2}),
    "Construction": ("COST", "건설 규모", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 2, "roic_sustainability": 2}),
    "Waste Management": ("EFFICIENT_SCALE", "폐기물 규제독점", {"switching_costs": 3, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 3, "roic_sustainability": 3}),

    # 소비재
    "Retail": ("COST", "유통 규모경제", {"switching_costs": 1, "network_effects": 1, "ip_efficiency": 0, "cost_advantage": 3, "roic_sustainability": 2}),
    "Restaurant": ("INTANGIBLE", "브랜드 프랜차이즈", {"switching_costs": 1, "network_effects": 1, "ip_efficiency": 2, "cost_advantage": 2, "roic_sustainability": 2}),
    "Apparel": ("INTANGIBLE", "패션 브랜드", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 2, "cost_advantage": 1, "roic_sustainability": 1}),
    "Luxury": ("INTANGIBLE", "럭셔리 브랜드", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 3, "cost_advantage": 1, "roic_sustainability": 3}),
    "Auto": ("COST", "자동차 규모경제", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 2, "cost_advantage": 3, "roic_sustainability": 2}),
    "Beverage": ("INTANGIBLE", "음료 브랜드", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 3, "cost_advantage": 2, "roic_sustainability": 3}),
    "Packaged Foods": ("INTANGIBLE", "식품 브랜드", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 2, "cost_advantage": 2, "roic_sustainability": 2}),
    "Household": ("INTANGIBLE", "생활용품 브랜드", {"switching_costs": 2, "network_effects": 0, "ip_efficiency": 2, "cost_advantage": 2, "roic_sustainability": 2}),
    "Personal Products": ("INTANGIBLE", "퍼스널케어 브랜드", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 2, "cost_advantage": 2, "roic_sustainability": 2}),
    "Tobacco": ("INTANGIBLE", "담배 중독성", {"switching_costs": 4, "network_effects": 0, "ip_efficiency": 1, "cost_advantage": 2, "roic_sustainability": 4}),
    "Gaming": ("INTANGIBLE", "게임 IP", {"switching_costs": 1, "network_effects": 2, "ip_efficiency": 3, "cost_advantage": 0, "roic_sustainability": 1}),
    "Gambling": ("EFFICIENT_SCALE", "카지노 면허독점", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 1, "cost_advantage": 2, "roic_sustainability": 3}),
    "Resorts & Casinos": ("EFFICIENT_SCALE", "카지노 면허독점", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 1, "cost_advantage": 2, "roic_sustainability": 3}),
    "Hotels": ("INTANGIBLE", "호텔 브랜드", {"switching_costs": 1, "network_effects": 1, "ip_efficiency": 2, "cost_advantage": 1, "roic_sustainability": 2}),
    "Leisure": ("INTANGIBLE", "레저 브랜드", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 1, "cost_advantage": 1, "roic_sustainability": 1}),

    # REIT
    "REIT": ("EFFICIENT_SCALE", "부동산 임대", {"switching_costs": 2, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 2, "roic_sustainability": 3}),
    "Real Estate": ("EFFICIENT_SCALE", "부동산 임대", {"switching_costs": 2, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 2, "roic_sustainability": 3}),

    # 소재
    "Chemicals": ("COST", "화학 규모경제", {"switching_costs": 2, "network_effects": 0, "ip_efficiency": 1, "cost_advantage": 3, "roic_sustainability": 2}),
    "Steel": ("COST", "철강 규모경제", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 3, "roic_sustainability": 1}),
    "Mining": ("COST", "광산 자원", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 3, "roic_sustainability": 2}),
    "Gold": ("COST", "금 채굴", {"switching_costs": 0, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 3, "roic_sustainability": 2}),
    "Metals": ("COST", "금속 자원", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 3, "roic_sustainability": 2}),
    "Lumber": ("COST", "목재 자원", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 2, "roic_sustainability": 2}),
    "Paper": ("COST", "종이/포장 규모", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 3, "roic_sustainability": 2}),
    "Packaging": ("SWITCHING", "포장재 전환비용", {"switching_costs": 3, "network_effects": 0, "ip_efficiency": 1, "cost_advantage": 2, "roic_sustainability": 2}),
    "Agriculture": ("COST", "농업 규모", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 3, "roic_sustainability": 2}),

    # 통신/미디어
    "Telecom": ("EFFICIENT_SCALE", "통신 인프라 독점", {"switching_costs": 3, "network_effects": 1, "ip_efficiency": 0, "cost_advantage": 2, "roic_sustainability": 3}),
    "Broadcasting": ("INTANGIBLE", "미디어 콘텐츠", {"switching_costs": 1, "network_effects": 2, "ip_efficiency": 3, "cost_advantage": 0, "roic_sustainability": 1}),
    "Entertainment": ("INTANGIBLE", "엔터 IP/콘텐츠", {"switching_costs": 1, "network_effects": 2, "ip_efficiency": 3, "cost_advantage": 0, "roic_sustainability": 1}),
    "Advertising": ("NETWORK", "광고 네트워크", {"switching_costs": 2, "network_effects": 3, "ip_efficiency": 1, "cost_advantage": 1, "roic_sustainability": 2}),
    "Publishing": ("INTANGIBLE", "출판 콘텐츠", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 2, "cost_advantage": 1, "roic_sustainability": 1}),

    # 운송
    "Railroads": ("EFFICIENT_SCALE", "철도 독점", {"switching_costs": 3, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 3, "roic_sustainability": 4}),
    "Trucking": ("COST", "물류 규모", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 2, "roic_sustainability": 1}),
    "Airlines": ("COST", "항공 규모", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 2, "roic_sustainability": 1}),
    "Marine Shipping": ("COST", "해운 규모", {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 2, "roic_sustainability": 1}),

    # 비즈니스 서비스
    "Staffing": ("NETWORK", "인력 네트워크", {"switching_costs": 2, "network_effects": 2, "ip_efficiency": 0, "cost_advantage": 1, "roic_sustainability": 1}),
    "Consulting": ("SWITCHING", "컨설팅 전환비용", {"switching_costs": 3, "network_effects": 0, "ip_efficiency": 2, "cost_advantage": 0, "roic_sustainability": 2}),
    "Data Processing": ("SWITCHING", "데이터 전환비용", {"switching_costs": 3, "network_effects": 1, "ip_efficiency": 2, "cost_advantage": 1, "roic_sustainability": 3}),
    "Security": ("SWITCHING", "보안 전환비용", {"switching_costs": 3, "network_effects": 0, "ip_efficiency": 2, "cost_advantage": 1, "roic_sustainability": 2}),

    # 부동산
    "Homebuilding": ("COST", "주택건설 규모", {"switching_costs": 0, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 3, "roic_sustainability": 2}),

    # ETF
    "Exchange Traded Fund": ("NONE", "ETF", {"switching_costs": 0, "network_effects": 0, "ip_efficiency": 0, "cost_advantage": 0, "roic_sustainability": 0}),
}

def match_industry(industry_str):
    """yfinance industry 문자열에서 가장 적합한 해자 매핑을 찾는다."""
    if not industry_str:
        return None
    industry_lower = industry_str.lower()
    # 정확한 키워드 매칭 (긴 키워드 우선)
    best_match = None
    best_len = 0
    for key in INDUSTRY_MOAT_MAP:
        if key.lower() in industry_lower and len(key) > best_len:
            best_match = key
            best_len = len(key)
    return best_match

def format_cap(cap):
    """시가총액을 한글로 포맷."""
    if not cap or cap == 0:
        return ""
    if cap >= 1e12:
        return f"시총 ${cap/1e12:.1f}T"
    elif cap >= 1e9:
        return f"시총 ${cap/1e9:.0f}B"
    elif cap >= 1e6:
        return f"시총 ${cap/1e6:.0f}M"
    return ""

def make_detail_kr(ticker, info, category, label):
    """기업별 한글 설명을 생성한다."""
    name = info.get("name", ticker)
    industry = info.get("industry", "")
    summary = info.get("summary", "")
    cap_str = format_cap(info.get("marketCap", 0))
    roe = info.get("roe")
    gm = info.get("grossMargins")
    om = info.get("operatingMargins")

    # 영문 summary에서 핵심 키워드 추출
    biz_desc = ""
    if summary:
        # 첫 문장 추출
        first_sent = summary.split(". ")[0] + "."
        if len(first_sent) > 200:
            first_sent = first_sent[:197] + "..."
        biz_desc = first_sent

    # 재무 지표 문자열
    metrics = []
    if cap_str:
        metrics.append(cap_str)
    if roe and roe > 0:
        metrics.append(f"ROE {roe*100:.0f}%")
    if gm and gm > 0:
        metrics.append(f"매출총이익률 {gm*100:.0f}%")
    if om and om > 0:
        metrics.append(f"영업이익률 {om*100:.0f}%")
    metrics_str = ", ".join(metrics[:3])

    # 카테고리별 설명 프레임
    cat_desc = {
        "SWITCHING": "고객 전환비용이 높아 기존 사용자 이탈이 어려운 구조",
        "NETWORK": "사용자/거래 참여자가 늘수록 플랫폼 가치가 증가하는 네트워크 효과 보유",
        "IP": "특허·기술·규제 인허가 등 무형자산 기반의 경쟁 우위 확보",
        "INTANGIBLE": "브랜드 인지도와 고객 충성도에 기반한 가격결정력 보유",
        "COST": "규모의 경제와 운영 효율성을 통한 원가 우위 확보",
        "EFFICIENT_SCALE": "규제 또는 자연독점으로 신규 진입이 제한된 시장에서 안정적 수익 창출",
        "NONE": "구조적 해자가 뚜렷하지 않은 종목",
    }
    frame = cat_desc.get(category, "")

    parts = [f"{name}({ticker})"]
    if industry:
        parts.append(f"— {industry}")
    parts.append(f"— {frame}.")
    if metrics_str:
        parts.append(f"({metrics_str})")

    detail = " ".join(parts)
    if len(detail) > 250:
        detail = detail[:247] + "..."
    return detail

def adjust_scores(base_scores, info):
    """재무 지표 기반으로 점수를 미세 조정한다."""
    scores = dict(base_scores)
    roe = info.get("roe")
    gm = info.get("grossMargins")
    om = info.get("operatingMargins")
    cap = info.get("marketCap", 0)

    # 높은 ROE → roic_sustainability +1
    if roe and roe > 0.20:
        scores["roic_sustainability"] = min(4, scores["roic_sustainability"] + 1)
    elif roe and roe < 0:
        scores["roic_sustainability"] = max(0, scores["roic_sustainability"] - 1)

    # 높은 매출총이익률 → ip_efficiency +1 (SW, 바이오 등)
    if gm and gm > 0.60:
        scores["ip_efficiency"] = min(4, scores["ip_efficiency"] + 1)
    elif gm and gm < 0.20:
        scores["ip_efficiency"] = max(0, scores["ip_efficiency"] - 1)

    # 높은 영업이익률 → cost_advantage +1
    if om and om > 0.25:
        scores["cost_advantage"] = min(4, scores["cost_advantage"] + 1)

    # 대형주 → switching_costs or cost_advantage +1
    if cap and cap > 100e9:  # $100B+
        if scores["switching_costs"] >= 2:
            scores["switching_costs"] = min(4, scores["switching_costs"] + 1)
        else:
            scores["cost_advantage"] = min(4, scores["cost_advantage"] + 1)

    return scores

# ── 4. 해자 데이터 정밀화 ──
updated = 0
for ticker in targets:
    info = cache.get(ticker, {})
    industry = info.get("industry", "")

    match_key = match_industry(industry)
    if match_key:
        category, label, base_scores = INDUSTRY_MOAT_MAP[match_key]
    else:
        # 매칭 실패 시 기존 데이터 유지하되 detail만 업데이트
        if ticker in moat_data and isinstance(moat_data[ticker], dict):
            old = moat_data[ticker]
            category = old.get("category", "NONE")
            label = old.get("label", "미분류")
            base_scores = old.get("scores", {"switching_costs": 1, "network_effects": 0,
                                              "ip_efficiency": 1, "cost_advantage": 1,
                                              "roic_sustainability": 1})
        else:
            category = "NONE"
            label = "미분류"
            base_scores = {"switching_costs": 1, "network_effects": 0, "ip_efficiency": 1,
                          "cost_advantage": 1, "roic_sustainability": 1}

    scores = adjust_scores(base_scores, info)
    detail = make_detail_kr(ticker, info, category, label)

    moat_data[ticker] = {
        "category": category,
        "label": label,
        "detail": detail,
        "confidence": "yfinance_refined",
        "scores": scores,
    }
    updated += 1

print(f"[INFO] {updated}개 해자 데이터 정밀화 완료")

# ── 5. 저장 ──
with open(MOAT_PATH, "w", encoding="utf-8") as f:
    json.dump(moat_data, f, ensure_ascii=False, indent=2)

print(f"[DONE] moat_data.json 저장 완료 (총 {len(moat_data)}개 항목)")

# 통계
conf_counts = {}
for v in moat_data.values():
    if isinstance(v, dict):
        c = v.get("confidence", "unknown")
        conf_counts[c] = conf_counts.get(c, 0) + 1
print("\n[STATS] confidence 분포:")
for c, n in sorted(conf_counts.items(), key=lambda x: -x[1]):
    print(f"  {c}: {n}")
