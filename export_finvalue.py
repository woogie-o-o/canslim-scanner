# -*- coding: utf-8 -*-
"""시총 상위 50종목 FinValue 엑셀 출력."""
import sys, os, io
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime
import xlsxwriter
import naver_quarter as nq
import fundamental_value_grade as fvg

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

def main():
    print("데이터 수집 중...")
    records = []
    raw = {}
    for i, (code, name) in enumerate(TICKERS.items()):
        try:
            q = nq.get_quarter_metrics(code)
            if not q or not q.get("available"):
                continue
            raw[code] = q
            records.append({
                "ticker": code, "name": name, "sector": "ALL",
                "rev_qoq": q.get("rev_qoq"), "op_qoq": q.get("op_qoq"),
                "ni_qoq": q.get("ni_qoq"), "ocf_qoq": None,
                "rev_yoy": q.get("rev_yoy"), "op_yoy": q.get("op_yoy"),
                "ni_yoy": q.get("ni_yoy"), "ocf_yoy": None,
                "gpa": None, "accrual": None, "streak": q.get("streak", 0),
                "roe": q.get("roe"), "pegr": None, "pbr": q.get("pbr"),
                "psr": None, "fcf_yield": None, "ev_ebitda": None,
            })
            sys.stdout.write(f"\r  [{i+1}/{len(TICKERS)}] {name}")
            sys.stdout.flush()
        except Exception:
            pass

    print(f"\n수집 완료: {len(records)}종목")

    g = fvg.compute_grades(records, basis="universe")
    name_map = {r["ticker"]: r["name"] for r in records}
    ranked = sorted(g.items(), key=lambda kv: kv[1]["grade"], reverse=True)

    # ── 엑셀 생성 ──
    fname = f"FinValue_시총50_{datetime.now().strftime('%Y%m%d')}.xlsx"
    wb = xlsxwriter.Workbook(fname)

    # 포맷
    hdr_fmt = wb.add_format({
        "bold": True, "bg_color": "#1B64DA", "font_color": "#FFFFFF",
        "border": 1, "align": "center", "valign": "vcenter", "font_size": 11,
    })
    tier3_fmt = wb.add_format({"bg_color": "#E8F5E9", "border": 1, "num_format": "0.0", "align": "center"})
    tier2_fmt = wb.add_format({"bg_color": "#FFF8E1", "border": 1, "num_format": "0.0", "align": "center"})
    tier1_fmt = wb.add_format({"bg_color": "#FFFFFF", "border": 1, "num_format": "0.0", "align": "center"})
    tier0_fmt = wb.add_format({"bg_color": "#FFEBEE", "border": 1, "num_format": "0.0", "align": "center"})
    txt_fmt = wb.add_format({"border": 1, "align": "center", "valign": "vcenter"})
    name_fmt = wb.add_format({"border": 1, "align": "left", "bold": True})
    pct_fmt = wb.add_format({"border": 1, "align": "center", "num_format": "+0%;-0%;0%"})
    num_fmt = wb.add_format({"border": 1, "align": "center", "num_format": "0.00"})
    int_fmt = wb.add_format({"border": 1, "align": "center", "num_format": "0"})
    na_fmt = wb.add_format({"border": 1, "align": "center", "font_color": "#BDBDBD"})

    # ── Sheet 1: 순위 ──
    ws = wb.add_worksheet("FinValue 순위")
    ws.set_tab_color("#1B64DA")
    ws.freeze_panes(1, 0)
    ws.set_default_row(22)

    headers = [
        "순위", "종목코드", "종목명", "FinValue\n(재무점수)", "등급",
        "ROE", "PBR",
        "매출\nQoQ", "영업이익\nQoQ", "순이익\nQoQ",
        "매출\nYoY", "영업이익\nYoY", "순이익\nYoY",
        "연속성장\n(분기)",
    ]
    widths = [5, 10, 14, 12, 8, 8, 8, 9, 9, 9, 9, 9, 9, 10]
    for c, (h, w) in enumerate(zip(headers, widths)):
        ws.set_column(c, c, w)
        ws.write(0, c, h, hdr_fmt)
    ws.set_row(0, 36)

    for row_i, (t, info) in enumerate(ranked):
        r = row_i + 1
        score = info["grade"]
        q = raw.get(t, {})

        # 등급
        if score >= 70:
            tier, sfmt = "★★★", tier3_fmt
        elif score >= 55:
            tier, sfmt = "★★", tier2_fmt
        elif score >= 40:
            tier, sfmt = "★", tier1_fmt
        else:
            tier, sfmt = "-", tier0_fmt

        ws.write_number(r, 0, row_i + 1, txt_fmt)
        ws.write_string(r, 1, t, txt_fmt)
        ws.write_string(r, 2, name_map[t], name_fmt)
        ws.write_number(r, 3, score, sfmt)
        ws.write_string(r, 4, tier, txt_fmt)

        # ROE
        roe = q.get("roe")
        if roe is not None:
            ws.write_number(r, 5, roe / 100, pct_fmt)
        else:
            ws.write_string(r, 5, "-", na_fmt)

        # PBR
        pbr = q.get("pbr")
        if pbr is not None:
            ws.write_number(r, 6, pbr, num_fmt)
        else:
            ws.write_string(r, 6, "-", na_fmt)

        # QoQ 3종
        for ci, key in enumerate(["rev_qoq", "op_qoq", "ni_qoq"]):
            v = q.get(key)
            if v is not None:
                ws.write_number(r, 7 + ci, v, pct_fmt)
            else:
                ws.write_string(r, 7 + ci, "-", na_fmt)

        # YoY 3종
        for ci, key in enumerate(["rev_yoy", "op_yoy", "ni_yoy"]):
            v = q.get(key)
            if v is not None:
                ws.write_number(r, 10 + ci, v, pct_fmt)
            else:
                ws.write_string(r, 10 + ci, "-", na_fmt)

        # 연속성장
        streak = q.get("streak", 0)
        if streak > 0:
            ws.write_number(r, 13, streak, int_fmt)
        else:
            ws.write_string(r, 13, "-", na_fmt)

    # ── Sheet 2: 점수 기준 설명 ──
    ws2 = wb.add_worksheet("점수 기준 설명")
    ws2.set_tab_color("#7C3AED")
    ws2.set_column(0, 0, 20)
    ws2.set_column(1, 1, 14)
    ws2.set_column(2, 2, 50)
    ws2.set_column(3, 3, 12)

    title_fmt = wb.add_format({"bold": True, "font_size": 14, "bottom": 2})
    sub_fmt = wb.add_format({"bold": True, "bg_color": "#F3E8FF", "border": 1})
    cell_fmt = wb.add_format({"border": 1, "text_wrap": True, "valign": "vcenter"})
    bold_fmt = wb.add_format({"bold": True, "border": 1, "text_wrap": True, "valign": "vcenter"})

    ws2.write(0, 0, "FinValue (재무가치 점수) 기준", title_fmt)
    ws2.write(1, 0, "방법론: 전체 상장종목 횡단면 백분위 (0~100점). 높을수록 재무적으로 우수+저평가.", cell_fmt)
    ws2.merge_range(1, 0, 1, 3, "방법론: 전체 상장종목 횡단면 백분위 (0~100점). 높을수록 재무적으로 우수+저평가.", cell_fmt)

    r = 3
    sections = [
        ("성장 QoQ (15%)", [
            ("매출 성장률 QoQ", "3.75%", "직전분기 대비 매출 증가율"),
            ("영업이익 성장률 QoQ", "3.75%", "직전분기 대비 영업이익 증가율"),
            ("순이익 성장률 QoQ", "3.75%", "직전분기 대비 순이익 증가율"),
            ("영업CF QoQ", "3.75%", "직전분기 대비 영업현금흐름 (DART 연동 시)"),
        ]),
        ("성장 YoY (15%)", [
            ("매출 성장률 YoY", "3.75%", "전년 동기 대비 매출 증가율"),
            ("영업이익 성장률 YoY", "3.75%", "전년 동기 대비 영업이익 증가율"),
            ("순이익 성장률 YoY", "3.75%", "전년 동기 대비 순이익 증가율"),
            ("영업CF YoY", "3.75%", "전년 동기 대비 영업현금흐름 (DART 연동 시)"),
        ]),
        ("퀄리티 (10%)", [
            ("GPA", "5%", "매출총이익/총자산. 순수 사업 수익성 (DART 연동 시)"),
            ("Accrual Ratio", "3%", "(순이익-영업CF)/총자산. 낮을수록 이익의 질 우수 (DART 연동 시)"),
            ("연속 성장 분기 수", "2%", "순이익이 연속 증가한 분기 수. 구조적 성장 판별"),
        ]),
        ("밸류에이션 (60%)", [
            ("ROE", "12%", "자기자본이익률. 높을수록 고득점"),
            ("PEGR", "13%", "PER/이익성장률. 낮을수록 성장 대비 저평가 (TTM 필요)"),
            ("PBR", "12%", "주가순자산비율. 낮을수록 고득점"),
            ("PSR", "12%", "주가매출비율. 낮을수록 고득점 (TTM 필요)"),
            ("FCF Yield", "5%", "(영업CF-설비투자)/시총. 높을수록 현금창출력 우수 (DART 연동 시)"),
            ("EV/EBITDA", "6%", "기업가치/EBITDA. 낮을수록 저평가 (TTM 필요)"),
        ]),
    ]

    for section_name, items in sections:
        ws2.merge_range(r, 0, r, 3, section_name, sub_fmt)
        r += 1
        for name, weight, desc in items:
            ws2.write(r, 0, name, bold_fmt)
            ws2.write(r, 1, weight, cell_fmt)
            ws2.write(r, 2, desc, cell_fmt)
            ws2.write(r, 3, "", cell_fmt)
            r += 1
        r += 1

    # TotalScore 설명
    r += 1
    ws2.write(r, 0, "TotalScore (종합 점수) 기준", title_fmt)
    r += 1
    ws2.merge_range(r, 0, r, 3,
        "방법론: 23개 팩터 가중합 (0~100점). 재무 + 모멘텀 + 수급 + 기술적 지표를 종합 평가. "
        "전체 스캐너 실행 시에만 산출 가능.", cell_fmt)
    r += 2

    ts_sections = [
        ("CAN SLIM (35%)", [
            ("C — 분기 EPS 가속", "10%", "최근 분기 EPS 성장률 + 가속도"),
            ("A — 연간 EPS 성장", "8%", "3~5년 연간 EPS 성장률 + ROE"),
            ("N — 신고가 돌파", "7%", "52주 신고가 근접도 + 컵핸들 패턴"),
            ("S — 수급 확인", "5%", "거래량 돌파 확인 (OBV, 거래량비)"),
            ("L — 주도주 여부", "5%", "RS Rating 80+ 주도주 필터"),
        ]),
        ("팩터 모델 (35%)", [
            ("Fama-French 3팩터", "13%", "시장/사이즈/가치 팩터 (SMB, HML)"),
            ("퀄리티 팩터", "10%", "수익성+안정성+성장 복합 (RMW)"),
            ("모멘텀 팩터", "12%", "3/6/12개월 가격 모멘텀 (UMD)"),
        ]),
        ("스마트머니 (15%)", [
            ("기관/외국인 수급", "8%", "최근 기관·외국인 순매수 추세"),
            ("자금 흐름", "7%", "Smart Money Flow Index, 대량매매"),
        ]),
        ("기술적 분석 (15%)", [
            ("추세 강도", "8%", "이동평균 배열, ADX, 볼린저 위치"),
            ("리스크 조정", "7%", "변동성, 최대낙폭, 샤프비율"),
        ]),
    ]

    for section_name, items in ts_sections:
        ws2.merge_range(r, 0, r, 3, section_name, sub_fmt)
        r += 1
        for name, weight, desc in items:
            ws2.write(r, 0, name, bold_fmt)
            ws2.write(r, 1, weight, cell_fmt)
            ws2.write(r, 2, desc, cell_fmt)
            ws2.write(r, 3, "", cell_fmt)
            r += 1
        r += 1

    # 등급 기준
    r += 1
    ws2.write(r, 0, "등급 기준", title_fmt)
    r += 1
    grades = [
        ("★★★", "70점 이상", "상위 재무 — 성장+저평가+퀄리티 모두 우수"),
        ("★★", "55~69점", "양호 — 대부분 지표가 평균 이상"),
        ("★", "40~54점", "보통 — 일부 지표 양호, 일부 부진"),
        ("-", "40점 미만", "부진 — 성장 둔화 또는 고평가"),
    ]
    for tier, cutoff, desc in grades:
        ws2.write(r, 0, tier, bold_fmt)
        ws2.write(r, 1, cutoff, cell_fmt)
        ws2.write(r, 2, desc, cell_fmt)
        ws2.write(r, 3, "", cell_fmt)
        r += 1

    # 교차 해석
    r += 2
    ws2.write(r, 0, "교차 해석 (FinValue x TotalScore)", title_fmt)
    r += 1
    cross = [
        ("Total 높음 + Fin 높음", "주도주인데 재무도 좋음 → 최상"),
        ("Total 높음 + Fin 낮음", "테마·수급 드라이브 → 과열 주의"),
        ("Total 낮음 + Fin 높음", "재무 좋은데 시장이 아직 안 봄 → 선취매 후보"),
        ("Total 낮음 + Fin 낮음", "패스"),
    ]
    for label, desc in cross:
        ws2.write(r, 0, label, bold_fmt)
        ws2.merge_range(r, 1, r, 3, desc, cell_fmt)
        r += 1

    wb.close()
    print(f"\n엑셀 저장 완료: {fname}")

if __name__ == "__main__":
    main()
