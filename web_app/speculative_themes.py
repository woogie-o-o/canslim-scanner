"""
speculative_themes.py — 투기성 테마주 식별 및 점수 보정.

양자컴퓨팅·우주 등 일부 테마주는 적자/매출 미미/극단 변동성으로
일반적인 기술/펀더 점수 산출이 왜곡된다. 화이트리스트로 라벨링하고
TotalScore를 B등급 상한(59)으로 캡해 S/A 후보에서 제외한다.

식별 기준:
  1) SPECULATIVE_TICKERS 화이트리스트 (수동 큐레이션)
  2) (선택) row 메트릭으로 보강 — EPS<0 & 매출 매우 작음

설계:
  - 백엔드 단일 진입점: apply_speculative_correction(rows)
  - row 변경: IsSpeculativeTheme, ThemeWarning, TotalScore(캡)
  - 한줄평·UI 가 이 플래그를 보고 경고 배지/문구 표시
"""
from __future__ import annotations

# 수동 큐레이션 — 적자·매출 미미·극단 변동성·내러티브 의존도 매우 높음.
# 동작 원리: 일반 점수 산출(기술/펀더)이 의미를 갖기 어려운 종목군.
SPECULATIVE_TICKERS: dict[str, str] = {
    # 양자컴퓨팅 — 상용 매출 거의 없음, 내러티브로 움직임
    "IONQ":  "양자컴 — 상용 매출 미미, 점수 신뢰도 낮음",
    "RGTI":  "양자컴 — 상용 매출 미미, 점수 신뢰도 낮음",
    "QUBT":  "양자컴 — 상용 매출 미미, 점수 신뢰도 낮음",
    "QBTS":  "양자컴 — 상용 매출 미미, 점수 신뢰도 낮음",
    "ARQQ":  "양자컴 — 상용 매출 미미, 점수 신뢰도 낮음",
    # 우주/위성 SPAC 출신 — 매출 변동 극심, 적자 누적
    "ASTS":  "우주/위성 — 적자 누적, 내러티브 의존 강함",
    # 핵 SMR — 상용 가동 전, FOMO 변동성
    "OKLO":  "SMR — 상용 가동 전, 점수 신뢰도 낮음",
    "SMR":   "SMR — 상용 매출 초기, 점수 신뢰도 낮음",
    # 자율주행·EV 적자 SPAC 잔존
    "LCID":  "EV — 적자 누적, 펀더 점수 왜곡 가능",
}

# 점수 상한 — B등급 경계(60) 미만으로 캡 → S/A 등급 후보에서 자동 제외
_SCORE_CAP = 59.0


def is_speculative(ticker: str) -> tuple[bool, str]:
    """티커가 화이트리스트에 있으면 (True, 사유)."""
    if not ticker:
        return (False, "")
    key = str(ticker).upper().strip()
    if key in SPECULATIVE_TICKERS:
        return (True, SPECULATIVE_TICKERS[key])
    return (False, "")


def _resolve_cap(cap: float | None) -> float:
    """MF-003: 환경변수/인자로 동적 score_cap 오버라이드.

    SCORE_CAP_OVERRIDE 환경변수가 있으면 우선, 다음 인자, 다음 기본값.
    """
    import os as _os
    env = _os.environ.get("SCORE_CAP_OVERRIDE")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    return float(cap) if cap is not None else _SCORE_CAP


def apply_to_row(row: dict, score_cap: float | None = None) -> dict:
    """단일 row in-place 보정. 보정된 dict 반환(동일 객체)."""
    if not row:
        return row
    ticker = row.get("Ticker") or ""
    spec, reason = is_speculative(ticker)
    if not spec:
        return row
    cap = _resolve_cap(score_cap)
    ts = row.get("TotalScore")
    if isinstance(ts, (int, float)) and ts > cap:
        row.setdefault("_RawTotalScore", float(ts))
        row["TotalScore"] = cap
    row["IsSpeculativeTheme"] = True
    row["ThemeWarning"] = reason
    return row


def apply_speculative_correction(rows: list[dict], score_cap: float | None = None) -> None:
    """rows를 in-place 보정. 투기성 테마주는 점수 캡 + 플래그 부착.

    score_cap 인자/환경변수 SCORE_CAP_OVERRIDE 로 체제별 동적 캡 가능.
    원본 점수는 _RawTotalScore에 보존해 디버깅·재계산 가능.
    """
    for r in rows:
        apply_to_row(r, score_cap=score_cap)
