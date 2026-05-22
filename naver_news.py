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
from typing import Any

API_URL = "https://openapi.naver.com/v1/search/news.json"
_TAG_RE = re.compile(r"<[^>]+>")
_ENV_FALLBACKS = (
    os.path.join(os.path.dirname(__file__), ".env"),
    r"D:\Download\scalping_final\.env",
)

POSITIVE_KO = {
    "호재", "상승", "급등", "실적개선", "최고치", "성장", "이익", "매수",
    "강세", "흑자", "수주", "확대", "신고가", "돌파", "회복", "개선",
}
NEGATIVE_KO = {
    "악재", "하락", "급락", "부진", "손실", "경고", "매도", "소송",
    "약세", "우려", "적자", "감소", "하향", "신저가", "이탈", "파산",
    "리콜", "조사", "압수수색",
}

# ── 명백히 무관한 기사(연예/스포츠/생활) 차단 키워드 ──
_IRRELEVANT_KW = {
    "맛집", "카페", "여행", "골프", "야구", "축구", "농구", "배구",
    "드라마", "영화", "예능", "아이돌", "콘서트", "팬미팅", "화보",
    "연애", "결혼", "이혼", "열애", "패션", "뷰티", "다이어트",
    "날씨", "요리", "레시피", "부고", "장례",
}


_WS_RE = re.compile(r"\s+")


def _is_subject(title: str, query: str) -> bool:
    """제목에 종목명이 등장하면 해당 종목 기사로 인정 (공백 무시).

    제목에 종목명이 있으면 그 회사가 기사의 주제일 가능성이 높고,
    본문에만 스쳐 지나간 타사 기사는 제목에 안 나오므로 자연히 걸러진다.
    (기존엔 '제목이 종목명으로 시작'만 통과시켜 90% 이상을 버렸음)
    """
    norm_q = _WS_RE.sub("", query)
    if not norm_q:
        return False
    return norm_q in _WS_RE.sub("", title)


def _is_relevant(title: str, desc: str, query: str) -> bool:
    """뉴스가 해당 종목의 주식/사업과 관련 있는지 판별."""
    # 제목에 종목명이 없으면 제외 (본문에만 스쳐간 타사 기사 차단)
    if not _is_subject(title, query):
        return False
    # 연예/스포츠/맛집 등 명백히 무관한 기사만 제외
    text = f"{title} {desc}"
    if any(kw in text for kw in _IRRELEVANT_KW):
        return False
    return True


def _load_env() -> tuple[str, str]:
    cid = os.environ.get("NAVER_CLIENT_ID", "").strip()
    sec = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
    if cid and sec:
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
    pos = sum(1 for k in POSITIVE_KO if k in text)
    neg = sum(1 for k in NEGATIVE_KO if k in text)
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
    raw = search_news(query, display=min(limit * 3, 100))
    items = [it for it in raw if _is_relevant(it["title"], it["description"], query)]
    if not items:
        return {
            "query": query,
            "count": 0,
            "avg_sentiment": 0.0,
            "positive": 0,
            "negative": 0,
            "neutral": 0,
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
    if is_available():
        r = summarize("삼성전자", limit=5)
        assert "summary_text" in r
        print("NAVER_NEWS OK", r["count"], "items, avg=", r["avg_sentiment"])
    else:
        print("NAVER_NEWS OK (no creds, skipped live test)")
