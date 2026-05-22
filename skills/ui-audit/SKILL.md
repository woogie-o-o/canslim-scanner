---
name: ui-audit
description: This skill should be used when the user asks to "UI 점검", "UI 체크", "페이지 비교", "불일치 찾기", "프론트 리뷰", "디자인 체크", "빠진 필드 찾아줘", "목록이랑 상세 맞는지", "check UI consistency", "audit UI". Use this skill whenever the user wants to verify that data fields, styles, or UI elements are consistent across multiple HTML pages in a web application — catching missing fields, hardcoded dummy data, and style inconsistencies that slip through during feature development.
---

# UI/UX 디테일 점검

> 웹앱의 목록/상세/팝업 페이지 간 데이터 필드 누락, 스타일 불일치, 하드코딩 더미 데이터를 자동으로 탐지하고 수정안을 제안한다.

## 워크플로우

### Step 1: 프론트엔드 자산 수집
**타입**: script

프로젝트의 웹 프론트엔드 파일을 수집하고 구조화된 데이터를 추출한다.

1. Glob으로 `web_app/` (또는 사용자 지정 경로) 하위의 `*.html`, `*.js`, `*.css` 파일을 탐색한다.
2. `scripts/extract_fields.py`를 실행하여 각 파일에서 다음을 추출한다:
   - **HTML**: `id="..."` 속성이 있는 모든 요소 (태그명, id, 파일:라인)
   - **JS**: `setText('id', d.Field)`, `document.getElementById('id')`, `d.FieldName` 패턴의 데이터 바인딩
   - **CSS**: `var(--token)` 사용처, 인라인 `style="..."` 속성 (값, 파일:라인)
3. 추출 결과를 JSON 구조로 stdout에 출력한다.

Flask 라우트 파일(`app.py`)에서 `render_template` 호출을 파싱하여 페이지 역할(목록/상세)을 자동 식별한다. 자동 식별이 안 되면 사용자에게 확인한다.

### Step 2: 페이지 간 필드 교차비교
**타입**: prompt

Step 1에서 추출한 데이터를 바탕으로 페이지 간 불일치를 분석한다.

비교 대상:
- **목록 페이지** (테이블 컬럼 + 인라인 팝업) vs **상세 페이지** (히어로 카드 + 탭)
- **API 응답 키** vs **각 페이지의 바인딩 필드**

탐지 항목:
- 한쪽 페이지에만 존재하는 데이터 필드 (예: `Conviction`이 팝업에만 있고 detail에 없음)
- API가 반환하지만 어디에서도 표시하지 않는 필드
- HTML에 `id`는 있지만 JS에서 값을 채우지 않는 빈 요소
- 하드코딩된 더미 데이터 (예: `28.5조` 같은 고정 텍스트가 데이터 바인딩 없이 존재)

의도된 차이 구분 — 목록에는 요약만 보여주고 상세에 풀 데이터를 보여주는 것은 정상이다. "같은 데이터인데 한쪽에만 있는 경우"와 "의도적으로 생략한 경우"를 구분하기 위해, 데이터의 성격(핵심 지표 vs 보조 정보)을 고려하여 신뢰도(high/medium/low)를 부여한다.

### Step 3: 스타일 일관성 점검
**타입**: script + prompt

인라인 스타일과 CSS 변수 사용의 일관성을 점검한다.

1. **인라인 스타일 파싱**: `style="font-size:11px;..."` 형태의 값을 추출하고, `theme.css`의 디자인 토큰(`--text-primary`, `--radius` 등)과 대조한다.
2. **동일 의미 요소 비교**: 같은 역할의 요소(예: 상승여력 표시)가 페이지별로 다른 폰트 크기, 색상, 여백을 사용하는지 탐지한다.
3. **동적 스타일 예외 처리**: JS에서 `el.style.color = 'var(--success)'` 같은 조건부 스타일 할당은 의도적 인라인이므로 경고에서 제외한다.

### Step 4: 불일치 보고서 생성
**타입**: generate

탐지 결과를 심각도 순서로 정렬하여 보고서를 생성한다.

```
## UI 점검 결과

### 데이터 누락 (높음)
| # | 필드 | 있는 곳 | 없는 곳 | 제안 |
|---|------|---------|---------|------|
| 1 | Conviction | scanner 팝업 | detail.html | hero 카드에 추가 |

### 스타일 불일치 (중간)
| # | 요소 | 파일:라인 | 현재 | 권장 |
|---|------|----------|------|------|
| 1 | 상승여력 font-size | detail:519 | 11px 인라인 | var(--font-xs) |

### 하드코딩 더미 (높음)
| # | 내용 | 파일:라인 |
|---|------|----------|
| 1 | "28.5조" | detail:565 |
```

상위 10개 이슈만 우선 표시한다. 전체 목록이 필요하면 사용자가 요청할 수 있다.

### Step 5: 수정 패치 제안
**타입**: review + generate

보고서를 사용자에게 보여주고, 수정할 항목을 확인받은 뒤 코드 패치를 생성한다.

AskUserQuestion으로 수정 범위를 확인한다:
- "전부 고쳐줘" — 모든 높음/중간 이슈에 대해 Edit 도구로 수정
- "이것만 고쳐줘" — 사용자가 선택한 항목만 수정
- "보고서만 볼게" — 수정 없이 종료

수정 시 기존 코드 스타일(인라인 vs 클래스)을 존중하되, CSS 변수 사용을 권장하는 코멘트를 포함한다.

## Scripts
- **`scripts/extract_fields.py`** — HTML/JS/CSS에서 id, 데이터 바인딩, 스타일 토큰을 추출하는 Python 스크립트. JSON 형식으로 stdout 출력.

## Settings
| 설정 | 기본값 | 변경 방법 |
|------|--------|-----------|
| 분석 경로 | `web_app/` | "경로 지정" 자연어 또는 인수 |
| 최대 표시 이슈 | 10 | "전체 보여줘"로 확장 |
| 스타일 점검 포함 | Yes | "필드만 점검해줘"로 스킵 |
