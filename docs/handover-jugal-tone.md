# 주갤톤 한줄평 확장 작업 인수인계서

**작업일**: 2026-05-20
**상태**: 진행 중 (negative 모듈 주갤밈 리라이트 완료, 나머지 3개 모듈 대기)

---

## 1. 작업 목표

`web_app/one_liner.py`의 한줄평 시스템을 주식갤러리(주갤) 커뮤니티 톤으로 대폭 확장.
기존 ~1000개 구문 풀을 ~2640개로 늘리고, 반등 감지 로직을 추가.

---

## 2. 완료된 작업

### 2-1. 반등(rebound) 감지 구문 추가
- `_metric_tags()`에 "rebound" 태그 추가 (DayChg >= 3% AND RSI <= 35)
- `_signal_strength()`에 DayChg 차원 추가: `min(abs(day_chg)/0.05, 2.0)`
- `_METRIC_PHRASES`에 반등 구문 20개 추가:
  - (AVOID, rebound): 6개 - 경고 유지하면서 반등 인정
  - (FALLING_KNIFE, rebound): 6개 - 낙하 중 일시 반등 경고
  - (OVERSOLD, rebound): 8개 - 조심스러운 회복 가능성
- **설계 핵심**: `_bucket()` 로직은 건드리지 않음. AVOID 등급 불변성 유지.
  반등 인식은 구문 선택 레이어(`_metric_tags` + `_METRIC_PHRASES`)에서만 처리.

### 2-2. 스마트 줄바꿈 (Korean line-breaking)
- `wrap_oneliner()` 함수: 한글 문법 구조 인식 줄바꿈
- `_BREAK_AFTER_SUFFIXES`: 20+ 한국어 연결/종결 어미 등록
- `_WRAP_MIN_CHARS = 13`: 포스터 폰트 기준 한 줄 글자 수
- 구문 결속 비용 시스템: 관형형 분리 방지, 부사 고아 방지

### 2-3. 구문 풀 대확장 (Spicy V1 + V2)
- **Base `_PHRASES`**: 18 버킷, 1817개
- **Spicy V1 `_SPICY_ADDITIONS`**: 18 버킷, 270개 추가
- **Spicy V2 `_SPICY_ADDITIONS_V2`**: 18 버킷, 553개 추가 (인라인)
- **합계**: ~2640개 (중복 제거 전)
- 병합 방식: for 루프로 `_PHRASES`에 중복 체크 후 append

### 2-4. 별도 모듈 파일 4개 생성
| 파일 | 내용 | 구문 수 | 주갤밈 리라이트 |
|------|------|---------|----------------|
| `_spicy_v2_negative.py` | VALUE_TRAP, BUBBLE, OVERBOUGHT, FALLING_KNIFE, AVOID | 150 | **완료** |
| `_spicy_v2_positive_a.py` | TRUE_VALUE, EXPENSIVE_JUSTIFIED, MOMENTUM_LEADER, BREAKOUT | 120 | 미완료 |
| `_spicy_v2_positive_b.py` | SLEEPING_GIANT(43), CASH_COW, SECTOR_LEADER, DEFENSIVE | 133 | 미완료 |
| `_spicy_v2_mixed.py` | EARNINGS_BEAT, STRONG_BUY, STORY_STOCK, OVERSOLD, NEUTRAL | 150 | 미완료 |

### 2-5. `_spicy_v2_negative.py` 주갤밈 리라이트 상세
기존 "금융 블로거" 톤에서 찐 주갤 커뮤니티 밈 톤으로 150개 전면 리라이트.
검증 결과: 격식체 0건, 마침표 0건, 기술용어 0건, 중복 0건.

