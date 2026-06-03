`C:\Users\Administrator\Documents\카카오톡 받은 파일\종목스캐너\web_app\symbol_alias.py` 를 **새로 생성**. 기존 파일 수정 금지.

## 목적
Yahoo Finance에서 리네임/상폐된 미국 티커가 반복적으로 404 / "possibly delisted" 에러를 일으킨다.
이를 사전 차단할 alias map + 필터 함수를 한 모듈로 제공.

## 공개 API (정확히 이대로)
```python
# 모듈 docstring
"""symbol_alias — Yahoo Finance 티커 정규화/필터 유틸.

리네임된 티커는 SYMBOL_ALIASES로 자동 변환,
상폐된 티커는 DELISTED로 사전 필터링.
사이드 이펙트 없음 (네트워크/IO 호출 금지).
"""

SYMBOL_ALIASES: dict[str, str] = {
    "FB": "META",      # 2022 Meta rebrand
    "BRK.B": "BRK-B",  # Yahoo는 하이픈 사용
    "BF.B": "BF-B",
    "BRK.A": "BRK-A",
}

DELISTED: frozenset[str] = frozenset({
    "ATVI",  # MSFT 인수 완료 (2023-10)
    "TWTR",  # 비상장 전환 (2022-10)
    "VMW",   # AVGO 인수 완료 (2023-11)
    "SIVB",  # 파산 (2023-03)
    "FRC",   # JPM 인수 (2023-05)
    "SBNY",  # 파산 (2023-03)
    "CS",    # UBS 인수 (2023-06)
})


def normalize_symbol(ticker: str) -> str:
    """대문자화 + alias 변환. None/빈문자열은 빈 문자열 반환."""
    if not ticker:
        return ""
    up = str(ticker).strip().upper()
    return SYMBOL_ALIASES.get(up, up)


def is_delisted(ticker: str) -> bool:
    """DELISTED 셋에 있으면 True. 대소문자 무관."""
    if not ticker:
        return False
    return str(ticker).strip().upper() in DELISTED


def filter_symbols(tickers: list[str]) -> list[str]:
    """normalize + delisted 제거 + 중복 제거(순서 보존)."""
    out: list[str] = []
    for t in tickers:
        norm = normalize_symbol(t)
        if not norm or is_delisted(norm):
            continue
        if norm not in out:
            out.append(norm)
    return out
```

## 요구사항
- 표준 라이브러리만 사용 (외부 import 0개)
- 모듈 import 시 사이드 이펙트 없음 (logging/print/IO 호출 금지)
- 한국 티커(.KS/.KQ 접미사)는 대문자화만 하고 그대로 통과 (alias map에 한국 티커 없음 — 자동으로 그대로 통과)

## 검증
```bash
python -m py_compile "C:\Users\Administrator\Documents\카카오톡 받은 파일\종목스캐너\web_app\symbol_alias.py"
cd "C:\Users\Administrator\Documents\카카오톡 받은 파일\종목스캐너\web_app"
python -c "from symbol_alias import SYMBOL_ALIASES, DELISTED, normalize_symbol, is_delisted, filter_symbols; assert normalize_symbol('fb')=='META'; assert is_delisted('atvi'); assert filter_symbols(['fb','ATVI','aapl','FB'])==['META','AAPL']; print('OK')"
```

## 금지사항
- 다른 파일 수정 금지
- 외부 라이브러리 import 금지
- print / logging 호출 금지
