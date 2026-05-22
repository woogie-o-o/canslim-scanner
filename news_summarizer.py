from __future__ import annotations

from typing import Any

import yfinance as yf

POSITIVE_KEYWORDS = {
    "beat",
    "surge",
    "record",
    "upgrade",
    "strong",
    "gain",
    "rally",
    "growth",
    "profit",
    "buy",
    "호재",
    "상승",
    "급등",
    "실적개선",
    "최고치",
    "성장",
    "이익",
    "매수",
    "강세",
}

NEGATIVE_KEYWORDS = {
    "miss",
    "plunge",
    "downgrade",
    "weak",
    "loss",
    "drop",
    "decline",
    "warn",
    "sell",
    "lawsuit",
    "악재",
    "하락",
    "급락",
    "부진",
    "손실",
    "경고",
    "매도",
    "소송",
    "약세",
    "우려",
}


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _extract_timestamp(item: dict[str, Any]) -> int:
    for key in (
        "providerPublishTime",
        "published_ts",
        "pubDate",
        "published_at",
    ):
        value = item.get(key)
        if value is None:
            continue
        try:
            if isinstance(value, str) and value.isdigit():
                return int(value)
            if isinstance(value, (int, float)):
                return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _extract_link(item: dict[str, Any]) -> str:
    direct = _safe_str(item.get("link"))
    if direct:
        return direct

    for content in item.get("content", []) or []:
        if not isinstance(content, dict):
            continue
        candidate = _safe_str(content.get("canonicalUrl", {}).get("url"))
        if candidate:
            return candidate
        candidate = _safe_str(content.get("clickThroughUrl", {}).get("url"))
        if candidate:
            return candidate
        candidate = _safe_str(content.get("url"))
        if candidate:
            return candidate
    return ""


def _extract_summary(item: dict[str, Any]) -> str:
    for key in ("summary", "description"):
        value = _safe_str(item.get(key))
        if value:
            return value

    for content in item.get("content", []) or []:
        if not isinstance(content, dict):
            continue
        for key in ("summary", "description", "excerpt"):
            value = _safe_str(content.get(key))
            if value:
                return value
    return ""


def fetch_news(ticker: str, *, limit: int = 10) -> list[dict]:
    """Return normalized Yahoo Finance news items for the ticker."""
    if limit <= 0:
        return []

    try:
        raw_items = yf.Ticker(ticker).news
    except Exception:
        return []

    if not raw_items:
        return []

    news_items: list[dict] = []
    for item in raw_items[:limit]:
        if not isinstance(item, dict):
            continue
        # yfinance 최신 버전: 데이터가 item["content"] dict 안에 중첩
        content = item.get("content") if isinstance(item.get("content"), dict) else None
        src = content or item
        publisher = ""
        if content:
            prov = content.get("provider")
            if isinstance(prov, dict):
                publisher = _safe_str(prov.get("displayName"))
        if not publisher:
            publisher = _safe_str(item.get("publisher"))
        link = ""
        if content:
            cu = content.get("canonicalUrl")
            if isinstance(cu, dict):
                link = _safe_str(cu.get("url"))
            if not link:
                ct = content.get("clickThroughUrl")
                if isinstance(ct, dict):
                    link = _safe_str(ct.get("url"))
        if not link:
            link = _extract_link(item)
        news_items.append(
            {
                "title": _safe_str(src.get("title")),
                "publisher": publisher,
                "link": link,
                "published_ts": _extract_timestamp(src),
                "summary": _safe_str(src.get("summary")) or _safe_str(src.get("description")) or _extract_summary(item),
            }
        )
    return news_items


def score_sentiment(text: str) -> float:
    """Score headline/summary sentiment from -1.0 to +1.0 using keyword hits."""
    text_lower = _safe_str(text).lower()
    if not text_lower:
        return 0.0

    pos_count = sum(1 for keyword in POSITIVE_KEYWORDS if keyword.lower() in text_lower)
    neg_count = sum(1 for keyword in NEGATIVE_KEYWORDS if keyword.lower() in text_lower)
    total = pos_count + neg_count
    if total == 0:
        return 0.0
    return (pos_count - neg_count) / max(1, total)


