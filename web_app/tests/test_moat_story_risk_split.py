"""MF-000/MF-001: 해자 카테고리와 스토리리스크 분리 + 큐레이션 우선."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from moat import _rule_based, _resolve_one, INTANGIBLE, NONE  # type: ignore


def test_rule_based_speculative_theme_keeps_sector_category():
    out = _rule_based(sector="communication services", theme="위성·발사체", mcap=5e9)
    assert out["category"] != NONE, f"speculative theme blanket override leaked: {out}"
    assert out.get("story_risk") is True
    assert "story_risk" in out


def test_rule_based_non_speculative_theme_no_story_risk():
    out = _rule_based(sector="technology", theme="AI 인프라", mcap=1e11)
    assert out.get("story_risk") is False


def test_rule_based_none_theme_no_story_risk():
    out = _rule_based(sector="technology", theme=None, mcap=1e11)
    assert out.get("story_risk") is False


def test_asts_curated_override_wins_over_speculative():
    row = {
        "Ticker": "ASTS",
        "Sector": "communication services",
        "Theme": "위성·발사체",
        "_MarketCap": 5e9,
    }
    out = _resolve_one(row)
    assert out["category"] == INTANGIBLE, f"curated override lost: {out}"
    assert out["source"] == "curated"
    assert out.get("story_risk") is True
