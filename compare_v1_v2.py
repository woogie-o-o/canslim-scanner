# -*- coding: utf-8 -*-
"""v1 vs v2 FinValue 비교 스크립트.

한국 주요 종목 ~50개에 대해 기존(v1, 6지표)과 신규(v2, 17지표) 점수를 비교.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import naver_quarter as nq
import fundamental_value_grade as fvg

# 한국 주요 종목 (대형+중형+소형 혼합)
TICKERS = {
    "005930": "삼성전자", "000660": "SK하이닉스", "035420": "NAVER",
    "035720": "카카오", "051910": "LG화학", "006400": "삼성SDI",
    "005380": "현대차", "000270": "기아", "055550": "신한지주",
    "105560": "KB금융", "316140": "우리금융", "086790": "하나금융",
    "003670": "포스코퓨처엠", "066570": "LG전자", "012330": "현대모비스",
    "028260": "삼성물산", "003550": "LG", "034730": "SK",
    "032830": "삼성생명", "009150": "삼성전기", "018260": "삼성에스디에스",
    "033780": "KT&G", "030200": "KT", "017670": "SK텔레콤",
    "011200": "HMM", "010130": "고려아연", "034020": "두산에너빌리티",
    "003490": "대한항공", "180640": "한진칼", "047050": "포스코인터내셔널",
    "010950": "S-Oil", "036570": "엔씨소프트", "251270": "넷마블",
    "263750": "펄어비스", "352820": "하이브", "112040": "위메이드",
    "259960": "크래프톤", "041510": "에스엠", "122870": "와이지엔터",
    "000810": "삼성화재", "002790": "아모레G", "090430": "아모레퍼시픽",
    "004020": "현대제철", "005490": "POSCO홀딩스", "096770": "SK이노베이션",
    "207940": "삼성바이오로직스", "068270": "셀트리온", "326030": "SK바이오팜",
    "128940": "한미약품", "006800": "미래에셋증권",
}

# v1 가중치 (Phase 1: 작동했던 6개만)
V1_WEIGHTS = {
    "rev_qoq": 0.05, "op_qoq": 0.05, "ni_qoq": 0.05,
    "roe": 0.25, "pbr": 0.25, "psr": 0.25,
}
# 나머지는 없으니 자동 재정규화로 이 6개가 100%

V1_INVERTED = frozenset({"pbr", "psr"})

def main():
    print("=" * 70)
    print("  FinValue v1 vs v2 비교")
    print("=" * 70)
    print(f"\n종목 수: {len(TICKERS)}개")
    print("데이터 수집 중...\n")

    records = []
    failed = []

    for i, (code, name) in enumerate(TICKERS.items()):
        try:
            q = nq.get_quarter_metrics(code)
            if not q or not q.get("available"):
                failed.append(f"  {code} {name}: 데이터 없음")
                continue

            rec = {
                "ticker": code,
                "name": name,
                "sector": "ALL",
                # QoQ
                "rev_qoq": q.get("rev_qoq"),
                "op_qoq": q.get("op_qoq"),
                "ni_qoq": q.get("ni_qoq"),
                "ocf_qoq": None,
                # YoY
                "rev_yoy": q.get("rev_yoy"),
                "op_yoy": q.get("op_yoy"),
                "ni_yoy": q.get("ni_yoy"),
                "ocf_yoy": None,
                # Quality
                "gpa": None,  # DART 없이 비교
                "accrual": None,
                "streak": q.get("streak", 0),
                # Valuation
                "roe": q.get("roe"),
                "pegr": None,  # TTM 없이
                "pbr": q.get("pbr"),
                "psr": None,   # TTM 없이
                "fcf_yield": None,
                "ev_ebitda": None,
            }
            records.append(rec)
            sys.stdout.write(f"\r  [{i+1}/{len(TICKERS)}] {name} OK")
            sys.stdout.flush()
        except Exception as e:
            failed.append(f"  {code} {name}: {e}")

    print(f"\n\n수집 완료: {len(records)}종목 성공, {len(failed)}종목 실패")
    if failed:
        print("실패 목록:")
        for f in failed[:10]:
            print(f)

    if len(records) < 5:
        print("비교할 종목이 너무 적습니다.")
        return

    # v2 점수 (현재 DEFAULT_WEIGHTS)
    g_v2 = fvg.compute_grades(records, basis="universe")

    # v1 점수 (기존 6개 지표만)
    # compute_grades는 가중치에 있는 키만 사용하므로 v1 weights 전달
    g_v1 = fvg.compute_grades(records, basis="universe", weights=V1_WEIGHTS)

    # 이름 매핑
    name_map = {r["ticker"]: r["name"] for r in records}

    # 순위 비교
    rank_v1 = sorted(g_v1.items(), key=lambda kv: kv[1]["grade"], reverse=True)
    rank_v2 = sorted(g_v2.items(), key=lambda kv: kv[1]["grade"], reverse=True)

    v1_rank = {t: i+1 for i, (t, _) in enumerate(rank_v1)}
    v2_rank = {t: i+1 for i, (t, _) in enumerate(rank_v2)}

    print("\n" + "=" * 70)
    print("  v2 기준 상위 20종목 (v1 순위 비교)")
    print("=" * 70)
    print(f"{'순위':>4}  {'종목':<16} {'v2점수':>7} {'v1점수':>7} {'v1순위':>6} {'변동':>6}")
    print("-" * 60)
    for i, (t, info) in enumerate(rank_v2[:20]):
        v2s = info["grade"]
        v1s = g_v1[t]["grade"]
        r1 = v1_rank[t]
        diff = r1 - (i + 1)
        arrow = f"+{diff}" if diff > 0 else str(diff) if diff < 0 else "="
        print(f"  {i+1:>2}   {name_map[t]:<14} {v2s:>7.1f} {v1s:>7.1f} {r1:>5}위 {arrow:>5}")

    print("\n" + "=" * 70)
    print("  v2 기준 하위 10종목")
    print("=" * 70)
    print(f"{'순위':>4}  {'종목':<16} {'v2점수':>7} {'v1점수':>7} {'v1순위':>6} {'변동':>6}")
    print("-" * 60)
    for i, (t, info) in enumerate(rank_v2[-10:]):
        rank = len(rank_v2) - 10 + i + 1
        v2s = info["grade"]
        v1s = g_v1[t]["grade"]
        r1 = v1_rank[t]
        diff = r1 - rank
        arrow = f"+{diff}" if diff > 0 else str(diff) if diff < 0 else "="
        print(f"  {rank:>2}   {name_map[t]:<14} {v2s:>7.1f} {v1s:>7.1f} {r1:>5}위 {arrow:>5}")

    # 순위 변동 큰 종목
    changes = []
    for t in v2_rank:
        changes.append((t, v1_rank[t] - v2_rank[t]))
    changes.sort(key=lambda x: abs(x[1]), reverse=True)

    print("\n" + "=" * 70)
    print("  순위 변동 TOP 10 (v1→v2 차이 큰 종목)")
    print("=" * 70)
    print(f"{'종목':<16} {'v1순위':>6} {'v2순위':>6} {'변동':>6}  {'해석'}")
    print("-" * 65)
    for t, diff in changes[:10]:
        v1r = v1_rank[t]
        v2r = v2_rank[t]
        arrow = f"+{diff}" if diff > 0 else str(diff)
        reason = "v2에서 상승 (YoY/퀄리티 반영)" if diff > 0 else "v2에서 하락 (퀄리티 감점?)"
        print(f"  {name_map[t]:<14} {v1r:>5}위 {v2r:>5}위 {arrow:>5}  {reason}")

    # v2에서 사용된 지표 통계
    print("\n" + "=" * 70)
    print("  지표별 데이터 가용률")
    print("=" * 70)
    all_metrics = list(fvg.DEFAULT_WEIGHTS.keys())
    for m in all_metrics:
        avail = sum(1 for r in records if r.get(m) is not None)
        pct = avail / len(records) * 100
        bar = "#" * int(pct / 5)
        print(f"  {m:<12} {avail:>3}/{len(records)}  ({pct:>5.1f}%) {bar}")

if __name__ == "__main__":
    main()
