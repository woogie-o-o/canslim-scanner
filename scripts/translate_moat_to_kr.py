# -*- coding: utf-8 -*-
"""moat_data.json의 영문 label/detail을 한국어로 일괄 번역.

122개 unique label + 63개 unique detail을 정확한 한글 표현으로 매핑한다.
실행: python scripts/translate_moat_to_kr.py
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "web_app", "moat_data.json")

# ── 영문 label → 한글 label (간결한 6~12자 배지 텍스트) ──────────────
LABEL_KR: dict[str, str] = {
    "5G/Satellite Tech": "5G·위성 기술",
    "ADC/Gene Therapy IP": "ADC·유전자치료 IP",
    "AI GPU·HBM Core": "AI GPU·HBM 코어",
    "AI Infra Tech": "AI 인프라 기술",
    "AI Platform Lock-in": "AI 플랫폼 락인",
    "ARM ISA": "ARM 명령어셋 IP",
    "AdTech Data Network": "광고 데이터 네트워크",
    "Ag Input Scale": "농자재 규모",
    "Alt Asset Lock-in": "대체자산 락인",
    "Analog/Fabless IP": "아날로그·팹리스 IP",
    "Arm & Hammer": "Arm & Hammer 브랜드",
    "BNPL": "후불결제 플랫폼",
    "Bank Scale Moat": "은행 규모 해자",
    "Battery Tech IP": "배터리 기술 IP",
    "Beverage Brand Power": "음료 브랜드 파워",
    "Bilibili": "비리비리 커뮤니티",
    "Birkenstock": "버켄스탁 브랜드",
    "Biz Process Lock-in": "업무 프로세스 락인",
    "Bloom Energy SOFC": "블룸에너지 SOFC",
    "Budweiser": "버드와이저 브랜드",
    "Building Materials Moat": "건축자재 해자",
    "Bumble": "범블 매칭 네트워크",
    "CDN": "콘텐츠 전송망",
    "CGM": "연속혈당측정기",
    "CJ ENM": "CJ ENM 콘텐츠 IP",
    "CPG Distribution Scale": "생활용품 유통 규모",
    "CRE Location Moat": "상업부동산 입지",
    "CRISPR": "크리스퍼 유전자편집",
    "Cabometyx": "카보메틱스 약물 IP",
    "Calvin·Tommy": "캘빈·타미 브랜드",
    "Cannabis Commodity": "대마 상품화 시장",
    "Chemical Scale Moat": "화학 규모 해자",
    "Clean Energy Tech": "청정에너지 기술",
    "Coach·Kate": "코치·케이트 브랜드",
    "Content IP Library": "콘텐츠 IP 라이브러리",
    "Coors·Miller": "쿠어스·밀러 브랜드",
    "Crocs·HEYDUDE": "크록스·헤이듀드 브랜드",
    "Crypto Mining/Infra": "암호화폐 채굴·인프라",
    "Cybersecurity Lock-in": "사이버보안 락인",
    "DC REIT Scale": "데이터센터 리츠 규모",
    "Defense Contract Lock-in": "방산 계약 락인",
    "Dominion": "도미니언 전력",
    "E-commerce Network": "이커머스 네트워크",
    "EM Platform Scale": "신흥국 플랫폼 규모",
    "EPC": "EPC 종합 건설",
    "ETF·Aladdin": "ETF·알라딘 플랫폼",
    "EV/Auto Tech": "전기차·자동차 기술",
    "Edgewell": "에지웰 생활용품",
    "Edison International": "에디슨 전력",
    "Exchange Data Moat": "거래소 데이터 해자",
    "FanDuel·Sky Bet": "팬듀얼·스카이베트",
    "Fintech Network": "핀테크 네트워크",
    "Fox News": "폭스 뉴스 채널",
    "Franklin Templeton": "프랭클린템플턴 운용",
    "GLP-1 Drug IP": "GLP-1 비만약 IP",
    "GTA·NBA2K": "GTA·NBA2K 게임 IP",
    "Gaming IP & Community": "게임 IP·커뮤니티",
    "Gaming License Moat": "카지노 라이선스 해자",
    "Grid Infra Scale": "전력망 인프라 규모",
    "HDD·NAND": "HDD·낸드 저장장치",
    "HPLC·MS": "HPLC·질량분석 장비",
    "HR Platform Lock-in": "HR 플랫폼 락인",
    "HVAC": "냉난방공조 시스템",
    "Harley-Davidson": "할리데이비슨 브랜드",
    "Healthcare Platform": "헬스케어 플랫폼",
    "Homebuilder Scale": "주택건설 규모",
    "ISC": "ISC 반도체 테스트",
    "IT Services Lock-in": "IT 서비스 락인",
    "Industrial Scale": "산업재 규모",
    "Insurance Scale": "보험 규모",
    "Jira·Confluence": "지라·컨플루언스",
    "Johnnie Walker": "조니워커 브랜드",
    "LCC": "저비용항공사",
    "LS Electric": "LS일렉트릭 전력기기",
    "LS Marine Solution": "LS마린솔루션 해저케이블",
    "Li Auto EREV SUV": "리오토 EREV SUV",
    "Luxury Brand IP": "럭셔리 브랜드 IP",
    "MCU·SiC": "MCU·SiC 전력반도체",
    "MH·RV": "조립주택·RV 차량",
    "MedTech Certification": "의료기기 인증 해자",
    "Medicaid": "메디케이드 보험",
    "Memory/Packaging Tech": "메모리·패키징 기술",
    "Mining Cost Curve": "광물 원가곡선",
    "NASH": "지방간염 치료제",
    "Nuclear/Uranium Barrier": "원전·우라늄 진입장벽",
    "O&G Scale Advantage": "석유·가스 규모 우위",
    "OSAT": "반도체 후공정",
    "OTA·VRBO": "OTA·바캉스 렌탈",
    "Oilfield Svc Lock-in": "유전 서비스 락인",
    "On Running": "온 러닝 신발",
    "PLM·CAD": "PLM·CAD 설계SW",
    "Packaging Scale": "포장재 규모",
    "PayPal·Venmo": "페이팔·벤모",
    "Peterbilt·Kenworth": "피터빌트·켄워스 트럭",
    "Pharma IP Portfolio": "제약 IP 포트폴리오",
    "Pinduoduo·Temu": "핀둬둬·테무 이커머스",
    "Pipeline Monopoly": "송유관 독점",
    "Precious Metals Scale": "귀금속 채굴 규모",
    "Quantum Tech IP": "양자 기술 IP",
    "Regulated Utility": "규제 유틸리티",
    "Restaurant Brand IP": "외식 브랜드 IP",
    "Retail Scale Advantage": "유통 규모 우위",
    "Robotics IP": "로봇 기술 IP",
    "SIEM·SOC": "SIEM·보안관제",
    "SaaS Workflow Lock-in": "SaaS 워크플로 락인",
    "Sam Adams·Truly": "샘 애덤스·트룰리",
    "Semicap Equipment IP": "반도체 장비 IP",
    "Taobao·Tmall": "타오바오·티몰",
    "Telecom Scale Moat": "통신 규모 해자",
    "Ticketmaster": "티켓마스터 예매망",
    "Tinder·Hinge": "틴더·힌지 매칭",
    "Tommy Bahama": "타미바하마 브랜드",
    "Transport Network Scale": "물류망 규모",
    "UCaaS": "클라우드 통신 플랫폼",
    "UFC·WWE": "UFC·WWE 격투 IP",
    "UGG·HOKA": "어그·호카 브랜드",
    "Urban Outfitters": "어반아웃피터스",
    "Vans·North Face": "반스·노스페이스",
    "WeChat": "위챗 소셜 네트워크",
    "Zen·EPYC": "Zen·EPYC CPU 코어",
    "Zero Trust": "제로트러스트 보안",
    "siRNA": "siRNA 유전자치료",
}

# ── 영문 detail → 한글 detail (1~2문장 설명) ────────────────────────
DETAIL_KR: dict[str, str] = {
    "5G networking or satellite communications provider with proprietary technology and spectrum/orbit assets.":
        "5G 네트워크·위성통신 사업자로 독자 기술과 주파수·궤도 자산이 진입장벽을 만든다.",
    "ADC or gene therapy developer with proprietary platform technology and clinical-stage pipeline.":
        "ADC·유전자치료 개발사로 독자 플랫폼 기술과 임상 단계 파이프라인이 해자를 형성한다.",
    "Aerospace/defense contractor with long-term government contracts, security clearances, and program-level switching costs.":
        "항공우주·방산 계약업체로 장기 정부계약, 보안 인가, 프로그램 단위 전환비용이 락인을 만든다.",
    "Agricultural input company with manufacturing scale and seasonal distribution network advantages.":
        "농자재 기업으로 제조 규모와 계절별 유통망 우위가 해자를 형성한다.",
    "Alternative asset manager with sticky LP capital commitments and long fund lifecycles creating structural switching costs.":
        "대체자산 운용사로 LP 자본의 약정 기간과 펀드 장기 운용이 구조적 전환비용을 만든다.",
    "Automotive or EV technology company with proprietary platform, sensor, or manufacturing IP.":
        "자동차·전기차 기술 기업으로 독자 플랫폼·센서·제조 IP가 해자를 형성한다.",
    "Beverage company with iconic brand portfolio and global distribution creating intangible asset moat.":
        "글로벌 유통망과 상징적인 브랜드 포트폴리오를 보유한 음료 기업으로 무형자산 해자가 강하다.",
    "Building materials producer with quarry/plant proximity creating regional transport cost advantages.":
        "채석장·공장 근접성으로 지역 운송비 우위를 가진 건축자재 제조사다.",
    "Business process outsourcing or platform with deep workflow integration and contractual switching costs.":
        "업무 프로세스 아웃소싱·플랫폼 기업으로 워크플로 통합과 계약 기반 전환비용이 해자다.",
    "Cannabis company in fragmented market with limited structural moat due to regulatory uncertainty and commodity dynamics.":
        "규제 불확실성과 상품화 압력이 큰 분절된 시장의 대마 기업으로 구조적 해자는 제한적이다.",
    "Clean energy technology provider with IP in solar, storage, or grid optimization systems.":
        "태양광·에너지저장·전력망 최적화 IP를 보유한 청정에너지 기술 기업이다.",
    "Commercial real estate operator with prime location assets and long-term lease structures.":
        "프라임 입지 자산과 장기 임대 구조를 가진 상업부동산 운영사다.",
    "Consumer packaged goods company with distribution scale and shelf-space dominance in retail channels.":
        "유통 규모와 소매 진열대 지배력이 큰 생활소비재(CPG) 기업이다.",
    "Critical AI infrastructure with technical differentiation.":
        "AI 핵심 인프라 사업자로 기술 차별화가 해자를 만든다.",
    "Cryptocurrency mining or blockchain infrastructure with limited structural moat; competitive advantage depends on energy costs and hashrate scale.":
        "암호화폐 채굴·블록체인 인프라로 구조적 해자는 약하며, 전기료와 해시레이트 규모에 따라 경쟁력이 좌우된다.",
    "Cybersecurity solutions integrated into enterprise infrastructure with high switching costs due to security policy dependencies.":
        "기업 인프라에 통합된 사이버보안 솔루션으로 보안 정책 의존성이 큰 전환비용을 만든다.",
    "Data center REIT with critical infrastructure positioning, power access, and long-term enterprise leases.":
        "데이터센터 리츠로 핵심 인프라 입지, 전력 확보, 장기 기업 임대가 해자를 만든다.",
    "Designs or manufactures cutting-edge AI GPUs or HBM chips with significant IP moat in advanced packaging and compute architecture.":
        "첨단 AI GPU·HBM 칩 설계·제조 기업으로 어드밴스드 패키징과 연산 아키텍처에 강한 IP 해자를 가진다.",
    "Diversified industrial company with manufacturing scale, distribution networks, and established customer relationships.":
        "제조 규모·유통망·고객 관계가 검증된 종합 산업재 기업이다.",
    "E-commerce or travel platform with marketplace network effects between buyers and sellers.":
        "구매자·판매자 간 마켓플레이스 네트워크 효과를 가진 이커머스·여행 플랫폼이다.",
    "Emerging market platform with local network effects and regulatory barriers to foreign competition.":
        "현지 네트워크 효과와 외국 경쟁 진입을 막는 규제 장벽을 가진 신흥국 플랫폼이다.",
    "Enterprise SaaS platform deeply embedded in customer workflows, creating high switching costs through data and process dependencies.":
        "고객 워크플로에 깊이 내장된 엔터프라이즈 SaaS 플랫폼으로 데이터·프로세스 의존성이 큰 전환비용을 만든다.",
    "Fabless or analog semiconductor designer with proprietary IP in power management, signal processing, or mixed-signal domains.":
        "전력관리·신호처리·믹스드시그널 분야의 독자 IP를 보유한 팹리스·아날로그 반도체 설계사다.",
    "Financial exchange or data provider with network effects from liquidity pools and proprietary datasets.":
        "유동성 풀과 독자 데이터셋의 네트워크 효과를 가진 거래소·금융 데이터 제공자다.",
    "Fintech or payment platform with network effects from merchant/consumer flywheel and embedded financial data.":
        "가맹점·소비자 양방향 플라이휠과 내장된 금융 데이터로 네트워크 효과를 가진 핀테크·결제 플랫폼이다.",
    "GLP-1/obesity therapeutic developer with proprietary clinical data and patent protection on novel mechanisms.":
        "신규 작용기전 특허와 독자 임상 데이터를 가진 GLP-1·비만 치료제 개발사다.",
    "Game developer with owned IP franchises and player community/in-game asset network effects.":
        "자체 IP 프랜차이즈와 플레이어 커뮤니티·인게임 자산의 네트워크 효과를 가진 게임 개발사다.",
    "Gold or precious metals miner/streamer with low-cost production or royalty/streaming contract portfolio.":
        "저비용 생산 또는 로열티·스트리밍 계약 포트폴리오를 가진 금·귀금속 채굴·스트리머 기업이다.",
    "HR or payroll platform embedded in enterprise workflows with employee data creating high switching costs.":
        "기업 업무에 내장된 HR·급여 플랫폼으로 직원 데이터가 큰 전환비용을 만든다.",
    "Healthcare services platform with patient data network effects and provider ecosystem lock-in.":
        "환자 데이터의 네트워크 효과와 의료기관 생태계 락인을 가진 헬스케어 서비스 플랫폼이다.",
    "Homebuilder or proptech company with land bank, construction scale, or technology-driven cost advantages.":
        "토지 매입, 건설 규모, 기술 기반 원가 우위를 가진 주택건설·프롭테크 기업이다.",
    "Hotel or casino operator with limited gaming licenses and prime location advantages.":
        "제한된 카지노 라이선스와 프라임 입지를 가진 호텔·카지노 운영사다.",
    "IT consulting or services firm with deep enterprise integration and project-level switching costs.":
        "기업 시스템 통합이 깊고 프로젝트 단위 전환비용이 큰 IT 컨설팅·서비스 기업이다.",
    "Insurance provider with actuarial data advantage and regulatory barriers to entry.":
        "보험 통계 데이터 우위와 규제 진입장벽을 가진 보험사다.",
    "Large bank or investment bank benefiting from regulatory barriers, scale economies in deposits, and relationship-driven lending.":
        "규제 장벽, 예금 규모의 경제, 관계 기반 대출 영업의 우위를 가진 대형 은행·투자은행이다.",
    "Large pharmaceutical company with deep patent portfolio, global distribution, and multi-decade drug franchises.":
        "두터운 특허 포트폴리오, 글로벌 유통망, 수십 년 단위 약물 프랜차이즈를 가진 대형 제약사다.",
    "Large retailer with purchasing scale, store density, and supply chain efficiency creating cost advantages.":
        "구매 규모, 매장 밀도, 공급망 효율성이 원가 우위를 만드는 대형 유통사다.",
    "Large telecom operator with spectrum assets, network infrastructure, and subscriber scale creating natural monopoly characteristics.":
        "주파수 자산, 네트워크 인프라, 가입자 규모가 자연독점 성격을 만드는 대형 통신사다.",
    "Lithium or battery technology company with proprietary cell chemistry, solid-state technology, or resource access.":
        "독자 셀 화학, 전고체 기술, 광물 자원 확보를 가진 리튬·배터리 기술 기업이다.",
    "Long-term government contracts with security clearances and program switching costs.":
        "장기 정부계약, 보안 인가, 프로그램 단위 전환비용이 해자를 만든다.",
    "Luxury or apparel brand with intangible brand equity and pricing power from heritage and perception.":
        "헤리티지와 브랜드 인식에서 비롯된 무형 브랜드 자산과 가격 결정력을 가진 럭셔리·의류 브랜드다.",
    "Marketplace network effects between buyers and sellers.":
        "구매자·판매자 간 마켓플레이스 네트워크 효과가 해자를 만든다.",
    "Medical device maker with FDA-cleared products and clinical workflow integration creating switching costs.":
        "FDA 승인 제품과 임상 워크플로 통합으로 전환비용을 만드는 의료기기 제조사다.",
    "Memory or advanced packaging technology provider with process know-how and customer qualification barriers.":
        "공정 노하우와 고객 인증 장벽을 가진 메모리·어드밴스드 패키징 기술 기업이다.",
    "Metals and mining company with low-cost ore deposits and established extraction infrastructure.":
        "저비용 광체와 검증된 채광 인프라를 가진 금속·광물 채굴 기업이다.",
    "Midstream pipeline operator with geographic route monopoly and long-term take-or-pay contracts.":
        "지역 노선 독점과 장기 take-or-pay 계약을 가진 미드스트림 송유관 운영사다.",
    "Nuclear energy or uranium supply chain participant with extreme regulatory barriers and specialized technical know-how.":
        "극도로 높은 규제 장벽과 전문 기술 노하우를 요구하는 원전·우라늄 공급망 참여자다.",
    "Offers AI platform or cloud services where customer data and workflows create high switching costs.":
        "고객 데이터와 워크플로가 큰 전환비용을 만드는 AI 플랫폼·클라우드 서비스 제공사다.",
    "Oil and gas producer with low-cost acreage, basin-level scale advantages, and proven reserves.":
        "저비용 광구, 분지 단위 규모 우위, 검증된 매장량을 가진 석유·가스 생산사다.",
    "Packaging company with manufacturing scale and long-term customer contracts in consumer staples.":
        "제조 규모와 생활필수품 영역의 장기 고객 계약을 가진 포장재 기업이다.",
    "Power grid and electrical infrastructure provider benefiting from scale economies and critical infrastructure positioning.":
        "규모의 경제와 핵심 인프라 입지의 이점을 가진 전력망·전기 인프라 사업자다.",
    "Proprietary platform technology with clinical pipeline and patent protection.":
        "임상 파이프라인과 특허 보호를 가진 독자 플랫폼 기술 기업이다.",
    "Proprietary platform, sensor, or manufacturing IP in automotive.":
        "자동차 영역의 독자 플랫폼·센서·제조 IP가 해자를 만든다.",
    "Proprietary robotics/drone technology with defense or industrial applications.":
        "방산·산업용으로 활용되는 독자 로봇·드론 기술이 해자를 만든다.",
    "Provides critical AI infrastructure (networking, cooling, power, compute) with technical differentiation in data center buildout.":
        "데이터센터 구축에 필수적인 네트워크·냉각·전력·컴퓨트 인프라를 기술 차별화로 제공한다.",
    "Quantum computing IP with extreme technical barriers to entry.":
        "극도로 높은 기술 진입장벽을 가진 양자컴퓨팅 IP가 해자를 만든다.",
    "Regulated utility with geographic monopoly, stable rate base, and regulatory barriers to competition.":
        "지역 독점, 안정적인 요금 기반, 경쟁 진입 규제 장벽을 가진 규제 유틸리티 사업자다.",
    "Restaurant chain with recognized brand, standardized operations, and franchise/unit economics flywheel.":
        "검증된 브랜드, 표준화된 운영, 프랜차이즈 유닛 이코노믹스 플라이휠을 가진 외식 체인이다.",
    "Semiconductor capital equipment maker with proprietary process technology and recipe-level customer lock-in.":
        "독자 공정 기술과 레시피 단위 고객 락인을 가진 반도체 장비 제조사다.",
    "Social media or ad-tech platform with user data network effects and advertiser demand-side flywheel.":
        "사용자 데이터의 네트워크 효과와 광고주 수요 측 플라이휠을 가진 소셜미디어·광고 기술 플랫폼이다.",
    "Specialty or commodity chemical producer with process scale and feedstock cost advantages.":
        "공정 규모와 원료비 우위를 가진 스페셜티·범용 화학 생산사다.",
    "Streaming or content company with proprietary content library and subscriber base creating scale advantages.":
        "독자 콘텐츠 라이브러리와 가입자 기반이 규모 우위를 만드는 스트리밍·콘텐츠 기업이다.",
    "Transportation company with route density and fleet scale creating cost advantages in freight logistics.":
        "노선 밀도와 차량 규모가 화물 물류 원가 우위를 만드는 운송 기업이다.",
}


def is_eng(s: str) -> bool:
    if not isinstance(s, str) or not s:
        return False
    return not any("가" <= c <= "힣" for c in s)


def main() -> int:
    with open(PATH, encoding="utf-8") as f:
        data = json.load(f)

    missing_labels: set[str] = set()
    missing_details: set[str] = set()
    changed = 0
    label_changed = 0
    detail_changed = 0

    for tk, entry in data.items():
        if not isinstance(entry, dict):
            continue
        ent_changed = False
        lbl = entry.get("label", "") or ""
        if is_eng(lbl):
            if lbl in LABEL_KR:
                entry["label"] = LABEL_KR[lbl]
                ent_changed = True
                label_changed += 1
            else:
                missing_labels.add(lbl)
        dtl = entry.get("detail", "") or ""
        if is_eng(dtl):
            if dtl in DETAIL_KR:
                entry["detail"] = DETAIL_KR[dtl]
                ent_changed = True
                detail_changed += 1
            else:
                missing_details.add(dtl)
        if ent_changed:
            changed += 1

    if missing_labels:
        print(f"[WARN] 미매핑 영문 label {len(missing_labels)}개:", file=sys.stderr)
        for x in sorted(missing_labels):
            print(f"  {x!r}", file=sys.stderr)
    if missing_details:
        print(f"[WARN] 미매핑 영문 detail {len(missing_details)}개:", file=sys.stderr)
        for x in sorted(missing_details):
            print(f"  {x!r}", file=sys.stderr)

    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"종목 변경: {changed}  label 번역: {label_changed}  detail 번역: {detail_changed}")
    return 0 if not (missing_labels or missing_details) else 2


if __name__ == "__main__":
    sys.exit(main())
