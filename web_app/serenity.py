"""Serenity (@aleabitoreddit) insight lookup from local skill data."""
from __future__ import annotations

import re
from pathlib import Path
from functools import lru_cache

_SKILL_BASE = Path(__file__).parent.parent / '.agents' / 'skills' / 'serenity-aleabitoreddit'
_THESES_PATH = _SKILL_BASE / 'references' / 'theses.md'
_TRACK_PATH  = _SKILL_BASE / 'references' / 'track-record.md'


def _signal_color(signal_text: str) -> str:
    t = signal_text.lower()
    if any(w in t for w in ('strongly bullish', 'long', 'bullish')):
        return 'green'
    if any(w in t for w in ('cautious', 'mixed', 'risk watch', 'commentary')):
        return 'yellow'
    if any(w in t for w in ('bearish', 'short', 'avoid')):
        return 'red'
    return 'neutral'


def _extract_tickers_from_header(header: str) -> list[str]:
    """Extract normalized ticker symbols from a section header."""
    tickers = []
    # $TICKER pattern
    for m in re.finditer(r'\$([A-Z]{1,6})', header):
        tickers.append(m.group(1))
    # Korean/Taiwan numeric codes: (093370.KS), (3231 TWO), etc.
    for m in re.finditer(r'\((\d{4,6})(?:\s*[A-Z]{2,3}|\.KS|\.KQ|\.KT)?\)', header):
        tickers.append(m.group(1))
    # LON:XXX pattern
    for m in re.finditer(r'LON:\s*([A-Z]{2,5})', header):
        tickers.append(m.group(1))
    return tickers


@lru_cache(maxsize=1)
def _load_theses() -> dict:
    """Parse theses.md into a ticker → insight dict (cached)."""
    if not _THESES_PATH.exists():
        return {}

    text = _THESES_PATH.read_text(encoding='utf-8')
    sections = re.split(r'\n---\n', text)
    result = {}

    for section in sections:
        lines = section.strip().splitlines()
        if not lines:
            continue

        # Find ## header
        header_line = next((l for l in lines if l.startswith('## ')), None)
        if not header_line:
            continue

        tickers = _extract_tickers_from_header(header_line)
        if not tickers:
            continue

        # Parse fields
        body = '\n'.join(lines)
        signal_m = re.search(r'\*\*Latest signal\*\*:\s*(.+?)(?:\n|$)', body)
        tweet_m  = re.search(r'\*\*Latest tweet\*\*:\s*\[(\d+)\]\((https://x\.com/[^\)]+)\)\s*[—–-]\s*(\S+)', body)
        quote_m  = re.search(r'\*\*Quote\*\*:\s*"(.+?)"', body, re.DOTALL)
        ctx_m    = re.search(r'\*\*Context\*\*:\s*(.+?)(?:\n\n|\Z)', body, re.DOTALL)

        signal_text = signal_m.group(1).strip() if signal_m else ''
        tweet_url   = tweet_m.group(2).strip() if tweet_m else ''
        tweet_date  = tweet_m.group(3).strip() if tweet_m else ''
        quote       = re.sub(r'\s+', ' ', quote_m.group(1)).strip() if quote_m else ''
        context     = re.sub(r'\s+', ' ', ctx_m.group(1)).strip() if ctx_m else ''
        # Trim context to 300 chars
        if len(context) > 300:
            context = context[:297] + '…'

        entry = {
            'signal':     signal_text,
            'color':      _signal_color(signal_text),
            'quote':      quote,
            'context':    context,
            'tweet_url':  tweet_url,
            'tweet_date': tweet_date,
            'header':     header_line[3:].strip(),
        }

        for t in tickers:
            result[t.upper()] = entry

    return result


def get_serenity_insight(ticker: str) -> dict | None:
    """Return Serenity's latest insight for ticker, or None if not covered."""
    # Normalize: strip exchange suffix (ACMR.KS → ACMR, 093370.KS → 093370)
    t = ticker.upper().split('.')[0]
    db = _load_theses()
    return db.get(t)


def list_covered_tickers() -> list[str]:
    return sorted(_load_theses().keys())
