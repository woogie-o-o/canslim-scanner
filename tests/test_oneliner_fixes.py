"""Regression tests for codex-approved fixes.

#1 EARNINGS_BEAT gate alignment (mom3>=3 or near_h required)
#2 SECTOR_LEADER score>=60 floor
#4 _FORBIDDEN_CTA substring no longer blocks data-tag '매수'
   (insider/consensus/foreign/institution flow tags must pass through
    _safe_for_bucket, but final _scrub_oneliner still blocks CTA phrases).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "web_app"))

from one_liner import (  # noqa: E402
    _positive_for,
    _scrub_oneliner,
    get_oneliner_data,
)


def test_insider_buy_passes_data_tag():
    """내부자 매수 — 'STRONG_BUY' 버킷은 p[]가 비어 있어 extra만 들어가므로
    28자 클램프 안에 떨어진다. 이전 코드는 _safe_for_bucket의 '매수' 부분일치로
    이걸 떨궜다 — 이제는 _ok_data로 통과해야 한다."""
    d = {
        "Ticker": "AAPL",
        "TotalScore": 80,
        "Mom12M": 0.35,
        "_ROE": 0.25,
        "_EPSGrowth": 0.30,
        "_OperatingMargin": 0.20,
        "RSI": 60,
        "_FH_Available": True,
        "_FH_InsiderNet": 50000,
    }
    # STRONG_BUY: _data_tag의 p[]가 빈 버킷 → extra만 사용 → 짧은 태그 보장
    out = get_oneliner_data(d, "STRONG_BUY")
    assert "내부자 매수" in out, f"insider buy not in: {out!r}"


def test_consensus_buy_passes_data_tag():
    """컨센서스 매수 (yf rec='buy') — STRONG_BUY 버킷이라 p[]=[] → extra만 입성."""
    d = {
        "Ticker": "TSLA",
        "TotalScore": 75,
        "Mom12M": 0.40,
        "_ROE": 0.22,
        "_EPSGrowth": 0.25,
        "_OperatingMargin": 0.18,
        "RSI": 60,
        "_YF_Available": True,
        "_YF_RecKey": "buy",
        "_YF_NumAnalysts": 25,
    }
    out = get_oneliner_data(d, "STRONG_BUY")
    assert "매수" in out, f"consensus buy not in: {out!r}"


def test_data_tag_buy_no_longer_filtered_by_cta_substring():
    """단위 검증: _safe_for_bucket는 여전히 '매수' 부분일치를 차단(자유 문구 보호용)
    하지만 데이터 태그는 _ok_data 경로로 _safe_for_bucket를 우회한다 — 본 픽스의 핵심.

    이 테스트는 두 경로의 분리를 직접 확인한다:
      - _safe_for_bucket('내부자 매수') → False (자유 문구 게이트는 보수적으로 유지)
      - _data_tag(d, ...) 결과는 _ok_data 경로 → CTA 필터 미적용
    """
    from one_liner import _data_tag, _safe_for_bucket

    # _safe_for_bucket는 '매수' 부분일치를 여전히 차단 — 자유 문구 풀 보호.
    assert _safe_for_bucket("내부자 매수", "STRONG_BUY") is False
    assert _safe_for_bucket("강력 매수", "SECTOR_LEADER") is False

    # _data_tag는 매수 데이터 태그를 생성한다(인사이더 양순매수).
    d = {"_FH_Available": True, "_FH_InsiderNet": 50000}
    tag = _data_tag(d, "STRONG_BUY")
    assert "내부자 매수" in tag, f"_data_tag missing insider: {tag!r}"


def test_scrub_blocks_real_cta():
    """_scrub_oneliner는 진짜 CTA('매수하세요'/'강력 매수'/'지금 사')를 여전히 차단."""
    assert _scrub_oneliner("지금 사세요") == "AI 코멘트 점검 중"
    assert _scrub_oneliner("강력 매수 추천합니다") == "AI 코멘트 점검 중"
    assert _scrub_oneliner("매수하세요 지금") == "AI 코멘트 점검 중"


def test_scrub_allows_data_buy():
    """'내부자 매수'/'컨센서스 매수'는 데이터 태그 — 최종 스크럽도 통과."""
    safe1 = "업종의 앞줄 · 내부자 매수"
    safe2 = "1년수익률 +25% · 컨센서스 매수(20명)"
    assert _scrub_oneliner(safe1) == safe1
    assert _scrub_oneliner(safe2) == safe2


def test_sector_leader_requires_score_floor():
    """IsLeader=True 만으로 SECTOR_LEADER 단정 금지 — score>=60 동반 필수."""
    low = _positive_for(
        {"IsLeader": True, "Mom12M": 0.10, "_ROE": 0.05},
        score=50,
    )
    assert low != "SECTOR_LEADER", f"low-score leader → {low!r}"

    high = _positive_for(
        {"IsLeader": True, "Mom12M": 0.10, "_ROE": 0.20},
        score=70,
    )
    assert high == "SECTOR_LEADER", f"high-score leader → {high!r}"


def test_earnings_beat_requires_price_confirm():
    """EPS 가속 + EPS 성장 ≥10% 만으로 EARNINGS_BEAT 금지 — mom3>=3 or near_h 필수."""
    no_confirm = _positive_for(
        {"EPSAcceleration": True, "_EPSGrowth": 0.20, "_Mom3M": 0.5, "NearHighPass": False},
        score=70,
    )
    assert no_confirm != "EARNINGS_BEAT", f"no price confirm → {no_confirm!r}"

    with_mom3 = _positive_for(
        {"EPSAcceleration": True, "_EPSGrowth": 0.20, "_Mom3M": 5.0, "NearHighPass": False},
        score=70,
    )
    assert with_mom3 == "EARNINGS_BEAT", f"mom3 confirm → {with_mom3!r}"

    with_nh = _positive_for(
        {"EPSAcceleration": True, "_EPSGrowth": 0.20, "_Mom3M": 0.5, "NearHighPass": True},
        score=70,
    )
    assert with_nh == "EARNINGS_BEAT", f"near_h confirm → {with_nh!r}"


if __name__ == "__main__":
    tests = [
        test_insider_buy_passes_data_tag,
        test_consensus_buy_passes_data_tag,
        test_data_tag_buy_no_longer_filtered_by_cta_substring,
        test_scrub_blocks_real_cta,
        test_scrub_allows_data_buy,
        test_sector_leader_requires_score_floor,
        test_earnings_beat_requires_price_confirm,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  OK   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
    if failed:
        sys.exit(1)
    print(f"\nALL {len(tests)} TESTS PASSED")
