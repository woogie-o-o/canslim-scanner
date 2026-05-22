"""Regression tests: 점수 등급 ↔ 한줄평 톤 일관성.

별점/상태(TotalScore) · 진입 타이밍(EntryStatus) · 한줄평(_bucket)이
서로 다른 이야기를 하던 미스매치 버그의 재발을 막는다.

규칙:
  - score >= 72  → 한줄평은 긍정 톤이어야 한다 (진입 타이밍이 AVOID여도)
  - score <  35  → 한줄평은 부정 톤이어야 한다 (강한 팩터가 있어도)
  - 35 <= score < 48 → 강한 긍정(STRONG_BUY/BREAKOUT) 금지
  - 48 <= score < 72 → 강한 부정(AVOID/FALLING_KNIFE/VALUE_TRAP/BUBBLE) 금지

Uses pytest if available; otherwise falls back to unittest.
No external network calls.
"""

from __future__ import annotations

import os
import sys
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
_WEB_APP = os.path.join(_PROJECT_ROOT, "web_app")
for _p in (_PROJECT_ROOT, _WEB_APP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import one_liner as ol  # noqa: E402


def _polarity(bucket: str) -> str:
    if bucket in ol._POS_BUCKETS:
        return "pos"
    if bucket in ol._NEG_BUCKETS:
        return "neg"
    return "neu"


# (이름, 종목 dict, 기대 톤)
_CASES = [
    # 고득점 + 진입 AVOID — 예전 버그: AVOID 한줄평이 별 4개와 모순
    ("high_score_entry_avoid",
     {"Ticker": "AAA", "TotalScore": 91, "EntryStatus": "AVOID",
      "Drawdown": -0.05, "_Mom3M": 2, "IsLeader": True}, "pos"),
    # 고득점인데 폴백 NEUTRAL 로 빠지던 케이스
    ("high_score_fallback_neutral",
     {"Ticker": "BBB", "TotalScore": 78, "Mom12M": 0.25}, "pos"),
    # ⭐⭐ 경계 (72) — 긍정이어야
    ("leader_boundary_72",
     {"Ticker": "BB2", "TotalScore": 72, "EntryStatus": "AVOID",
      "Drawdown": -0.10, "_Mom3M": -3}, "pos"),
    # 저득점인데 강한 팩터로 긍정 버킷 → 부정으로 정렬
    ("low_score_strong_factors",
     {"Ticker": "CCC", "TotalScore": 22, "EntryStatus": "STRONG",
      "NearHighPass": True, "Mom12M": 0.4}, "neg"),
    # 관망 구간 + 원시 강부정 → 중립으로 완화
    ("solid_score_raw_negative",
     {"Ticker": "DDD", "TotalScore": 55, "EntryStatus": "AVOID",
      "Drawdown": -0.30, "_Mom3M": -10}, "neu"),
    # 진짜 나쁜 종목은 그대로 부정
    ("truly_bad_stays_negative",
     {"Ticker": "EEE", "TotalScore": 18, "EntryStatus": "AVOID",
      "Drawdown": -0.4, "_Mom3M": -12}, "neg"),
    # 진짜 좋은 종목은 그대로 긍정
    ("truly_good_stays_positive",
     {"Ticker": "FFF", "TotalScore": 88, "EntryStatus": "STRONG",
      "NearHighPass": True, "Mom12M": 0.45, "IsLeader": True}, "pos"),
]


def _check(name, d, want):
    bucket = ol._bucket(d)
    got = _polarity(bucket)
    assert got == want, (
        f"{name}: score={d.get('TotalScore')} "
        f"raw={ol._raw_bucket(d)} -> {bucket} ({got}), want {want}"
    )


def test_score_grade_thresholds():
    assert ol._score_grade(72) == "STRONG"
    assert ol._score_grade(71.9) == "SOLID"
    assert ol._score_grade(48) == "SOLID"
    assert ol._score_grade(47) == "WEAK"
    assert ol._score_grade(35) == "WEAK"
    assert ol._score_grade(34) == "AVOID"


def test_weak_score_blocks_strong_positive():
    # 35~47 구간: 강한 긍정 버킷 금지
    d = {"Ticker": "WK1", "TotalScore": 40, "EntryStatus": "STRONG",
         "NearHighPass": True, "Mom12M": 0.5, "IsLeader": True}
    assert ol._bucket(d) not in ("STRONG_BUY", "BREAKOUT", "SECTOR_LEADER")


def test_consistency_cases():
    for name, d, want in _CASES:
        _check(name, d, want)


# ─────────────────────────────────────────────────────────────
# OneLinerData 보조 라인 회귀 테스트
# ─────────────────────────────────────────────────────────────


def test_oneliner_data_numeric_for_strong_signal():
    # E1: 강신호 종목 → 수치(B) 라인 채택. SOLID 구간(score 60) + 깊은 가치 +
    # 큰 ROE/EPS 성장 + 큰 모멘텀으로 _signal_strength 가 게이트(3.0) 초과.
    d = {"Ticker": "TV1", "TotalScore": 60,
         "_PER": 5, "_ROE": 0.30, "_EPSGrowth": 0.50, "Mom12M": 0.45}
    assert ol._signal_strength(d) >= ol._SIGNAL_GATE
    bucket = ol._bucket(d)
    out = ol.get_oneliner_data(d, bucket)
    # _data_tag 가 채워지는 버킷이면 수치 표현이 들어가고, 아니면 플레이버 폴백.
    # 어느 쪽이든 28자 이내.
    assert out and len(out) <= ol._DATA_MAX_LEN, (bucket, out)


def test_oneliner_data_no_buy_cta_in_negative_buckets():
    # E2: BUBBLE 약신호 — 권유 단어("매수"/"진입"/"지금 사") 절대 금지
    d = {"Ticker": "BB1", "TotalScore": 28, "_PER": 99}
    bucket = ol._bucket(d)
    out = ol.get_oneliner_data(d, bucket)
    for forbidden in ("매수", "진입", "풀매수", "들어가", "지금 사"):
        assert forbidden not in out, (bucket, out, forbidden)


def test_oneliner_data_non_empty_for_all_buckets():
    # E3: 모든 18 버킷에서 보조 라인이 비어있지 않다(평범 종목 폴백 보장)
    all_buckets = (
        list(ol._POS_BUCKETS) + list(ol._NEG_BUCKETS) +
        ["NEUTRAL", "OVERSOLD", "STORY_STOCK"]
    )
    for b in all_buckets:
        d = {"Ticker": f"X{b[:3]}"}
        out = ol.get_oneliner_data(d, b)
        assert out, f"bucket {b} returned empty"
        assert len(out) <= ol._DATA_MAX_LEN, (b, out, len(out))


def test_oneliner_data_negative_per_safe():
    # E4: 적자기업(PER<0) — "PER -" 음수 노출 금지
    d = {"Ticker": "NEG", "TotalScore": 40, "_PER": -12, "Mom12M": 0.05}
    bucket = ol._bucket(d)
    out = ol.get_oneliner_data(d, bucket)
    # 음수 PER이 그대로 텍스트화되어선 안 됨
    assert "-12" not in out, (bucket, out)
    assert out  # 폴백으로 어떤 라인이든 나옴


def test_oneliner_data_length_clamp():
    # E5: 모든 후보 라인이 28자 클램프 통과(클램프는 거절 방식)
    samples = [
        {"Ticker": "S1", "TotalScore": 85, "_PER": 12, "_ROE": 0.25,
         "_EPSGrowth": 0.30, "Mom12M": 0.50, "_OperatingMargin": 0.30,
         "NearHighPass": True, "IsLeader": True},
        {"Ticker": "S2", "TotalScore": 20, "Drawdown": -0.45, "_Mom3M": -15},
        {"Ticker": "S3", "TotalScore": 55, "RSI": 82, "Mom12M": 0.4},
    ]
    for s in samples:
        out = ol.get_oneliner_data(s)
        assert len(out) <= ol._DATA_MAX_LEN, (s.get("Ticker"), out, len(out))


def test_oneliner_data_seed_decorrelated_from_oneliner():
    # E6: 같은 종목에서 한줄평과 보조 라인이 동일 인덱스로 동기화되지 않음.
    #     20 종목 표본에서 둘이 같은 인덱스(0)에 동시에 떨어지는 비율 < 80%
    same = 0
    total = 20
    for i in range(total):
        d = {"Ticker": f"SD{i:02d}", "TotalScore": 60, "Mom12M": 0.15}
        bucket = ol._bucket(d)
        flavor_pool = ol._FLAVOR_PHRASES.get(bucket) or ol._FLAVOR_PHRASES["NEUTRAL"]
        main_pool = ol._PHRASES.get(bucket) or ol._PHRASES["NEUTRAL"]
        ol_text = ol.get_one_liner(d)
        data_text = ol.get_oneliner_data(d, bucket)
        # 둘 다 첫 항목이면 동기화된 것 — 패턴이 아니라 분산 확인.
        if (main_pool and ol_text == main_pool[0]
                and data_text == flavor_pool[0]):
            same += 1
    assert same < total, f"seed not decorrelated: {same}/{total}"


def test_high_score_never_negative_oneliner_sweep():
    # 72점 이상이면 어떤 진입상태/팩터 조합에서도 부정 톤이 나오면 안 된다
    for score in (72, 80, 90, 99):
        for entry in ("AVOID", "NEUTRAL", "STRONG", "RED", "GREEN"):
            d = {"Ticker": "SWP", "TotalScore": score, "EntryStatus": entry,
                 "Drawdown": -0.35, "_Mom3M": -12, "_PER": 90, "RSI": 80}
            b = ol._bucket(d)
            assert b not in ol._NEG_BUCKETS, (
                f"score={score} entry={entry} -> {b} (부정 톤 금지)"
            )


def test_low_score_never_positive_oneliner_sweep():
    # 35점 미만이면 어떤 조합에서도 긍정 톤이 나오면 안 된다
    for score in (0, 10, 25, 34):
        for entry in ("STRONG", "GREEN", "NEUTRAL"):
            d = {"Ticker": "SWN", "TotalScore": score, "EntryStatus": entry,
                 "NearHighPass": True, "Mom12M": 0.6, "IsLeader": True,
                 "EPSAcceleration": True}
            b = ol._bucket(d)
            assert b not in ol._POS_BUCKETS, (
                f"score={score} entry={entry} -> {b} (긍정 톤 금지)"
            )


import re as _re

# 일반 풀(_PHRASES)은 종목 데이터를 안 보고 해시로 뽑힌다.
# 따라서 특정 사실(테마/섹터/실적/수치/기술적)을 단정하면 그 종목엔
# 거짓이 된다. 일반 풀은 polarity 톤만 담고, 검증된 단정은 _METRIC_PHRASES만.
_FORBIDDEN_GENERIC = _re.compile(
    r"정치\s*테마|정책\s*테마|양자컴|2차전지|메타버스|필수소비재"
    # 섹터/테마 단정: 해시로 뽑히는 일반 풀이 특정 업종을 단정하면
    # 그 업종이 아닌 종목(예: 광학주 LPTH)에 거짓이 된다.
    r"|바이오\s*신약|바이오\s*테마|바이오주|신약\s*테마"
    r"|임상\s*(실패|성공|결과|중단|\d)|항암제|백신주|치료제"
    r"|파이프라인|진단키트|제약\s*(테마|바이오|주)"
    r"|유틸리티\s*종목|규제\s*산업|경기방어주"
    r"|코스피\s*\d{3,}|코스닥\s*\d{3,}|베타\s*0|샤프비율|VIX"
    r"|RSI\s*\d|PER\s*\d|PBR\s*\d|ROE\s*\d+\s*%|배당\s*\d|부채비율\s*\d"
    r"|거래량\s*\d+\s*배|골든크로스|데드크로스|볼린저밴드|일목균형표"
    r"|어닝\s*서프라이즈|추정치\s*상[향회]|적자\s*전환|자본\s*잠식"
    r"|상장폐지|거래정지|유상증자"
    # 회사 무관심/무명 단정: 해시 풀이 "아무도 안 보는 회사"라고 단정하면
    # 삼성물산 같은 관심 많은 대형주에 거짓이 된다. 단, 독자 FOMO("너만
    # 소외", "너만 그걸 모르는")와 바닥 불확실성("바닥은 아무도 모르는")은
    # 정당하므로 매칭되지 않게 한다.
    r"|아무도\s*안\s*(쳐다|봐|보는|볼|봄)|아무도\s*(다\s*)?관심\s*없"
    r"|관심\s*(밖\s*종목|밖\s*회사|0|제로)|시장이?\s*관심\s*없"
    r"|대중[이은가도]\s*(아직\s*)?모르|증권사\s*리포트도?\s*안"
    r"|검색량\s*바닥|주목\s*(을\s*)?못\s*받|카페나?\s*블로그"
    r"|ETF에?도?\s*안\s*들어|남들\s*안\s*볼|남들\s*(다\s*)?떠난\s*자리"
    r"|남들\s*모를\s*때"
)


def test_generic_pools_have_no_unverified_assertions():
    offenders = []
    for bucket, lst in ol._PHRASES.items():
        for p in lst:
            if _FORBIDDEN_GENERIC.search(p):
                offenders.append((bucket, p))
    assert not offenders, (
        "일반 풀에 검증 불가 단정이 남아있음: "
        + "; ".join(f"{b}:{p}" for b, p in offenders[:10])
    )


def test_generic_pools_variety_and_no_dupes():
    # 사용자 요구: "다양하게 나오게" — 버킷당 충분한 풀 + 중복 없음
    for bucket, lst in ol._PHRASES.items():
        assert len(lst) >= 60, f"{bucket}: 풀이 {len(lst)}개뿐 (>=60 필요)"
        assert len(lst) == len(set(lst)), f"{bucket}: 중복 문구 존재"
        for p in lst:
            assert not p.rstrip().endswith("."), f"{bucket}: 마침표로 끝남 -> {p}"


def test_no_period_in_metric_pools():
    for key, lst in ol._METRIC_PHRASES.items():
        for p in lst:
            assert not p.rstrip().endswith("."), f"{key}: 마침표로 끝남 -> {p}"


# 사용자 요구: "문구를 기술적인 건 쓰지말고 주갤형식으로만 평가하는걸로 해."
# 일반 풀과 데이터 게이트 풀(_METRIC_PHRASES) 둘 다 순수 주갤 구어체만 남고
# 애널리스트/차트 전문용어는 한 단어도 남으면 안 된다.
# 슬랭(1등/반토막/한 방/존버/줍줍 등)은 허용 — 단어 기반 매칭이라 오검출 없음.
_FORBIDDEN_TECH = _re.compile(
    r"RSI|MACD|볼린저|일목|스토캐스틱|이격도|ADX|엘리엇|피보나치"
    r"|이평선|이동평균|골든크로스|데드크로스|다이버전스"
    r"|PER|PBR|PSR|PEG|ROE|ROA|ROIC|WACC|EBITDA"
    r"|밸류에이션|밸류|멀티플|베타|변동성|드로다운|MDD|샤프|VIX|표준편차"
    r"|펀더멘탈|펀더|어닝|컨센서스|가이던스|추정치|EPS|영업이익률|마진율"
    r"|부채비율|현금흐름|FCF|모멘텀|과매수|과매도|변곡점|신고가|신저가"
    r"|배당수익률|시가총액|시총|디스카운트|괴리율|괴리|리레이팅|안전마진"
    r"|청산가치|양봉|음봉|거래량|지지선|업사이드|목표가|숏스퀴즈|숏커버"
    r"|서프라이즈|트리거|시그널"
    r"|\d+\s*%|\d+\s*프로|한\s*자릿수|두\s*자릿수"
)


def test_no_technical_jargon_anywhere():
    offenders = []
    for bucket, lst in ol._PHRASES.items():
        for p in lst:
            m = _FORBIDDEN_TECH.search(p)
            if m:
                offenders.append(("GEN", bucket, m.group(0), p))
    for key, lst in ol._METRIC_PHRASES.items():
        k = "|".join(key) if isinstance(key, tuple) else key
        for p in lst:
            m = _FORBIDDEN_TECH.search(p)
            if m:
                offenders.append(("MET", k, m.group(0), p))
    assert not offenders, (
        "기술 용어가 남아있음 (순수 주갤만 허용): "
        + "; ".join(f"{pl}/{b}[{t}]:{p}" for pl, b, t, p in offenders[:15])
    )


# NEUTRAL은 fall-through 버킷이다. 점수는 괜찮지만 특정 패턴에 안 걸린
# 종목(예: 강한 추세 + 평범한 펀더)이 여기로 떨어진다. 카드가 "강한 상승
# / 추세 5축 / 외국인 순매수"를 보여주는 종목에 "관심 없는 회사 / 횡보 /
# 사라져도 모를"이라고 단정하면 한줄평이 카드와 정면충돌한다.
# → NEUTRAL 풀은 방향 단정 없는 "확신 보류 / 관망" 톤만 담아야 한다.
_NEUTRAL_FORBIDDEN = _re.compile(
    r"관심\s*(없|밖|종목에서\s*빼)|아무도\s*모를|시장에서\s*사라"
    r"|인덱스\s*ETF|돈\s*벌었다는\s*사람|노잼|횡보|박스권|존재감\s*없"
    r"|식물인간|동면|냉동실|변기|청춘|머리\s*다\s*셀|거미줄|적막"
    r"|죽어\s*있|죽어있|재미없|지루|뉴스거리가\s*안|리포트도\s*안"
    r"|토론방|예금이\s*더\s*나|점심\s*뭐|낮잠|멍\s*때리|백수"
    r"|동전\s*던지기|시간\S*낭비|인생\S*(줄|정체|낭비)"
)


def test_neutral_pool_is_tone_safe():
    offenders = []
    for p in ol._PHRASES["NEUTRAL"]:
        m = _NEUTRAL_FORBIDDEN.search(p)
        if m:
            offenders.append((m.group(0), p))
    assert not offenders, (
        "NEUTRAL 풀에 카드와 충돌하는 단정 문구가 남아있음 "
        "(NEUTRAL은 방향 단정 없는 관망 톤만): "
        + "; ".join(f"[{t}] {p}" for t, p in offenders[:10])
    )


# 버킷 '성격' 정밀 분류 회귀 — _raw_bucket이 종목 성격을 엉뚱한
# 버킷으로 보내던 버그(성장주→VALUE_TRAP, 우량주→NEUTRAL,
# 강진입 가치주→MOMENTUM_LEADER, 정당한 프리미엄 성장주→BUBBLE) 재발 방지.
# (이름, 종목 dict, 기대 raw 버킷)
_CHARACTER_CASES = [
    # 저PER·저ROE라도 고성장 회복주는 밸류트랩이 아니다
    ("growth_recovery_not_trap",
     {"Ticker": "GRW", "TotalScore": 55, "_PER": 12,
      "_ROE": 0.06, "_EPSGrowth": 0.45, "Mom12M": 0.10}, "NOT_VALUE_TRAP"),
    # 고ROE인데 일시 역성장한 우량주도 밸류트랩이 아니다
    ("quality_dip_not_trap",
     {"Ticker": "QDP", "TotalScore": 58, "_PER": 13,
      "_ROE": 0.24, "_EPSGrowth": -0.03, "Mom12M": 0.0}, "NOT_VALUE_TRAP"),
    # 싸고 우량하고 성장 평탄 → NEUTRAL이 아니라 TRUE_VALUE
    ("cheap_quality_is_true_value",
     {"Ticker": "CQV", "TotalScore": 58, "_PER": 14,
      "_ROE": 0.20, "_EPSGrowth": 0.04, "Mom12M": 0.18}, "TRUE_VALUE"),
    # 성장이 멀티플을 정당화하는 프리미엄주가 잠깐 눌림 → BUBBLE 아님
    ("premium_grower_not_bubble",
     {"Ticker": "PGR", "TotalScore": 70, "_PER": 45,
      "_EPSGrowth": 0.35, "Mom12M": 0.40, "_Mom3M": -2}, "EXPENSIVE_JUSTIFIED"),
    # 강진입 + 고점수 가치주는 MOMENTUM_LEADER가 아니라 성격대로 TRUE_VALUE
    ("strong_entry_value_keeps_character",
     {"Ticker": "SEV", "TotalScore": 65, "EntryStatus": "STRONG",
      "_PER": 10, "_ROE": 0.22, "_EPSGrowth": 0.12, "Mom12M": 0.05}, "TRUE_VALUE"),
    # 저평가·견조 ROE면 NEUTRAL로 흘리지 말고 방어주 성격 부여
    ("solid_value_not_bland_neutral",
     {"Ticker": "SVN", "TotalScore": 52, "_PER": 22,
      "_ROE": 0.14, "_EPSGrowth": 0.05, "Mom12M": 0.20}, "NOT_NEUTRAL"),
    # 삼성전기형: RSI 77이지만 추세 살아있고 모멘텀 꺾임 없음 → 과열 단정 금지,
    # 모멘텀리더로 흡수돼야 (composite gate: rsi≥70 + mom12≥20 + mom3>-3)
    ("rsi_hot_but_trend_alive_is_momentum_leader",
     {"Ticker": "SEC", "TotalScore": 75, "RSI": 77,
      "Mom12M": 0.45, "_Mom3M": 5, "_PER": 22,
      "_ROE": 0.18, "_EPSGrowth": 0.20}, "MOMENTUM_LEADER"),
]


def test_character_buckets_precise():
    for name, d, want in _CHARACTER_CASES:
        raw = ol._raw_bucket(d)
        if want == "NOT_VALUE_TRAP":
            assert raw != "VALUE_TRAP", f"{name}: raw={raw} (밸류트랩 오분류)"
        elif want == "NOT_NEUTRAL":
            assert raw != "NEUTRAL", f"{name}: raw={raw} (성격 없이 NEUTRAL 추락)"
        else:
            assert raw == want, f"{name}: raw={raw}, want {want}"


# ── 반등 감지 테스트 ──────────────────────────────────────────────


def test_rebound_tag_conditions():
    """_metric_tags()의 rebound 태그 발행 조건 검증."""
    # 1) DayChg >= 3% AND RSI <= 35 -> rebound 태그 발행
    tags = ol._metric_tags({"DayChg": 0.05, "RSI": 28})
    assert "rebound" in tags, "DayChg=5%, RSI=28 -> rebound 태그 필요"

    # 2) DayChg >= 3% AND RSI > 35 -> rebound 미발행 (RSI 게이트)
    tags = ol._metric_tags({"DayChg": 0.05, "RSI": 40})
    assert "rebound" not in tags, "RSI=40이면 rebound 태그 불가"

    # 3) DayChg < 3% AND RSI <= 35, 모멘텀 반전 없음 -> rebound 미발행
    tags = ol._metric_tags({"DayChg": 0.02, "RSI": 25})
    assert "rebound" not in tags, "DayChg=2%이면 rebound 태그 불가"

    # 4) 추세 반전: Mom1M 양전 + Mom3M 음전 + RSI <= 35 -> rebound 발행
    tags = ol._metric_tags({"DayChg": 0.01, "RSI": 30, "_Mom1M": 0.05, "_Mom3M": -10})
    assert "rebound" in tags, "Mom1M 양전 + Mom3M 음전 -> 추세 반전 rebound"

    # 5) 깊은 낙폭(-30%) + 약한 반등(+2%) + RSI <= 35 -> rebound 발행
    tags = ol._metric_tags({"DayChg": 0.02, "RSI": 28, "Drawdown": -0.35})
    assert "rebound" in tags, "DD=-35%, DayChg=2% -> 깊은 낙폭 반등 rebound"


def test_signal_strength_daychg_dimension():
    """_signal_strength()에 DayChg 차원이 반영되는지 검증."""
    base = {"RSI": 25, "Drawdown": -0.20, "_ROE": 0.05}

    # DayChg 없는 baseline
    s_base = ol._signal_strength(base)

    # DayChg=5% 추가 -> signal_strength가 유의미하게 상승
    s_with = ol._signal_strength({**base, "DayChg": 0.05})
    assert s_with > s_base + 0.5, (
        f"DayChg=5% 추가 시 signal_strength 상승 부족: {s_base} -> {s_with}"
    )

    # DayChg 축 상한 2.0 준수
    s_extreme = ol._signal_strength({**base, "DayChg": 0.20})
    s_high = ol._signal_strength({**base, "DayChg": 0.10})
    assert s_extreme == s_high, (
        f"DayChg 상한 2.0 초과: 10%={s_high}, 20%={s_extreme}"
    )


def test_rebound_phrases_exist_and_clean():
    """반등 문구가 존재하고 기존 규칙을 준수하는지 검증."""
    import re as _re
    forbidden = _re.compile(
        r"RSI|MACD|볼린저|일목|스토캐스틱|이격도|ADX|엘리엇|피보나치"
        r"|이평선|이동평균|골든크로스|데드크로스|다이버전스"
        r"|PER|PBR|PSR|PEG|ROE|ROA|ROIC|WACC|EBITDA"
    )
    for bucket in ("AVOID", "FALLING_KNIFE", "OVERSOLD"):
        key = (bucket, "rebound")
        pool = ol._METRIC_PHRASES.get(key, [])
        assert len(pool) >= 6, f"{key}: 문구 {len(pool)}개 (>=6 필요)"
        for p in pool:
            assert not p.rstrip().endswith("."), f"{key}: 마침표 -> {p}"
            m = forbidden.search(p)
            assert not m, f"{key}: 기술용어 [{m.group(0)}] -> {p}"


def test_bucket_avoid_invariant_preserved():
    """grade=AVOID일 때 OVERSOLD raw 버킷은 여전히 부정으로 치환되어야 한다."""
    d = {"Ticker": "INV", "TotalScore": 25, "RSI": 28, "_ROE": 0.10,
         "DayChg": 0.08, "Drawdown": -0.15, "_Mom3M": -5}
    bucket = ol._bucket(d)
    assert bucket in ol._NEG_BUCKETS, (
        f"AVOID 불변성 위반: TotalScore=25, DayChg=8% -> {bucket} "
        f"(부정 버킷이어야 함)"
    )


def test_rebound_metric_phrase_reachable():
    """반등 조건을 갖춘 종목이 signal_strength 게이트를 통과할 수 있는지 검증."""
    d = {"Ticker": "RCH", "RSI": 18, "DayChg": 0.05,
         "Drawdown": -0.30, "_ROE": 0.05, "_Mom3M": -8,
         "Mom12M": -0.20}
    ss = ol._signal_strength(d)
    assert ss >= ol._SIGNAL_GATE, (
        f"반등 조건 종목이 게이트 미통과: signal_strength={ss:.2f}, "
        f"gate={ol._SIGNAL_GATE}"
    )
    tags = ol._metric_tags(d)
    assert "rebound" in tags, f"rebound 태그 미발행: tags={tags}"


def test_dividend_phrase_not_in_general_pool():
    """배당 멘트는 _METRIC_PHRASES에만 있고 일반 _PHRASES 풀에서는 빠져야 한다.

    배당 안 주는 종목한테 '받는 배당 감안하면' 같은 잘못된 멘트가
    나오던 회귀 방지. 일반 풀에서 '배당' 단어를 찾으면 안 됨.
    """
    for bucket, phrases in ol._PHRASES.items():
        for ph in phrases:
            assert "배당" not in ph, (
                f"_PHRASES['{bucket}']에 배당 멘트가 잔존: {ph!r}"
            )


def test_zero_dividend_stock_never_gets_dividend_tag():
    """배당 안 주는 종목한테는 high_dividend 태그가 안 붙어야 한다."""
    # 배당 정보 없음
    d_none = {"Ticker": "NOD", "_ROE": 0.20, "_OperatingMargin": 0.30,
              "_EPSGrowth": 40}
    tags = ol._metric_tags(d_none)
    assert "high_dividend" not in tags, (
        f"_DivYield 없는 종목에 high_dividend 태그가 붙음: tags={tags}"
    )

    # 명시적 0
    d_zero = {"Ticker": "ZRO", "_DivYield": 0.0, "_ROE": 0.20,
              "_OperatingMargin": 0.30, "_EPSGrowth": 40}
    tags = ol._metric_tags(d_zero)
    assert "high_dividend" not in tags, (
        f"_DivYield=0 종목에 high_dividend 태그가 붙음: tags={tags}"
    )

    # 미미한 배당 (1% 이하) — 매력 멘트 자격 없음
    d_tiny = {"Ticker": "TNY", "_DivYield": 0.005, "_ROE": 0.20,
              "_OperatingMargin": 0.30, "_EPSGrowth": 40}
    tags = ol._metric_tags(d_tiny)
    assert "high_dividend" not in tags, (
        f"_DivYield=0.5% 종목에 high_dividend 태그가 붙음: tags={tags}"
    )


def test_high_dividend_stock_can_get_dividend_phrase():
    """배당 충분히 주는 TRUE_VALUE 종목은 high_dividend 태그가 붙고
    매칭되는 _METRIC_PHRASES 엔트리가 존재해야 한다."""
    d = {"Ticker": "DIV", "_DivYield": 0.045,  # 4.5%
         "_PER": 7, "_ROE": 0.15, "_OperatingMargin": 0.20,
         "_EPSGrowth": 15}
    tags = ol._metric_tags(d)
    assert "high_dividend" in tags, (
        f"_DivYield=4.5% 종목에 high_dividend 태그 미발행: tags={tags}"
    )
    key = ("TRUE_VALUE", "high_dividend")
    assert key in ol._METRIC_PHRASES, (
        f"_METRIC_PHRASES에 {key} 엔트리가 없음"
    )
    phrases = ol._METRIC_PHRASES[key]
    assert len(phrases) >= 2, (
        f"{key} 풀이 너무 빈약함: {len(phrases)}개"
    )


class _UT(unittest.TestCase):
    def test_all(self):
        test_score_grade_thresholds()
        test_weak_score_blocks_strong_positive()
        test_consistency_cases()
        test_high_score_never_negative_oneliner_sweep()
        test_low_score_never_positive_oneliner_sweep()
        test_generic_pools_have_no_unverified_assertions()
        test_generic_pools_variety_and_no_dupes()
        test_no_period_in_metric_pools()
        test_no_technical_jargon_anywhere()
        test_neutral_pool_is_tone_safe()
        test_character_buckets_precise()
        test_rebound_tag_conditions()
        test_signal_strength_daychg_dimension()
        test_rebound_phrases_exist_and_clean()
        test_bucket_avoid_invariant_preserved()
        test_rebound_metric_phrase_reachable()
        test_dividend_phrase_not_in_general_pool()
        test_zero_dividend_stock_never_gets_dividend_tag()
        test_high_dividend_stock_can_get_dividend_phrase()


if __name__ == "__main__":
    unittest.main()