**사용된 주갤 밈/슬랭:**
| 밈/슬랭 | 사용 버킷 |
|---------|----------|
| 나뚜믄오른다/나뚜믄내린다 | VALUE_TRAP, BUBBLE, OVERBOUGHT, FALLING_KNIFE |
| 존버/존버충 | VALUE_TRAP, OVERBOUGHT, AVOID |
| 물타기 지옥 | VALUE_TRAP, FALLING_KNIFE |
| 깡통 | VALUE_TRAP, FALLING_KNIFE, AVOID |
| 호구 | VALUE_TRAP, BUBBLE, OVERBOUGHT, AVOID |
| 떡상/떡락 | BUBBLE, OVERBOUGHT, FALLING_KNIFE |
| 개미/개미무덤/개미지옥 | VALUE_TRAP, BUBBLE, FALLING_KNIFE, AVOID |
| 뇌동매매 | VALUE_TRAP, OVERBOUGHT |
| 주린이 | AVOID |
| 리딩방 | VALUE_TRAP, AVOID |
| 파란불 | VALUE_TRAP |
| 방바닥 구름 | BUBBLE |
| 치킨값 | VALUE_TRAP, AVOID |
| ㄹㅇ / ㅋㅋ | 전 버킷 |

### 2-6. 기타
- `run_quant_nexus.bat`: 존재하지 않는 `swing_mom_scan_alert.py` 시작 명령 제거
- 섹터 히트맵: 초기 로드 시 접힌 상태로 변경 (`scanner.html`, `app.js`)

---

## 3. 미완료 / 남은 작업

### 3-1. 나머지 3개 모듈 주갤밈 리라이트 (핵심 잔여 작업)
`_spicy_v2_negative.py`와 동일한 수준으로 주갤 밈/슬랭을 적용해야 함:
- `_spicy_v2_positive_a.py` (120개) — 긍정 밈: FOMO, "지금 안 사면 후회", 떡상 기대
- `_spicy_v2_positive_b.py` (133개) — 가치 밈: 숨은 알짜, 개미가 먼저 발견
- `_spicy_v2_mixed.py` (150개) — 양면 밈: 존버 vs 손절, 바닥론 자조

**주의**: NEUTRAL 버킷은 방향성 명령(사라/팔아라) 금지 규칙 유지해야 함.

### 3-2. 테스트 실패 3건 (인라인 `_SPICY_ADDITIONS_V2` 내 금지어)
```
FAILED test_no_technical_jargon_anywhere
  - VALUE_TRAP: "전저점 깬 놈이 저평가라고? 그게 신저가 예고임" → "신저가" 금지어
  - OVERSOLD: "과매도 자리는..." → "과매도" 금지어
  - SLEEPING_GIANT: "...줍는 시그널인 거임" → "시그널" 금지어

FAILED test_generic_pools_have_no_unverified_assertions
  - TRUE_VALUE: "실력은 있는데 아직 주목 못 받은 놈이 제일 맛있음" → 검증 불가 단정
```
**수정 방법**: 해당 구문의 금지어를 순한국어로 교체하거나 구문 자체를 교체.

### 3-3. 인라인 `_SPICY_ADDITIONS_V2`와 모듈 파일 동기화
현재 `one_liner.py` 인라인 553개와 모듈 파일 553개가 **중복 공존** 중.
negative 모듈은 주갤밈으로 리라이트되어 인라인과 **완전히 다른 상태**.

**권장 통합 방향**:
1. 4개 모듈 주갤밈 리라이트 전부 완료
2. 인라인 `_SPICY_ADDITIONS_V2` (line 3077~3667) 전체 삭제
3. 4개 모듈에서 import하여 `_PHRASES`에 병합하는 루프 추가
```python
from _spicy_v2_positive_a import POSITIVE_A_PHRASES
from _spicy_v2_positive_b import POSITIVE_B_PHRASES
from _spicy_v2_negative import NEGATIVE_PHRASES
from _spicy_v2_mixed import MIXED_PHRASES

for _src in (POSITIVE_A_PHRASES, POSITIVE_B_PHRASES, NEGATIVE_PHRASES, MIXED_PHRASES):
    for _sk, _sv in _src.items():
        _spool = _PHRASES.setdefault(_sk, [])
        for _sp in _sv:
            if _sp not in _spool:
                _spool.append(_sp)
```