def _classify(score: float) -> str:
    if score > 0.34:
        return "positive"
    if score < -0.34:
        return "negative"
    return "neutral"


def _build_summary_text(
    ticker: str,
    count: int,
    positive: int,
    negative: int,
    neutral: int,
    avg_sentiment: float,
    top_positive: list[dict],
    top_negative: list[dict],
) -> str:
    if count == 0:
        return f"{ticker} 관련 뉴스가 없습니다."

    tone = "중립적"
    if avg_sentiment > 0.15:
        tone = "대체로 긍정적"
    elif avg_sentiment < -0.15:
        tone = "대체로 부정적"

    sentence_1 = (
        f"{ticker} 최근 뉴스 {count}건을 분석한 결과, 긍정 {positive}건·부정 {negative}건·중립 {neutral}건으로 "
        f"전체 분위기는 {tone}입니다."
    )

    highlights: list[str] = []
    if top_positive:
        highlights.append(f"긍정 이슈는 '{top_positive[0]['title']}'가 두드러졌습니다")
    if top_negative:
        highlights.append(f"부정 이슈는 '{top_negative[0]['title']}'가 대표적입니다")

    if highlights:
        sentence_2 = ". ".join(highlights) + "."
    else:
        sentence_2 = "뚜렷한 호재나 악재 키워드는 제한적이었습니다."

    return f"{sentence_1} {sentence_2}"


def summarize(ticker: str, *, limit: int = 10) -> dict:
    """Fetch, score, and summarize recent ticker news."""
    news_items = fetch_news(ticker, limit=limit)
    if not news_items:
        return {
            "ticker": ticker,
            "count": 0,
            "avg_sentiment": 0.0,
            "positive": 0,
            "negative": 0,
            "neutral": 0,
            "top_positive": [],
            "top_negative": [],
            "summary_text": f"{ticker} 관련 뉴스가 없습니다.",
        }

    scored_items: list[dict[str, Any]] = []
    positive = 0
    negative = 0
    neutral = 0

    for item in news_items:
        text = " ".join(part for part in (item["title"], item["summary"]) if part).strip()
        sentiment = score_sentiment(text)
        bucket = _classify(sentiment)
        if bucket == "positive":
            positive += 1
        elif bucket == "negative":
            negative += 1
        else:
            neutral += 1

        scored_items.append(
            {
                **item,
                "sentiment": sentiment,
                "bucket": bucket,
            }
        )

    count = len(scored_items)
    avg_sentiment = sum(item["sentiment"] for item in scored_items) / count

    top_positive = [
        {
            "title": item["title"],
            "sentiment": round(item["sentiment"], 4),
            "link": item["link"],
        }
        for item in sorted(
            (item for item in scored_items if item["sentiment"] > 0),
            key=lambda row: (-row["sentiment"], -row["published_ts"], row["title"]),
        )[:3]
    ]

    top_negative = [
        {
            "title": item["title"],
            "sentiment": round(item["sentiment"], 4),
            "link": item["link"],
        }
        for item in sorted(
            (item for item in scored_items if item["sentiment"] < 0),
            key=lambda row: (row["sentiment"], -row["published_ts"], row["title"]),
        )[:3]
    ]

    return {
        "ticker": ticker,
        "count": count,
        "avg_sentiment": round(avg_sentiment, 4),
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
        "top_positive": top_positive,
        "top_negative": top_negative,
        "summary_text": _build_summary_text(
            ticker=ticker,
            count=count,
            positive=positive,
            negative=negative,
            neutral=neutral,
            avg_sentiment=avg_sentiment,
            top_positive=top_positive,
            top_negative=top_negative,
        ),
    }


if __name__ == "__main__":
    assert score_sentiment("Apple earnings beat estimates, stock surged") > 0
    assert score_sentiment("Lawsuit filed, shares plunge") < 0
    assert score_sentiment("Apple unveils new product") == 0

    result = summarize("AAPL")
    assert isinstance(result, dict)
    assert "summary_text" in result
    assert isinstance(result["summary_text"], str)

    print("NEWS_SUMMARIZER OK")
