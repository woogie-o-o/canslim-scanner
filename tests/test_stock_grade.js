// _stockGrade() 단위 테스트 — JS 프레임워크가 없어 standalone Node 스크립트로 작성.
// app.js 에서 _stockGrade 함수 소스를 정규식으로 추출해 격리 평가한다 (순수 함수, DOM 의존 없음).
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const src = fs.readFileSync(
  path.join(__dirname, '..', 'web_app', 'static', 'app.js'), 'utf8');

const m = src.match(/function _stockGrade\s*\([\s\S]*?\r?\n\}/);
if (!m) { console.error('FAIL: _stockGrade 함수를 app.js 에서 찾지 못함'); process.exit(1); }
const _stockGrade = new Function(m[0] + '\nreturn _stockGrade;')();

const cases = [
  [75, 'S'], [80, 'S'], [100, 'S'],
  [74, 'A'], [60, 'A'],
  [59, 'B'], [45, 'B'],
  [44, 'C'], [0, 'C'],
  [null, null], [undefined, null], ['', null], [NaN, null], ['abc', null],
];
for (const [input, expected] of cases) {
  const got = _stockGrade(input);
  assert.strictEqual(got, expected,
    `_stockGrade(${JSON.stringify(input)}) = ${JSON.stringify(got)}, 기대값 ${JSON.stringify(expected)}`);
}
console.log(`PASS: _stockGrade ${cases.length}/${cases.length} cases`);
