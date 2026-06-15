"""
build_index_membership.py — 미국 주가지수 구성종목 매핑 생성기 (1회성 도구)

S&P 500 / S&P 400(미드캡) / S&P 600(스몰캡) / 나스닥100 의 실제 구성종목을
위키피디아에서 수집해 web_app/index_membership.json 으로 저장한다.

스캐너 런타임은 이 JSON 만 읽으므로 실행 중 네트워크 호출이 없다.
구성종목이 바뀌면(분기 리밸런싱 등) 이 스크립트를 다시 실행하면 된다.

    python scripts/build_index_membership.py

심볼 표기는 yfinance/스캐너 규칙(BRK-B, BF-B)에 맞춰 '.' → '-' 로 정규화한다.
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from datetime import datetime

import pandas as pd
import requests

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_MIN_CONSTITUENTS = 50   # 구성종목 테이블 sanity 하한 (지수당 최소 종목 수)
_MAX_RETRIES = 3         # 위키 요청 실패 시 재시도 횟수

# (지수 키, 위키 URL, 심볼 컬럼 후보들)
_SOURCES = [
    ("SP500", "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ("Symbol",)),
    ("SP400", "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", ("Symbol", "Ticker symbol", "Ticker")),
    ("SP600", "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies", ("Symbol", "Ticker symbol", "Ticker")),
    ("NDX",   "https://en.wikipedia.org/wiki/Nasdaq-100",                  ("Ticker", "Symbol")),
]


def _normalize(sym: str) -> str:
    """위키 표기를 yfinance/스캐너 표기로 정규화한다."""
    s = str(sym).strip().upper()
    # 각주 마커·공백 제거
    s = s.split("[")[0].strip()
    # BRK.B → BRK-B, BF.B → BF-B
    s = s.replace(".", "-")
    return s


def _fetch_html(url: str) -> str:
    """위키 페이지를 지수 backoff 재시도로 가져온다(일시적 네트워크/429 대응)."""
    last_err: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(url, headers={"User-Agent": _UA}, timeout=20)
            resp.raise_for_status()
            return resp.text
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < _MAX_RETRIES - 1:
                time.sleep(2 ** attempt)  # 1s, 2s, ...
    raise RuntimeError(f"요청 실패({_MAX_RETRIES}회): {url} — {last_err}")


def _fetch_symbols(url: str, col_candidates: tuple[str, ...]) -> list[str]:
    tables = pd.read_html(io.StringIO(_fetch_html(url)))
    # 심볼 컬럼을 가진 첫 테이블을 사용
    for df in tables:
        cols = {str(c).strip(): c for c in df.columns}
        for cand in col_candidates:
            if cand in cols:
                raw = df[cols[cand]].dropna().tolist()
                syms = sorted({_normalize(x) for x in raw if str(x).strip()})
                if len(syms) >= _MIN_CONSTITUENTS:  # 구성종목 테이블이 맞는지 sanity check
                    return syms
    raise RuntimeError(f"심볼 컬럼을 찾지 못함: {url} (후보 {col_candidates})")


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(here, "..", "web_app", "index_membership.json")
    out_path = os.path.abspath(out_path)

    result: dict[str, object] = {
        "_meta": {
            "source": "wikipedia",
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "note": "S&P500/400/600 + Nasdaq-100 구성종목. 심볼은 yfinance 표기(. → -)로 정규화.",
        }
    }

    for key, url, cols in _SOURCES:
        try:
            syms = _fetch_symbols(url, cols)
            result[key] = syms
            print(f"  {key:6s} {len(syms):4d} 종목  ← {url}")
        except Exception as e:  # noqa: BLE001
            print(f"  {key:6s} 실패: {e}", file=sys.stderr)
            result[key] = []

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"\n저장: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
