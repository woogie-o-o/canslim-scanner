# Codex 작업 지시 — 종목 목록 렌더링 최적화

## 컨텍스트
Flask 한국 주식 스캐너 웹앱. 현재 `/api/scan` 결과 1500+ 종목을 한 번에 DOM에 주입해 스크롤·필터·정렬이 심하게 버벅임. Claude(파트너)는 별도로 종목 상세 패널 lazy-load 최적화를 진행 중.

## 작업 파일
- `web_app/static/app.js` (수정 대상)
- `web_app/templates/scanner.html` (테이블 구조 참고용)

## 현재 동작 (병목 지점)
`web_app/static/app.js` line **1489**:
```javascript
tbody.innerHTML = view.map((s, i) => renderStockRow(s, i + 1)).join('');
```
- `view`는 보통 1300~1500개 행. 각 행에 onclick/onmouseenter/onmouseleave + 8개 셀 + 리스크 배지 → 추정 DOM 노드 70만+
- 필터·정렬·검색 시마다 `_applyView()` 호출 → 매번 전체 재렌더

## 목표
**필터 한 번 적용 시 첫 paint를 300ms 이내**로. 풀 렌더는 백그라운드에서 점진적으로.

## 구현 가이드 (점진 렌더링 — 가상 스크롤 X, 단순함 우선)

1. `_applyView()` 함수의 `tbody.innerHTML = ...` 부분을 다음 패턴으로 교체:

```javascript
// 초기 100행 동기 렌더 (즉시 화면 표시)
const INITIAL_BATCH = 100;
const BATCH_SIZE = 200;
const initial = view.slice(0, INITIAL_BATCH).map((s, i) => renderStockRow(s, i + 1)).join('');
tbody.innerHTML = initial;

// 나머지는 requestIdleCallback로 200행씩 추가 (백그라운드)
if (view.length > INITIAL_BATCH) {
  let offset = INITIAL_BATCH;
  const renderToken = ++_renderToken;  // 다음 _applyView 호출 시 이전 작업 취소
  const appendBatch = () => {
    if (renderToken !== _renderToken) return;  // 새 필터 적용됨 → 중단
    if (offset >= view.length) return;
    const end = Math.min(offset + BATCH_SIZE, view.length);
    const html = view.slice(offset, end).map((s, i) => renderStockRow(s, offset + i + 1)).join('');
    tbody.insertAdjacentHTML('beforeend', html);
    offset = end;
    if (offset < view.length) {
      (window.requestIdleCallback || setTimeout)(appendBatch, 16);
    }
  };
  (window.requestIdleCallback || setTimeout)(appendBatch, 16);
}
```

2. 모듈 최상단에 `let _renderToken = 0;` 선언 추가 (이미 존재하면 재사용)

3. **모바일 카드 리스트(`_updateMobileList`)도 동일하게** 점진 렌더 적용.
   - `mobile-stock-list`의 `innerHTML = ...` 부분을 같은 패턴으로 변경.
   - 모바일은 초기 50행, 배치 100행으로.

4. **정렬·필터링 자체의 비용도 줄이기**:
   - `_applyView()` 안에서 정렬 함수 안의 `_getByPath(a, _sortKey)` 호출이 매 비교마다 일어남. → 일회 추출 후 비교.
   - 다음과 같이 schwartzian transform 적용:
     ```javascript
     view = stocks
       .map(s => ({ s, k: _getByPath(s, _sortKey) }))
       .sort((a, b) => {
         const aa = a.k == null ? -Infinity : a.k;
         const bb = b.k == null ? -Infinity : b.k;
         return _sortDir * (aa > bb ? 1 : aa < bb ? -1 : 0);
       })
       .map(x => x.s);
     ```

## 절대 금지
- 라이브러리 추가 금지 (react/vue/순수 가상스크롤 라이브러리 X). 모든 변경은 vanilla JS.
- `renderStockRow()`의 HTML 마크업 변경 금지 — CSS 셀렉터 깨짐 방지.
- 정렬 인디케이터, 워치리스트 ☆ 토글, 체크박스 선택 상태 등 기존 기능 유지.
- 검색바·퀵필터 동작 변경 금지.

## 검증
변경 후 다음을 확인:
1. `python -c "import ast; ast.parse(open('web_app/app.py').read())"` (관계없지만 syntax 안전 확인)
2. JS 문법 검증: `node -c web_app/static/app.js` 또는 `node --check`
3. 변경 라인 수 보고 (`git diff --stat web_app/static/app.js`)

## 출력
완료 시 짧게:
- 변경한 함수/라인 범위
- 도입한 변수/패턴
- 예상 효과 (실측 X, 추정만)
- 잠재 부작용 (있으면)
