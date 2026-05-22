# -*- coding: utf-8 -*-
"""데이터 품질·한계 명시 — yfinance 15분 지연 라벨 등."""
from __future__ import annotations

DATA_DELAY_MIN = 15  # yfinance 무료 데이터 지연(분)
DATA_SOURCE = "yfinance"


def build_delay_badge_text() -> str:
    return f"⚠ DELAYED {DATA_DELAY_MIN}min · {DATA_SOURCE}"


def build_delay_badge_style() -> dict:
    """tkinter Label 용 스타일 dict."""
    return {"fg": "#FFFFFF", "bg": "#F04452", "font": ("Segoe UI", 8, "bold"),
            "padx": 6, "pady": 2}


def is_market_data_delayed(source: str = DATA_SOURCE) -> bool:
    return source.lower() in {"yfinance", "yahoo"}


if __name__ == "__main__":
    print(build_delay_badge_text())
    assert DATA_DELAY_MIN == 15
    assert "DELAYED" in build_delay_badge_text()
    assert is_market_data_delayed()
    print("[OK] data_quality")
