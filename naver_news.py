"""Naver Search News API client + sentiment scoring (한국어 우선).

ENV:
    NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
또는 D:/Download/scalping_final/.env 자동 로드.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

API_URL = "https://openapi.naver.com/v1/search/news.json"
_TAG_RE = re.compile(r"<[^>]+>")
_ENV_FALLBACKS = (
    os.path.join(os.path.dirname(__file__), ".env"),
    r"D:\Download\scalping_final\.env",
)
_CONFIG_JSON_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def _load_from_config_json() -> tuple[str, str]:
    """config.json에서 NAVER 키를 직접 로드 (apply_to_environ 미호출 환경 대비)."""
    try:
        with open(_CONFIG_JSON_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return (
            (data.get("NAVER_CLIENT_ID") or "").strip(),
            (data.get("NAVER_CLIENT_SECRET") or "").strip(),
        )
    except (OSError, json.JSONDecodeError):
        return "", ""

POSITIVE_KO = {
    "호재", "상승", "급등", "실적개선", "최고치", "성장", "이익", "매수",
    "강세", "흑자", "수주", "확대", "신고가", "돌파", "회복", "개선",
    "호황", "수혜", "최대", "최고", "증가", "증익", "상향", "낙관",
    "질주", "랠리", "슈퍼사이클", "대박", "본격화",
}
NEGATIVE_KO = {
    "악재", "하락", "급락", "부진", "손실", "경고", "매도", "소송",
    "약세", "우려", "적자", "감소", "하향", "신저가", "이탈", "파산",
    "리콜", "조사", "압수수색", "피소", "특허침해", "침해", "파업",
    "논란", "논쟁", "갈등", "혼란", "박탈감", "무효", "규제", "쇼크",
    "위기", "리스크", "둔화", "침체", "감산", "낙인", "흔들", "잃었",
    "잃다", "대기자금", "투기판", "과열",
}

# ── 명백히 무관한 기사(연예/스포츠/생활) 차단 키워드 ──
_IRRELEVANT_KW = {
    "맛집", "카페", "여행", "골프", "야구", "축구", "농구", "배구",
    "드라마", "영화", "예능", "아이돌", "콘서트", "팬미팅", "화보",
    "연애", "결혼", "이혼", "열애", "패션", "뷰티", "다이어트",
    "날씨", "요리", "레시피", "부고", "장례", "구몬", "시구",
    "잠실구장",
}

_BUSINESS_CONTEXT_KW = {
    "주가", "주식", "증시", "코스피", "코스닥", "실적", "매출", "영업익",
    "영업이익", "순이익", "이익", "수익", "투자", "수주", "계약", "공급",
    "생산", "출하", "판매", "점유율", "인수", "합병", "M&A", "공시",
    "배당", "자사주", "컨센", "목표가", "반도체", "파운드리", "HBM",
    "메모리", "D램", "DRAM", "낸드", "NAND", "스마트폰", "갤럭시",
    "AI", "특허", "소송", "피소", "규제", "노조", "파업", "성과급",
    "초과이익", "초과이윤", "리콜", "조사",
}

_BROAD_MARKET_KW = {
    "코스피", "코스닥", "증시", "대형주", "빚투", "신용잔고", "공매도",
    "불장", "지수", "개미", "순매수", "순매도",
}

_OFFTOPIC_KW = {
    "후보", "사전투표", "경기지사", "민주당", "국민의힘", "범민주",
    "대선", "총선", "표심", "강행군", "재정파탄론", "부채율",
    "초등학생", "주민센터", "언론 노출",
}

_OTHER_COMPANY_FOCUS_KW = {
    "LG", "구광모", "현대차", "네이버", "카카오", "화웨이", "HD현대",
    "모토로라", "SK하이닉스", "하이닉스",
}

_COMPANY_ALIASES = {
    "삼성전자": {
        "삼성전자", "삼전", "삼성DX", "삼성DS", "삼성파운드리",
        "갤럭시", "갤럭시AI", "삼전닉스", "이재용",
    },
    "SK하이닉스": {"SK하이닉스", "하이닉스", "SK하닉", "하닉", "SKHYNIX"},
    "LG": {"LG", "엘지", "구광모"},
}

_COMPANY_DOMAIN_KW = {
    "삼성전자": {
        "반도체", "파운드리", "HBM", "메모리", "D램", "DRAM", "낸드",
        "NAND", "갤럭시", "스마트폰", "엑시노스", "DS", "DX", "삼전닉스",
        "성과급", "초기업노조", "엔비디아", "젠슨황",
    },
    "SK하이닉스": {"반도체", "HBM", "메모리", "D램", "DRAM", "낸드", "NAND"},
}

_COMPANY_SPECIFIC_DOMAIN_KW = {
    "삼성전자": {
        "파운드리", "HBM", "메모리", "D램", "DRAM", "낸드", "NAND",
        "갤럭시", "스마트폰", "엑시노스", "DS", "DX", "삼전닉스",
        "성과급", "초기업노조", "엔비디아", "젠슨황",
        "초과이익", "초과이윤",
    },
    "SK하이닉스": {"HBM", "메모리", "D램", "DRAM", "낸드", "NAND"},
}

_POS_WEIGHTS = {
    "호재": 1.8,
    "급등": 1.8,
    "신고가": 1.7,
    "실적개선": 1.7,
    "흑자": 1.6,
    "수주": 1.5,
    "상향": 1.5,
    "슈퍼사이클": 1.5,
    "강세": 1.3,
    "상승": 1.2,
    "최고치": 1.2,
    "최고": 1.1,
    "수혜": 1.1,
    "성장": 1.0,
    "개선": 1.0,
    "회복": 1.0,
    "돌파": 1.0,
    "이익": 0.7,
}

_NEG_WEIGHTS = {
    "피소": 2.4,
    "특허침해": 2.4,
    "침해": 1.6,
    "소송": 2.0,
    "무효": 2.0,
    "파업": 2.0,
    "압수수색": 2.0,
    "박탈감": 1.9,
    "혼란": 1.8,
    "잃었": 2.5,
    "잃다": 2.5,
    "악재": 1.8,
    "급락": 1.8,
    "적자": 1.7,
    "손실": 1.7,
    "부진": 1.6,
    "우려": 1.4,
    "리스크": 1.4,
    "하향": 1.4,
    "논란": 1.3,
    "논쟁": 1.2,
    "규제": 1.2,
    "쇼크": 1.2,
    "낙인": 1.2,
    "흔들": 1.2,
    "과열": 1.0,
    "하락": 1.0,
}


_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[\s·ㆍ\-_.,:;!?\"'‘’“”()\[\]{}<>]+")


def _norm(s: str) -> str:
    return _PUNCT_RE.sub("", s or "").upper()


def _query_base(query: str) -> str:
    q = _WS_RE.sub("", query or "")
    return (
        q.replace("(주)", "")
        .replace("㈜", "")
        .replace("주식회사", "")
    )


def _terms_for_query(query: str, mapping: dict[str, set[str]]) -> set[str]:
    base = _query_base(query)
    terms = {base}
    flex = base.replace("지주", "").replace("홀딩스", "")
    if len(flex) >= 2:
        terms.add(flex)
    base_norm = _norm(base)
    for key, aliases in mapping.items():
        if _norm(key) == base_norm:
            terms.update(aliases)
            break
    return {t for t in terms if t}


def _contains_any(text: str, terms: set[str]) -> bool:
    text_norm = _norm(text)
    return any(_norm(t) in text_norm for t in terms if len(_norm(t)) >= 2)


def _business_terms_for_query(query: str) -> set[str]:
    terms = set(_BUSINESS_CONTEXT_KW)
    base_norm = _norm(_query_base(query))
    for key, kws in _COMPANY_DOMAIN_KW.items():
        if _norm(key) == base_norm:
            terms.update(kws)
            break
    return terms


def _specific_domain_terms_for_query(query: str) -> set[str]:
    base_norm = _norm(_query_base(query))
    for key, kws in _COMPANY_SPECIFIC_DOMAIN_KW.items():
        if _norm(key) == base_norm:
            return set(kws)
    return set()


def _is_subject(text: str, query: str) -> bool:
    """텍스트에 종목명이 등장하면 해당 종목 기사로 인정 (공백 무시).

    제목 또는 요약에 종목명이 있으면 관련 기사로 인정한다.
    단, 이후 명백한 생활/연예/스포츠 잡뉴스 키워드는 별도로 제외한다.
    """
    return _contains_any(text, _terms_for_query(query, _COMPANY_ALIASES))


def _is_relevant(title: str, desc: str, query: str) -> bool:
    """뉴스가 해당 종목의 주식/사업과 관련 있는지 판별."""
    text = f"{title} {desc}"
    title_subject = _is_subject(title, query)
    body_subject = _is_subject(text, query)
    if not body_subject:
        return False
    # 연예/스포츠/맛집 등 명백히 무관한 기사만 제외
    if any(kw in text for kw in _IRRELEVANT_KW):
        return False
    business_terms = _business_terms_for_query(query)
    title_business = _contains_any(title, business_terms)
    text_business = _contains_any(text, business_terms)
    off_topic = _contains_any(title, _OFFTOPIC_KW)

    # 제목에 직접 종목/별칭이 있으면 기본 통과하되, 정치·생활성 제목은
    # 사업/투자 맥락이 없을 때 제외한다.
    if title_subject:
        if off_topic and not title_business:
            return False
        return True

    # 요약에만 종목명이 걸린 경우는 넓게 섞이기 쉬우므로 제목에도 해당
    # 회사의 사업/투자 이슈가 있어야 통과시킨다.
    if not title_business or not text_business:
        return False
    if _contains_any(title, _OTHER_COMPANY_FOCUS_KW):
        return False
    if _contains_any(title, _BROAD_MARKET_KW) and not _contains_any(
        title, _specific_domain_terms_for_query(query)
    ):
        return False
    if off_topic:
        return False
    return True


def _load_env() -> tuple[str, str]:
    cid = os.environ.get("NAVER_CLIENT_ID", "").strip()
    sec = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
    if cid and sec:
        return cid, sec
    # config.json 폴백 (web 설정 UI 제거 후에도 키 자동 로드)
    c_cid, c_sec = _load_from_config_json()
    if not cid:
        cid = c_cid
    if not sec:
        sec = c_sec
    if cid and sec:
        os.environ["NAVER_CLIENT_ID"] = cid
        os.environ["NAVER_CLIENT_SECRET"] = sec
        return cid, sec
    for path in _ENV_FALLBACKS:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k == "NAVER_CLIENT_ID" and not cid:
                        cid = v
                    elif k == "NAVER_CLIENT_SECRET" and not sec:
                        sec = v
        except OSError:
            continue
        if cid and sec:
            break
    return cid, sec


def _strip_html(s: str) -> str:
    if not s:
        return ""
    s = _TAG_RE.sub("", s)
    return (
        s.replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .strip()
    )


def search_news(query: str, *, display: int = 20, sort: str = "date") -> list[dict]:
    """Return list of {title, description, link, pub_date} from Naver Search API.

    sort: "date" (최신) | "sim" (관련도)
    """
    cid, sec = _load_env()
    if not cid or not sec:
        raise RuntimeError("NAVER_CLIENT_ID/SECRET 미설정")
    if not query or not str(query).strip():
        return []
    display = max(1, min(int(display), 100))
    qs = urllib.parse.urlencode(
        {"query": query, "display": display, "sort": sort}
    )
    req = urllib.request.Request(
        f"{API_URL}?{qs}",
        headers={
            "X-Naver-Client-Id": cid,
            "X-Naver-Client-Secret": sec,
            "User-Agent": "Mozilla/5.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    items = data.get("items") or []
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        out.append(
            {
                "title": _strip_html(it.get("title", "")),
                "description": _strip_html(it.get("description", "")),
                "link": (it.get("originallink") or it.get("link") or "").strip(),
                "pub_date": (it.get("pubDate") or "").strip(),
            }
        )
    return out


def score_sentiment(text: str) -> float:
    if not text:
        return 0.0
    pos = sum(_POS_WEIGHTS.get(k, 1.0) for k in POSITIVE_KO if k in text)
    neg = sum(_NEG_WEIGHTS.get(k, 1.0) for k in NEGATIVE_KO if k in text)
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / max(1, total)


def _classify(s: float) -> str:
    if s > 0.34:
        return "positive"
    if s < -0.34:
        return "negative"
    return "neutral"


def summarize(query: str, *, limit: int = 20) -> dict:
    """검색어로 네이버 뉴스 요약 + 감성 분석 (관련성 필터 적용)."""
    # 관련성 필터 후 충분한 기사를 확보하기 위해 넉넉하게 가져옴
    raw = search_news(query, display=min(limit * 4, 100))
    items = []
    for it in raw:
        if _is_relevant(it["title"], it["description"], query):
            items.append(it)
        if len(items) >= limit:
            break
    if not items:
        return {
            "query": query,
            "count": 0,
            "avg_sentiment": 0.0,
            "positive": 0,
            "negative": 0,
            "neutral": 0,
            "items": [],
            "top_positive": [],
            "top_negative": [],
            "summary_text": f"{query} 관련 네이버 뉴스가 없습니다.",
        }
    scored = []
    pos = neg = neu = 0
    for it in items:
        text = f"{it['title']} {it['description']}"
        s = score_sentiment(text)
        b = _classify(s)
        if b == "positive":
            pos += 1
        elif b == "negative":
            neg += 1
        else:
            neu += 1
        scored.append({**it, "sentiment": s, "bucket": b})
    avg = sum(x["sentiment"] for x in scored) / len(scored)
    top_pos = [
        {"title": x["title"], "sentiment": round(x["sentiment"], 3), "link": x["link"]}
        for x in sorted(scored, key=lambda r: -r["sentiment"]) if x["sentiment"] > 0
    ][:3]
    top_neg = [
        {"title": x["title"], "sentiment": round(x["sentiment"], 3), "link": x["link"]}
        for x in sorted(scored, key=lambda r: r["sentiment"]) if x["sentiment"] < 0
    ][:3]
    public_items = [
        {
            "title": x["title"],
            "description": x["description"],
            "sentiment": round(x["sentiment"], 3),
            "bucket": x["bucket"],
            "link": x["link"],
            "pub_date": x.get("pub_date", ""),
        }
        for x in scored
    ]
    tone = "중립적"
    if avg > 0.15:
        tone = "대체로 긍정적"
    elif avg < -0.15:
        tone = "대체로 부정적"
    s1 = (
        f"{query} 네이버 뉴스 {len(scored)}건 분석 결과, "
        f"긍정 {pos}·부정 {neg}·중립 {neu}건으로 분위기는 {tone}입니다."
    )
    parts = []
    if top_pos:
        parts.append(f"긍정 이슈: '{top_pos[0]['title']}'")
    if top_neg:
        parts.append(f"부정 이슈: '{top_neg[0]['title']}'")
    s2 = ". ".join(parts) + "." if parts else "뚜렷한 호/악재 키워드는 제한적입니다."
    return {
        "query": query,
        "count": len(scored),
        "avg_sentiment": round(avg, 4),
        "positive": pos,
        "negative": neg,
        "neutral": neu,
        "items": public_items,
        "top_positive": top_pos,
        "top_negative": top_neg,
        "summary_text": f"{s1} {s2}",
    }


def is_available() -> bool:
    cid, sec = _load_env()
    return bool(cid and sec)


if __name__ == "__main__":
    assert score_sentiment("실적개선 호재로 급등") > 0
    assert score_sentiment("악재 손실 우려로 급락") < 0
    assert _classify(score_sentiment("특허침해 피소")) == "negative"
    assert _classify(score_sentiment("노조는 이겼지만 삼성은 무엇을 잃었나 회복하려면")) == "negative"
    assert _classify(score_sentiment("메모리 슈퍼사이클 가속 D램 낸드 최고가 행진")) == "positive"
    assert not _is_relevant("김태흠 박수현 재정파탄론", "삼성전자 언급", "삼성전자")
    assert not _is_relevant("[뉴스초점] 코스피, 또 역대 최고치", "삼성전자 반도체 관련", "삼성전자")
    assert _is_relevant("산업계 넘어 정부로 반도체 초과이익 논쟁 확산", "삼성전자를 포함한 업계 이슈", "삼성전자")
    assert _is_relevant("[Biz&Law] 삼성전자, 인도 NPE에 갤럭시 AI 특허침해 피소", "", "삼성전자")
    if is_available():
        r = summarize("삼성전자", limit=5)
        assert "summary_text" in r
        print("NAVER_NEWS OK", r["count"], "items, avg=", r["avg_sentiment"])
    else:
        print("NAVER_NEWS OK (no creds, skipped live test)")