### 3-4. 커밋
변경 사항이 크므로 기능별 분리 커밋 권장:
1. 반등 감지 + 스마트 줄바꿈 + 테스트
2. 구문 풀 확장 (주갤밈 리라이트 포함)
3. 히트맵 토글 + bat 수정

---

## 4. 파일 변경 요약

| 파일 | 변경 규모 | 상태 |
|------|----------|------|
| `web_app/one_liner.py` | +1446 / -227 | Modified, 테스트 실패 3건 |
| `web_app/_spicy_v2_negative.py` | 신규 (주갤밈 리라이트 완료) | Untracked |
| `web_app/_spicy_v2_positive_a.py` | 신규 (리라이트 대기) | Untracked |
| `web_app/_spicy_v2_positive_b.py` | 신규 (리라이트 대기) | Untracked |
| `web_app/_spicy_v2_mixed.py` | 신규 (리라이트 대기) | Untracked |
| `tests/test_one_liner_consistency.py` | +183 | Modified |
| `tests/test_oneliner_wrap.py` | 신규 | Untracked |
| `web_app/templates/scanner.html` | Modified | 히트맵 토글 |
| `web_app/static/app.js` | Modified | toggleHeatmap() |
| `run_quant_nexus.bat` | -3줄 | swing alert 제거 |

---

## 5. 아키텍처 메모

### 구문 선택 흐름
```
get_one_liner(d)
  → _bucket(d)           # 버킷 결정 (18종)
    → _raw_bucket(d)     # 지표 기반 초기 버킷
    → _score_grade(d)    # STRONG/SOLID/WEAK/AVOID
    → 등급별 조정        # AVOID면 긍정→부정 강제 전환
  → _metric_tags(d)      # 조건부 태그 (rebound 등)
  → _signal_strength(d)  # >= 3.0이면 메트릭 구문 활성화
  → _PHRASES[bucket]     # 일반 구문 풀
  → _METRIC_PHRASES[(bucket, tag)]  # 조건부 구문 풀
  → 해시 기반 선택       # 티커+시간으로 안정적 1개 선택
```

### 핵심 불변성
- `_bucket()` 내 AVOID 등급: 모든 긍정/중립 버킷 → 부정으로 강제 전환
- 반등 인식은 `_bucket()`을 건드리지 않고 `_metric_tags()`에서만 처리
- `_signal_strength()` >= 3.0 게이트: 메트릭 구문이 평범한 종목에 노출되는 것 방지

### 구문 규칙 (금지 사항)
- 격식체 금지 (습니다/합니다/입니다)
- 기술용어 금지 (RSI/PER/PBR/신저가/과매도/시그널 등)
- 숫자/마침표 금지
- NEUTRAL 버킷: 방향성 명령 금지 (사라/팔아라/오를 거/빠질 거)

### 주갤밈 톤 가이드 (리라이트 시 참고)
- **밈 필수**: 나뚜믄오른다, 존버, 물타기, 깡통, 호구, 떡상/떡락, 개미, 뇌동매매, 주린이, ㄹㅇ, ㅋㅋ
- **패턴**: 대조법(기대 vs 현실), 자조/운명론, FOMO 자극, 일상 비유, 조건부 경고
- **극성 일치**: 긍정 버킷에 자조 밈 금지, 부정 버킷에 확신 톤 금지
- **톤**: 10년차 개미 고인물이 후배한테 한 마디 던지는 느낌

---

## 6. 즉시 조치 우선순위

1. **나머지 3개 모듈 주갤밈 리라이트** → negative와 동일 톤으로
2. **테스트 실패 3건 수정** → 인라인 금지어 구문 4개 교체
3. **인라인 → 모듈 import 전환** → 인라인 590줄 삭제, import 루프 추가
4. **커밋** → 기능별 분리 커밋 권장
