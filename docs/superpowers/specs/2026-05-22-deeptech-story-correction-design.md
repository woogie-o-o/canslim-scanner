# 딥테크 스토리 종목 보정 설계

**작성일:** 2026-05-22
**대상 파일:** `quant_nexus_v20.py`, `web_app/one_liner.py`

## 배경

증권사 리포트는 우주·방산·바이오 같은 딥테크 섹터를 매출 성장과 수주잔고 기반으로 평가하지만, 우리 스캐너는 EPS·ROE·PER 같은 퀄리티 팩터를 합산해 점수를 매긴다. 그 결과 쎄트렉아이(099320) 같은 종목이 AVOID("돈에 대한 모독")로 분류된다.

스캐너가 "퀄리티 스캐너" 정체성을 유지하면서도, 미래 산업 수주형 종목을 잡주와 구분해서 STORY_STOCK으로 라우팅하는 보정을 도입한다.

## 목표

- 딥테크 섹터의 수주형 적자 기업이 부정 버킷(AVOID/VALUE_TRAP)이 아닌 STORY_STOCK 버킷에 배치된다.
- 실제 잡주·테마 좌비주(매출 정체, 소형주)는 기존대로 AVOID 유지.
- RED grade(분식·상폐 시그널) 종목은 예외 없이 AVOID 유지.
- 기존 흑자 딥테크 종목(예: 한화에어로스페이스)의 분류는 변하지 않는다.

## 비목표

- 점수 자체를 인위적으로 끌어올리지 않는다(STORY_STOCK은 중립~약긍정 톤).
- PSR/매출 기반 대체 스코어는 이번 범위에서 제외(후속 작업).
- 미국주(US) 섹터는 이번 범위에서 제외(섹터 매핑 분리되어 있음).

## 설계

### 1. 딥테크 섹터 화이트리스트

`quant_nexus_v20.py` 상수 영역에 추가:

```python
_DEEPTECH_SECTORS: set[str] = {
    "드론·우주",
    "위성·발사체",
    "양자컴퓨팅",
    "SMR/원전",
    "수소·연료전지",
    "자율주행",
    "바이오·신약",
}
```

기존 섹터 매핑(`quant_nexus_v20.py:11322`, `11547` 등)이 이미 같은 키를 쓰고 있으므로 그대로 일치시킨다.

### 2. 게이트 함수

```python
def _is_deeptech_story(ticker: str, d: dict) -> bool:
    """딥테크 보정 대상 여부.

    True 조건:
      - 섹터 ∈ _DEEPTECH_SECTORS
      - 매출 YoY > 0 (전년 동기 대비 증가)
      - 시가총액 > 1000억원
    데이터 결측 시 보수적으로 False.
    """
```

호출 위치는 두 곳:
- EPS Fail-Safe 결정 직전 (`quant_nexus_v20.py:5180` 근처, 지주사 면제 분기와 같은 자리)
- `_raw_bucket()` 내부 점수 < 30 분기 (`web_app/one_liner.py:3636`)

### 3. EPS Fail-Safe 면제

지주사 면제 패턴(`quant_nexus_v20.py:5180`)을 모델로 다음 분기 추가:

```python
if _is_deeptech_story(ticker, row):
    eps_fail_safe = False  # 주가 기반 RS Fail-Safe는 별도 적용
```

RS Fail-Safe(`quant_nexus_v20.py:4466`)는 그대로 적용 — 가격이 망가진 종목은 여전히 걸러야 한다.

### 4. 버킷 라우팅

`web_app/one_liner.py:_raw_bucket()` 의 점수 < 30 분기를 다음과 같이 수정:

```python
if "AVOID" in signal or (score and score < 30):
    # RED grade는 면제 없음 — 즉시 AVOID
    if grade == "RED":
        return "AVOID"
    # 딥테크 스토리 종목은 STORY_STOCK 후보
    if _is_deeptech_story(ticker, d):
        return "STORY_STOCK"
    return "AVOID"
```

`STORY_STOCK` 버킷은 이미 정의돼 있고(`one_liner.py:354`) one-liner 톤이 중립~호기심형이라 적자 수주주 성격과 맞는다.

### 5. 모멘텀 override와의 관계

기존 `SCORE_CEIL_MOMENTUM_OVERRIDE=70` (`quant_nexus_v20.py:400`)는 RS≥90 & 12M>200% & Hurst>0.65 같은 빡빡한 조건이다. 딥테크 게이트와 모멘텀 override는 **OR**로 결합 — 둘 중 하나라도 통과하면 면제. 중복 면제는 무해(이미 면제된 종목을 다시 면제해도 결과 동일).

## 데이터 흐름

```
종목 평가 시작
    ↓
섹터 판정 (sector_map lookup)
    ↓
_is_deeptech_story() 게이트
    ├─ True  → EPS FailSafe skip, _raw_bucket에서 STORY_STOCK 라우팅 가능
    └─ False → 기존 로직 (퀄리티 점수 그대로)
    ↓
RS FailSafe 별도 평가 (가격 망가지면 여전히 차단)
    ↓
최종 버킷 결정
```

## 엣지케이스

| 케이스 | 처리 |
|---|---|
| 매출 데이터 결측 | 게이트 미통과 (보수적) |
| 시총 결측 | 게이트 미통과 |
| 섹터 매핑에서 종목 누락 | 게이트 미통과 (기존 분류 유지) |
| RED grade (분식·상폐 시그널) | 무조건 AVOID, 면제 없음 |
| RS Fail-Safe 트리거 (가격 폭락) | 그대로 적용, 면제 없음 |
| 흑자 딥테크 종목 | 게이트는 통과하지만 점수가 이미 높아 AVOID 분기 안 탐 — 영향 없음 |
| 미국주 | 미국 섹터 매핑은 `_DEEPTECH_SECTORS` 키와 다름 — 자동 미적용 |

## 테스트

- **쎄트렉아이(099320, 위성·발사체)**: 매출 성장 중·시총 통과 → STORY_STOCK 진입 확인
- **한화에어로스페이스(흑자, 드론·우주)**: 게이트 통과하지만 점수 충분 → 기존 STRONG_BUY/MOMENTUM_LEADER 유지
- **이름만 우주 테마인 소형주(매출 정체)**: 게이트 미통과 → 여전히 AVOID
- **바이오 임상 실패 종목(RED grade)**: 면제 없음, AVOID 유지
- **RS Fail-Safe 트리거된 우주주**: AVOID 유지

## 영향 범위

- 변경 파일: `quant_nexus_v20.py`, `web_app/one_liner.py`
- 신규 상수: `_DEEPTECH_SECTORS`
- 신규 함수: `_is_deeptech_story()`
- 기존 함수 수정: EPS Fail-Safe 결정 분기, `_raw_bucket()` 점수 < 30 분기

## 후속 작업 (이번 범위 밖)

- PSR/매출성장 기반 대체 스코어로 c_score 재산출
- 수주잔고 데이터 소스 연동 (DART, 회사 IR)
- 미국 딥테크 섹터(우주·국방·바이오테크) 동일 보정
