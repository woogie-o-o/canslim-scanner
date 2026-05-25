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
    "WIRE",  # Prysmian 인수 (2024-07)
    "CHK",   # Southwestern Energy 합병 → EXE 재상장 (2024-10)
    # ── 2025+ 추가 (라이브 로그에서 확인된 404/Not Found) ──
    "MYR",   # MYR Group → 리네임/상폐
    "MMC",   # Marsh McLennan → MMC 티커 정리
    "CMA",   # Comerica → 합병
    "SNV",   # Synovus → 합병
    "CIVI",  # Civitas Resources → 리네임/합병
    "HES",   # Hess → CVX 인수 완료
    "NOVA",  # Sunnova Energy → 파산/상폐
    "MRO",   # Marathon Oil → COP 인수 완료 (2024-11)
    "SAVA",  # Cassava Sciences → 리네임
    "DCPH",  # Deciphera → ONO 인수 (2024)
    "AXNX",  # Axonics → BSX 인수 완료 (2024)
    "EXAS",  # Exact Sciences → 리네임/상폐
    "CTLT",  # Catalent → Novo Holdings 인수 (2024-12)
    "GPS",   # Gap Inc → GAP 티커 변경
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
