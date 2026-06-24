/**
 * app.js — 종목분석기 웹 프론트엔드
 * scanner.html (데스크탑 테이블) / detail.html 공용 스크립트
 */

// ── 세이프 모드 (공공장소용 — 로고 클릭으로 토글) ─────────────────────
const SafeMode = (() => {
  const KEY = 'safeMode';
  const SAFE = '종목 스캐너';
  const REAL = '종목분석기';
  let on = localStorage.getItem(KEY) === '1';
  function name() { return on ? SAFE : REAL; }
  function toggle() { on = !on; localStorage.setItem(KEY, on ? '1' : '0'); apply(); }
  function apply() {
    const logo = document.querySelector('.topbar-logo');
    if (logo) logo.innerHTML = on ? `<span>${SAFE}</span>` : `종목 <span>분석기</span>`;
    document.title = document.title.replace(on ? REAL : SAFE, name());
  }
  return { name, toggle, apply, on: () => on };
})();
document.addEventListener('DOMContentLoaded', () => {
  SafeMode.apply();
  const logo = document.querySelector('.topbar-logo');
  if (logo) logo.addEventListener('click', SafeMode.toggle);
});

// ── 상태 ─────────────────────────────────────────────────────────────────
let currentMarket   = 'KR';
let currentStrategy = 'BALANCED';
let currentSector   = '';   // '' = 전체
let allStocks       = [];   // 마지막 스캔 결과 캐시
let _scanStocks     = [];   // 검색/필터 기준이 되는 전체 스캔 결과
let _cachedGroups   = {};   // loadSectors 결과 보관 (sidebar 재렌더용)
let _cachedSectors  = {};   // loadSectors 결과 보관
let _sortKey        = 'TotalScore';  // 현재 정렬 키 (기본: 복합 점수)
let _sortDir        = -1;   // 0=없음, 1=asc, -1=desc
let _stockMap       = {};   // ticker → stock data (팝업용)
let _activeFilters  = new Set(); // 활성 퀵필터 집합 (비어있으면 전체)
let _activeIndex    = 'all';     // 지수 보기: all | SP500 | SP400 | SP600 | NDX | OTHER
let _indexMeta      = null;      // 지수 명단 기준일·신선도 (/api/index-meta)
let _scanCacheAgeMin = null;     // 현재 스캔 결과 캐시 경과(분) — 신선도 표시용
let _watchlist      = new Set(); // 현재 마켓의 워치리스트 티커 집합
let _selectedStocks = new Set(); // 공유 카드용 선택 종목

// ── 워치리스트 (localStorage) ───────────────────────────────────────────
let _currentResults = [];   // search/view basis
let _oneLinerFilter = null; // OneLinerTag filter
let _compareSet = new Set();
let _renderToken = 0;

// ── 클라이언트 API 캐시 (sessionStorage) ──────────────────────────────
const _clientCache = {
  _TTL: 5 * 60 * 1000, // 5분
  _key(endpoint) { return `_sc_${endpoint}`; },
  get(endpoint) {
    try {
      const raw = sessionStorage.getItem(this._key(endpoint));
      if (!raw) return null;
      const { data, ts } = JSON.parse(raw);
      if (Date.now() - ts > this._TTL) { sessionStorage.removeItem(this._key(endpoint)); return null; }
      return data;
    } catch { return null; }
  },
  set(endpoint, data) {
    try { sessionStorage.setItem(this._key(endpoint), JSON.stringify({ data, ts: Date.now() })); } catch {}
  }
};

function _scheduleDeferredRender(callback) {
  if (window.requestIdleCallback) {
    window.requestIdleCallback(callback, { timeout: 16 });
    return;
  }
  window.setTimeout(callback, 16);
}

function _renderHtmlInBatches(container, items, renderItem, initialBatch, batchSize, renderToken) {
  if (!container) return;
  const initialHtml = items.slice(0, initialBatch).map((item, i) => renderItem(item, i + 1)).join('');
  container.innerHTML = initialHtml;
  if (items.length <= initialBatch) return;

  let offset = initialBatch;
  const appendBatch = () => {
    if (renderToken !== _renderToken) return;
    if (offset >= items.length) return;
    const end = Math.min(offset + batchSize, items.length);
    const html = items.slice(offset, end).map((item, i) => renderItem(item, offset + i + 1)).join('');
    container.insertAdjacentHTML('beforeend', html);
    offset = end;
    if (offset < items.length) _scheduleDeferredRender(appendBatch);
  };

  _scheduleDeferredRender(appendBatch);
}

// html2canvas lazy-loader — 캡쳐/공유 카드 클릭 시에만 430KB 로드
let _html2canvasLoading = null;
function _ensureHtml2Canvas() {
  if (window.html2canvas) return Promise.resolve();
  if (_html2canvasLoading) return _html2canvasLoading;
  _html2canvasLoading = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = 'https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js';
    s.onload = () => resolve();
    s.onerror = () => { _html2canvasLoading = null; reject(new Error('html2canvas load failed')); };
    document.head.appendChild(s);
  });
  return _html2canvasLoading;
}

function _wlKey(market) { return `scanner_watchlist_${market || currentMarket}`; }

// 서버 영속화 워치리스트 + localStorage 폴백/마이그레이션
async function loadWatchlist(market) {
  const m = market || currentMarket;
  // 1) 우선 localStorage로 즉시 채워서 UI 반응성 유지
  try {
    const raw = localStorage.getItem(_wlKey(m));
    _watchlist = new Set(raw ? JSON.parse(raw) : []);
  } catch { _watchlist = new Set(); }
  // 2) 서버에서 fetch
  try {
    const res = await fetch(`/api/watchlist?market=${encodeURIComponent(m)}`);
    if (!res.ok) return;
    const serverList = await res.json();
    if (!Array.isArray(serverList)) return;
    const serverSet = new Set(serverList);
    // 3) localStorage에만 있던 종목이 있으면 서버로 마이그레이션 (1회성)
    const localOnly = [..._watchlist].filter(t => !serverSet.has(t));
    if (localOnly.length) {
      try {
        await fetch('/api/watchlist/bulk', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tickers: localOnly })
        });
        localOnly.forEach(t => serverSet.add(t));
      } catch {}
    }
    _watchlist = serverSet;
    // 4) localStorage도 동기화 유지
    try { localStorage.setItem(_wlKey(m), JSON.stringify([...serverSet])); } catch {}
    if (typeof _refreshFilteredView === 'function') _refreshFilteredView();
  } catch (e) {
    // 서버 장애 시 localStorage 그대로 사용
    console.warn('watchlist server load failed, using localStorage', e);
  }
}

function saveWatchlist() {
  // localStorage 백업 (서버 장애 대비)
  try { localStorage.setItem(_wlKey(), JSON.stringify([..._watchlist])); } catch {}
}

function toggleWatchlist(ticker, ev) {
  if (ev) { ev.stopPropagation(); }
  const adding = !_watchlist.has(ticker);
  if (adding) _watchlist.add(ticker);
  else _watchlist.delete(ticker);
  saveWatchlist();
  _refreshFilteredView();
  // 서버 동기 (fire-and-forget)
  try {
    if (adding) {
      fetch('/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker })
      }).catch(() => {});
    } else {
      fetch(`/api/watchlist/${encodeURIComponent(ticker)}`, { method: 'DELETE' }).catch(() => {});
    }
  } catch {}
}

// ── 공통 유틸 ────────────────────────────────────────────────────────────

function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// 서버 wrap_oneliner()가 넣은 \n을 <br>로. esc() 이후에만 치환하므로
// HTML 주입 위험 없음.
function olHtml(s) {
  return esc(s).replace(/\r\n|\r|\n/g, '<br>');
}

function scoreClass(score) {
  if (score >= 70) return 'score-high';
  if (score >= 50) return 'score-mid';
  return 'score-low';
}

function _scoreVerdict(score) {
  if (score >= 70) return '우수';   // green
  if (score >= 50) return '양호';   // yellow
  if (score >= 20) return '미흡';   // red-light
  if (score >= 0)  return '주의';   // red
  return '미달';                    // red, below zero
}

function _signalTier(signal) {
  if (!signal) return 'neutral';
  const s = signal.toUpperCase();
  if (s.includes('BREAKOUT') || s.includes('STRONG LEADER') || s.includes('MOMENTUM')) return 'strong';
  if (s.includes('LEADER') || s.includes('WATCH') || s.includes('ACCUMULATE')) return 'buy';
  if (s.includes('NEUTRAL') || s.includes('HOLD')) return 'hold';
  if (s.includes('SELL') || s.includes('AVOID') || s.includes('LAGGARD') || s.includes('BEAR') || s.includes('CAUTION')) return 'sell';
  return 'neutral';
}

// 종합점수(TotalScore) → 종목 등급 S/A/B/C. 숫자가 아니면 null.
function _stockGrade(totalScore) {
  if (totalScore == null || (typeof totalScore === 'string' && totalScore.trim() === '')) return null;
  const n = Number(totalScore);
  if (Number.isNaN(n)) return null;
  if (n >= 75) return 'S';
  if (n >= 60) return 'A';
  if (n >= 45) return 'B';
  return 'C';
}

function signalColor(signal) {
  const tier = _signalTier(signal);
  return { strong: 'var(--success)', buy: 'var(--info)', hold: 'var(--warning)', sell: 'var(--destructive)', neutral: 'var(--text-secondary)' }[tier];
}

function signalBg(signal) {
  const tier = _signalTier(signal);
  return { strong: 'rgba(0,192,115,0.10)', buy: 'rgba(49,130,246,0.10)', hold: 'rgba(255,146,0,0.10)', sell: 'rgba(240,68,82,0.10)', neutral: 'var(--surface-subtle)' }[tier];
}

// 시그널 문자열에서 메인 시그널과 [태그]를 분리
function _splitSignal(signal) {
  if (!signal) return { base: signal, tags: [] };
  const tags = [];
  const base = signal.replace(/\s*\u{1F514}?\[([^\]]+)\]/gu, (_, tag) => {
    tags.push(tag);
    return '';
  }).trim();
  return { base, tags };
}

const _TAG_KO = {
  'BREAKOUT':'돌파','PIVOT':'피벗','TREND':'추세',
  'EPS':'EPS','VOL':'거래량','LOW LIQ':'유동성↓',
};

// V5.1_TUNED: STRONG/NEUTRAL/AVOID → 색상/아이콘 매핑
const _ENTRY_COLOR = { STRONG: 'green', NEUTRAL: 'yellow', AVOID: 'red',
                       GREEN: 'green', YELLOW: 'yellow', RED: 'red' };
const _ENTRY_ICON  = { STRONG: '🟢', NEUTRAL: '🟡', AVOID: '🔴',
                       GREEN: '🟢', YELLOW: '🟡', RED: '🔴' };
const _ENTRY_LABEL = { STRONG: '근접 구간', NEUTRAL: '눌림대기', AVOID: '부적합',
                       GREEN: '근접 구간', YELLOW: '눌림대기', RED: '부적합' };

// STRONG/GREEN 라벨을 entry_discount(%) 와 atrPct(%) 에 따라 분기.
// disc<0 → '풀백대기' (현재가가 entry 위, 추격),
// atrPct 있으면 disc/atrPct 비율로 — <0.5 근접 구간, <1.0 이격 구간, 그 외 풀백대기.
// atrPct null/0 이면 절대값 fallback (1.5%/5%).
// asOfTs (epoch sec) 가 5분 초과 stale 면 라벨에 ' · 지연' 접미사 (EG-005).
function _entryLabel(st, disc, atrPct, asOfTs, ddPct, headlineAction, mddCurrent, mddRisk, volRegime, ddVelocity5d) {
  let label;
  if (st === 'STRONG' || st === 'GREEN') {
    if (disc == null || isNaN(disc)) {
      label = '데이터 부족';
    } else if (disc < 0) {
      label = '풀백대기';
    } else if (atrPct != null && !isNaN(atrPct) && atrPct > 0) {
      const r = disc / atrPct;
      label = (r < 0.5) ? '근접 구간' : (r < 1.0) ? '이격 구간' : '풀백대기';
    } else {
      label = (disc < 1.5) ? '근접 구간' : (disc < 5.0) ? '이격 구간' : '풀백대기';
    }
    // P13: 경고 피로 방지 — 가장 심각한 배지 1개만 (급락 > 고위험 > 경고 > 주의)
    if (label !== '데이터 부족') {
      // P4: 급락 속도 경보 (5일간 -5%p 이상 급락)
      const vel = (ddVelocity5d != null && !isNaN(ddVelocity5d)) ? ddVelocity5d : 0;
      // 복합 드로다운: 52주/MDD 중 나쁜 쪽
      const dd52w = (ddPct != null && !isNaN(ddPct)) ? ddPct : 0;
      const ddMdd = (mddCurrent != null && !isNaN(mddCurrent)) ? mddCurrent : 0;
      const worstDd = Math.min(dd52w, ddMdd);
      const vScale = (volRegime === 'LOW') ? 0.6 : (volRegime === 'HIGH') ? 1.6 : 1.0;
      const t1 = -15 * vScale, t2 = -20 * vScale, t3 = -30 * vScale;
      if (vel < -5)                label += ' · 급락';
      else if (worstDd <= t3)      label += ' · 고위험';
      else if (worstDd <= t2)      label += ' · 경고';
      else if (worstDd <= t1)      label += ' · 주의';
    }
  } else if (headlineAction) {
    label = headlineAction;
    if (mddRisk === 'EXTREME') label += ' · 고위험';
    else if (mddRisk === 'HIGH') label += ' · 경고';
  } else {
    label = _ENTRY_LABEL[st] || '';
  }
  if (label && asOfTs != null && !isNaN(asOfTs) && asOfTs > 0) {
    const ageSec = Date.now() / 1000 - asOfTs;
    if (ageSec > 300) label += ' · 지연';
  }
  return label;
}

// 종합점수(Y) × 진입 타이밍(X) 2축 사분면 배지
function _renderQuadrant(d) {
  const card = document.getElementById('dp-quadrant-card');
  const host = document.getElementById('dp-quadrant');
  if (!card || !host) return;
  const ts = (d.TotalScore != null) ? Number(d.TotalScore) : null;
  const es = (d.EntryScore != null) ? Number(d.EntryScore) : null;
  if (ts == null || Number.isNaN(ts) || es == null || Number.isNaN(es)) {
    card.style.display = 'none';
    return;
  }
  card.style.display = '';
  const clamp = v => Math.max(0, Math.min(100, v));
  const X = clamp(es), Y = clamp(ts);
  const TX = 50, TY = 60;                       // 진입 STRONG≥50 · 종합 '좋은 회사' 60
  const PW = 200, PH = 150, P = 8;
  const plotW = PW - P * 2, plotH = PH - P * 2;
  const px = P + (X / 100) * plotW;
  const py = P + (1 - Y / 100) * plotH;          // y 반전: 위 = 높은 종합
  const tx = P + (TX / 100) * plotW;
  const ty = P + (1 - TY / 100) * plotH;
  const goodCo = Y >= TY, goodTime = X >= TX;
  let label, desc, col;
  if (goodCo && goodTime)       { label = '좋은 회사 · 좋은 타이밍'; desc = '지금이 바로 그 자리';        col = '#16A34A'; }
  else if (goodCo && !goodTime) { label = '좋은 회사 · 나쁜 타이밍'; desc = '좋은 종목, 진입은 눌림 대기'; col = '#D97706'; }
  else if (!goodCo && goodTime) { label = '약한 회사 · 단타 구간';   desc = '타이밍만 좋음 — 짧게';        col = '#2563EB'; }
  else                          { label = '약한 회사 · 나쁜 타이밍'; desc = '관심 보류 권장';             col = '#DC2626'; }
  const op = (on) => on ? 0.16 : 0.05;
  host.innerHTML = `
  <svg viewBox="0 0 ${PW} ${PH}" style="width:100%;height:auto;display:block;">
    <rect x="${tx}" y="${P}" width="${P + plotW - tx}" height="${ty - P}" fill="#16A34A" opacity="${op(goodCo && goodTime)}"/>
    <rect x="${P}" y="${P}" width="${tx - P}" height="${ty - P}" fill="#D97706" opacity="${op(goodCo && !goodTime)}"/>
    <rect x="${tx}" y="${ty}" width="${P + plotW - tx}" height="${P + plotH - ty}" fill="#2563EB" opacity="${op(!goodCo && goodTime)}"/>
    <rect x="${P}" y="${ty}" width="${tx - P}" height="${P + plotH - ty}" fill="#DC2626" opacity="${op(!goodCo && !goodTime)}"/>
    <line x1="${tx}" y1="${P}" x2="${tx}" y2="${P + plotH}" stroke="var(--border)" stroke-width="1" stroke-dasharray="3 3"/>
    <line x1="${P}" y1="${ty}" x2="${P + plotW}" y2="${ty}" stroke="var(--border)" stroke-width="1" stroke-dasharray="3 3"/>
    <rect x="${P}" y="${P}" width="${plotW}" height="${plotH}" fill="none" stroke="var(--border)" stroke-width="1"/>
    <circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="6" fill="${col}" stroke="#fff" stroke-width="2"/>
  </svg>
  <div style="display:flex;justify-content:space-between;font-size:9px;color:var(--text-tertiary);margin-top:2px;">
    <span>← 진입 타이밍 나쁨</span><span>좋음 →</span>
  </div>
  <div style="margin-top:8px;font-size:13px;font-weight:700;color:${col};">${esc(label)}</div>
  <div style="font-size:11px;color:var(--text-secondary);margin-top:1px;">${esc(desc)} · 종합 ${Math.round(Y)} / 진입 ${Math.round(X)}</div>
  `;
}


// 변동성 체제 독립 배지 (진입 라벨과 분리)
function _volRegimeBadge(stock) {
  const vr = stock.EntryPlan && stock.EntryPlan.vol_regime;
  if (!vr || vr === 'NORMAL') return '';
  const atrPct = (stock.EntryPlan && stock.EntryPlan.atr_pct != null) ? stock.EntryPlan.atr_pct : null;
  const atrTip = atrPct != null ? `일평균 변동폭 ${atrPct.toFixed(1)}%` : '';
  if (vr === 'HIGH')
    return `<span class="vol-regime-badge vol-high" title="${esc(atrTip)}">고변동</span>`;
  if (vr === 'LOW')
    return `<span class="vol-regime-badge vol-low" title="${esc(atrTip)}">저변동</span>`;
  return '';
}

function _entryLight(stock) {
  if (!stock || !stock.EntryStatus) return '';
  const st = stock.EntryStatus;
  const ico = _ENTRY_ICON[st] || '⚪';
  const _disc = (stock.EntryPlan && stock.EntryPlan.entry_discount != null) ? stock.EntryPlan.entry_discount : null;
  const _atrPct = (stock.EntryPlan && stock.EntryPlan.atr_pct != null) ? stock.EntryPlan.atr_pct : null;
  const _asOf = (stock.EntryPlan && stock.EntryPlan.as_of_ts != null) ? stock.EntryPlan.as_of_ts : null;
  const _ddPct = (stock.EntryPlan && stock.EntryPlan.drawdown_pct != null) ? stock.EntryPlan.drawdown_pct : null;
  const _headline = (stock.EntryPlan && stock.EntryPlan.headline_action) ? stock.EntryPlan.headline_action : null;
  const _mddCur = (stock.EntryPlan && stock.EntryPlan.mdd_current != null) ? stock.EntryPlan.mdd_current : null;
  const _mddRisk = (stock.EntryPlan && stock.EntryPlan.mdd_risk) ? stock.EntryPlan.mdd_risk : null;
  const _volRegime = (stock.EntryPlan && stock.EntryPlan.vol_regime) ? stock.EntryPlan.vol_regime : null;
  const _vel5d = (stock.EntryPlan && stock.EntryPlan.dd_velocity_5d != null) ? stock.EntryPlan.dd_velocity_5d : null;
  const lbl = _entryLabel(st, _disc, _atrPct, _asOf, _ddPct, _headline, _mddCur, _mddRisk, _volRegime, _vel5d);
  const cls = _ENTRY_COLOR[st] || 'neutral';
  const phr = stock.EntryPhrase || '';
  const sc  = stock.EntryScore != null ? `진입 타이밍 ${stock.EntryScore}/100` : '';
  let tip = phr ? `${phr}${sc ? ' (' + sc + ')' : ''}` : sc;
  // P4-P8 리치 툴팁: 드로다운 메트릭스
  const ep = stock.EntryPlan || {};
  const _tipParts = [];
  if (ep.underwater_days > 0) _tipParts.push(`수면하 ${ep.underwater_days}일`);
  if (ep.calmar_ratio != null && ep.calmar_ratio !== 0) _tipParts.push(`Calmar ${ep.calmar_ratio}`);
  if (ep.cvar_95 != null && ep.cvar_95 !== 0) _tipParts.push(`CVaR95 ${ep.cvar_95.toFixed(1)}% (100만원→${Math.round(1000000*(1+ep.cvar_95/100)).toLocaleString()}원)`);
  if (ep.downside_beta != null) _tipParts.push(`하방β ${ep.downside_beta}`);
  if (_tipParts.length) tip += ' | ' + _tipParts.join(' · ');
  let aqBadge = '';
  if (stock.AQ_Verdict || stock.EntryScore_aq != null) {
    const vc = stock.AQ_VerdictCode;
    const col = vc === 'BUY' ? '#16A34A' : vc === 'ACCUMULATE' ? '#F59E0B' : vc === 'AVOID' ? '#DC2626' : '#6b7280';
    const reg = stock.AQ_Regime ? ` · ${stock.AQ_Regime}` : '';
    const aqSc = stock.EntryScore_aq != null ? ` AQ${Math.round(stock.EntryScore_aq)}` : '';
    tip += ` | AgentQuant: ${stock.AQ_Verdict || '—'}${reg}${aqSc}`;
    aqBadge = `<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:${col};margin-left:2px;vertical-align:middle;" title="${esc(stock.AQ_Verdict||'')}"></span>`;
  }
  const volBadge = _volRegimeBadge(stock);
  return `<span class="entry-badge entry-${cls}" title="${esc(tip)}">${ico}${lbl ? `<span class="entry-badge-label">${esc(lbl)}</span>` : ''}${aqBadge}</span>${volBadge}`;
}

function _renderSignalHtml(signal, stock) {
  const { base, tags } = _splitSignal(signal);
  const tr = _trKo(base || '—');
  // 종목 퀄리티 축: STORY_STOCK 버킷은 "스토리" 칩, 그 외는 TotalScore 파생 등급.
  // 등급/칩을 못 만들면 기존 시그널 라벨로 폴백.
  let qualityHtml;
  if (stock && stock.OneLinerTag === 'STORY_STOCK') {
    qualityHtml = `<span class="story-chip" title="스토리 종목 — 등급 척도 비적용">스토리</span>`;
  } else {
    const g = stock ? _stockGrade(stock.TotalScore) : null;
    qualityHtml = g
      ? `<span class="grade-badge grade-${g}" title="종합점수 ${Math.round(Number(stock.TotalScore))} 기준 등급">${g}</span>`
      : `<span class="signal-badge" style="color:${signalColor(base)};background:${signalBg(base)}">${esc(tr)}</span>`;
  }
  // 태그([BREAKOUT],[VOL] 등)는 핵심 이유 컬럼과 중복 → 등급+진입만 표시
  return `<div class="signal-row">${_entryLight(stock)}${qualityHtml}</div>`;
}

function fmt(v, digits = 0) {
  if (v === null || v === undefined) return '—';
  return Number(v).toFixed(digits);
}

function fmtPrice(v) {
  if (!v) return '—';
  const n = Number(v);
  if (n >= 1000) return n.toLocaleString('ko-KR', { maximumFractionDigits: 0 });
  return n.toFixed(2);
}

function _fmtAvgVol(v) {
  if (v == null || !isFinite(v) || v <= 0) return '—';
  const n = Number(v);
  if (currentMarket === 'KR') {
    if (n >= 1e8) return (n / 1e8).toFixed(1) + '억주';
    if (n >= 1e4) return Math.round(n / 1e4).toLocaleString('ko-KR') + '만주';
    return Math.round(n).toLocaleString('ko-KR') + '주';
  }
  if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return Math.round(n).toLocaleString('en-US');
}

function _fmtMarketCap(v) {
  if (v == null || !isFinite(v) || v <= 0) return '—';
  const n = Number(v);
  if (currentMarket === 'KR') {
    if (n >= 1e12) return (n / 1e12).toFixed(1) + '조';
    if (n >= 1e8)  return Math.round(n / 1e8).toLocaleString('ko-KR') + '억';
    return Math.round(n).toLocaleString('ko-KR');
  }
  if (n >= 1e12) return '$' + (n / 1e12).toFixed(2) + 'T';
  if (n >= 1e9)  return '$' + (n / 1e9).toFixed(2)  + 'B';
  if (n >= 1e6)  return '$' + (n / 1e6).toFixed(1)  + 'M';
  return '$' + Math.round(n).toLocaleString('en-US');
}

// ── 마켓/전략 변경 (scanner.html select) ─────────────────────────────────

function _setSegActive(groupId, val) {
  document.querySelectorAll(`#${groupId} .btn-seg`).forEach(b => {
    b.classList.toggle('active', b.dataset.val === val);
  });
}

function onMarketChange(val) {
  if (currentMarket === val) {
    _setSegActive('market-btn-group', val);
    return;
  }
  currentMarket = val;
  currentSector = '';
  allStocks     = [];
  _activeIndex  = 'all';      // 지수 보기는 미국 전용 — 마켓 전환 시 전체로 리셋
  _syncIndexBarUI();
  _setSegActive('market-btn-group', val);
  loadSectors();
  runScan().then(() => {
    loadWatchlist();
    if (typeof _loadMacroStrip === 'function') _loadMacroStrip(val);
  });
  loadScoreEval();
}

// 중첩 키 접근 ("Scores.BALANCED" → obj.Scores?.BALANCED)
function _getByPath(obj, path) {
  if (!obj || !path) return undefined;
  return path.split('.').reduce((o, k) => (o == null ? o : o[k]), obj);
}

// ── 스캐너 페이지 ────────────────────────────────────────────────────────

async function loadSectors() {
  try {
    const res  = await fetch(`/api/sectors?market=${currentMarket}`);
    const data = await res.json();
    _cachedGroups  = data.groups  || {};
    _cachedSectors = data.sectors || {};
    renderSectorList(_cachedGroups, _cachedSectors);
  } catch (e) {
    console.error('loadSectors 실패:', e);
  }
}

function renderSectorList(groups, sectors) {
  const list = document.getElementById('filter-list');
  if (!list) return;

  // 전체 스캔 버튼
  const allActive = currentSector === '' ? ' active' : '';
  let html = `<button class="sector-btn-all${allActive}" onclick="selectSector(this,'')">전체 스캔</button>`;

  // 카테고리 + 하위 섹터 가나다/알파벳 정렬 (ko 로케일)
  const collator = new Intl.Collator('ko', { numeric: true, sensitivity: 'base' });
  const sortedGroups = Object.entries(groups)
    .map(([cat, subs]) => [cat, [...subs].sort((a, b) => collator.compare(a, b))])
    .sort((a, b) => collator.compare(a[0], b[0]));

  // 카테고리 그룹 아코디언 (첫 번째 그룹만 자동 열림)
  sortedGroups.forEach(([cat, subsectors], idx) => {
    const isOpen   = idx === 0;
    const open     = isOpen ? ' open' : '';
    const chevOpen = isOpen ? ' open' : '';

    html += `
<div class="sector-group">
  <button class="sector-group-header" onclick="toggleGroup(this)">
    <span class="sector-group-label">${esc(cat)}</span>
    <svg class="sector-group-chevron${chevOpen}" width="12" height="12" viewBox="0 0 24 24"
         fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="6 9 12 15 18 9"/>
    </svg>
  </button>
  <div class="sector-group-body${open}">`;

    subsectors.forEach(sub => {
      const active = currentSector === sub ? ' active' : '';
      html += `<button class="sector-btn${active}" onclick="selectSector(this,'${esc(sub)}')">
  <span class="sector-btn-label">${esc(sub)}</span>
</button>`;
    });

    html += `</div></div>`;
  });

  list.innerHTML = html;
}

function _searchBaseStocks() {
  return _scanStocks.length ? _scanStocks : allStocks;
}

function toggleGroup(headerBtn) {
  const body    = headerBtn.nextElementSibling;
  const chevron = headerBtn.querySelector('.sector-group-chevron');
  const isOpen  = body.classList.contains('open');
  body.classList.toggle('open', !isOpen);
  chevron.classList.toggle('open', !isOpen);
}

function selectSector(btn, sector) {
  currentSector = sector;
  // 모든 섹터 버튼 비활성화
  document.querySelectorAll('#filter-list .sector-btn, #filter-list .sector-btn-all')
    .forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  setText('stat-sector', sector || '전체');

  const inp = document.getElementById('search-input');
  if (inp) inp.value = '';

  runScan();
}

// 처음 접속 시엔 서버가 아직 준비 안 됐을 수 있어 잠시 후 자동 재시도.
// 콜드 스타트 직후 fetch 실패를 "서버 접속안됨" 처럼 보이지 않도록 부드럽게 처리.
let _runScanAttempt = 0;
const _RUN_SCAN_MAX_RETRY = 4;            // 총 시도 5회 (초기 + 재시도 4)
const _RUN_SCAN_BACKOFF_MS = [2000, 4000, 8000, 12000];
// 워밍업 응답(X-Warming-In-Progress)을 받을 때마다 재시도 — 무한 폴링을 막기 위해 캡.
let _warmingRetries = 0;
const _WARMING_MAX_RETRY = 12;            // 12회 × 5초 = 약 60초
// 섹터 변경 race condition 가드: 늦게 도착한 stale 요청이 최신 요청 덮어쓰지 않게.
let _scanToken = 0;
let _lastAutoScanTs = 0;

async function runScan() {
  const btn = document.getElementById('btn-scan');
  if (btn) { btn.disabled = true; }

  setStatHTML('stat-total',  '…<span class="unit">개</span>');
  setStatHTML('stat-strong', '…<span class="unit">개</span>');
  showScanLoading();

  const myToken = ++_scanToken;
  const reqSector = currentSector;
  const reqMarket = currentMarket;

  // localStorage 즉시 표시 — 서버 워밍 대기 없이 이전 결과를 먼저 렌더링
  const _lsCacheKey = `_scan_${reqMarket}_${currentStrategy}_${reqSector}`;
  try {
    const _lsRaw = localStorage.getItem(_lsCacheKey);
    if (_lsRaw && allStocks.length === 0) {
      const _lsData = JSON.parse(_lsRaw);
      if (Array.isArray(_lsData) && _lsData.length > 0) {
        allStocks = _lsData;
        _scanStocks = allStocks;
        _refreshFilteredView();
      }
    }
  } catch {}

  try {
    const p = new URLSearchParams({ market: reqMarket, strategy: currentStrategy });
    if (reqSector) p.set('sector', reqSector);

    const res    = await fetch(`/api/scan?${p}`);
    // 이미 더 새로운 요청이 떴으면 이 결과는 폐기
    if (myToken !== _scanToken) return;
    // 시세 캐시 신선도 캡처 (지수바 메타 표시용)
    const _age = res.headers.get('X-Cache-Age-Min');
    _scanCacheAgeMin = _age != null && _age !== '' ? Number(_age) : null;
    // body 를 한 번만 읽고 직접 파싱 — res.json() 실패 후 res.text() 호출 시
    // "body stream already read" 로 raw 가 비어 진짜 원인(NaN/Infinity 등)을 가린다.
    const bodyText = await res.text().catch(() => '');
    let payload;
    try {
      payload = JSON.parse(bodyText);
    } catch (parseErr) {
      const sample = (bodyText || '').slice(0, 160);
      throw new Error(`JSON parse failed (HTTP ${res.status}): ${parseErr.message} | body[0:160]=${sample}`);
    }
    if (!res.ok) {
      const msg = payload && typeof payload === 'object' ? payload.error : '';
      throw new Error(msg || `HTTP ${res.status}`);
    }
    const stocks = payload;
    allStocks    = Array.isArray(stocks) ? stocks : [];
    _scanStocks  = allStocks;
    const visibleTickers = new Set(allStocks.map(s => s.Ticker));
    _compareSet = new Set([..._compareSet].filter(ticker => visibleTickers.has(ticker)));
    // 서버가 워밍 중이고 결과가 없으면 자동 재시도 — 단, 영구 루프 방지를 위해 캡.
    if (allStocks.length === 0 && res.headers.get('X-Warming-In-Progress') === 'true') {
      if (_warmingRetries < _WARMING_MAX_RETRY) {
        _warmingRetries += 1;
        setStockListMsg(`데이터 준비 중… 자동으로 불러옵니다 (${_warmingRetries}/${_WARMING_MAX_RETRY})`);
        setTimeout(() => { if (!document.hidden) runScan(); }, 5000);
      } else {
        // 캡 도달 — 명시적 실패 안내, 카운터 리셋(사용자가 직접 다시 시도하면 재개).
        setStockListMsg('서버 준비 지연 중임. 잠시 후 새로고침 ㄱㄱ');
        _warmingRetries = 0;
      }
      return;
    }
    _runScanAttempt = 0;  // 성공 시 카운터 리셋
    _warmingRetries = 0;
    // localStorage에 저장 — 다음 로드 시 서버 워밍 전 즉시 표시용
    try { localStorage.setItem(_lsCacheKey, JSON.stringify(allStocks)); } catch {}
    _refreshFilteredView();
  } catch (e) {
    console.error('runScan 실패:', e);
    // 콜드 스타트 / 네트워크 흔들림 — 백오프하며 자동 재시도.
    if (_runScanAttempt < _RUN_SCAN_MAX_RETRY) {
      const delay = _RUN_SCAN_BACKOFF_MS[_runScanAttempt] || 12000;
      _runScanAttempt += 1;
      const secs = Math.round(delay / 1000);
      setStockListMsg(`서버 준비 중… ${secs}초 후 자동 재시도 (${_runScanAttempt}/${_RUN_SCAN_MAX_RETRY})`);
      setTimeout(() => { if (!document.hidden) runScan(); }, delay);
    } else {
      // 재시도 다 소진 — 그제야 명시적 실패 안내.
      setStockListMsg('서버 연결 실패함. 새로고침하거나 서버 상태 확인 ㄱㄱ');
      _runScanAttempt = 0;
    }
  } finally {
    // stale 요청이 최신 요청의 로딩 UI를 끄지 못하게 가드
    if (myToken === _scanToken) {
      stopScanLoading();
      if (btn) btn.disabled = false;
    }
  }
}

// 퀵필터 적용 (다중 선택 AND)
function _matchesFilter(s, f) {
  switch (f) {
    case 'watchlist':   return _watchlist.has(s.Ticker);
    case 'entry_green': return s.EntryStatus === 'STRONG' || s.EntryStatus === 'GREEN';
    case 'strong':      { const g = _stockGrade(s.TotalScore); return g === 'S' || g === 'A'; }
    case 'rs_leader':   return (s.RSRating ?? 0) >= 80;
    case 'breakout':    return typeof s.Signal === 'string' && /BREAKOUT|PIVOT/.test(s.Signal);
    case 'eps_accel':   return !!s.EPSAcceleration;
    case 'vol_surge':   return (s.VolRatio ?? 0) >= 2.0;
    case 'near_high':   return !!s.NearHighPass;
    case 'pullback':    return s.RSI != null && s.RSI <= 40;
    case 'greedzone':   return !!s.GreedZone;
    case 'bf_buy':      return (s.BFScore ?? 0) >= 25;            // 저점매수 후보 (25+: 관심, 40+: 적극, 60+: 강력)
    case 'bottleneck':  return (s.BottleneckScore ?? 0) >= 60;   // 공급망 병목 후보(상류 희소층 근접)
    case 'bottleneck_entry': return s.BottleneckEntryPass === true;  // 병목 ∩ 진입타이밍(폭등꼭대기·과매수 제외)
    case 'score_surge': return (s.ScoreDelta ?? -Infinity) >= 3;   // 어제 대비 점수 +3 이상 급등
    case 'new_entry':   return !!s.IsNew;                          // 기준일 이후 새로 진입
    case 'laggard':     return (s.RSRating ?? 99) < 40 || _signalTier(s.Signal) === 'sell';
    default: return true;
  }
}

function _applyQuickFilter(stocks) {
  if (!_activeFilters.size) return stocks;
  return stocks.filter(s => [..._activeFilters].every(f => _matchesFilter(s, f)));
}

// 지수별 보기 필터 — all=전체, OTHER=4개 지수 미편입, 그 외=해당 지수 편입 종목
function _applyIndexFilter(stocks) {
  if (_activeIndex === 'all') return stocks;
  if (_activeIndex === 'OTHER') return stocks.filter(s => !(s.Indices && s.Indices.length));
  return stocks.filter(s => Array.isArray(s.Indices) && s.Indices.includes(_activeIndex));
}

// 표가 실제로 보여주는 것과 동일한 필터 결과(검색 → 퀵필터 → 원라이너 버킷)
function _scopedStocks() {
  let scoped = _applySearchFilter(_searchBaseStocks());
  scoped = _applyIndexFilter(scoped);
  scoped = _applyQuickFilter(scoped);
  if (_oneLinerFilter) {
    scoped = scoped.filter(s => (s.OneLinerTag || '') === _oneLinerFilter);
  }
  return scoped;
}

function _refreshFilteredView() {
  updateCompareActions();
  renderStockTable(_applySearchFilter(_searchBaseStocks()));
}

function _applySearchFilter(stocks) {
  const inp = document.getElementById('search-input');
  const q = inp ? inp.value.trim().toLowerCase() : '';
  if (!q) return stocks;
  return stocks.filter(s =>
    (s.Ticker || '').toLowerCase().includes(q) ||
    (s.Name   || '').toLowerCase().includes(q) ||
    (s.Sector || '').toLowerCase().includes(q)
  );
}

function _renderOneLinerFilterChip(baseStocks) {
  const chip = document.getElementById('oneliner-filter-chip');
  if (!chip) return;

  if (!_oneLinerFilter) {
    chip.hidden = true;
    chip.innerHTML = '';
    return;
  }

  const count = baseStocks.filter(s => (s.OneLinerTag || '') === _oneLinerFilter).length;
  chip.hidden = false;
  chip.innerHTML = `
    <span class="oneliner-filter-chip-label">버킷: ${esc(_oneLinerFilter)} (${count})</span>
    <button type="button" class="oneliner-filter-chip-clear" aria-label="원라이너 버킷 필터 해제">&times;</button>
  `;
}

function applyOneLinerFilter(tag) {
  _oneLinerFilter = tag || null;
  _refreshFilteredView();
}

function clearOneLinerFilter() {
  if (!_oneLinerFilter) return;
  _oneLinerFilter = null;
  _refreshFilteredView();
}

// Moat 카테고리 → 한국어 메타 (디테일 페이지용)
const _MOAT_CAT_META = {
  INTANGIBLE:      { name: '브랜드·무형자산',  icon: '💎', desc: '브랜드·특허·면허·규제 라이선스 같은 무형자산이 가격결정력을 지킵니다. 신규 진입자가 흉내내려면 수십 년의 신뢰 자본이나 임상·인허가가 필요합니다.' },
  SWITCHING:       { name: '전환비용 락인',    icon: '🔒', desc: '고객이 경쟁사 제품으로 옮기는 데 드는 비용·시간·위험이 커서, 한 번 도입되면 매출이 장기간 유지됩니다. ERP/보안/결제 같은 인프라성 SaaS에서 강력합니다.' },
  NETWORK:         { name: '네트워크 효과',    icon: '🌐', desc: '사용자가 늘수록 서비스 가치가 비선형으로 증가해, 한쪽으로 쏠리는 양면시장 효과(2-sided)가 자연 독점을 만듭니다.' },
  COST:            { name: '구조적 원가우위',  icon: '⚙️', desc: '규모·입지·통합·기술·계약 구조 등으로 단위원가 자체가 낮아, 경쟁사가 같은 가격을 부르면 적자가 납니다.' },
  EFFICIENT_SCALE: { name: '효율적 규모',      icon: '📐', desc: '시장 자체가 제한돼 한두 사업자만 흑자를 낼 수 있는 구조입니다. 신규 진입은 모두의 마진을 떨어뜨려 비합리적입니다.' },
  NONE:            { name: '뚜렷한 해자 없음', icon: '⚠',  desc: '구조적 진입장벽이 약합니다. 가격·실행력·자본력 같은 비해자 요소로 경쟁해야 합니다.' },
};

// 데이터 신뢰도 신호등 헬퍼 — verified/estimated/unverified
const _CONF_META = {
  verified:   { cls: 'verified',   title: '검증완료 — 공시 1차 출처(10-K·DEF 14A·13F·사업보고서) 직접 인용' },
  estimated:  { cls: 'estimated',  title: '추정치 — IR 자료·분기지연·재분류 등으로 차이 가능' },
  unverified: { cls: 'unverified', title: '미확인 — 출처 부족, 참고용' }
};
function _applyConfDot(elId, confidence) {
  const el = document.getElementById(elId);
  if (!el) return;
  const key = String(confidence || 'estimated').toLowerCase();
  const meta = _CONF_META[key] || _CONF_META.estimated;
  el.className = `dp-conf-dot ${meta.cls}`;
  el.title = meta.title;
}

// 디테일 페이지 — 핵심 해자 섹션 렌더
function _renderMoatDetail(d) {
  const card = document.getElementById('dp-moat-card');
  if (!card) return;
  if (!d || !d.Moat) {
    card.style.display = 'none';
    return;
  }
  const cat = String(d.MoatCategory || 'NONE').toUpperCase();
  const data = d.MoatData || {};
  const meta = _MOAT_CAT_META[cat] || _MOAT_CAT_META.NONE;
  const label = data.label || d.Moat || '';
  const detail = data.detail || '';
  const source = data.source || '';
  const sourceTxt = source === 'curated' ? '큐레이션 사전' : source === 'sector_rule' ? '섹터 룰 기반 추정' : source === 'llm' ? 'LLM 분석' : '추정';

  document.getElementById('dp-moat-icon').textContent = meta.icon;
  document.getElementById('dp-moat-cat').textContent = meta.name;
  document.getElementById('dp-moat-cat').className = `dp-moat-cat moat-${cat}`;
  document.getElementById('dp-moat-label').textContent = label;
  document.getElementById('dp-moat-detail').textContent = detail || meta.desc;
  document.getElementById('dp-moat-source').textContent = sourceTxt;
  _applyConfDot('dp-moat-conf', data.confidence);

  // 5축 해자 바 차트
  var chartEl = document.getElementById('dp-moat-axes');
  if (chartEl) {
    var sc = data.scores;
    if (sc && typeof sc === 'object') {
      var axes = [
        { key: 'switching_costs',    label: '전환비용' },
        { key: 'network_effects',    label: '네트워크' },
        { key: 'ip_efficiency',      label: 'IP·R&D' },
        { key: 'cost_advantage',     label: '비용우위' },
        { key: 'roic_sustainability', label: 'ROIC' },
      ];
      var total = 0;
      var html = '';
      for (var i = 0; i < axes.length; i++) {
        var v = sc[axes[i].key] || 0;
        total += v;
        var pct = (v / 4 * 100).toFixed(0);
        var cls = v >= 3 ? 'moat-ax-high' : v >= 2 ? 'moat-ax-mid' : 'moat-ax-low';
        html += '<div class="moat-ax-row">'
          + '<span class="moat-ax-label">' + axes[i].label + '</span>'
          + '<div class="moat-ax-track"><div class="moat-ax-fill ' + cls + '" style="width:' + pct + '%"></div></div>'
          + '<span class="moat-ax-val">' + v + '</span>'
          + '</div>';
      }
      html += '<div class="moat-ax-total">가산점 <strong>+' + (total / 2).toFixed(1) + '</strong> / 10</div>';
      chartEl.innerHTML = html;
      chartEl.style.display = '';
    } else {
      chartEl.innerHTML = '';
      chartEl.style.display = 'none';
    }
  }

  card.style.display = '';
}

// === 경쟁사 비교 카드 ===
// US 종목은 USD 원시값이라 한국식 억/조 단위를 붙이면 단위가 어긋난다.
// 티커에 .KS/.KQ가 있으면 KRW(원), 그 외에는 USD로 처리.
function _peersIsKR(ticker) {
  const t = String(ticker || '');
  return /\.(KS|KQ)$/i.test(t) || /^\d{6}$/.test(t);
}
function _peersMcap(v, ticker) {
  if (v == null || isNaN(v) || v <= 0) return '—';
  const n = Number(v);
  if (_peersIsKR(ticker)) {
    if (n >= 1e12) return (n / 1e12).toFixed(1) + '조원';
    if (n >= 1e8)  return (n / 1e8).toFixed(0) + '억원';
    if (n >= 1e4)  return (n / 1e4).toFixed(0) + '만원';
    return String(Math.round(n)) + '원';
  }
  // US: USD 단위로 $T/$B/$M 표기
  if (n >= 1e12) return '$' + (n / 1e12).toFixed(2) + 'T';
  if (n >= 1e9)  return '$' + (n / 1e9).toFixed(2) + 'B';
  if (n >= 1e6)  return '$' + (n / 1e6).toFixed(0) + 'M';
  return '$' + Math.round(n).toLocaleString();
}
function _peersNum(v, digits) {
  if (v == null || isNaN(v)) return '—';
  return Number(v).toFixed(digits != null ? digits : 1);
}
function _peersPct(v, digits) {
  if (v == null || isNaN(v)) return '—';
  return (Number(v) * 100).toFixed(digits != null ? digits : 1) + '%';
}

function _renderPeersCard(payload) {
  const card = document.getElementById('dp-peers-card');
  if (!card) return;
  if (!payload || !payload.ok) {
    card.style.display = 'none';
    return;
  }
  const tbody = document.getElementById('dp-peers-tbody');
  const sectorEl = document.getElementById('dp-peers-sector');
  const countEl = document.getElementById('dp-peers-count');
  if (!tbody || !sectorEl || !countEl) return;

  const sector = payload.industry || payload.sector || '';
  sectorEl.textContent = sector || '—';
  const peers = Array.isArray(payload.peers) ? payload.peers : [];
  countEl.textContent = peers.length + '개 비교';

  const all = [];
  if (payload.self) all.push(Object.assign({}, payload.self, { _isSelf: true }));
  for (const p of peers) all.push(Object.assign({}, p, { _isSelf: false }));
  all.sort((a, b) => (Number(b.MarketCap) || 0) - (Number(a.MarketCap) || 0));

  if (!all.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="dp-peers-empty">비교 가능한 동종업체 없음.</td></tr>';
    card.style.display = '';
    return;
  }

  const rows = all.map(r => {
    const cls = r._isSelf ? 'dp-peers-self' : '';
    const mom = r.Mom12M;
    const momCls = mom != null ? (mom >= 0 ? 'dp-peers-num-pos' : 'dp-peers-num-neg') : '';
    const momTxt = mom != null ? ((mom >= 0 ? '+' : '') + (mom * 100).toFixed(1) + '%') : '—';
    const score = r.TotalScore != null ? Math.round(r.TotalScore) : '—';
    const nameTxt = r.Name && r.Name !== r.Ticker ? r.Name : (r.Ticker || '');
    return `<tr class="${cls}">
      <td class="dp-peers-td-name">
        <span class="dp-peers-name">${esc(nameTxt)}</span>
        <span class="dp-peers-tk">${esc(r.Ticker || '')}</span>
      </td>
      <td>${score}</td>
      <td>${esc(_peersMcap(r.MarketCap, r.Ticker))}</td>
      <td>${esc(_peersNum(r.PER, 1))}</td>
      <td>${esc(_peersNum(r.PBR, 2))}</td>
      <td>${esc(_peersPct(r.ROE, 1))}</td>
      <td>${esc(_peersPct(r.OperatingMargin, 1))}</td>
      <td class="${momCls}">${esc(momTxt)}</td>
    </tr>`;
  }).join('');
  tbody.innerHTML = rows;
  card.style.display = '';
}

async function _loadPeersCard(ticker, market) {
  if (!ticker) return;
  const card = document.getElementById('dp-peers-card');
  if (card) card.style.display = 'none';
  try {
    const url = `/api/peers/${encodeURIComponent(ticker)}?market=${encodeURIComponent(market || 'US')}`;
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) return;
    const payload = await res.json();
    _renderPeersCard(payload);
  } catch (e) {
    console.error('peers card load failed:', e);
  }
}


// ───────── 매출 세그먼트 파이 ─────────
const _SEG_COLORS = [
  '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
  '#06b6d4', '#ec4899', '#84cc16', '#f97316', '#64748b'
];
function _segPolar(cx, cy, r, deg) {
  const rad = (deg - 90) * Math.PI / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}
function _segArcPath(cx, cy, r, startDeg, endDeg) {
  // 풀 원(360°) 처리
  if (Math.abs(endDeg - startDeg) >= 359.999) {
    return `M ${cx - r} ${cy} A ${r} ${r} 0 1 1 ${cx + r} ${cy} A ${r} ${r} 0 1 1 ${cx - r} ${cy} Z`;
  }
  const a = _segPolar(cx, cy, r, startDeg);
  const b = _segPolar(cx, cy, r, endDeg);
  const large = (endDeg - startDeg) > 180 ? 1 : 0;
  return `M ${cx} ${cy} L ${a.x} ${a.y} A ${r} ${r} 0 ${large} 1 ${b.x} ${b.y} Z`;
}
function _renderSegmentsCard(payload) {
  const card = document.getElementById('dp-segments-card');
  if (!card) return;
  if (!payload || !payload.ok) { card.style.display = 'none'; return; }

  const metaEl   = document.getElementById('dp-segments-meta');
  const countEl  = document.getElementById('dp-segments-count');
  const svg      = document.getElementById('dp-segments-svg');
  const legend   = document.getElementById('dp-segments-legend');
  if (!metaEl || !countEl || !svg || !legend) return;

  const pie   = Array.isArray(payload.pie) ? payload.pie.slice() : [];
  const all   = Array.isArray(payload.segments) ? payload.segments : [];
  const fy    = payload.fy || '—';
  const src   = payload.source || '';
  metaEl.textContent  = `${fy}${src ? ' · ' + src : ''}`;
  countEl.textContent = `${all.length}개 부문`;
  _applyConfDot('dp-segments-conf', payload.confidence);

  if (!pie.length) {
    svg.innerHTML = '';
    legend.innerHTML = '<div class="dp-segments-empty">세그먼트 비중 데이터 없음.</div>';
    card.style.display = '';
    return;
  }

  // 합계 100으로 정규화 (의도적 미달/초과 보정)
  const total = pie.reduce((s, p) => s + Number(p.pct || 0), 0);
  const norm = total > 0 ? (100 / total) : 1;

  let acc = 0;
  const slices = pie.map((p, i) => {
    const pct = Number(p.pct || 0) * norm;
    const start = acc;
    const end = acc + (pct * 3.6); // 360 / 100
    acc = end;
    const color = _SEG_COLORS[i % _SEG_COLORS.length];
    const d = _segArcPath(50, 50, 48, start, end);
    return `<path class="dp-seg-slice" d="${d}" fill="${color}" stroke="var(--bg-card)" stroke-width="0.6"><title>${esc(p.name)} ${pct.toFixed(1)}%</title></path>`;
  }).join('');
  svg.innerHTML = slices;

  // 범례 — 원본 세그먼트 모두 표시(음수 포함). 색은 pie 순서 우선, 음수는 회색 처리.
  const pieIdx = new Map();
  pie.forEach((p, i) => pieIdx.set(p.name, i));
  const rows = all.map(s => {
    const v = Number(s.pct || 0);
    const idx = pieIdx.get(s.name);
    const color = (idx != null) ? _SEG_COLORS[idx % _SEG_COLORS.length] : '#94a3b8';
    const negCls = v < 0 ? ' neg' : '';
    const sign = v >= 0 ? '' : '−';
    return `<div class="dp-seg-row">
      <span class="dp-seg-swatch" style="background:${color};"></span>
      <span class="dp-seg-name">${esc(s.name || '—')}</span>
      <span class="dp-seg-pct${negCls}">${sign}${Math.abs(v).toFixed(1)}%</span>
    </div>`;
  }).join('');
  legend.innerHTML = rows;

  card.style.display = '';
}
async function _loadSegmentsCard(ticker) {
  if (!ticker) return;
  const card = document.getElementById('dp-segments-card');
  if (card) card.style.display = 'none';
  try {
    const res = await fetch(`/api/segments/${encodeURIComponent(ticker)}`, { cache: 'no-store' });
    if (!res.ok) return;
    _renderSegmentsCard(await res.json());
  } catch (e) {
    console.error('segments card load failed:', e);
  }
}

// ───────── 매크로 이벤트 스트립 (상단 공통) ─────────
const _MACRO_KIND_LABEL = { fomc:'FOMC', cpi:'CPI', nfp:'고용', bok:'한은', other:'기타' };
function _macroDdayCls(d) {
  if (d <= 0) return 'today';
  if (d <= 3) return 'urgent';
  if (d <= 14) return 'soon';
  return '';
}
function _macroDdayText(d) {
  if (d === 0) return 'D-day';
  if (d > 0)  return `D-${d}`;
  return `D+${-d}`;
}
async function _loadMacroStrip(region) {
  const strip = document.getElementById('macro-events-strip');
  const list  = document.getElementById('macro-events-strip-list');
  if (!strip || !list) return;
  const reg = String(region || 'US').toUpperCase();
  try {
    const res = await fetch(`/api/macro-events?region=${encodeURIComponent(reg)}`, { cache: 'no-store' });
    if (!res.ok) { strip.classList.add('empty'); return; }
    const j = await res.json();
    const evs = (j && Array.isArray(j.events)) ? j.events.slice(0, 10) : [];
    if (!evs.length) { list.innerHTML = ''; strip.classList.add('empty'); return; }
    list.innerHTML = evs.map(e => {
      const kind = String(e.kind || 'other').toLowerCase();
      const label = _MACRO_KIND_LABEL[kind] || _MACRO_KIND_LABEL.other;
      return `<span class="macro-chip" title="${esc(e.name)} · ${esc(e.date)}">
        <span class="macro-chip-dday ${_macroDdayCls(e.dday)}">${_macroDdayText(e.dday)}</span>
        <span class="macro-chip-kind ${kind}">${esc(label)}</span>
        <span>${esc(e.name)}</span>
      </span>`;
    }).join('');
    strip.classList.remove('empty');
  } catch (e) {
    console.error('macro strip load failed:', e);
    strip.classList.add('empty');
  }
}

// ───────── 인사이더 거래 ─────────
const _INS_SIDE_LABEL = {
  buy: '취득', sell: '처분', option: '옵션행사', grant: '부여', other: '기타'
};
function _insFmtVal(v) {
  if (v == null || isNaN(v)) return '—';
  const n = Math.abs(Number(v));
  const sign = Number(v) < 0 ? '-' : '';
  if (n >= 1e9) return `${sign}$${(n/1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${sign}$${(n/1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${sign}$${(n/1e3).toFixed(1)}K`;
  return `${sign}$${n.toFixed(0)}`;
}
function _renderInsiderCard(payload) {
  const card = document.getElementById('dp-insider-card');
  if (!card) return;
  if (!payload || !payload.ok) { card.style.display = 'none'; return; }
  const body = document.getElementById('dp-insider-body');
  const buyEl = document.getElementById('dp-insider-buy');
  const sellEl = document.getElementById('dp-insider-sell');
  const cntEl = document.getElementById('dp-insider-count');
  const netEl = document.getElementById('dp-insider-net');
  if (!body) return;

  const sm  = payload.summary || {};
  const txs = Array.isArray(payload.transactions) ? payload.transactions : [];
  if (buyEl)  buyEl.textContent  = _insFmtVal(sm.buy);
  if (sellEl) sellEl.textContent = _insFmtVal(sm.sell);
  if (cntEl)  cntEl.textContent  = String(sm.count || txs.length);
  if (netEl) {
    const net = Number(sm.net || 0);
    netEl.textContent = net > 0 ? `순취득 ${_insFmtVal(net)}` : net < 0 ? `순처분 ${_insFmtVal(-net)}` : '균형';
    netEl.className = 'dp-insider-net ' + (net > 0 ? 'pos' : net < 0 ? 'neg' : 'zero');
  }

  if (!txs.length) {
    body.innerHTML = '<div class="dp-insider-empty">최근 거래 데이터 없음.</div>';
    card.style.display = '';
    return;
  }

  body.innerHTML = txs.map(t => {
    const side = String(t.side || 'other').toLowerCase();
    const label = _INS_SIDE_LABEL[side] || _INS_SIDE_LABEL.other;
    const name = esc(t.name || '—');
    const role = t.role ? `<div class="dp-ins-name-role">${esc(t.role)}</div>` : '';
    return `<div class="dp-ins-row">
      <span class="dp-ins-date">${esc(t.date)}</span>
      <span class="dp-ins-side ${side}">${esc(label)}</span>
      <div class="dp-ins-name"><div class="dp-ins-name-main">${name}</div>${role}</div>
      <span class="dp-ins-val">${_insFmtVal(t.value)}</span>
    </div>`;
  }).join('');
  card.style.display = '';
}
async function _loadInsiderCard(ticker) {
  if (!ticker) return;
  const card = document.getElementById('dp-insider-card');
  if (card) card.style.display = 'none';
  try {
    const res = await fetch(`/api/insider/${encodeURIComponent(ticker)}`, { cache: 'no-store' });
    if (!res.ok) return;
    _renderInsiderCard(await res.json());
  } catch (e) {
    console.error('insider card load failed:', e);
  }
}

// ───────── 이벤트 캘린더 ─────────
const _EVT_KIND_LABEL = {
  earnings: '실적', dividend: '배당', fomc: 'FOMC',
  cpi: 'CPI', nfp: '고용', bok: '한은', other: '기타'
};
function _evtDdayCls(dday) {
  if (dday <= 0) return 'today';
  if (dday <= 3) return 'urgent';
  if (dday <= 14) return 'soon';
  return '';
}
function _evtDdayText(dday) {
  if (dday === 0) return 'D-day';
  if (dday > 0)  return `D-${dday}`;
  return `D+${-dday}`;
}
function _renderEventsCard(payload) {
  const card = document.getElementById('dp-events-card');
  if (!card) return;
  const body = document.getElementById('dp-events-body');
  const cnt  = document.getElementById('dp-events-count');
  if (!body || !cnt) return;

  const evs = (payload && Array.isArray(payload.events)) ? payload.events : [];
  if (!payload || !payload.ok || !evs.length) {
    body.innerHTML = '<div class="dp-events-empty">예정된 이벤트 없음.</div>';
    cnt.textContent = '0';
    // 매크로 단독으로도 카드를 보여줄 가치는 있지만, 텅 비면 숨김
    card.style.display = 'none';
    return;
  }
  cnt.textContent = `${evs.length}건`;

  body.innerHTML = evs.map(e => {
    const kind = String(e.kind || 'other').toLowerCase();
    const label = _EVT_KIND_LABEL[kind] || _EVT_KIND_LABEL.other;
    return `<div class="dp-evt-row">
      <span class="dp-evt-dday ${_evtDdayCls(e.dday)}">${_evtDdayText(e.dday)}</span>
      <span class="dp-evt-kind ${kind}">${esc(label)}</span>
      <span class="dp-evt-name">${esc(e.name)}</span>
      <span class="dp-evt-date">${esc(e.date)}</span>
    </div>`;
  }).join('');
  card.style.display = '';
}
async function _loadEventsCard(ticker) {
  if (!ticker) return;
  const card = document.getElementById('dp-events-card');
  if (card) card.style.display = 'none';
  try {
    const res = await fetch(`/api/events/${encodeURIComponent(ticker)}`, { cache: 'no-store' });
    if (!res.ok) return;
    _renderEventsCard(await res.json());
  } catch (e) {
    console.error('events card load failed:', e);
  }
}

// ───────── 오너십 지도 ─────────
const _OWN_KIND_COLORS = {
  insider: '#8b5cf6',   // 보라 — 내부자/최대주주
  foreign: '#06b6d4',   // 시안 — 외국인
  inst:    '#3b82f6',   // 파랑 — 기관
  retail:  '#94a3b8',   // 슬레이트 — 개인
  other:   '#64748b'
};
const _OWN_KIND_LABEL = {
  insider: '내부자',
  foreign: '외국인',
  inst:    '기관',
  retail:  '개인',
  other:   '기타'
};
function _renderOwnershipCard(payload) {
  const card = document.getElementById('dp-ownership-card');
  if (!card) return;
  if (!payload || !payload.ok) { card.style.display = 'none'; return; }

  const metaEl   = document.getElementById('dp-ownership-meta');
  const asofEl   = document.getElementById('dp-ownership-asof');
  const barEl    = document.getElementById('dp-ownership-bar');
  const legEl    = document.getElementById('dp-ownership-legend');
  const topWrap  = document.getElementById('dp-ownership-top-wrap');
  const topEl    = document.getElementById('dp-ownership-top');
  if (!barEl || !legEl) return;

  const bd = Array.isArray(payload.breakdown) ? payload.breakdown : [];
  const top = Array.isArray(payload.top) ? payload.top : [];
  const asof = payload.asof || '';
  const src  = payload.source || '';
  if (metaEl) metaEl.textContent = src || '—';
  if (asofEl) asofEl.textContent = asof || '—';
  _applyConfDot('dp-ownership-conf', payload.confidence);

  if (!bd.length) {
    barEl.innerHTML = '';
    legEl.innerHTML = '<div class="dp-ownership-empty">지분 데이터 없음.</div>';
    if (topWrap) topWrap.style.display = 'none';
    card.style.display = '';
    return;
  }

  // 정규화 (합계 100 기준)
  const total = bd.reduce((s, b) => s + Math.max(0, Number(b.pct || 0)), 0);
  const norm = total > 0 ? (100 / total) : 1;

  const segs = bd.map(b => {
    const kind = String(b.kind || 'other').toLowerCase();
    const color = _OWN_KIND_COLORS[kind] || _OWN_KIND_COLORS.other;
    const pct  = Math.max(0, Number(b.pct || 0)) * norm;
    return { name: b.name || _OWN_KIND_LABEL[kind] || '—', kind, color, pct, raw: Number(b.pct || 0) };
  });

  barEl.innerHTML = segs.map(s =>
    `<div class="dp-own-seg" style="width:${s.pct.toFixed(2)}%;background:${s.color};" title="${esc(s.name)} ${s.raw.toFixed(1)}%"></div>`
  ).join('');

  legEl.innerHTML = segs.map(s =>
    `<span class="dp-own-leg-item">
       <span class="dp-own-leg-swatch" style="background:${s.color};"></span>
       <span>${esc(s.name)}</span>
       <span class="dp-own-leg-pct">${s.raw.toFixed(1)}%</span>
     </span>`
  ).join('');

  if (topWrap && topEl) {
    if (top.length) {
      topEl.innerHTML = top.map(t => {
        const v = Number(t.pct || 0);
        return `<div class="dp-own-top-row">
          <span class="dp-own-top-name">${esc(t.name || '—')}</span>
          <span class="dp-own-top-pct">${v.toFixed(2)}%</span>
        </div>`;
      }).join('');
      topWrap.style.display = '';
    } else {
      topEl.innerHTML = '';
      topWrap.style.display = 'none';
    }
  }

  card.style.display = '';
}
async function _loadOwnershipCard(ticker) {
  if (!ticker) return;
  const card = document.getElementById('dp-ownership-card');
  if (card) card.style.display = 'none';
  try {
    const res = await fetch(`/api/ownership/${encodeURIComponent(ticker)}`, { cache: 'no-store' });
    if (!res.ok) return;
    _renderOwnershipCard(await res.json());
  } catch (e) {
    console.error('ownership card load failed:', e);
  }
}

// (호환용) 과거 호출부가 남아있을 경우를 대비한 no-op
function _renderMoatBadge(stock) {
  if (!stock || !stock.Moat) return '';
  const cat = String(stock.MoatCategory || 'NONE').toUpperCase();
  const data = stock.MoatData || {};
  const detail = data.detail || '';
  const conf = String(stock.MoatConfidence || data.confidence || 'heuristic').toLowerCase();
  const confIcon = conf === 'verified' ? '✓' : conf === 'heuristic' ? '⚠' : '';
  const confTitle = conf === 'verified' ? 'Morningstar Wide Moat 검증' : conf === 'heuristic' ? '규칙 기반 추정(미검증)' : '';
  const titleTxt = (detail ? `해자: ${stock.Moat} — ${detail}` : `해자: ${stock.Moat}`) + (confTitle ? ` · ${confTitle}` : '');
  const confSpan = confIcon ? ` <span class="moat-conf moat-conf-${conf}" title="${esc(confTitle)}">${confIcon}</span>` : '';
  return `<span class="moat-badge moat-${esc(cat)}" title="${esc(titleTxt)}">🛡 ${esc(stock.Moat)}${confSpan}</span>`;
}

// P11: 섹터 집중도 경고 — 한 섹터 비중 40% 초과 시 배너
function _renderSectorConcentration(stocks) {
  const el = document.getElementById('sector-concentration-banner');
  if (!el) return;
  if (!stocks || stocks.length < 5) { el.style.display = 'none'; return; }
  const counts = {};
  stocks.forEach(s => { const sec = s.Sector || 'Unknown'; counts[sec] = (counts[sec] || 0) + 1; });
  const total = stocks.length;
  let worst = null, worstPct = 0;
  for (const [sec, cnt] of Object.entries(counts)) {
    const pct = cnt / total;
    if (pct > worstPct) { worstPct = pct; worst = sec; }
  }
  if (worstPct > 0.4) {
    const pctStr = (worstPct * 100).toFixed(0);
    el.innerHTML = `<div style="margin:4px 12px;padding:8px 12px;background:#fff3cd;border:1px solid #ffc107;border-radius:8px;font-size:13px;color:#664d03;">⚠️ 분산 부족 — <b>${esc(worst)}</b> 섹터 ${pctStr}% 집중. 포트폴리오 분산 권장</div>`;
    el.style.display = 'block';
  } else {
    el.style.display = 'none';
  }
}

function renderStockTable(stocks) {
  const renderToken = ++_renderToken;
  const tbody = document.getElementById('stock-list');
  if (!tbody) return;
  _currentResults = Array.isArray(stocks) ? stocks : [];

  // 워치리스트 카운트 (전체 기준)
  const wlEl = document.getElementById('chip-watch-count');
  if (wlEl) wlEl.textContent = stocks.filter(s => _watchlist.has(s.Ticker)).length;

  // 지수 보기 카운트 갱신 + 지수/퀵필터 적용
  _updateIndexBar(_currentResults);
  // _updateIndexBar가 _activeIndex를 리셋할 수 있으므로 헤더 가시성을 항상 동기화
  _toggleMidcapCol(_activeIndex === 'SP400');
  const indexScoped   = _applyIndexFilter(_currentResults);
  const quickFiltered = _applyQuickFilter(indexScoped);
  _renderOneLinerFilterChip(quickFiltered);
  const filtered = _oneLinerFilter
    ? quickFiltered.filter(s => (s.OneLinerTag || '') === _oneLinerFilter)
    : quickFiltered;

  const strong = filtered.filter(s => _signalTier(s.Signal) === 'strong').length;
  setStatHTML('stat-total',  `${filtered.length}<span class="unit">개</span>`);
  setStatHTML('stat-strong', `${strong}<span class="unit">개</span>`);
  _renderSectorConcentration(filtered);

  if (filtered.length === 0) {
    const emptyMsg = (_activeFilters.size === 1 && _activeFilters.has('watchlist'))
      ? '워치리스트 비어있음. 표의 ☆ 버튼으로 추가 ㄱㄱ'
      : (_activeIndex !== 'all' && !_activeFilters.size)
        ? '이 지수에 해당하는 종목 없음.'
        : (_activeFilters.size > 0 || _activeIndex !== 'all') ? '필터 조건에 맞는 종목 없음.' : '결과 없음';
    tbody.innerHTML = `<tr><td colspan="${_colCount()}" class="state-msg">${esc(emptyMsg)}</td></tr>`;
    _updateMobileList([], emptyMsg, renderToken);
    return;
  }

  _stockMap = {};
  filtered.forEach(s => { _stockMap[s.Ticker] = s; });
  // 이후 sort 로직에서 사용되는 `stocks` 변수 재할당
  stocks = filtered;

  // 현재 정렬 적용
  let view = stocks;
  if (_sortKey && _sortDir !== 0) {
    view = stocks
      .map((s, idx) => ({ s, k: _getByPath(s, _sortKey), idx }))
      .sort((a, b) => {
        if (typeof a.k === 'string' || typeof b.k === 'string') {
          const sa = a.k == null ? '' : String(a.k);
          const sb = b.k == null ? '' : String(b.k);
          if (!sa && sb) return 1;
          if (sa && !sb) return -1;
          const cmp = sa.localeCompare(sb, 'ko');
          return cmp === 0 ? a.idx - b.idx : _sortDir * cmp;
        }

        const aa = a.k == null ? -Infinity : a.k;
        const bb = b.k == null ? -Infinity : b.k;
        const cmp = aa > bb ? 1 : aa < bb ? -1 : 0;
        return cmp === 0 ? a.idx - b.idx : _sortDir * cmp;
      })
      .map(x => x.s);
  }
  // 보이는 뷰만 렌더링 — 데스크톱/모바일 중복 렌더링 제거 (1400종목 × 2 → × 1)
  // 초기 100개만 렌더링 → "더 보기" 버튼으로 나머지 로드 (DOM 부하 93% 절감)
  const _INITIAL_CAP = 100;
  const capped = view.length > _INITIAL_CAP ? view.slice(0, _INITIAL_CAP) : view;
  const remaining = view.length - capped.length;

  if (window.innerWidth <= 768) {
    tbody.innerHTML = '';
    _updateMobileList(capped, null, renderToken);
    if (remaining > 0) {
      const mEl = document.getElementById('mobile-stock-list');
      if (mEl) {
        const btn = document.createElement('div');
        btn.className = 'load-more-btn';
        btn.innerHTML = `<button onclick="this.parentElement.remove(); _renderAllStocks()">나머지 ${remaining}개 더 보기</button>`;
        mEl.appendChild(btn);
      }
    }
  } else {
    _renderHtmlInBatches(tbody, view.slice(0, _INITIAL_CAP), renderStockRow, 30, 50, renderToken);
    if (remaining > 0) {
      _scheduleDeferredRender(() => {
        if (renderToken !== _renderToken) return;
        const tr = document.createElement('tr');
        tr.innerHTML = `<td colspan="${_colCount()}" class="center" style="padding:12px;">
          <button class="load-more-btn-inner" onclick="this.closest('tr').remove(); _renderAllStocks()">나머지 ${remaining}개 더 보기</button>
        </td>`;
        tbody.appendChild(tr);
      });
    }
  }
  // 전체 뷰 저장 (더 보기 클릭 시 사용)
  window._pendingFullView = remaining > 0 ? view : null;
  window._pendingRenderToken = renderToken;
}

// "더 보기" 클릭 시 나머지 종목 렌더링
function _renderAllStocks() {
  const view = window._pendingFullView;
  const token = window._pendingRenderToken;
  if (!view || token !== _renderToken) return;
  window._pendingFullView = null;
  const _CAP = 100;
  const rest = view.slice(_CAP);
  if (window.innerWidth <= 768) {
    const mEl = document.getElementById('mobile-stock-list');
    if (mEl) _renderHtmlInBatches(mEl, rest, renderMobileCard, 15, 30, token);
  } else {
    const tbody = document.getElementById('stock-list');
    if (tbody) {
      const html = rest.map((item, i) => renderStockRow(item, _CAP + i + 1)).join('');
      // 배치로 분할 삽입
      const frag = document.createElement('tbody');
      frag.innerHTML = html;
      while (frag.firstChild) tbody.appendChild(frag.firstChild);
    }
  }
}

function _deltaBadge(stock) {
  if (stock && stock.IsNew) {
    return `<span class="score-delta new" title="기준일 이후 새로 진입한 종목">NEW</span>`;
  }
  const d = stock ? stock.ScoreDelta : null;
  if (d == null) return '';
  const days = stock.DeltaDays || 1;
  const rd = stock.RankDelta;
  const rdTxt = rd == null ? '' : (rd > 0 ? ` · 순위 ▲${rd}` : rd < 0 ? ` · 순위 ▼${-rd}` : ' · 순위 —');
  if (d > 0.05) {
    return `<span class="score-delta up" title="${days}일 전 대비 점수 +${d.toFixed(1)}${rdTxt}">▲${d.toFixed(1)}</span>`;
  }
  if (d < -0.05) {
    return `<span class="score-delta down" title="${days}일 전 대비 점수 ${d.toFixed(1)}${rdTxt}">▼${Math.abs(d).toFixed(1)}</span>`;
  }
  return `<span class="score-delta flat" title="${days}일 전 대비 변동 없음${rdTxt}">—</span>`;
}

// GreedZone 뱃지 — 한 줄 pill: 🔥 + 5단 도트 게이지 + 점수.
// GreedZone(greedzone.py)은 역추세 경고: 점수↑ = 저점 대비 더 오래·깊게 과열 = 추격매수 위험.
// 강도별 틴트색(40↑경고·70↑고위험) + 도트 개수(20점당 1칸)로 위험도를 한눈에. 라이트·다크 모두 대응.
function _greedBadge(stock) {
  if (!stock || !stock.GreedZone) return '';
  const gz   = stock.GreedZoneScore || 0;
  const tier = gz >= 70 ? 'hi' : gz >= 40 ? 'mid' : 'lo';
  const on   = gz <= 0 ? 0 : Math.min(5, Math.max(1, Math.ceil(gz / 20)));
  let segs = '';
  for (let i = 0; i < 5; i++) segs += `<i class="${i < on ? 'on' : ''}"></i>`;
  const tip = `저점 대비 과열 ${gz}/100 · ${stock.GreedZoneDays || 0}일 연속`
            + `${stock.GreedZoneEntry ? ' · 오늘 신규 진입!' : ''} — 추격매수 주의 (역추세 경고)`;
  return ` <span class="gz-badge ${tier}" title="${esc(tip)}">`
       + `🔥<span class="gz-seg">${segs}</span>`
       + `<span class="gz-val">${gz}</span>`
       + `</span>`;
}

// 테이블 RSI 셀 — 맨숫자에 과매수(빨강)/과매도(파랑) 미니바 추가.
function _rsiCellHtml(stock) {
  const v = stock.RSI != null ? Number(stock.RSI) : null;
  const txt = v != null ? fmt(v, 1) : '—';
  if (v == null) return `<span class="rsi-num">—</span>`;
  const col = v > 70 ? '#E03131' : v < 30 ? '#1971C2' : '#9CA3AF';
  const bw  = Math.min(100, Math.max(0, v));
  return `<span class="rsi-num">${txt}</span>`
       + `<div class="rsi-seg-track"><div class="rsi-seg-fill" style="width:${bw}%;background:${col}"></div></div>`;
}

// 공급망 병목 배지 — 종목명 옆 인라인 표시 (🔩+근접도, 진입신호별 색상)
function _bottleneckBadge(stock) {
  const s = stock.BottleneckScore || 0;
  if (s < 60) return '';
  const entry = stock.BottleneckEntry || '';
  const pass  = stock.BottleneckEntryPass === true;
  const emoji = entry ? entry.split(' ')[0] : '🔩';
  const cls   = pass ? 'bn-pass' : (entry.indexOf('조정') !== -1 ? 'bn-wait' : 'bn-weak');
  const layer = stock.BottleneckTop || '';
  const why   = (stock.BottleneckEntryReasons || []).join(', ');
  const title = `공급망 병목 ${s}/100 · ${layer}` + (entry ? ` · ${entry}` : '') + (why ? ` (${why})` : '');
  return ` <span class="bn-badge ${cls}" title="${esc(title)}">${emoji} ${s}</span>`;
}

// yfinance 영문 업종 → 한글. 설명(Desc)이 빈 종목(지수보강 신규 편입 등)의 설명란 폴백용.
// 매핑에 없으면 영문 그대로 표시(빈칸보다 나음).
const _INDUSTRY_KO = {
  'Semiconductors': '반도체', 'Semiconductor Equipment & Materials': '반도체 장비·소재',
  'Software - Infrastructure': '인프라 소프트웨어', 'Software - Application': '응용 소프트웨어',
  'Information Technology Services': 'IT 서비스', 'Communication Equipment': '통신장비',
  'Computer Hardware': '컴퓨터 하드웨어', 'Consumer Electronics': '가전·전자제품',
  'Electronic Components': '전자부품', 'Electronics & Computer Distribution': '전자·컴퓨터 유통',
  'Scientific & Technical Instruments': '과학·기술기기', 'Solar': '태양광',
  'Internet Content & Information': '인터넷 콘텐츠', 'Internet Retail': '인터넷 소매',
  'Electronic Gaming & Multimedia': '게임·멀티미디어', 'Entertainment': '엔터테인먼트',
  'Telecom Services': '통신서비스', 'Advertising Agencies': '광고',
  'Banks - Regional': '지방은행', 'Banks - Diversified': '종합은행',
  'Asset Management': '자산운용', 'Capital Markets': '자본시장',
  'Financial Data & Stock Exchanges': '금융데이터·거래소', 'Credit Services': '여신·신용서비스',
  'Insurance - Property & Casualty': '손해보험', 'Insurance - Life': '생명보험',
  'Insurance - Diversified': '종합보험', 'Insurance - Specialty': '특수보험',
  'Insurance - Reinsurance': '재보험', 'Insurance Brokers': '보험중개',
  'Mortgage Finance': '모기지금융', 'Financial Conglomerates': '금융복합',
  'Biotechnology': '바이오테크', 'Drug Manufacturers - General': '제약(대형)',
  'Drug Manufacturers - Specialty & Generic': '제약(특수·제네릭)', 'Medical Devices': '의료기기',
  'Medical Instruments & Supplies': '의료기구·소모품', 'Diagnostics & Research': '진단·연구',
  'Healthcare Plans': '건강보험', 'Medical Care Facilities': '의료시설',
  'Health Information Services': '헬스 IT', 'Medical Distribution': '의약품 유통',
  'Pharmaceutical Retailers': '약국·의약품 소매',
  'Oil & Gas E&P': '석유·가스 탐사생산', 'Oil & Gas Integrated': '종합 석유·가스',
  'Oil & Gas Midstream': '석유·가스 중류', 'Oil & Gas Equipment & Services': '유전 장비·서비스',
  'Oil & Gas Refining & Marketing': '정유·판매', 'Oil & Gas Drilling': '시추',
  'Uranium': '우라늄', 'Thermal Coal': '석탄',
  'Aerospace & Defense': '항공우주·방산', 'Specialty Industrial Machinery': '특수 산업기계',
  'Industrial Distribution': '산업재 유통', 'Building Products & Equipment': '건축자재·장비',
  'Engineering & Construction': '엔지니어링·건설', 'Farm & Heavy Construction Machinery': '농기계·중장비',
  'Electrical Equipment & Parts': '전기장비·부품', 'Tools & Accessories': '공구·액세서리',
  'Pollution & Treatment Controls': '환경·정화설비', 'Security & Protection Services': '보안서비스',
  'Staffing & Employment Services': '인력·고용서비스', 'Consulting Services': '컨설팅',
  'Specialty Business Services': '전문 비즈니스 서비스', 'Rental & Leasing Services': '렌탈·리스',
  'Waste Management': '폐기물 관리', 'Conglomerates': '복합기업',
  'Metal Fabrication': '금속 가공', 'Steel': '철강', 'Aluminum': '알루미늄',
  'Copper': '구리', 'Gold': '금', 'Silver': '은',
  'Other Industrial Metals & Mining': '산업금속·광업', 'Other Precious Metals & Mining': '귀금속·광업',
  'Specialty Chemicals': '특수화학', 'Chemicals': '화학', 'Agricultural Inputs': '농업자재',
  'Building Materials': '건축소재', 'Lumber & Wood Production': '목재',
  'Paper & Paper Products': '제지', 'Packaging & Containers': '포장·용기',
  'Specialty Retail': '전문소매', 'Apparel Retail': '의류소매', 'Apparel Manufacturing': '의류 제조',
  'Footwear & Accessories': '신발·액세서리', 'Luxury Goods': '명품',
  'Restaurants': '외식', 'Auto Manufacturers': '자동차 제조', 'Auto Parts': '자동차 부품',
  'Auto & Truck Dealerships': '자동차 딜러', 'Recreational Vehicles': '레저용 차량',
  'Travel Services': '여행서비스', 'Lodging': '숙박', 'Resorts & Casinos': '리조트·카지노',
  'Leisure': '레저', 'Gambling': '게이밍',
  'Packaged Foods': '포장식품', 'Beverages - Non-Alcoholic': '음료(비주류)',
  'Beverages - Wineries & Distilleries': '주류', 'Beverages - Brewers': '맥주',
  'Confectioners': '제과', 'Farm Products': '농축산물', 'Food Distribution': '식품 유통',
  'Grocery Stores': '식료품점', 'Discount Stores': '할인점', 'Department Stores': '백화점',
  'Household & Personal Products': '생활용품', 'Tobacco': '담배',
  'Furnishings, Fixtures & Appliances': '가구·가전', 'Home Improvement Retail': '홈인테리어 소매',
  'Residential Construction': '주택건설', 'Real Estate Services': '부동산 서비스',
  'Real Estate - Development': '부동산 개발', 'Real Estate - Diversified': '부동산(종합)',
  'REIT - Specialty': '리츠(특수)', 'REIT - Residential': '리츠(주거)', 'REIT - Retail': '리츠(소매)',
  'REIT - Industrial': '리츠(산업)', 'REIT - Office': '리츠(오피스)', 'REIT - Mortgage': '리츠(모기지)',
  'REIT - Healthcare Facilities': '리츠(헬스케어)', 'REIT - Hotel & Motel': '리츠(호텔)',
  'REIT - Diversified': '리츠(종합)',
  'Utilities - Regulated Electric': '전력 유틸리티', 'Utilities - Regulated Gas': '가스 유틸리티',
  'Utilities - Regulated Water': '수도 유틸리티', 'Utilities - Diversified': '종합 유틸리티',
  'Utilities - Renewable': '재생에너지 유틸리티', 'Utilities - Independent Power Producers': '독립발전',
  'Airlines': '항공', 'Railroads': '철도', 'Trucking': '운송(트럭)',
  'Integrated Freight & Logistics': '물류', 'Marine Shipping': '해운', 'Airports & Air Services': '공항·항공서비스',
  'Education & Training Services': '교육·훈련', 'Broadcasting': '방송', 'Publishing': '출판',
  'Textile Manufacturing': '섬유 제조', 'Business Equipment & Supplies': '사무기기·소모품',
  'Personal Services': '개인 서비스',
};

function _industryKo(ind) {
  if (!ind) return '';
  return _INDUSTRY_KO[ind] || ind;   // 매핑 없으면 영문 그대로
}

function renderStockRow(stock, rank) {
  const rankClass  = rank <= 3 ? `rank-${rank}` : 'rank-other';
  const score      = Math.round(stock.TotalScore || 0);
  const sc         = scoreClass(score);
  const barW       = Math.min(100, Math.max(0, score));
  const dayChg     = stock.DayChg || 0;
  const chgPct     = (dayChg * 100).toFixed(2);
  const chgClass   = dayChg > 0 ? 'chg-up' : dayChg < 0 ? 'chg-down' : 'chg-flat';
  const chgSign    = dayChg > 0 ? '+' : '';
  const rsi        = stock.RSI != null ? fmt(stock.RSI, 1) : '—';
  const avgVol     = _fmtAvgVol(stock._AvgVol20);
  const marketCap  = _fmtMarketCap(stock._MarketCap);
  const targetLabel = stock.NomuraUsed ? '섹터 밸류에이션 목표가' : 'DCF 메인 목표가';
  const upsidePct  = stock.TargetUpside != null
    ? (stock.TargetUpside >= 0 ? '+' : '') + fmt(stock.TargetUpside * 100, 1) + '%'
    : '—';
  const upsideColor = stock.TargetUpside != null
    ? (stock.TargetUpside >= 0 ? 'var(--success)' : 'var(--destructive)')
    : '';
  const upside     = stock.TargetPrice
    ? `<div class="target-price">${fmtPrice(stock.TargetPrice)}</div><div class="target-upside" style="color:${upsideColor}">${upsidePct}</div>`
    : `<span style="color:${upsideColor}">${upsidePct}</span>`;

  // 리스크 배지 HTML
  let riskHtml = '';
  const risks = [];
  if (stock.LowLiquidity) risks.push('<span class="risk-badge risk-badge-liq" title="거래대금 부족 — 유동성이 낮아 매매 시 주의">유동성↓</span>');
  if (stock.FailSafe)     risks.push('<span class="risk-badge risk-badge-fail" title="안전장치 발동 — EPS 적자 또는 RS 40 미만으로 점수 상한 제한">' + (stock._fail_eps ? 'EPS적자' : '안전장치') + '</span>');
  if (stock.BearCap)      risks.push('<span class="risk-badge risk-badge-bear" title="하락장 상한 발동 — 하락장으로 점수 50점 상한 제한">하락장↓</span>');
  if (risks.length) riskHtml = `<div class="risk-badges">${risks.join('')}</div>`;

  // TopReason 태그 HTML
  const reasonHtml = _renderReasonTags(stock.TopReason);

  // 증권사 컨센서스 목표가 HTML (IIFE 제거 — 500+ 종목 렌더 시 함수 생성 오버헤드 제거)
  let brokerHtml;
  if (stock.BrokerTarget) {
    const bUp = stock.Price ? ((stock.BrokerTarget - stock.Price) / stock.Price) * 100 : null;
    const bColor = bUp != null && bUp >= 0 ? 'var(--success)' : 'var(--destructive)';
    const bPct = bUp != null ? (bUp >= 0 ? '+' : '') + fmt(bUp, 1) + '%' : '';
    brokerHtml = `<div class="target-price">${fmtPrice(stock.BrokerTarget)}</div><div class="target-upside" style="color:${bColor}">${bPct}</div>`;
  } else {
    brokerHtml = '<div class="target-empty">컨센서스 없음</div>';
  }

  const checked = _selectedStocks.has(stock.Ticker);
  const t = esc(stock.Ticker);
  // US 종목 로고 (Finnhub 정적 URL 패턴 — API 호출 0, lazy 로드, 없으면 onerror로 숨김)
  const _rawT = (stock.Ticker || '').toUpperCase();
  const _usLogo = (_rawT && !_rawT.includes('.') && !/^\d+$/.test(_rawT))
    ? `https://static2.finnhub.io/file/publicdatany/finnhubimage/stock_logo/${_rawT}.png`
    : '';
  const logoHtml = _usLogo
    ? `<img class="row-logo" src="${_usLogo}" loading="lazy" alt="" onerror="this.style.display='none'" style="width:18px;height:18px;border-radius:4px;object-fit:contain;vertical-align:middle;margin-right:6px;background:var(--surface-2);">`
    : '';
  return `
<tr onclick="openDetail('${t}')" data-ticker="${t}" style="cursor:pointer;">
  <td class="center"><input type="checkbox" ${checked ? 'checked' : ''} onclick="toggleSelectStock('${t}', event)" style="cursor:pointer;width:16px;height:16px;accent-color:#3182F6;"></td>
  <td class="center"><span class="rank-cell ${rankClass}">${rank}</span></td>
  <td class="name-cell" onmouseenter="showStockPopup('${t}', event)" onmouseleave="hideStockPopup()">
    <span class="stock-name">${logoHtml}${esc(stock.Name || stock.Ticker)}${stock.IsSpeculativeTheme ? ` <span class="theme-warn" title="${esc(stock.ThemeWarning || '투기성 테마주 — 점수 신뢰도 낮음')}">⚠ 테마</span>` : ''}${stock.MicroOutlier ? ` <span class="micro-outlier" title="${esc(stock.MicroOutlierReason || '마이크로구조 이상치')}">🔬 마이크로 이상</span>` : ''}${_greedBadge(stock)}${_bottleneckBadge(stock)}</span>
    <span class="stock-code">${t}</span>
  </td>
  <td class="desc-cell">${esc(stock.Desc || _industryKo(stock.Industry) || '')}</td>
  <td><span class="sector-tag">${esc(stock.Sector || '—')}</span></td>
  <td class="score-col">
    <div class="score-line"><span class="score-num ${sc}">${score}</span>${_deltaBadge(stock)}</div>
    <div class="score-bar-wrap"><div class="score-bar-fill ${sc}" style="width:${barW}%"></div></div>
  </td>
  <td>
    ${_renderSignalHtml(stock.Signal, stock)}
    ${riskHtml}
  </td>
  <td class="right">${fmtPrice(stock.Price)}</td>
  <td class="right ${chgClass}">${chgSign}${chgPct}%</td>
  <td class="right rsi-cell">${_rsiCellHtml(stock)}</td>
  <td class="right">${avgVol}</td>
  <td class="right">${marketCap}</td>
  <td class="right" title="${stock.BrokerTargetSource ? esc(stock.BrokerTargetSource) : '증권사 컨센서스 없음'}">${brokerHtml}</td>
  <td class="reason-cell">${reasonHtml}</td>
  ${_midcapAlphaCell(stock)}
</tr>`;
}

function _updateMobileList(view, emptyMsg, renderToken) {
  const el = document.getElementById('mobile-stock-list');
  if (!el) return;
  const token = renderToken == null ? ++_renderToken : renderToken;
  if (!view || view.length === 0) {
    el.innerHTML = `<div class="mobile-stock-list-msg">${emptyMsg || '결과 없음'}</div>`;
    return;
  }
  _renderHtmlInBatches(el, view, renderMobileCard, 15, 30, token);
}

// Mobile override: clearer hierarchy and lighter information density on small screens.
function renderMobileCard(stock, rank) {
  const score    = Math.round(stock.TotalScore || 0);
  const sc       = scoreClass(score);
  const dayChg   = stock.DayChg || 0;
  const chgPct   = (dayChg * 100).toFixed(2);
  const chgClass = dayChg > 0 ? 'chg-up' : dayChg < 0 ? 'chg-down' : 'chg-flat';
  const chgSign  = dayChg > 0 ? '+' : '';
  const t = esc(stock.Ticker);
  return `
<div class="stock-card" data-ticker="${t}" onclick="openDetail('${t}')">
  <div class="stock-card-row1">
    <div class="stock-card-main">
      <span class="stock-card-rank">${rank}</span>
      <div class="stock-card-name">
        <span class="stock-card-name-main">${esc(stock.Name || stock.Ticker)}${stock.IsSpeculativeTheme ? ` <span class="theme-warn" title="${esc(stock.ThemeWarning || '투기성 테마주 — 점수 신뢰도 낮음')}">⚠</span>` : ''}${stock.MicroOutlier ? ` <span class="micro-outlier" title="${esc(stock.MicroOutlierReason || '마이크로구조 이상치')}">🔬</span>` : ''}</span>
        <span class="stock-card-ticker">${t}${stock.Sector ? ` · ${esc(stock.Sector)}` : ''}</span>
      </div>
    </div>
    <div class="stock-card-meta">
      <span class="stock-card-score ${sc}">${score}</span>
      <span class="stock-card-chg ${chgClass}">${chgSign}${chgPct}%</span>
    </div>
  </div>
  <div class="stock-card-row2">
    ${_renderSignalHtml(stock.Signal, stock)}
    ${stock.RSI != null ? `<span class="stock-card-rsi" style="font-size:11px;font-weight:700;color:${Number(stock.RSI) > 70 ? '#E03131' : Number(stock.RSI) < 30 ? '#1971C2' : '#9CA3AF'}">RSI ${fmt(stock.RSI, 0)}</span>` : ''}
    ${_greedBadge(stock)}
    ${_midcapMobileChip(stock)}
  </div>
</div>`;
}

// ── 회사 정보 팝업 ──────────────────────────────────────────────────────

function showStockPopup(ticker, ev) {
  const s = _stockMap[ticker];
  if (!s) return;
  const pop = document.getElementById('stock-popup');
  if (!pop) return;

  const name = esc(s.Name || ticker);
  const industry = esc(s.Industry || '');
  const info = esc(s.CompanyInfo || '');

  pop.innerHTML = `
    <div class="popup-header">
      <strong class="popup-name">${name}</strong>
      <span class="popup-ticker">${esc(ticker)}</span>
    </div>
    ${industry ? `<div class="popup-industry">${industry}</div>` : ''}
    ${info ? `<div class="popup-desc">${info}</div>` : ''}`;

  // 위치 계산 — 셀 아래에 표시, 화면 밖으로 나가지 않도록 조정
  const rect = ev.currentTarget.getBoundingClientRect();
  const scrollY = window.scrollY || document.documentElement.scrollTop;
  let left = rect.left;
  if (left + 340 > window.innerWidth) left = window.innerWidth - 350;
  if (left < 8) left = 8;
  pop.style.left = left + 'px';
  pop.style.top  = (rect.bottom + scrollY + 6) + 'px';
  pop.classList.add('visible');
}

function hideStockPopup() {
  const pop = document.getElementById('stock-popup');
  if (pop) pop.classList.remove('visible');
}

function _colCount() {
  const all = document.querySelectorAll('.stock-table thead th');
  const hidden = document.querySelectorAll('.stock-table thead th[style*="display: none"], .stock-table thead th[style*="display:none"]');
  return (all.length - hidden.length) || 11;
}

// ── 미드캡 알파 — 단일 컬럼 셀 (SP400 탭 정렬 가능) ────────────────────
function _midcapAlphaCell(stock) {
  if (_activeIndex !== 'SP400') return '';
  const alpha = stock.MidcapAlpha;
  if (alpha == null) return '<td class="right midcap-col"><span style="color:var(--text-tertiary)">—</span></td>';
  const a = Math.round(alpha);
  const label = stock.MidcapLabel || '모니터링';
  const tier = a >= 70 ? 'high' : a >= 40 ? 'mid' : 'low';
  const shortLabel = label !== '모니터링' ? label.split(' + ')[0] : '';
  const labelHtml = shortLabel ? `<span class="mc-cell-label mc-${_mcLabelCls(shortLabel)}">${esc(shortLabel)}</span>` : '';
  return `<td class="right midcap-col" title="미드캡알파 ${a} · 승격${Math.round(stock.MidcapPromotion||0)} · 매집${Math.round(stock.MidcapAccum||0)}"><div class="mc-cell"><span class="mc-cell-score mc-${tier}">${a}</span>${labelHtml}</div></td>`;
}

function _mcLabelCls(label) {
  if (label.includes('승격')) return 'promo';
  if (label.includes('매집')) return 'accum';
  if (label.includes('성장')) return 'growth';
  return 'neutral';
}

// ── 미드캡 알파 — 모바일 칩 ────────────────────────────────────────────
function _midcapMobileChip(stock) {
  if (_activeIndex !== 'SP400') return '';
  const alpha = stock.MidcapAlpha;
  if (alpha == null) return '';
  const a = Math.round(alpha);
  const label = stock.MidcapLabel || '';
  const tier = a >= 70 ? 'high' : a >= 40 ? 'mid' : 'low';
  const short = label && label !== '모니터링' ? label.split(' + ')[0] : '';
  return ` <span class="mc-mobile-chip mc-${tier}" title="미드캡알파 ${a}">◆${a}${short ? ' ' + esc(short) : ''}</span>`;
}

// ── 미드캡 알파 — L3 디테일 패널 렌더링 ────────────────────────────────
function _renderMidcapDetail(d) {
  const card = document.getElementById('dp-midcap-card');
  if (!card) return;
  const indices = d.Indices || [];
  if (!indices.includes('SP400') || d.MidcapAlpha == null) { card.style.display = 'none'; return; }
  card.style.display = '';
  const labelEl = document.getElementById('dp-midcap-label');
  const barsEl  = document.getElementById('dp-midcap-bars');
  const label = d.MidcapLabel || '모니터링';
  const colorMap = { '승격 임박': 'promo', '매집 초기': 'accum', '성장 가속': 'growth' };
  let cls = 'mc-neutral';
  for (const [k, v] of Object.entries(colorMap)) { if (label.includes(k)) { cls = 'mc-' + v; break; } }
  if (labelEl) labelEl.innerHTML = `<span class="midcap-label-badge ${cls} lg">${esc(label)}</span>`;
  if (barsEl) {
    const items = [
      { name: '종합 알파', val: Math.round(d.MidcapAlpha || 0), key: 'alpha' },
      { name: '승격 준비도', val: Math.round(d.MidcapPromotion || 0), key: 'promo' },
      { name: '매집 신호', val: Math.round(d.MidcapAccum || 0), key: 'accum' },
    ];
    barsEl.innerHTML = items.map(it => {
      const tier = it.val >= 70 ? 'high' : it.val >= 40 ? 'mid' : 'low';
      return `<div class="midcap-bar-row"><span class="midcap-bar-name">${it.name}</span>`
        + `<div class="midcap-bar-track"><div class="midcap-bar-fill midcap-bar-${tier}" style="width:${it.val}%"></div></div>`
        + `<span class="midcap-bar-val midcap-bar-${tier}">${it.val}</span></div>`;
    }).join('');
  }
}

function setStockListMsg(msg) {
  ++_renderToken;
  const tbody = document.getElementById('stock-list');
  if (tbody) tbody.innerHTML = `<tr><td colspan="${_colCount()}" class="state-msg">${esc(msg)}</td></tr>`;
  _updateMobileList([], msg, _renderToken);
}

// ── Game-style Loading Screen (쓸데없는 주식 잡학) ─────────────────────
const _STOCK_TRIVIA = [
  '버크셔 해서웨이 A주는 한 주 가격이 수십만 달러에 달하는 초고가 주식이다.',
  '워런 버핏은 11살에 첫 주식을 샀다. Cities Service 우선주 3주를 주당 38.25달러에 매입했다.',
  '뉴욕증권거래소(NYSE)의 개장벨은 원래 중국식 징이었다. 1903년에 지금의 놋쇠 벨로 바뀌었다.',
  '"월스트리트"라는 이름은 17세기 네덜란드 식민지 시절 원주민 방어용 나무 벽(wall)에서 유래했다.',
  '1602년 네덜란드 동인도회사(VOC)는 세계 최초의 상장 주식회사로 흔히 꼽힌다. 장기간 연 18% 안팎의 배당을 지급한 것으로 전해진다.',
  '나스닥(NASDAQ)은 약어다: National Association of Securities Dealers Automated Quotations.',
  '코스닥의 "닥"도 약어다: Korea Securities Dealers Automated Quotations.',
  '1929년 대공황 때 제시 리버모어는 공매도로 1억 달러 안팎을 벌었다는 일화로 유명하다. 현재 가치로는 수십억 달러 규모다.',
  '일본 닛케이225는 1989년 12월 29일에 최고점을 찍고, 무려 34년 만인 2024년 초에 신고가를 갱신했다.',
  'S&P 500 기업의 평균 수명은 약 15~20년이다. 1960년대에는 60년이었다.',
  '코카콜라를 1919년 IPO 때 40달러에 샀으면, 배당 재투자 시 지금 약 1천만 달러가 됐다.',
  '"검은 월요일"(1987년 10월 19일) 다우존스는 하루에 22.6% 폭락했다. 역대 최대 일일 낙폭.',
  '알고리즘 트레이딩이 미국 주식 거래량의 약 60~70%를 차지한다. 사람이 소수파다.',
  '피터 린치는 마젤란 펀드를 13년간 운용하며 연평균 29.2% 수익률을 달성했다.',
  '한국 주식시장의 점심시간 휴장은 2000년 5월 22일에 폐지되었다.',
  '역사상 가장 비싼 주문 실수 중 하나: 미즈호증권이 "1엔에 610,000주" 주문을 냈고, 손실은 약 407억 엔(당시 약 3억4천만 달러)에 달했다.',
  '애플이 시가총액 1조 달러를 처음 돌파한 건 2018년 8월 2일이다.',
  '삼성전자는 1975년 상장 첫 거래일 기준 주가가 5,905원(수정주가 기준)이었다.',
  '한국 증시에서 외국인 투자 한도는 IMF 외환위기 직후인 1998년 5월 대부분 폐지됐다.',
  '주식시장에서 "불(bull)"과 "곰(bear)"의 어원은 각각 소가 위로 치받고 곰이 아래로 할퀴는 모습에서 왔다는 설명이 널리 알려져 있다.',
  'NYSE의 기원은 1792년 브로드가의 플라타너스 나무 아래다. 24명의 브로커가 그 아래서 버튼우드 협약을 맺었다.',
  '전 세계 주식시장 시가총액 합계는 약 110조 달러(2024년 기준). 지구인 1인당 약 1.4만 달러.',
  '테슬라는 2020년 한 해 동안 주가가 743% 올랐다.',
  '워런 버핏의 자산 99%는 50세 이후에 만들어졌다. 복리의 힘.',
  '일본의 트레이더 BNF는 데이트레이딩만으로 160만 엔을 2005년까지 약 153억 엔으로 불렸다.',
  '캔슬림(CAN SLIM)을 만든 윌리엄 오닐은 30살에 NYSE 최연소 회원석을 샀다.',
  '스눕 독, 마사 스튜어트, 심지어 스파이더맨 코스프레까지 NYSE 개장벨을 울린 적 있다.',
  '주식 "틱(tick)"의 어원은 시세 표시기(ticker tape)가 "틱틱" 소리를 내며 인쇄하던 것에서 유래.',
  '한국 코스피 역사상 최대 일일 상승률은 2008년 10월 30일의 11.95%다.',
  '"주식을 사면 그 회사의 화장실도 내 것" — 이론적으로는 맞다. 지분율만큼.',
  '현존하는 가장 오래된 주권(株券) 가운데 하나로 1606년 발행된 네덜란드 동인도회사(VOC) 주식이 알려져 있다. 수억 원대 가치로 평가되기도 한다.',
  '다우존스 산업평균지수는 원래 12개 종목이었다. 가장 오래 버텼던 GE(제너럴 일렉트릭)도 2018년에 제외됐다.',
  '찰리 멍거는 투자 비결을 한마디로 요약했다: "합리적인 가격에 훌륭한 기업을."',
  '1960년대 말 뉴욕 증권가는 거래량 폭증으로 매주 수요일에 문을 닫았다. 종이 서류 처리가 따라가지 못해서.',
  '코스피는 1956년 3월 3일 개장했다. 당시 상장 종목은 단 12개였다.',
  '가장 짧은 약세장: 2020년 코로나 폭락은 2월 19일~3월 23일, 단 33일 만에 저점을 찍고 반등했다.',
  '"시장은 단기적으로 투표 기계, 장기적으로 체중계다." — 벤저민 그레이엄.',
  '미국 연준(Fed)이 기준금리를 올리면 일반적으로 채권 가격에는 하락 압력이 생긴다. 금리와 채권 가격은 보통 반대 방향으로 움직인다.',
  '공매도 잔고가 폭발적으로 많은 종목이 급등하는 현상을 "숏 스퀴즈"라 한다. 2021년 게임스톱이 대표 사례.',
  'ETF(상장지수펀드)는 1993년 처음 등장했다. S&P 500을 추종하는 SPDR SPY가 원조다.',
  '"10루타"란 10배 오른 주식을 뜻한다. 피터 린치가 대중화한 표현이다.',
  'PER(주가수익비율)이 10이라면, 이익이 지금 수준으로 유지된다고 가정할 때 10년치 이익이 주가와 비슷하다는 뜻이다.',
  '삼성전자는 코스피에서 차지하는 비중이 매우 커서, 주가 흐름이 지수에 큰 영향을 준다.',
  '"공포에 사서 탐욕에 팔아라" — 반대로 행동하는 것이 주식 투자에서 가장 어렵다.',
  '미국 증시 개장 시간은 동부시간 오전 9:30~오후 4시. 한국 시간으로는 밤 11:30~새벽 6시(서머타임 제외).',
  '한국 파생상품 시장(KOSPI200 옵션)은 한때 세계 거래량 1위에 오른 적이 있다.',
  '모멘텀 투자란 최근 6~12개월 강세 종목이 계속 오른다는 현상을 이용한 전략이다. 실증 연구로 검증됐다.',
  '채권 금리가 주식 배당수익률보다 높아지면 상대적으로 채권의 매력이 커질 수 있다. 다만 실제 자금 이동은 이익 전망과 위험선호에도 좌우된다.',
  '"팔지 말고 기다려라"는 좋은 종목에만 통한다. 나쁜 종목은 기다려도 안 오른다. 종목 선택이 핵심.',
  '거래량이 적은 종목에는 유동성 위험이 있다. 팔고 싶을 때 사는 사람이 없으면 원하는 가격에 팔 수 없다.',
  '존 보글은 1975년 뱅가드를 설립하고 인덱스 펀드를 대중화했다. "시장을 이기려 하지 말고 시장이 되어라."',
  '한국 증시 상한가·하한가 제한폭은 ±30%다. 2015년 이전까지는 ±15%였다.',
  '코스피 2,000포인트를 처음 돌파한 건 2007년 7월 25일이다.',
  '나스닥은 2000년 3월 10일 5,048 포인트로 고점을 찍은 뒤 IT 버블 붕괴로 80% 가까이 폭락했다.',
  '아마존은 1997년 IPO 당시 공모가가 18달러였다.',
  '"블루칩(Blue Chip)"이란 카지노에서 가장 가치 높은 파란 칩에서 유래했다. 대형 우량주를 뜻한다.',
  '한국 코스피 서킷브레이커는 지수가 전일 대비 8% 이상 하락하면 1단계로 20분간 거래가 멈춘다.',
  '삼성전자는 2018년 5월 50:1 액면분할을 실시했다. 분할 전 주가는 250만 원대였다.',
  '"데드캣 바운스(Dead Cat Bounce)"란 급락한 주식이 잠시 반등하는 현상. 고양이도 높은 데서 떨어지면 한 번은 튀어 오른다는 데서 유래.',
  '"헤지펀드"라는 이름은 1949년 앨프리드 존스가 주식 매수와 공매도를 함께 써서 위험을 "헤지"한 데서 왔다.',
  'ROE(자기자본이익률)는 내 돈으로 얼마나 벌었는지 보여주는 지표다. 워런 버핏이 가장 중시하는 지표 중 하나.',
  'S&P 500 지수는 1957년에 처음 도입됐다. 이전에는 S&P 90이었다.',
  '손정의 소프트뱅크 회장은 2000년 알리바바에 약 2천만 달러를 투자해 수백억 달러 규모의 수익을 거뒀다.',
  '미국 증시 결제 주기는 2024년 5월부터 T+2에서 T+1로 단축됐다. 주식 팔면 다음 날 현금이 생긴다.',
  '워런 버핏은 코카콜라를 1988~1989년에 매수했다. 30년이 지난 지금도 보유 중이다.',
  '"1월 효과"란 연초에 중소형주가 강세를 보이는 경향이다. 연말 세금 손실 매도 후 재매수가 원인이라는 설이 있다.',
  '주가가 52주 신고가를 강한 거래량으로 돌파할 때를 "돌파(Breakout)"라 한다. CAN SLIM 전략의 핵심 매수 신호다.',
  '"갭 업(Gap up)"이란 전일 종가보다 높게 시가가 형성되는 것. 호재 뉴스 후 매수 압력이 집중될 때 나타난다.',
  '공매도는 주식을 빌려 팔고 나중에 싸게 사서 갚아 차익을 얻는 방법이다. 하락에 베팅하는 전략.',
  '"배당락일"이란 이 날 이후 주식을 사면 그 배당을 못 받는 날이다. 배당락 당일 주가는 배당금만큼 빠지는 경향이 있다.',
  '지수 추종 ETF의 운용보수는 연 0.03~0.1%대다. 일반 액티브 펀드의 1~2%와 비교하면 압도적으로 저렴하다.',
  '"가치함정(Value Trap)"이란 싸 보여서 샀지만 계속 더 싸지는 주식. 싸다는 것만으로는 충분하지 않다.',
  '워런 버핏은 주식 투자를 "야구의 타자"에 비유했다. 스트라이크 아웃이 없으니 좋은 공만 기다려 치면 된다.',
  '중국 A주가 MSCI 신흥국 지수에 편입되기 시작한 건 2018년이다. 한국은 1992년에 편입됐다.',
  '"턴어라운드(Turnaround) 종목"이란 실적이 바닥을 치고 반등하는 기업이다. 피터 린치가 즐겨 찾은 유형.',
  '"주식 시장은 자금을 인내심 없는 사람에게서 인내심 있는 사람에게로 이전하는 장치다." — 워런 버핏.',
  '선물(Futures)은 미래의 특정 시점에 특정 가격으로 사고팔기로 약속하는 계약이다. 보험과 투기에 모두 쓰인다.',
  '한국 개인 투자자들이 "개미"로 불리게 된 건 외국인·기관이라는 "큰 손"에 대비되는 표현에서 비롯됐다.',
  '2010년 5월 6일 "플래시 크래시" — 다우존스가 몇 분 만에 약 1,000포인트 급락했다 회복했다. 알고리즘 거래의 연쇄 반응이 원인이었다.',
  '1637년 네덜란드 튤립 투기 때 희귀 품종 구근 하나가 암스테르담 운하변 저택 한 채 값과 맞먹었다는 기록이 있다.',
  '아마존 주가는 닷컴 버블 붕괴로 1999년 고점 대비 94% 폭락했다. 거기서 버틴 사람은 나중에 수천 배를 벌었다.',
  '2020년 코로나 폭락 때 한국 개인투자자들이 대거 매수에 나서며 "동학개미운동"이라 불렸다.',
  '아이작 뉴턴도 1720년 남해회사 버블에 투자했다가 큰돈을 잃었다. "천체의 움직임은 계산할 수 있지만 인간의 광기는 못 한다"고 말했다 전해진다.',
  '조지 소로스는 1992년 영국 파운드화 공매도로 하루에 약 10억 달러를 벌었다. "영란은행을 무너뜨린 사나이"라 불린다.',
  '워런 버핏과의 점심 경매 최고 낙찰가는 2022년 1,900만 달러(약 250억 원)였다. 2000년 첫 경매 낙찰가는 2만 5천 달러.',
  '158년 역사의 투자은행 리먼 브라더스는 2008년 9월 15일 파산했다. 부채 6,130억 달러, 당시 미국 역사상 최대 파산.',
  '코스피가 3,000을 처음 돌파한 건 2021년 1월 7일이다. 2,000 돌파(2007년)로부터 약 13년 걸렸다.',
  '닌텐도는 1889년 화투 제조사로 시작했다. 창업 후 100년 넘게 지나 세계적 게임 회사가 됐다.',
  '래리 윌리엄스는 1987년 선물 트레이딩 대회에서 1만 달러를 1년 만에 약 114만 달러로 불렸다. 수익률 11,376%.',
  '존 템플턴은 2차 세계대전 직전, 뉴욕 증시에서 1달러 미만 주식 104종을 100달러어치씩 샀다. 34개가 망했지만 전체 수익은 4배였다.',
  'VIX(공포지수)는 S&P 500 옵션의 변동성을 측정한다. 20 이하면 안정, 30 이상이면 공포 국면. 2020년 3월에는 82.69까지 치솟았다.',
  '엔비디아는 원래 게임용 그래픽카드 회사였다. AI 붐으로 2024년 시가총액 세계 1위에 오르기도 했다.',
  '구글은 2004년 IPO를 네덜란드식 경매로 진행했다. 공모가 85달러. 월가의 전통 방식을 거부해 화제가 됐다.',
  '미국에는 50년 이상 연속으로 배당을 늘린 "배당왕(Dividend King)" 기업이 50개 가까이 된다.',
  '한국 주식 계좌 수는 2020년 이후 급증해 6,000만 개를 넘겼다. 인구(약 5,200만)보다 계좌가 더 많다.',
  '2000년 시가총액 세계 1위는 GE(제너럴 일렉트릭)였다. 2024년에는 애플·엔비디아·마이크로소프트가 다투고 있다.',
  '마이크로소프트는 1986년 IPO 당시 공모가 21달러. 빌 게이츠는 31살에 억만장자가 됐다.',
  '일본 도쿄증권거래소 상장사 중 마쓰이건설(松井建設)은 1586년 창업이다. 430년 넘게 살아남은 기업.',
  '조지 소로스의 퀀텀 펀드는 1969~2000년 연평균 약 30% 수익률을 기록했다.',
  '짐 사이먼스의 메달리온 펀드는 1988~2018년 수수료 차감 전 연평균 약 66% 수익률을 냈다. 수학자 출신 퀀트 투자의 전설.',
  '앙드레 코스톨라니는 "주식을 사고 수면제를 먹고 자라. 몇 년 뒤 깨면 부자가 돼 있을 것"이라 했다.',
  '벤저민 그레이엄의 《현명한 투자자》는 1949년 초판이 나왔다. 버핏은 이 책을 "투자서 중 최고"라 평했다.',
  '필립 피셔는 《위대한 기업에 투자하라》(1958)에서 성장주 투자를 체계화했다. 버핏에게 큰 영향을 줬다.',
  '레이 달리오는 1975년 아파트에서 브리지워터를 창업해 세계 최대 헤지펀드로 키웠다.',
  '에드워드 소프는 블랙잭 카드 카운팅을 발명한 수학자다. 이후 월가로 전향해 퀀트 헤지펀드에서도 성공했다.',
  '니콜라스 다바스는 전업 댄서였지만 독자적인 "박스 이론"으로 1950년대에 200만 달러를 벌었다.',
  '폴 튜더 존스는 1987년 블랙먼데이를 예측하고 공매도로 큰 수익을 올렸다.',
  '데이비드 테퍼는 2009년 금융위기 직후 은행주에 집중 투자해 한 해에 약 70억 달러를 벌었다.',
  '존 폴슨은 2007년 서브프라임 모기지 붕괴에 베팅해 약 150억 달러를 벌었다. "역사상 가장 위대한 트레이드"로 불린다.',
  '마이클 버리는 서브프라임 붕괴를 예측하고 CDS에 투자해 큰 수익을 올렸다. 영화 《빅쇼트》의 실제 인물.',
  '워런 버핏은 2008년 금융위기 때 골드만삭스에 50억 달러를 투자했다. 우선주 + 워런트 조건으로 큰 수익을 거뒀다.',
  '버핏이 처음 버크셔 해서웨이 주식을 산 건 1962년, 주당 7.50달러였다.',
  '버크셔 해서웨이는 원래 섬유 회사였다. 버핏은 이 인수를 자신의 최대 실수라 말했다.',
  '버핏은 유언장에 "아내 재산의 90%를 S&P 500 인덱스 펀드에 넣으라"고 적었다.',
  '워런 버핏의 주주서한은 1965년부터 매년 발표됐다. 투자의 교과서로 불린다.',
  '마크 미너비니는 US 인베스팅 챔피언십에서 연 155% 수익률로 우승한 적 있다.',
  '1997년 아시아 금융위기 때 한국 원화는 달러당 약 900원에서 약 1,960원까지 폭락했다.',
  '1997년 IMF 외환위기 당시 한국인들은 금 모으기 운동으로 약 227톤의 금을 모았다.',
  '2008년 금융위기 때 S&P 500은 고점 대비 약 57% 하락했다.',
  '역사상 가장 긴 미국 강세장은 2009년 3월~2020년 2월로, 약 11년간 지속됐다.',
  '2001년 9·11 테러 후 뉴욕증권거래소는 4거래일 연속 휴장했다. 1933년 이후 가장 긴 휴장.',
  '1998년 LTCM은 노벨상 수상자 2명이 참여한 헤지펀드였지만, 과도한 레버리지로 파산 위기에 처해 연준이 구제에 나섰다.',
  '2011년 S&P가 미국 국채 신용등급을 AAA에서 AA+로 강등했다. 미국 역사상 처음.',
  '1971년 닉슨 대통령이 금본위제를 폐지한 "닉슨 쇼크" 이후 달러는 변동환율제로 전환됐다.',
  '2015년 중국 상하이종합지수는 6월 고점에서 두 달 만에 약 40% 폭락했다.',
  '1987년 블랙먼데이 여파로 홍콩증시는 4거래일 휴장 후 재개장일에 33.3% 폭락했다.',
  '2020년 코로나 폭락 때 S&P 500은 최고점에서 -34%까지 단 23거래일 만에 떨어졌다. 역사상 가장 빠른 약세장 진입.',
  '1636년 네덜란드 튤립 버블 때 이미 선물 거래가 존재했다. 구근이 땅에 있을 때 미래 인도 약속으로 거래됐다.',
  '영국 "사우스 시 버블"(1720년)과 프랑스 "미시시피 버블"(1720년)은 같은 해에 터졌다.',
  '158년 역사의 투자은행 리먼 브라더스는 2008년 9월 15일 파산했다. 부채 6,130억 달러.',
  '한국 코스닥 시장은 1996년 7월 1일 개장했다.',
  '코스피 지수는 1983년 도입됐으며, 1980년 1월 4일을 기준일(100)로 산출한다.',
  '한국 코스피에서 개인 투자자의 거래 비중은 약 60~70%로, 미국(약 20~25%)보다 훨씬 높다.',
  '한국 증시에서 "따상"이란 공모주가 상장 첫날 시초가(공모가 2배)에 상한가(+30%)까지 달성하는 것이다.',
  '한국 상장사의 배당성향은 미국·유럽에 비해 낮은 편이다. "코리아 디스카운트"의 한 원인으로 꼽힌다.',
  '한국 국민연금은 세계 3위권의 대형 연기금이다. 국내외 주식에 수백조 원을 투자하고 있다.',
  '한국에서 ETF 거래에는 증권거래세가 면제된다.',
  '한국 코스피 시가총액 1·2위 모두 반도체 기업이다(SK하이닉스, 삼성전자). 2026년 6월 SK하이닉스가 삼성전자를 처음 제쳤다.',
  '한국에서 상장주식 매매 차익은 대주주가 아닌 한 양도소득세가 비과세다(2024년 기준).',
  '한국 증시에서 "테마주"란 특정 이슈에 엮여 급등하는 종목군이다. 선거철에 특히 기승을 부린다.',
  '한국 투자자의 해외주식 투자가 2020년 이후 급증하며 "서학개미"라는 신조어가 생겼다.',
  '코스피200 야간선물은 한국시간 새벽까지 거래된다. 미국장 동향을 실시간으로 반영한다.',
  '한국 주식 매매 수수료는 증권사 간 경쟁으로 0.01% 이하까지 내려간 곳도 있다.',
  '한국 공모주 청약에서 경쟁률이 수백 대 1을 넘기는 건 흔한 일이다.',
  '한국 주식 계좌 수는 인구(약 5,200만)보다 많다. 한 사람이 여러 증권사 계좌를 갖고 있어서.',
  '코스피가 3,000을 처음 돌파한 건 2021년 1월 7일이다. 2,000 돌파(2007년)로부터 약 13년 걸렸다.',
  '애플은 1997년 파산 직전이었다. 마이크로소프트가 1.5억 달러를 투자해 살렸다.',
  '테슬라는 2010년 상장 후 10년 만인 2020년에야 첫 연간 흑자를 냈다.',
  '넷플릭스는 2000년 블록버스터에 5,000만 달러에 인수를 제안했지만 거절당했다. 블록버스터는 2010년 파산.',
  '알리바바는 2014년 뉴욕증시 상장으로 250억 달러를 조달했다. 당시 세계 최대 IPO.',
  '야후는 1998년 구글을 100만 달러에 인수할 기회를 놓쳤다.',
  '메타(구 페이스북)는 2012년 IPO 첫날 시가총액 약 1,040억 달러였다. 당시 기술기업 IPO 사상 최대 규모.',
  '아마존의 원래 회사명은 "카다브라(Cadabra)"였다. "시체(cadaver)" 발음과 비슷해서 바꿨다.',
  'TSMC는 1987년 모리스 창이 설립했다. 반도체 위탁 생산(파운드리) 사업 모델의 원조.',
  '스타벅스는 1992년 IPO 당시 시가총액이 약 2.5억 달러였다.',
  '엔비디아의 젠슨 황은 1993년 공동 창업자 두 명과 데니스 레스토랑에서 사업 구상을 했다.',
  '손정의는 닷컴 버블 붕괴로 개인 자산의 약 99%를 잃었다고 알려져 있다.',
  'JP모건은 1907년 금융 패닉 때 개인 자금으로 시장을 안정시켰다. 미국 연준(Fed) 설립의 계기가 됐다.',
  '알렉산더 해밀턴은 미국 초대 재무장관이자 뉴욕은행(현 BNY멜론)의 창립자다. 10달러 지폐 인물.',
  '캔들스틱 차트(봉차트)는 18세기 일본 쌀 시장에서 혼마 무네히사가 개발한 것으로 알려져 있다.',
  '"골든 크로스"란 단기 이동평균선이 장기를 위로 뚫는 것. 반대는 "데드 크로스".',
  '"윈도 드레싱"이란 펀드 매니저가 분기 말에 수익률을 좋게 보이려고 포트폴리오를 단장하는 행위다.',
  '"감자(減資)"란 주식 수나 액면가를 줄이는 것이다. 무상감자는 보통 악재로 받아들여진다.',
  '"보호예수(lockup)"란 IPO 직후 대주주 등이 일정 기간 주식을 못 팔게 하는 제도다. 해제일에 매도 물량이 쏟아지기도 한다.',
  '세계 최초의 증권거래소는 1602년 암스테르담에 설립됐다. 네덜란드 동인도회사 주식을 거래하기 위해서.',
  '영국 런던증권거래소(LSE)는 1698년 조나단의 커피하우스에서 시작됐다.',
  '세계 최초의 뮤추얼 펀드는 1924년 설립된 매사추세츠 투자 신탁(MIT)이다.',
  '다우존스 지수는 "가격 가중"이라 비싼 주식이 지수에 더 큰 영향을 미친다. S&P 500은 "시가총액 가중".',
  'S&P 500에 편입되면 인덱스 펀드의 매수 수요로 주가가 단기 상승하는 "인덱스 효과"가 있다.',
  '인도 센섹스(Sensex) 지수는 1979년 기준 100에서 출발해 2024년 80,000을 넘겼다.',
  '세계 최대 국부펀드는 노르웨이 정부연금펀드로, 운용 자산이 약 1.7조 달러를 넘긴다.',
  '세계 최대 자산운용사는 블랙록이다. 운용 자산이 약 10조 달러(2024년 기준)를 넘긴다.',
  '독일 DAX 지수는 "총수익" 지수다. 배당을 재투자한 것으로 가정해 산출한다. 대부분의 주가지수와 다르다.',
  '"산타 랠리"란 12월 말~1월 초에 주가가 오르는 경향이다. 미국 시장에서 통계적으로 관찰된다.',
  '"셀 인 메이(Sell in May)" — 5월에 팔고 10월에 다시 사라는 격언. 5~10월 수익률이 통계적으로 낮은 경향에 근거한다.',
  'S&P 500의 역사적 연평균 수익률은 약 10%(명목)다. 인플레이션을 빼면 약 7%.',
  '"72의 법칙" — 투자금이 2배가 되는 데 걸리는 햇수 ≈ 72 ÷ 연수익률(%). 연 10%면 약 7.2년.',
  '"FOMO(Fear Of Missing Out)"란 놓칠까봐 뒤늦게 뛰어드는 심리. 고점 매수의 흔한 원인.',
  '"공포탐욕지수(Fear & Greed Index)"는 CNN이 만든 시장 심리 지표다. 0이면 극도의 공포, 100이면 극도의 탐욕.',
  '미국 증시의 "트리플 위칭데이"는 분기마다 주가지수 선물·옵션과 개별주식 옵션이 동시 만기되는 날. 변동성이 커진다.',
  '"밈 주식(Meme Stock)"이란 소셜미디어에서 개인 투자자들이 집단 매수하는 종목. 2021년 게임스톱이 원조 격.',
  'SPAC(기업인수목적회사)은 사업 없이 먼저 상장한 뒤 비상장 기업을 인수하는 빈 껍데기 회사다.',
  '"피보나치 되돌림"은 기술적 분석에서 38.2%, 50%, 61.8% 수준의 지지·저항을 찾는 데 쓰인다.',
  '"EPS 서프라이즈"란 실적이 시장 예상치를 웃도는 것. 주가 상승의 강력한 촉매다.',
  '미국 10년물 국채 금리는 금융시장의 기준금리로 통한다. 주식 밸류에이션에도 직접 영향을 미친다.',
  '"대통령 선거 주기 이론" — 미국 대선 전해(3년차)에 주식 수익률이 좋은 경향이 있다.',
  '미국 401(k) 퇴직연금은 1978년 세법 개정으로 탄생했다. 미국인의 주식 투자 참여율을 높인 제도.',
  '한국 증시 거래시간은 오전 9시~오후 3시 30분으로, 미국(6시간 30분)보다 30분 짧다.',
  '한국 코스피200 옵션 만기일은 매월 둘째 주 목요일이다.',
  '중국 본토 주식시장(A주)의 일일 가격 제한폭은 ±10%다. 한국(±30%)의 3분의 1.',
  '일본 닛케이225 지수도 다우존스처럼 "가격 가중" 방식이다.',
  '"무상증자"란 회사가 주주에게 공짜로 추가 주식을 나눠주는 것. 호재로 받아들여지는 경우가 많다.',
  '"자사주 매입·소각"은 유통 주식 수를 줄여 주당 가치를 높이는 효과가 있다.',
  '"블록딜"이란 대량의 주식을 시장 밖에서 한꺼번에 거래하는 것. 대주주 지분 매각에 자주 쓰인다.',
  'NYSE의 트레이딩 플로어는 영화에 자주 나오지만, 실제 거래 대부분은 전자 시스템으로 처리된다.',
  '미국에서 "페니 스톡"이란 주당 5달러 미만 주식을 말한다. 고위험·고변동성으로 유명.',
  '"차익거래(Arbitrage)"란 같은 자산의 가격 차이를 이용해 무위험 수익을 얻는 전략. 기회는 순식간에 사라진다.',
  '구글은 2004년 IPO를 네덜란드식 경매로 진행했다. 공모가 85달러. 월가의 전통 방식을 거부해 화제가 됐다.',
  '한국 주식시장에서 "공시"는 DART(dart.fss.or.kr)에서 확인할 수 있다.',
  '한국 증시 "작전주"란 특정 세력이 주가를 인위적으로 올린 뒤 물량을 떠넘기는 종목이다.',
  '버크셔 해서웨이 A주는 2024년 주당 60만 달러를 넘기며 세계에서 가장 비싼 주식 기록을 경신했다.',
  '한국의 "변동성 완화장치(VI)"는 개별 종목 가격이 급변하면 2분간 단일가 매매로 전환하는 제도다.',
];
let _triviaTimer = null;
let _triviaIdx = 0;

function _shuffledTrivia() {
  const a = [..._STOCK_TRIVIA];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

let _shuffled = _shuffledTrivia();

function _scanLoadingHtml(triviaId) {
  return `<div class="scan-loading">
      <div class="scan-loading-spinner">
        <div class="dot"></div><div class="dot"></div><div class="dot"></div><div class="dot"></div>
      </div>
      <div class="scan-loading-trivia-wrap">
        <div class="scan-loading-trivia" id="${triviaId}">
          <div class="scan-loading-trivia-label">알고 계셨나요?</div>
          <div class="scan-loading-trivia-text">${esc(_shuffled[0])}</div>
        </div>
      </div>
      <div class="scan-loading-progress"><div class="scan-loading-progress-bar"></div></div>
    </div>`;
}

function showScanLoading() {
  ++_renderToken;
  stopScanLoading();
  _shuffled = _shuffledTrivia();
  _triviaIdx = 0;
  const tbody = document.getElementById('stock-list');
  if (!tbody) return;
  const mEl = document.getElementById('mobile-stock-list');
  if (mEl) mEl.innerHTML = _scanLoadingHtml('scan-trivia-m');
  tbody.innerHTML = `<tr><td colspan="${_colCount()}" style="padding:0;border:none;">${_scanLoadingHtml('scan-trivia')}</td></tr>`;
  _triviaTimer = setInterval(_rotateScanTrivia, 5000);
}

function _rotateScanTrivia() {
  const id = window.innerWidth <= 768 ? 'scan-trivia-m' : 'scan-trivia';
  const cur = document.getElementById(id);
  const wrap = cur?.parentElement;
  if (!wrap || !cur) { stopScanLoading(); return; }
  cur.classList.add('fade-out');
  setTimeout(() => {
    _triviaIdx = (_triviaIdx + 1) % _shuffled.length;
    const el = document.createElement('div');
    el.className = 'scan-loading-trivia';
    el.id = id;
    el.innerHTML = `<div class="scan-loading-trivia-label">알고 계셨나요?</div>
      <div class="scan-loading-trivia-text">${esc(_shuffled[_triviaIdx])}</div>`;
    cur.remove();
    wrap.appendChild(el);
  }, 400);
}

function stopScanLoading() {
  if (_triviaTimer) { clearInterval(_triviaTimer); _triviaTimer = null; }
}

function setStatHTML(id, html) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = html;
}

// ── 검색 (클라이언트 필터) ───────────────────────────────────────────────

let _searchTimer = null;
let _searchSelectedIdx = -1;

function _createSuggestBox(inp) {
  let box = document.getElementById('search-suggest');
  if (box) return box;
  box = document.createElement('div');
  box.id = 'search-suggest';
  box.style.cssText = 'position:absolute;left:0;right:0;top:100%;background:var(--card);border:1px solid var(--border);border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,0.12);z-index:999;max-height:260px;overflow-y:auto;display:none;';
  inp.parentElement.style.position = 'relative';
  inp.parentElement.appendChild(box);
  return box;
}

function _hideSuggest() {
  const box = document.getElementById('search-suggest');
  if (box) box.style.display = 'none';
  _searchSelectedIdx = -1;
}

function _selectSuggestion(ticker) {
  const inp = document.getElementById('search-input');
  if (inp) inp.value = ticker;
  _hideSuggest();
  _lookupTicker(ticker);
}

async function _lookupTicker(ticker) {
  try {
    const p = new URLSearchParams({ market: currentMarket, strategy: currentStrategy });
    const res = await fetch(`/api/ticker/${encodeURIComponent(ticker)}?${p}`);
    if (!res.ok) {
      setStockListMsg(`'${esc(ticker)}' 종목 못 찾음.`);
      return;
    }
    const data = await res.json();
    if (!data || data.error || !data.Ticker) {
      setStockListMsg(`'${esc(ticker)}' 종목 못 찾음.`);
      return;
    }
    openDetail(data.Ticker);
  } catch (err) {
    console.error('search lookup failed:', err);
    setStockListMsg('검색 실패. 서버 상태 확인 ㄱㄱ');
  }
}

async function _fetchSuggestions(q, box) {
  try {
    // 한글이 섞이면 KR 우선, 그 외에는 currentMarket 우선 — 매칭 0건이면 반대 시장도 조회한다.
    const hasHangul = /[ㄱ-힝]/.test(q);
    const primary = hasHangul ? 'KR' : currentMarket;
    const secondary = primary === 'KR' ? 'US' : 'KR';
    const fetchOne = async (mkt) => {
      const p = new URLSearchParams({ q, market: mkt });
      const r = await fetch(`/api/search?${p}`);
      return await r.json();
    };
    let hits = await fetchOne(primary);
    if (!hits || !hits.length) hits = await fetchOne(secondary);
    if (!hits || !hits.length) { box.style.display = 'none'; return; }
    box.innerHTML = hits.map((h, i) =>
      `<div class="search-suggest-item" data-ticker="${esc(h.ticker)}" data-idx="${i}"
        style="padding:8px 12px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);font-size:13px;"
        onmousedown="_selectSuggestion('${esc(h.ticker)}')"
        onmouseenter="this.style.background='var(--bg-secondary)'" onmouseleave="this.style.background=''">
        <span style="font-weight:600;color:var(--text-primary);">${esc(h.ticker)}</span>
        <span style="color:var(--text-tertiary);font-size:11px;max-width:60%;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(h.name)}</span>
      </div>`
    ).join('');
    box.style.display = 'block';
    _searchSelectedIdx = -1;
  } catch (_) {
    box.style.display = 'none';
  }
}

function _navigateSuggest(box, dir) {
  const items = box.querySelectorAll('.search-suggest-item');
  if (!items.length) return;
  items.forEach(el => el.style.background = '');
  _searchSelectedIdx = Math.max(-1, Math.min(items.length - 1, _searchSelectedIdx + dir));
  if (_searchSelectedIdx >= 0) {
    items[_searchSelectedIdx].style.background = 'var(--bg-secondary)';
    items[_searchSelectedIdx].scrollIntoView({ block: 'nearest' });
  }
}

function initSearch() {
  const inp = document.getElementById('search-input');
  if (!inp) return;

  inp.setAttribute('placeholder', '종목명 또는 티커 검색 (예: RF, 삼성, AAPL)');
  const box = _createSuggestBox(inp);
  const clearBtn = document.getElementById('search-clear');

  // X 버튼 가시성 토글
  function _toggleClearBtn() {
    if (clearBtn) clearBtn.hidden = !inp.value.trim();
  }

  // X 버튼 클릭 — 검색어만 초기화
  if (clearBtn) {
    clearBtn.addEventListener('mousedown', (e) => e.preventDefault()); // blur 방지
    clearBtn.addEventListener('click', () => {
      if (!inp.value) return;
      inp.value = '';
      clearTimeout(_searchTimer);
      _hideSuggest();
      _searchSelectedIdx = -1;
      _toggleClearBtn();
      inp.focus();
      if (_searchBaseStocks().length) _refreshFilteredView();
    });
  }

  // 입력 중에는 자동완성만 — 스캐너 필터는 Enter/선택 시에만 적용 (속도 개선)
  inp.addEventListener('input', () => {
    _toggleClearBtn();
    const q = inp.value.trim();
    // 입력이 비면 기존 필터도 해제
    if (q.length < 1) {
      _hideSuggest();
      if (_searchBaseStocks().length) _refreshFilteredView();
      return;
    }
    // 자동완성 제안
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(() => _fetchSuggestions(q, box), 200);
  });

  inp.addEventListener('keydown', async (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); _navigateSuggest(box, 1); return; }
    if (e.key === 'ArrowUp')   { e.preventDefault(); _navigateSuggest(box, -1); return; }
    if (e.key === 'Escape')    { _hideSuggest(); return; }
    if (e.key !== 'Enter') return;
    e.preventDefault();
    // 선택된 제안이 있으면 그걸 사용
    const items = box.querySelectorAll('.search-suggest-item');
    if (_searchSelectedIdx >= 0 && items[_searchSelectedIdx]) {
      const tk = items[_searchSelectedIdx].dataset.ticker;
      inp.value = tk;
      _hideSuggest();
      _lookupTicker(tk);
      return;
    }
    _hideSuggest();
    const raw = inp.value.trim();
    if (!raw) return;
    const localMatches = _applySearchFilter(_searchBaseStocks());
    if (localMatches.length > 0) {
      _refreshFilteredView();
      return;
    }
    _lookupTicker(raw.toUpperCase());
  });

  inp.addEventListener('blur', () => setTimeout(_hideSuggest, 200));
}

// ── 디테일 페이지 ────────────────────────────────────────────────────────

async function loadDetail(ticker) {
  const p = new URLSearchParams({ market: currentMarket, strategy: currentStrategy });
  const cacheKey = `ticker:${ticker}:${currentMarket}:${currentStrategy}`;
  const cached = _clientCache.get(cacheKey);
  if (cached) { populateDetail(cached); loadScoreSparkline(ticker); return; }
  try {
    const res = await fetch(`/api/ticker/${ticker}?${p}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    _clientCache.set(cacheKey, data);
    populateDetail(data);
    loadScoreSparkline(ticker);
  } catch (e) {
    console.error('loadDetail 실패:', e);
    setText('detail-name', '데이터를 불러올 수 없습니다');
  }
}

function populateDetail(d) {
  setText('detail-name',   d.Name   || d.Ticker || '—');
  setText('detail-ticker', d.Ticker || '—');
  setText('detail-sector', d.Sector || '—');
  const ol = document.getElementById('detail-oneliner');
  if (ol) {
    if (d.OneLiner) {
      ol.innerHTML = olHtml(d.OneLiner)
        + (d.OneLinerSub ? `<span class="oneliner-sub">→ ${esc(d.OneLinerSub)}</span>` : '')
        + (d.OneLinerData ? `<span class="oneliner-data">${esc(d.OneLinerData)}</span>` : '');
      ol.setAttribute('data-tag', d.OneLinerTag || '');
      ol.style.display = '';
    } else {
      ol.style.display = 'none';
    }
  }
  setText('detail-rsi',    d.RSI != null ? `RSI ${fmt(d.RSI, 1)}` : '—');
  setText('detail-price',  d.Price != null ? fmtPrice(d.Price) : '—');
  setText('detail-target', d.TargetPrice ? fmtPrice(d.TargetPrice) : '—');
  setText('detail-broker-target', d.BrokerTarget ? fmtPrice(d.BrokerTarget) : '컨센서스 없음');
  setText('detail-nomura-target', d.NomuraTarget ? fmtPrice(d.NomuraTarget) : '—');

  // 섹터 밸류에이션 목표가 상승여력 + 방식
  const _detNomUp = document.getElementById('detail-nomura-upside');
  if (_detNomUp) {
    if (d.NomuraTarget && d.Price) {
      const pct = ((d.NomuraTarget - d.Price) / d.Price) * 100;
      _detNomUp.textContent = (pct >= 0 ? '+' : '') + fmt(pct, 1) + '%';
      _detNomUp.style.color = pct >= 0 ? 'var(--success)' : 'var(--destructive)';
    } else {
      _detNomUp.textContent = '';
    }
  }
  const _detNomMethod = document.getElementById('detail-nomura-method');
  if (_detNomMethod) {
    const method = d.NomuraMethod || d.TargetMethod || 'DCF';
    const bias = d.NomuraBias && d.NomuraBias !== 1 ? ` · bias ${fmt(d.NomuraBias, 2)}` : '';
    const routed = d.NomuraUsed ? '메인 목표가 반영' : '참고값';
    _detNomMethod.textContent = `${method}${bias} · ${routed}`;
  }

  // DCF 적정가 대비 상승여력
  const _detDcfUpside = document.getElementById('detail-dcf-upside');
  if (_detDcfUpside) {
    if (d.TargetPrice && d.Price) {
      const pct = ((d.TargetPrice - d.Price) / d.Price) * 100;
      _detDcfUpside.textContent = (pct >= 0 ? '+' : '') + fmt(pct, 1) + '%';
      _detDcfUpside.style.color = pct >= 0 ? 'var(--success)' : 'var(--destructive)';
    } else {
      _detDcfUpside.textContent = '';
    }
  }

  // 보조 검증용 증권사 목표가 대비 상승여력
  const _detBrkUpside = document.getElementById('detail-broker-upside');
  if (_detBrkUpside) {
    if (d.BrokerTarget && d.Price) {
      const pct = ((d.BrokerTarget - d.Price) / d.Price) * 100;
      _detBrkUpside.textContent = (pct >= 0 ? '+' : '') + fmt(pct, 1) + '%';
      _detBrkUpside.style.color = pct >= 0 ? 'var(--success)' : 'var(--destructive)';
    } else {
      _detBrkUpside.textContent = '';
    }
  }

  const _detBrkSrc = document.getElementById('detail-broker-src');
  if (_detBrkSrc) {
    if (d.BrokerTarget) {
      // 짧은 출처명 + 애널리스트 수
      let shortSrc = d.BrokerTargetSource || '증권사 컨센서스';
      if (shortSrc.includes('네이버')) shortSrc = '네이버증권 컨센서스';
      else if (shortSrc.includes('Yahoo')) {
        shortSrc = 'Yahoo Finance';
        if (d.BrokerAnalystCount) shortSrc += ` (목표가 제시 애널리스트 ${d.BrokerAnalystCount}명)`;
      }
      _detBrkSrc.textContent = shortSrc;
      _detBrkSrc.title = d.BrokerTargetSource;  // 풀 텍스트는 툴팁
    } else {
      _detBrkSrc.textContent = '컨센서스 없음';
      _detBrkSrc.title = '';
    }
  }

  // 미장: '메인 목표가' 박스 + 'DCF 적정가' 줄을 숨기고 증권사 목표가만 노출.
  // 단독 노출이라 점선 보조박스 대신 메인 박스처럼 강조. (한국장은 기존 그대로)
  (function _usTargetLayout() {
    const isUS = currentMarket === 'US';
    const mainBox  = document.getElementById('detail-main-box');
    const dcfLine  = document.getElementById('detail-dcf-line');
    const auxLabel = document.getElementById('detail-aux-label');
    const auxBox   = document.getElementById('detail-aux-box');
    if (mainBox)  mainBox.style.display  = isUS ? 'none' : '';
    if (dcfLine)  dcfLine.style.display  = isUS ? 'none' : '';
    if (auxLabel) auxLabel.style.display = isUS ? 'none' : '';
    if (auxBox) {
      auxBox.style.border     = isUS ? '1px solid var(--border)'
                                     : '1px dashed var(--border)';
      auxBox.style.background = isUS
        ? 'var(--brand-tint)'
        : 'var(--bg-tertiary)';
    }
  })();

  const scoreEl = document.getElementById('detail-score');
  if (scoreEl) {
    const sc = scoreClass(d.TotalScore || 0);
    scoreEl.textContent = Math.round(d.TotalScore || 0);
    scoreEl.className   = `hero-score ${sc}`;
  }

  const sigEl = document.getElementById('detail-signal');
  if (sigEl) {
    // 리스트 퀄리티 축과 어휘 통일: 등급(S/A/B/C) 우선, STORY_STOCK은 "스토리",
    // 등급 결측 시 원시 시그널 라벨 폴백. 진입 타이밍 축은 별도 카드에서 표시.
    const { base } = _splitSignal(d.Signal);
    const g = _stockGrade(d.TotalScore);
    if (d.OneLinerTag === 'STORY_STOCK') {
      sigEl.textContent = '스토리';
      sigEl.style.color = 'var(--text-secondary)';
    } else {
      sigEl.textContent = g ? g : _trKo(base || '—');
      sigEl.style.color = signalColor(base);
    }
  }

  if (Array.isArray(d.Breakdown)) {
    renderBreakdown(d.Breakdown);
    // CAN SLIM 요약 동적 갱신
    const scores = d.Breakdown.filter(b => typeof b[1] === 'number' && b[1] > 0);
    if (scores.length) {
      const avg = scores.reduce((s, b) => s + b[1], 0) / scores.length;
      const good = scores.filter(b => b[1] >= 60).length;
      const summaryEl = document.getElementById('detail-cs-summary-text');
      const summaryWrap = document.getElementById('detail-cs-summary');
      if (summaryEl) summaryEl.innerHTML = `평균점수 ${Math.round(avg)}점 &nbsp;&middot;&nbsp; ${scores.length}항목 중 ${good}항목 양호`;
      if (summaryWrap) summaryWrap.style.display = '';
    }
  }

  // 등락률
  const _detDayChg = document.getElementById('detail-day-chg');
  if (_detDayChg) {
    const dayChg = d.DayChg || 0;
    const sign = dayChg > 0 ? '+' : '';
    _detDayChg.textContent = `${sign}${(dayChg * 100).toFixed(2)}%`;
    _detDayChg.style.color = dayChg > 0 ? 'var(--destructive)' : dayChg < 0 ? 'var(--info)' : 'var(--text-tertiary)';
  }

  // RSI 색상 동적 적용
  const _rsiArrow = document.getElementById('detail-rsi-arrow');
  if (_rsiArrow && d.RSI != null) {
    const rsiColor = d.RSI > 70 ? 'var(--destructive)' : d.RSI < 30 ? 'var(--info)' : 'var(--success)';
    _rsiArrow.setAttribute('stroke', rsiColor);
    const rsiEl = document.getElementById('detail-rsi');
    if (rsiEl) rsiEl.style.color = rsiColor;
  }

  // 보조 검증용 DCF 적정가 출처
  const _detTgtSrc = document.getElementById('detail-target-src');
  if (_detTgtSrc) {
    _detTgtSrc.textContent = d.TargetSource ? `검증: ${d.TargetSource}` : '';
  }

  // 실적 한눈에 카드 (Hero 바로 아래)
  _renderEarningsSummary(d);

  // 기술/재무 탭 렌더링 (detail.html에도 dp-tech-content, dp-finance-content 존재)
  _renderTechTab(d);
  _renderFinanceTab(d);
}

function _renderEarningsSummary(d, cardId = 'detail-earnings-card', wrapId = 'detail-earnings-chips') {
  const card = document.getElementById(cardId);
  const wrap = document.getElementById(wrapId);
  if (!card || !wrap) return;

  // 페이지에 재무 데이터가 하나도 없으면 카드 자체를 숨김
  const hasAnyFinance = d._EPSGrowth || d._ROE || d._OperatingMargin || d._PER || d._PBR || d._MarketCap;
  if (!hasAnyFinance) { card.style.display = 'none'; return; }

  const metrics = [];

  if (d._EPSGrowth != null && d._EPSGrowth !== 0) {
    const v = d._EPSGrowth * 100;
    metrics.push(['EPS 성장률', (v >= 0 ? '+' : '') + fmt(v, 1) + '%',
      v >= 25 ? 'var(--success)' : v < 0 ? 'var(--destructive)' : null, '분기 순이익 전년비']);
  } else {
    metrics.push(['EPS 성장률', '—', null, '분기 순이익 전년비']);
  }

  if (d._ROE) {
    metrics.push(['ROE', fmt(d._ROE * 100, 1) + '%',
      d._ROE >= 0.15 ? 'var(--success)' : d._ROE < 0 ? 'var(--destructive)' : null, '자기자본이익률']);
  } else {
    metrics.push(['ROE', '—', null, '자기자본이익률']);
  }

  if (d._OperatingMargin) {
    metrics.push(['영업이익률', fmt(d._OperatingMargin * 100, 1) + '%',
      d._OperatingMargin >= 0.20 ? 'var(--success)' : d._OperatingMargin <= 0 ? 'var(--destructive)' : null, '매출 대비 영업이익']);
  } else {
    metrics.push(['영업이익률', '—', null, '매출 대비 영업이익']);
  }

  wrap.innerHTML = metrics.map(([l, v, c, s]) => `
    <div style="flex:1; min-width:92px; padding:10px 12px; border:1px solid var(--border); border-radius:12px; background:var(--bg-tertiary);">
      <div style="font-size:10px; font-weight:700; color:var(--text-tertiary); margin-bottom:4px;">${l}</div>
      <div style="font-size:16px; font-weight:800; color:${c || 'var(--text-primary)'};">${v}</div>
      <div style="font-size:9px; color:var(--text-tertiary); margin-top:2px;">${s}</div>
    </div>`).join('');
  card.style.display = '';
}

async function loadScoreSparkline(ticker) {
  const wrap = document.getElementById('score-sparkline-wrap');
  const svg  = document.getElementById('score-sparkline');
  const meta = document.getElementById('score-sparkline-meta');
  if (!wrap || !svg || !meta) return;

  try {
    const res = await fetch(`/api/score-history/${encodeURIComponent(ticker)}?market=${encodeURIComponent(currentMarket)}&days=30`);
    if (!res.ok) return;
    const { points } = await res.json();
    if (!points || points.length < 2) return;

    const scores = points.map(p => p.score).filter(s => s != null);
    if (scores.length < 2) return;

    const W = 200, H = 40, PAD = 3;
    const minS = Math.min(...scores), maxS = Math.max(...scores);
    const range = maxS - minS || 1;
    const toX = i => PAD + (i / (scores.length - 1)) * (W - PAD * 2);
    const toY = s => H - PAD - ((s - minS) / range) * (H - PAD * 2);

    const pts = scores.map((s, i) => `${toX(i).toFixed(1)},${toY(s).toFixed(1)}`).join(' ');
    const last = scores[scores.length - 1], first = scores[0];
    const delta = last - first;
    const color = delta >= 0 ? 'var(--success, #22c55e)' : 'var(--destructive, #ef4444)';

    svg.innerHTML = `
      <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="${toX(scores.length-1).toFixed(1)}" cy="${toY(last).toFixed(1)}" r="2.5" fill="${color}"/>`;

    const dateFirst = points.find(p => p.score != null)?.date || '';
    const dateLast  = [...points].reverse().find(p => p.score != null)?.date || '';
    const sign = delta >= 0 ? '+' : '';
    meta.innerHTML = `<span style="color:${color}">${sign}${delta.toFixed(1)}pt</span> · ${dateFirst} ~ ${dateLast} (${scores.length}일)`;
    wrap.style.display = '';
  } catch (e) {
    console.warn('sparkline 로드 실패:', e);
  }
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

/* 보조지표 영문→한글 번역 */
const _KALMAN_KO = {'BUY_TREND':'상승 추세','SELL_TREND':'하락 추세','POSSIBLE_REVERSAL':'반전 가능','NEUTRAL':'관망','OVERHEATED':'과열 구간','OVERSOLD':'과매도 구간','STRONG_BUY':'강한 상승','STRONG_SELL':'강한 하락'};
const _MARKET_KO = {'STRONG_BULL':'강한 상승장','BULL':'상승장','SIDEWAYS (Leaning Bull)':'횡보(상승 우위)','SIDEWAYS':'횡보','BEAR':'하락장','STRONG_BEAR':'강한 하락장'};
function _auxTextKo(badge, text, st) {
  if (badge === 'MATH') {
    const hm = text.match(/Hurst\s+([\d.]+)/);
    const km = text.match(/Kalman\s+(\S+)/);
    const h = hm ? hm[1] : '—';
    const k = km ? (_KALMAN_KO[km[1]] || km[1].replace('SELL','경계')) : '—';
    const hv = parseFloat(h);
    const hDesc = isNaN(hv) ? '' : hv > 0.7 ? '강한 추세' : hv > 0.5 ? '추세 지속' : hv > 0.4 ? '약한 추세' : '역추세(반전 가능)';
    const hTxt = hDesc ? `${h} (${hDesc})` : h;
    if (st === 'pass') return `추세 지속성 ${hTxt} · 칼만 ${k} — 신뢰도 높음`;
    if (st === 'warn') return `추세 지속성 ${hTxt} · 칼만 ${k} — 신뢰도 낮음`;
    return `추세 지속성 ${hTxt} · 칼만 ${k}`;
  }
  if (badge === 'M') {
    for (const [eng, ko] of Object.entries(_MARKET_KO)) {
      if (text.includes(eng)) return text.includes('Cap') ? `${ko} — 점수 상한 적용` : ko;
    }
  }
  if (badge === 'FS') {
    return text.replace('Fail-Safe', '안전장치').replace('EPS<0', 'EPS 적자');
  }
  if (badge === 'SG') {
    const mm = text.match(/×\s*([\d.]+)/);
    const cr = text.match(/\(([^)]+)\)/);
    return `슈퍼 성장 배율 ×${mm ? mm[1] : '?'}${cr ? ' (' + cr[1] + ')' : ''}`;
  }
  return text.replace('Bear Cap', '하락장 상한');
}

/* Breakdown 영문 레이블 → 한글 (label, 한줄 설명) */
const _LABEL_KO = {
  'EPS 가속도 (Current QE)':        ['EPS 가속도',       '분기 순이익 성장 가속 여부'],
  '연간실적 ROE 기준 (Annual EPS)': ['연간 ROE 실적',    '자기자본이익률 기준 충족 여부'],
  '신고가·피벗 돌파 (New Highs)':   ['신고가·피벗 돌파', '52주 최고가 및 패턴 돌파'],
  '거래량 확인 돌파 (Supply/Demand)':['거래량 확인 돌파', '기관 참여를 동반한 거래량 급증'],
  '주도주 판별 (Leader/Laggard)':   ['주도주 판별',      '시장 대비 상대강도 측정'],
  '기관 수급 (Institutional)':      ['기관 수급',        '기관 자금의 유입·유출 흐름'],
  '시장 방향 (Market Direction)':   ['시장 방향',        '전체 시장 추세와 방향성'],
  'Fama-French Factor':             ['가치·퀄리티 팩터', '저평가·고품질 종목 선별'],
  'Mean Reversion':                 ['평균 회귀',        'RSI·Z-Score 기반 반등/조정 가능성'],
  'Momentum (Carhart)':             ['모멘텀',           '12개월 수익률 기반 추세 지속력'],
  'Multi-Timeframe':                ['다중 시간대',      '단기·중기·장기 추세 종합'],
  'Drawdown Risk':                  ['낙폭 위험도',      '최근 최대 하락폭(MDD) 평가'],
  'Smart Money Flow':               ['스마트머니 흐름',  '기관 자금 흐름과 OBV 추세'],
  'Analyst Target':                 ['DCF 적정가','DCF 적정가 대비 상승 여력'],
  'Short Interest':                 ['공매도 비율',      '공매도 부담 수준 평가'],
  'Hurst Exponent':                 ['허스트 지수',      '추세 지속성과 방향 예측력'],
  'Kalman Filter':                  ['칼만 필터',        '노이즈 제거 후 추세 신호'],
  'Stat Arb Z-Score':               ['통계적 Z-Score',   '통계적 과매수/과매도 위치'],
  'Vol-Adjusted (DE Shaw)':         ['변동성 조정',      '변동성 대비 수익률 효율성'],
  '시장 심리 프록시':               ['시장 심리 추정',   '가격·거래량 기반 투자 심리'],
  'ORB 돌파':                       ['ORB 돌파',         '시가 범위 돌파 신호'],
  'NR7 변동성 압축':                ['NR7 압축',         '7일 최저 변동폭 수축'],
  '볼린저 반등':                    ['볼린저 반등',      '볼린저밴드 하단 반등 신호'],
};

const _CS_WHAT = {
  'C':         '분기 순이익 증가율 — 최근 분기 EPS가 전년 동기 대비 얼마나 성장했는지 봄. 오닐 기준 25% 이상이 목표임.',
  'A':         '연간 ROE 기준 — 자기자본으로 얼마나 벌어들이는지 봄. 오닐 기준 17% 이상이 합격선임.',
  'N':         '신고가·피벗 돌파 — 52주 최고가 근접 여부 + 컵앤핸들 피벗 돌파 측정함.',
  'S':         '거래량 수반 돌파 — 기관 참여 동반한 거래량 급증으로 진짜 돌파인지 확인함.',
  'L':         '시장 주도주 여부 — 시장 대비 상대강도(RS Rating) 측정함. 80점 이상이 주도주 기준임.',
  'I':         '기관 자금 수급 — 스마트머니(기관·세력)의 유입/유출 압력을 자금 흐름 지표로 측정함.',
  'M':         '시장 방향 — 지금 시장이 상승장(Bull)인지 하락장(Bear)인지 추세와 ADX로 봄.',
  'Quant':     '퀀트 보조 전략 — Fama-French 팩터, 모멘텀, 평균회귀 등 통계 기반 전략 점수임.',
  'Math':      '수학적 시계열 분석 — 허스트 지수(추세 지속성), 칼만 필터(노이즈 제거), Z-Score(통계적 위치) 활용함.',
  'Adj':       '변동성 조정 — DE Shaw 방식으로 변동성 대비 수익률 효율성 평가해 최종 점수 미세 조정함.',
  'Sentiment': '시장 심리 추정 — 뉴스 없이 가격·거래량만으로 투자 심리 간접 측정함.',
  'Scalp':     '단타 시그널 — ORB 돌파, NR7 변동성 압축, 볼린저밴드 반등 등 단기 트레이딩 신호임.',
};

function _breakdownItemHtml(item) {
  const [label, score, desc] = item;

  // CAN SLIM 원칙 요약 — 구조화된 컬러 카드
  if (label.includes('CAN SLIM')) {
    const lines = (desc || '').split('\n').filter(Boolean);
    const main = [], aux = [];
    for (const line of lines) {
      let st = 'neutral';
      if (/[✅🔥🚀⭐🔔]/.test(line)) st = 'pass';
      else if (/[⛔📉🚫]/.test(line)) st = 'fail';
      else if (/⚠/.test(line)) st = 'warn';
      const bm = line.match(/^\[([^\]]+)\]\s*(.*)/);
      const lm = line.match(/^([CANSLIM])\S*\s+(.*)/);
      const em = line.match(/^[⛔⭐]\s*(.*)/);
      const dm = line.match(/^\ud83d\udc8e\s*(.*)/);  // \ud83d\udc8e \uc9c0\uc8fc\uc0ac NAV
      if (dm) { main.unshift({b: '\ud83d\udc8e', t: dm[1] || line, st: /\uc800\ud3c9\uac00/.test(line) ? 'pass' : /\uace0\ud3c9\uac00/.test(line) ? 'fail' : 'neutral'}); }
      else if (bm) { aux.push({b: bm[1].replace(/[^\w]/g,''), t: bm[2], st}); }
      else if (lm) { main.push({b: lm[1], t: lm[2], st}); }
      else if (em) { const badge = line.startsWith('\u26d4') ? 'FS' : 'SG'; aux.push({b: badge, t: em[1], st}); }
      else { aux.push({b: '\u00b7', t: line, st}); }
    }
    let h = '<div class="cs-canslim-wrap">';
    h += `<div class="cs-canslim-hdr">${esc(label)}</div>`;
    for (const g of main) h += `<div class="cs-tag cs-tag-${g.st}"><span class="cs-tag-b">${esc(g.b)}</span><span class="cs-tag-t">${esc(_trKo(g.t))}</span></div>`;
    if (aux.length) {
      h += '<div class="cs-tag-sep">\ubcf4\uc870 \uc9c0\ud45c</div>';
      for (const g of aux) h += `<div class="cs-tag cs-tag-${g.st}"><span class="cs-tag-b cs-tag-b-aux">${esc(g.b)}</span><span class="cs-tag-t">${esc(_auxTextKo(g.b, g.t, g.st))}</span></div>`;
    }
    return h + '</div>';
  }

  const badgeMatch = label.match(/^\[([^\]]{1,12})\]/);
  if (!badgeMatch) {
    // 섹션 헤더 — 전체 너비 배너
    const descHtml = esc(desc || '').replace(/\n/g, '<br>');
    return `<div class="cs-section-banner">
  <div style="font-size:13px;font-weight:700;color:var(--brand);margin-bottom:4px;">${esc(label)}</div>
  <div style="font-size:12px;color:var(--text-secondary);line-height:1.6;">${descHtml}</div>
</div>`;
  }
  const badge    = badgeMatch[1];
  const cleanLbl = label.replace(/^\[[^\]]+\]\s*/, '');
  const _isNum   = (typeof score === 'number' && isFinite(score));
  const _isZero  = _isNum && Math.abs(score) < 1e-9;
  // 점수 0 = 실패가 아니라 '기여 없음/중립' → 빨강 대신 회색 처리
  const sc       = (_isZero || !_isNum) ? '' : scoreClass(score);
  const barW     = Math.min(100, Math.max(0, score));
  // 한글 레이블 + 한줄 설명
  const mapped    = _LABEL_KO[cleanLbl];
  const koLbl     = mapped ? mapped[0] : cleanLbl.replace(/\s*\([^)]*\)\s*/g, '');
  const koHint    = mapped ? mapped[1] : (_CS_WHAT[badge] || '').split('—')[0].trim();
  // desc 파싱: 설명 / 계산식 분리
  const allLines = (desc || '').split('\n').filter(Boolean);
  const formulaLine = allLines.find(l => l.startsWith('📐'));
  const descLines = allLines.filter(l => !l.startsWith('📐'));
  const briefDesc = descLines.slice(0, 2).join(' · ');
  const fullDesc = descLines.join(' ');
  return `<div class="cs-card cs-expandable" onclick="openCalcPopup(${typeof _bdIdx === 'number' ? _bdIdx : 0})">
  <div class="cs-card-top">
    <div class="cs-badge">${esc(badge)}</div>
    <div class="cs-card-label">
      <div class="cs-label-en">${esc(koLbl)}</div>
      ${koHint ? `<div class="cs-label-concept">${esc(koHint)}</div>` : ''}
    </div>
    <svg class="cs-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--text-tertiary)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
  </div>
  <div class="cs-score-big ${sc}">${(typeof score === 'number' && isFinite(score)) ? _scoreVerdict(score) : '<span style="font-size:11px;color:var(--text-tertiary);">데이터 부족</span>'}</div>
  <div class="cs-bar-wrap"><div class="cs-bar-fill ${sc}" style="width:${barW}%"></div></div>
  ${briefDesc ? `<div class="cs-desc-brief">${esc(_trKo(briefDesc))}</div>` : ''}
</div>`;
}

let _bdItems = [];

function renderBreakdown(items) {
  _bdItems = items;
  const list = document.getElementById('dp-breakdown-list');
  if (list) list.innerHTML = `<div class="cs-card-grid">${items.map((item, i) => { _bdIdx = i; return _breakdownItemHtml(item); }).join('')}</div>`;
}

function openCalcPopup(idx) {
  const item = _bdItems[idx];
  if (!item) return;
  const [label, score, desc, detail] = item;

  const badgeMatch = label.match(/^\[([^\]]{1,12})\]/);
  const badge = badgeMatch ? badgeMatch[1] : '';
  const cleanLbl = label.replace(/^\[[^\]]+\]\s*/, '');
  const mapped = _LABEL_KO[cleanLbl];
  const koLbl = mapped ? mapped[0] : cleanLbl.replace(/\s*\([^)]*\)\s*/g, '');
  const sc = scoreClass(score);
  const barW = Math.min(100, Math.max(0, score));

  // desc 파싱
  const allLines = (desc || '').split('\n').filter(Boolean);
  const formulaLine = allLines.find(l => l.startsWith('📐'));
  const descText = allLines.filter(l => !l.startsWith('📐')).join(' ');

  // detail 파싱 (4th element from engine)
  const detailLines = (detail || '').split('\n').filter(Boolean);
  let detailHtml = '';
  if (detailLines.length) {
    detailHtml = detailLines.map(l => {
      if (l.startsWith('📊') || l.startsWith('📐')) return `<div class="cp-section-title">${esc(l)}</div>`;
      if (l.startsWith('•') || l.startsWith('①') || l.startsWith('②') || l.startsWith('③') || l.startsWith('④'))
        return `<div class="cp-step">${esc(l)}</div>`;
      return `<div class="cp-line">${esc(l)}</div>`;
    }).join('');
  }

  // 팝업 생성
  let el = document.getElementById('calc-popup-overlay');
  if (!el) {
    el = document.createElement('div');
    el.id = 'calc-popup-overlay';
    el.className = 'cp-overlay';
    el.onclick = function(e) { if (e.target === el) closeCalcPopup(); };
    document.body.appendChild(el);
  }

  el.innerHTML = `
    <div class="cp-sheet">
      <div class="cp-handle" onclick="closeCalcPopup()"><div class="cp-handle-bar"></div></div>
      <div class="cp-header">
        <div class="cs-badge" style="width:36px;height:36px;font-size:14px;border-radius:10px;">${esc(badge)}</div>
        <div style="flex:1;min-width:0;">
          <div style="font-size:15px;font-weight:700;color:var(--text-primary);">${esc(koLbl)}</div>
          <div style="font-size:11px;color:var(--text-tertiary);margin-top:2px;">${esc(cleanLbl)}</div>
        </div>
        <div class="cp-score ${sc}">${fmt(score, 1)}<span style="font-size:12px;font-weight:500;color:var(--text-tertiary);">/100</span></div>
      </div>
      <div class="cp-bar-wrap"><div class="cs-bar-fill ${sc}" style="width:${barW}%;height:100%;border-radius:4px;"></div></div>
      <div class="cp-desc">${esc(_trKo(descText))}</div>
      ${formulaLine ? `<div class="cp-formula">${esc(formulaLine)}</div>` : ''}
      ${detailHtml ? `<div class="cp-detail">${detailHtml}</div>` : ''}
      <button class="cp-close-btn" onclick="closeCalcPopup()">닫기</button>
    </div>`;
  el.classList.add('visible');
  document.body.style.overflow = 'hidden';
}

function closeCalcPopup() {
  const el = document.getElementById('calc-popup-overlay');
  if (el) el.classList.remove('visible');
  document.body.style.overflow = '';
}

// ── 디테일 드로어 ────────────────────────────────────────────────────────

async function openDetail(ticker) {
  const overlay = document.getElementById('detail-overlay');
  const panel   = document.getElementById('detail-panel');
  if (!overlay || !panel) { location.href = `/detail/${encodeURIComponent(ticker)}?market=${currentMarket}&strategy=${currentStrategy}`; return; }

  const seq = ++_detailSeq;
  _clearPanelDetail();
  overlay.classList.add('visible');
  panel.classList.add('open');
  document.body.style.overflow = 'hidden';
  const _dpLink = document.getElementById('dp-detail-link');
  if (_dpLink) _dpLink.href = '/detail/' + encodeURIComponent(ticker) + '?market=' + currentMarket + '&strategy=' + currentStrategy;

  // 외부 링크 (네이버증권 / 야후파이낸스)
  const extLink = document.getElementById('dp-link-external');
  if (extLink) {
    const isKR = currentMarket === 'KR';
    const code = ticker.replace(/\.(KS|KQ)$/, '');
    if (isKR) {
      extLink.href = 'https://finance.naver.com/item/main.naver?code=' + encodeURIComponent(code);
      extLink.textContent = '📊 네이버증권';
    } else {
      extLink.href = 'https://finance.yahoo.com/quote/' + encodeURIComponent(ticker);
      extLink.textContent = '📊 Yahoo Finance';
    }
    extLink.style.display = 'flex';
  }

  // 스캔 데이터가 이미 있으면 즉시 렌더링 (빈 드로어 방지)
  const cached = _stockMap[ticker];
  if (cached) _populatePanelDetail(cached, /* skipFourAxis */ true, /* skipVerdict */ true);

  // 4축 차트 + 종목 상세 + AQ 시그널 + 증권사 컨센서스 + 센티먼트를 모두 병렬로 요청
  // F6: 4축 차트는 IntersectionObserver로 가시영역 진입 시 1회 호출 (rootMargin 200px 사전로딩)
  _scheduleLoadDpFourAxis(ticker);
  _loadSentiment(ticker, currentMarket, seq);
  _loadInvestorFlow(ticker, currentMarket, seq);
  loadConsensus(ticker, 'dp-consensus-card', 'dpcons');
  _loadSerenity(ticker, seq);

  try {
    const p   = new URLSearchParams({ market: currentMarket, strategy: currentStrategy });
    const res = await fetch(`/api/ticker/${encodeURIComponent(ticker)}?${p}`);
    if (seq !== _detailSeq) return; // 종목 전환됨 — stale 응답 무시
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    // 한줄평은 시드(score/RSI 양자화 + 시간 회전)에 따라 매번 달라질 수 있다.
    // 스캐너에서 본 한줄평이 패널 열 때 보였다가 API 응답 도착 시 다른 문구로
    // 바뀌는 깜빡임을 막기 위해, 캐시된 한줄평이 있으면 무조건 그대로 유지한다.
    // (버킷이 달라도 시각적 깜빡임이 더 거슬리므로 캐시 우선.)
    if (cached && cached.OneLiner) {
      data.OneLiner = cached.OneLiner;
      if (cached.OneLinerData != null) data.OneLinerData = cached.OneLinerData;
      if (cached.OneLinerTag != null) data.OneLinerTag = cached.OneLinerTag;
      if (cached.OneLinerSub  != null) data.OneLinerSub  = cached.OneLinerSub;
    }
    _populatePanelDetail(data, /* skipFourAxis */ true);
  } catch (e) {
    if (seq !== _detailSeq) return;
    console.error('openDetail 실패:', e);
    if (!cached) setText('dp-name', '데이터를 불러올 수 없습니다');
  }
}


async function _loadInvestorFlow(ticker, market, seq) {
  if (market !== 'KR') return;
  try {
    const res = await fetch(`/api/investor_flow/${encodeURIComponent(ticker)}?market=KR`);
    if (seq !== _detailSeq) return;
    if (!res.ok) return;
    const data = await res.json();
    if (!data.ok || seq !== _detailSeq) return;
    if (!_lastDetailData || _lastDetailData.Ticker !== ticker) return;
    Object.assign(_lastDetailData, data);
    _renderInvestorCard(_lastDetailData);
  } catch (e) {
    console.debug('investor_flow 로드 실패:', e);
  }
}

async function _loadSerenity(ticker, seq) {
  const card = document.getElementById('dp-serenity-card');
  if (card) card.style.display = 'none';
  try {
    const res = await fetch(`/api/serenity/${encodeURIComponent(ticker)}`);
    if (seq !== _detailSeq) return;
    if (!res.ok) return; // 커버리지 없는 종목 — 조용히 숨김
    const d = await res.json();
    if (!d || d.error || seq !== _detailSeq) return;
    const colorMap = { green: '#22c55e', yellow: '#f59e0b', red: '#ef4444', neutral: '#94a3b8' };
    const bgMap    = { green: 'rgba(34,197,94,0.08)', yellow: 'rgba(245,158,11,0.08)', red: 'rgba(239,68,68,0.08)', neutral: 'rgba(148,163,184,0.08)' };
    const c = d.color || 'neutral';
    const badge = document.getElementById('dp-serenity-signal-badge');
    const signalEl = document.getElementById('dp-serenity-signal');
    const quoteEl  = document.getElementById('dp-serenity-quote');
    const ctxEl    = document.getElementById('dp-serenity-context');
    const linkEl   = document.getElementById('dp-serenity-link');
    const dateEl   = document.getElementById('dp-serenity-date');
    if (badge)   { badge.textContent = d.signal.split('—')[0].trim().split('(')[0].trim(); badge.style.background = bgMap[c]; badge.style.color = colorMap[c]; badge.style.borderColor = colorMap[c] + '44'; }
    if (signalEl) signalEl.textContent = d.signal;
    if (quoteEl)  quoteEl.textContent  = d.quote ? `"${d.quote}"` : '';
    if (ctxEl)    ctxEl.textContent    = d.context || '';
    if (linkEl)   { linkEl.href = d.tweet_url || '#'; linkEl.style.display = d.tweet_url ? '' : 'none'; }
    if (dateEl)   dateEl.textContent = d.tweet_date ? d.tweet_date.slice(0, 10) : '';
    if (card) card.style.display = '';
  } catch (e) {
    console.debug('serenity 로드 실패:', e);
  }
}

async function _loadSentiment(ticker, market, seq) {
  if (market !== 'US') return;
  try {
    const res = await fetch(`/api/sentiment/${encodeURIComponent(ticker)}?market=${market}`);
    if (seq !== _detailSeq) return;
    if (!res.ok) return;
    const data = await res.json();
    if (!data.ok || seq !== _detailSeq) return;
    if (!_lastDetailData || _lastDetailData.Ticker !== ticker) return;
    // 키 머지 후 수급/센티먼트 카드만 재렌더
    Object.assign(_lastDetailData, data);
    _renderInvestorCard(_lastDetailData);
    _renderFhLogo(_lastDetailData);
  } catch (e) {
    console.debug('sentiment 로드 실패:', e);
  }
}

function closeDetailBtn() {
  const overlay = document.getElementById('detail-overlay');
  const panel   = document.getElementById('detail-panel');
  if (!overlay || !panel) return;
  overlay.classList.remove('visible');
  panel.classList.remove('open');
  document.body.style.overflow = '';
}

function _clearPanelDetail() {
  const _ab = document.getElementById('dp-about-box');
  if (_ab) _ab.style.display = 'none';
  const _mc = document.getElementById('dp-moat-card');
  if (_mc) _mc.style.display = 'none';
  const _pc = document.getElementById('dp-peers-card');
  if (_pc) _pc.style.display = 'none';
  const _sc = document.getElementById('dp-segments-card');
  if (_sc) _sc.style.display = 'none';
  const _oc = document.getElementById('dp-ownership-card');
  if (_oc) _oc.style.display = 'none';
  const _ec2 = document.getElementById('dp-events-card');
  if (_ec2) _ec2.style.display = 'none';
  const _ic = document.getElementById('dp-insider-card');
  if (_ic) _ic.style.display = 'none';
  const _src = document.getElementById('dp-serenity-card');
  if (_src) _src.style.display = 'none';
  const _tm = document.getElementById('dp-thermo-marker');
  if (_tm) _tm.style.left = '50%';
  const _lb = document.getElementById('dp-leader-badge');
  if (_lb) _lb.style.display = 'none';
  const _el = document.getElementById('dp-link-external');
  if (_el) _el.style.display = 'none';
  const _nb = document.getElementById('dp-news-bar');
  if (_nb) _nb.style.display = 'none';
  ['dp-name','dp-ticker','dp-sector','dp-about','dp-score','dp-signal',
   'dp-price','dp-day-chg','dp-target','dp-broker-target',
   'dp-axis-eps-val','dp-axis-roe-val','dp-axis-mom-val','dp-axis-rs-val'].forEach(id => setText(id, '…'));
  ['dp-dcf-upside','dp-broker-upside'].forEach(id => { const el = document.getElementById(id); if (el) el.textContent = ''; });
  const loading = '<div style="padding:32px 16px;text-align:center;color:var(--text-tertiary);font-size:13px;">로딩 중...</div>';
  const bl = document.getElementById('dp-breakdown-list');
  if (bl) bl.innerHTML = loading;
  const tc = document.getElementById('dp-tech-content');
  if (tc) tc.innerHTML = loading;
  const fc = document.getElementById('dp-finance-content');
  if (fc) fc.innerHTML = loading;
  const cc = document.getElementById('dp-consensus-card');
  if (cc) cc.style.display = 'none';
  const ec = document.getElementById('dp-earnings-card');
  if (ec) ec.style.display = 'none';
  // 새 카드 초기화
  ['dp-risk-summary','dp-factor-waterfall','dp-drawdown-risk','dp-ac-card','dp-liquidity-card'].forEach(id => {
    const el = document.getElementById(id); if (el) el.innerHTML = '';
  });
  const rg = document.getElementById('dp-risk-gauge');
  if (rg) rg.style.display = 'none';
}

function _populatePanelDetail(d, skipFourAxis, skipVerdict) {
  _lastDetailData = d;
  _renderHeroWatermark(d);
  setText('dp-name',    d.Name   || d.Ticker || '—');
  setText('dp-ticker',  d.Ticker || '—');
  setText('dp-sector',  d.Sector || '—');
  const aboutText = d.Desc || d.About || _industryKo(d.Industry) || '';
  const aboutEl   = document.getElementById('dp-about');
  const aboutBox  = document.getElementById('dp-about-box');
  if (aboutEl)  aboutEl.textContent = aboutText;
  if (aboutBox) aboutBox.style.display = aboutText ? '' : 'none';
  try { _renderMoatDetail(d); } catch (e) { console.error('moat render failed:', e); }
  try { _loadPeersCard(d.Ticker, (typeof currentMarket !== 'undefined' && currentMarket) || 'US'); } catch (e) { console.error('peers load failed:', e); }

  try { _loadSegmentsCard(d.Ticker); } catch (e) { console.error('segments load failed:', e); }
  try { _loadOwnershipCard(d.Ticker); } catch (e) { console.error('ownership load failed:', e); }
  try { _loadEventsCard(d.Ticker); } catch (e) { console.error('events load failed:', e); }
  try { _loadInsiderCard(d.Ticker); } catch (e) { console.error('insider load failed:', e); }
  setText('dp-price',   d.Price  != null ? fmtPrice(d.Price) : '—');
  setText('dp-target',  d.TargetPrice ? fmtPrice(d.TargetPrice) : '—');
  setText('dp-broker-target', d.BrokerTarget ? fmtPrice(d.BrokerTarget) : '—');

  // 미장: '메인 목표가' 행을 숨기고 증권사 목표가만 노출 (한국장은 기존 그대로)
  const _dpTgtRow = document.getElementById('dp-target-row');
  if (_dpTgtRow) _dpTgtRow.style.display = currentMarket === 'US' ? 'none' : '';

  // 메인 목표가 상승여력 (노무라식 우선, 없으면 DCF)
  const dpDcfUp = document.getElementById('dp-dcf-upside');
  if (dpDcfUp) {
    if (d.TargetPrice && d.Price) {
      const pct = ((d.TargetPrice - d.Price) / d.Price) * 100;
      dpDcfUp.textContent = (pct >= 0 ? '+' : '') + fmt(pct, 1) + '%';
      dpDcfUp.style.color = pct >= 0 ? 'var(--success)' : 'var(--destructive)';
    } else { dpDcfUp.textContent = ''; }
  }

  // 증권사 목표가 상승여력 (보조 검증)
  const dpBrkUp = document.getElementById('dp-broker-upside');
  if (dpBrkUp) {
    if (d.BrokerTarget && d.Price) {
      const pct = ((d.BrokerTarget - d.Price) / d.Price) * 100;
      dpBrkUp.textContent = (pct >= 0 ? '+' : '') + fmt(pct, 1) + '%';
      dpBrkUp.style.color = pct >= 0 ? 'var(--success)' : 'var(--destructive)';
    } else { dpBrkUp.textContent = ''; }
  }

  const brkSrcEl = document.getElementById('dp-broker-src');
  if (brkSrcEl) {
    if (d.BrokerTarget) {
      let shortSrc = d.BrokerTargetSource || '증권사 컨센서스';
      if (shortSrc.includes('네이버')) shortSrc = '네이버증권 컨센서스';
      else if (shortSrc.includes('Yahoo')) {
        shortSrc = 'Yahoo Finance';
        if (d.BrokerAnalystCount) shortSrc += ` (목표가 제시 애널리스트 ${d.BrokerAnalystCount}명)`;
      }
      brkSrcEl.textContent = shortSrc;
      brkSrcEl.title = d.BrokerTargetSource;
    } else {
      brkSrcEl.textContent = '컨센서스 없음';
      brkSrcEl.removeAttribute('title');
    }
  }
  const tgtSrcEl = document.getElementById('dp-target-src');
  if (tgtSrcEl) {
    if (d.TargetPrice && d.TargetSource) {
      const method = d.NomuraUsed ? '섹터 밸류에이션' : 'DCF 메인';
      tgtSrcEl.textContent = method;
      tgtSrcEl.title = `메인 목표가 방식: ${method} · 출처: ${d.TargetSource}`;
    } else {
      tgtSrcEl.textContent = '';
      tgtSrcEl.removeAttribute('title');
    }
  }

  const dayChg = d.DayChg || 0;
  const chgEl  = document.getElementById('dp-day-chg');
  if (chgEl) {
    const sign = dayChg > 0 ? '+' : '';
    chgEl.textContent = `${sign}${(dayChg * 100).toFixed(2)}%`;
    chgEl.className = 'dp-stat-val ' + (dayChg > 0 ? 'chg-up' : dayChg < 0 ? 'chg-down' : 'chg-flat');
  }

  // (상승여력은 DCF/증권사 각 행에 인라인 표시됨)

  const scoreEl = document.getElementById('dp-score');
  if (scoreEl) {
    scoreEl.textContent = Math.round(d.TotalScore || 0);
    scoreEl.className   = 'dp-score-num ' + scoreClass(d.TotalScore || 0);
  }

  const sigEl = document.getElementById('dp-signal');
  if (sigEl) {
    // 리스트 퀄리티 축(_renderSignalHtml)과 동일 어휘로 통일:
    // 등급(S/A/B/C) 우선, STORY_STOCK은 "스토리", 등급 결측 시 원시 시그널 라벨 폴백.
    // 진입 타이밍 축은 드로워의 _renderQuadrant 가 별도 표시(명령형 어휘 충돌 제거).
    const { base } = _splitSignal(d.Signal);
    const g = _stockGrade(d.TotalScore);
    if (d.OneLinerTag === 'STORY_STOCK') {
      sigEl.textContent = '스토리';
      sigEl.style.color = 'var(--text-secondary)';
      sigEl.style.background = 'var(--bg-tertiary)';
    } else if (g) {
      sigEl.textContent = g;
      sigEl.style.color = signalColor(base);
      sigEl.style.background = signalBg(base);
    } else {
      sigEl.textContent = _trKo(base || '—');
      sigEl.style.color = signalColor(base);
      sigEl.style.background = signalBg(base);
    }
  }

  // 4-axis 미니 지표
  const eps = d._EPSGrowth != null && d._EPSGrowth !== 0 ? (d._EPSGrowth * 100).toFixed(1) + '%' : '—';
  const roe = d._ROE       != null ? (d._ROE       * 100).toFixed(1) + '%' : '—';
  const mom = d.Mom12M     != null ? (d.Mom12M     * 100).toFixed(1) + '%' : '—';
  const rsr = d.RSRating != null ? Math.round(d.RSRating).toString() : '—';
  setText('dp-axis-eps-val', eps);
  setText('dp-axis-roe-val', roe);
  setText('dp-axis-mom-val', mom);
  setText('dp-axis-rs-val',  rsr);

  // 실적 한눈에 카드 (Hero 바로 아래)
  _renderEarningsSummary(d, 'dp-earnings-card', 'dp-earnings-chips');

  // 진입 타이밍 카드
  if (!skipVerdict) _renderEntryVerdict(d);

  // 종합×진입 2축 사분면 배지
  _renderQuadrant(d);

  // 기술·재무 탭
  _renderTechTab(d);
  _renderFinanceTab(d);
  _renderDetailFeatures(d);
  _renderRiskGauge(d);
  _renderDrawdownRisk(d);

  if (Array.isArray(d.Breakdown)) renderBreakdown(d.Breakdown);

  // KR 마켓일 때 공시·뉴스 탭 표시
  const dnBtn = document.getElementById('dp-btn-dartnews');
  if (dnBtn) dnBtn.style.display = currentMarket === 'KR' ? '' : 'none';
  _dpDartNewsLoaded = false;  // 종목 변경 시 리셋
  // KR이면 뉴스 바를 위해 즉시 fetch (탭 클릭 전에도 표시)
  if (currentMarket === 'KR' && d.Ticker) loadDpDartNews(d.Ticker);

  // US 마켓일 때 US 인사이트 탭 표시
  const usBtn = document.getElementById('dp-btn-usinsight');
  if (usBtn) usBtn.style.display = currentMarket === 'US' ? '' : 'none';
  _dpUSInsightLoaded = false;

  // 미드캡 알파 시그널 (SP400 전용)
  _renderMidcapDetail(d);

  // MECE 분석 프레임워크 카드 (Phase 1/2/3)
  _renderValuationContext(d);
  _renderScenarioTable(d);
  _renderPriceLevels(d);

  // 투자자 동향 카드
  _renderInvestorCard(d);

  // CAN SLIM 탭으로 초기화
  switchDpTab('canslim');


  // 차트 자동 로드 (openDetail에서 병렬 호출 시 skipFourAxis=true)
  if (!skipFourAxis) {
    const tk = (document.getElementById('dp-ticker')?.textContent || '').trim();
    if (tk && tk !== '—' && tk !== '…' && _dpFourAxisLoadedFor !== tk) {
      _scheduleLoadDpFourAxis(tk);
    }
  }
}

// ── Finnhub 뉴스 헤드라인 리스트 (US, 최근 7일) ──────────────────────────
// ── Finnhub 회사 로고 (US, 헤더 dp-ticker 좌측) ──────────────────────────
function _renderFhLogo(d) {
  const nameEl = document.getElementById('dp-name');
  let img = document.getElementById('dp-fh-logo');
  const url = d && d._FH_Available ? (d._FH_Logo || '') : '';
  const safe = /^https?:\/\//i.test(url) ? url : '';
  if (!safe) { if (img) img.remove(); return; }
  if (!nameEl || !nameEl.parentNode) return;
  if (!img) {
    img = document.createElement('img');
    img.id = 'dp-fh-logo';
    img.className = 'dp-fh-logo';
    img.alt = '';
    img.onerror = () => img.remove();
    nameEl.parentNode.insertBefore(img, nameEl);  // 회사명 맨 앞
  }
  img.src = safe;
}

// ── Hero Zone 로고 워터마크 (US, 우상단 배경) ────────────────────────────
function _renderHeroWatermark(d) {
  const zone = document.getElementById('dp-section-conclusion');
  if (!zone) return;
  let img = document.getElementById('dp-hero-wm');
  const t = (d && d.Ticker ? String(d.Ticker) : '').toUpperCase();
  const isUsLogo = t && !t.includes('.') && !/^\d+$/.test(t);
  if (!isUsLogo) { if (img) img.remove(); return; }  // KR·로고없음 → 미표시
  const url = `https://static2.finnhub.io/file/publicdatany/finnhubimage/stock_logo/${t}.png`;
  if (!img) {
    img = document.createElement('img');
    img.id = 'dp-hero-wm';
    img.className = 'dp-hero-watermark';
    img.alt = '';
    img.onerror = () => img.remove();  // 로고 404 → 제거
    zone.insertBefore(img, zone.firstChild);
  }
  img.src = url;
}

// ── 투자자 동향 카드 ─────────────────────────────────────────────────────
function _renderInvestorCard(d) {
  const wrap = document.getElementById('dp-investor-card');
  const grid = document.getElementById('dp-investor-grid');
  if (!wrap || !grid) return;

  const items = [];

  // KR: 네이버 외인/기관
  if (d._Investor_Available) {
    const fmtQty = v => {
      const abs = Math.abs(v);
      const sign = v >= 0 ? '+' : '-';
      return abs >= 10000 ? `${sign}${(abs/10000).toFixed(1)}만주` : `${sign}${abs.toLocaleString()}주`;
    };
    const frgn = d._Investor_Foreign || 0;
    const inst = d._Investor_Institution || 0;
    if (frgn !== 0) items.push({ label: '외국인', value: fmtQty(frgn), color: frgn > 0 ? 'var(--success)' : 'var(--destructive)' });
    if (inst !== 0) items.push({ label: '기관', value: fmtQty(inst), color: inst > 0 ? 'var(--success)' : 'var(--destructive)' });
  }

  if (items.length === 0) { wrap.style.display = 'none'; return; }
  wrap.style.display = '';
  grid.innerHTML = items.map(it => `
    <div style="display:grid; grid-template-columns:1fr auto; align-items:baseline; gap:12px; padding:10px 2px; border-bottom:1px solid var(--border);">
      <span style="font-size:12px; font-weight:600; color:var(--text-tertiary); letter-spacing:0.01em; white-space:nowrap;">${esc(it.label)}</span>
      <span style="font-size:16px; font-weight:700; letter-spacing:-0.015em; font-variant-numeric:tabular-nums; text-align:right; color:${it.color};">${esc(it.value)}${it.sub ? `<small style="display:block; font-size:10.5px; color:var(--text-tertiary); font-weight:600; margin-top:2px; text-align:right;">${it.subIsHtml ? it.sub : esc(it.sub)}</small>` : ''}</span>
    </div>
  `).join('');
}

// ── 영어 → 한국어 번역 테이블 ────────────────────────────────────────────

const _KO_MAP = {
  // Conviction
  'HIGH': '높음', 'MID': '보통', 'LOW': '낮음',
  // Regime
  'BULL': '상승장', 'BEAR': '하락장', 'NEUTRAL': '관망', 'SIDEWAYS': '횡보',
  // Signal keywords
  'STRONG LEADER': '강력 리더', 'LEADER': '리더', 'WATCH': '관찰',
  'ACCUMULATE': '주목', 'HOLD': '보유/관망', 'SELL': '경계',
  'AVOID': '회피', 'LAGGARD': '낙후', 'BREAKOUT': '돌파',
  'MOMENTUM': '모멘텀', 'CAUTION': '주의', 'BEAR MARKET': '하락장',
  // ORB/NR7/BB signals
  'BUY': '관심', 'NO SIGNAL': '신호 없음', 'PARTIAL': '부분',
  'OVERSOLD': '과매도', 'OVERBOUGHT': '과매수',
  'NR7 COMPRESSION': 'NR7 압축', 'SQUEEZE': '수축',
  'BUY_TREND': '상승 추세', 'SELL_TREND': '하락 추세',
  'POSSIBLE_REVERSAL': '반전 가능', 'RANGE': '횡보',
  'CONFIRMED': '확인', 'FAILED': '실패',
  // Breakdown desc 번역 (compound terms first)
  'STRONG_BUY': '강한 관심', 'STRONG_BULLISH': '강한 상승', 'STRONG_BEARISH': '강한 하락',
  'MILD_BULLISH': '약한 상승', 'MILD_BEARISH': '약한 하락',
  'STRONG_S_CONFIRMED': '강한 수급 확인', 'S_CONFIRMED': '수급 확인', 'S_WEAK': '수급 약함',
  'STRONG_DISTRIBUTION': '강한 분산', 'MILD_DISTRIBUTION': '약한 분산',
  'UNCONFIRMED_BREAKOUT': '미확인 돌파', 'NO_INTEREST': '관심 부족',
  'STRONG_TREND': '강한 추세', 'MEAN_REVERTING': '평균 회귀', 'RANDOM_WALK': '불규칙',
  'ORB_BREAKOUT': 'ORB 돌파', 'ORB_READY': 'ORB 관찰', 'ORB_WATCH': 'ORB 감시', 'ORB_WEAK': '약한 ORB', 'OVERHEATED': '과열',
  'MODERATE_BUY': '적정 관심', 'SLIGHT_UPSIDE': '소폭 상승 여력',
  'SLIGHT_OVERVALUED': '소폭 고평가', 'AT_TARGET': 'DCF 적정가 도달',
  'ABOVE_STRONG': '강한 상회', 'BELOW_WEAK': '약한 하회',
  'BULLISH': '상승', 'BEARISH': '하락', 'MODERATE': '보통',
  'ACCUMULATION': '주목', 'DISTRIBUTION': '분산',
  'ABOVE': '상회', 'BELOW': '하회', 'TRENDING': '추세 진행',
  'OVERVALUED': '고평가', 'EXTREME': '극심',
  'INFLOW': '유입', 'OUTFLOW': '유출', 'NONE': '없음',
};

const _ORB_NR7_KO = {
  'ORB_BREAKOUT': '돌파', 'ORB_READY': '준비', 'ORB_WEAK': '약세 돌파',
  'ORB_WATCH': '관찰', 'NR7_BREAKOUT': '압축 돌파', 'NR7_READY': '압축 준비',
  'NR7_WATCH': '압축 관찰',
};
function _orbNr7Label(sig) {
  if (!sig || sig === 'NONE' || sig === '-' || sig === 'NEUTRAL') return '—';
  return _ORB_NR7_KO[sig] || _trKo(sig);
}
function _orbNr7Color(sig) {
  if (!sig || sig === 'NONE' || sig === '-' || sig === 'NEUTRAL') return null;
  if (sig.includes('BREAKOUT')) return 'var(--success)';
  if (sig.includes('READY')) return 'var(--brand)';
  return null;
}

function _trKo(str) {
  if (!str) return str;
  let s = String(str);
  // 정확히 일치하는 키부터 (대소문자 무시)
  const upper = s.toUpperCase().trim();
  if (_KO_MAP[upper]) return s.replace(new RegExp(upper, 'i'), _KO_MAP[upper]);
  // 부분 치환
  s = Object.entries(_KO_MAP).reduce((acc, [en, ko]) => {
    return acc.replace(new RegExp('\\b' + en + '\\b', 'gi'), ko);
  }, s);
  // 동일 단어 중복 제거 ("관망 - 관망" → "관망")
  s = s.replace(/(\S+)(\s*[-·/|]\s*)\1\b/g, '$1');
  return s;
}

function _rsiLabel(v)  { if (!v) return ''; return v > 70 ? ' · 과매수' : v < 30 ? ' · 과매도' : v > 60 ? ' · 강세' : v < 40 ? ' · 약세' : ' · 관망'; }
function _adxLabel(v)  { if (!v) return ''; return v > 40 ? ' · 강한 추세' : v > 25 ? ' · 추세 형성' : ' · 추세 약함'; }
function _rsLabel(v)   { if (v == null) return ''; return v >= 80 ? ' · 섹터 리더' : v >= 60 ? ' · 평균 이상' : v < 40 ? ' · 하위권' : ''; }
function _roeLbl(v)    { if (!v) return ''; return v > 0.20 ? ' · 우수' : v > 0.15 ? ' · 양호' : v > 0 ? ' · 보통' : ' · 미흡'; }
function _epsLbl(v)    { if (!v) return ''; return v > 0.25 ? ' · 고성장' : v > 0.10 ? ' · 성장' : v > 0 ? ' · 완만' : ' · 감소'; }

function _indicatorRowHtml(label, val, sub, color) {
  const col = color || 'var(--text-primary)';
  const bc = col === 'var(--success)' ? ' ind-pos' : col === 'var(--destructive)' ? ' ind-neg' : col === 'var(--info)' ? ' ind-info' : '';
  return `
<div class="ind-row${bc}">
  <div>
    <div class="ind-label">${esc(label)}</div>
    ${sub ? `<div class="ind-sub">${esc(sub)}</div>` : ''}
  </div>
  <div class="ind-val" style="color:${col};">${esc(val)}</div>
</div>`;
}

function _renderTechTab(d) {
  const el = document.getElementById('dp-tech-content');
  if (!el) return;

  const rsiVal  = d.RSI    != null ? fmt(d.RSI, 1) + _rsiLabel(d.RSI)   : '—';
  const rsiCol  = d.RSI > 70 ? 'var(--destructive)' : d.RSI < 30 ? 'var(--info)' : 'var(--text-primary)';
  const adxRaw  = d._ADX != null ? fmt(d._ADX, 1) + _adxLabel(d._ADX) : '—';
  const adxCol  = d._ADX > 25 ? 'var(--success)' : 'var(--text-tertiary)';
  const vwapRaw = d.VWAPDistance != null ? (d.VWAPDistance >= 0 ? '+' : '') + fmt(d.VWAPDistance*100,1)+'%'
                  + (d.VWAPDistance > 0.03 ? ' · VWAP 위' : d.VWAPDistance < -0.03 ? ' · VWAP 아래' : ' · VWAP 근접') : '—';
  const volRaw  = d.VolRatio != null ? fmt(d.VolRatio, 2)+'x'
                  + (d.VolRatio > 2 ? ' · 급증' : d.VolRatio > 1.3 ? ' · 증가' : d.VolRatio < 0.7 ? ' · 감소' : ' · 보통') : '—';

  const macdRaw = d._MACDHist != null
    ? (d._MACDHist > 0 ? '상승 전환 중' : d._MACDHist < 0 ? '하락 중' : '중립')
    : '—';
  const macdCol = d._MACDHist > 0 ? 'var(--success)' : d._MACDHist < 0 ? 'var(--destructive)' : null;

  // 중복/저신호 행 제거: RS등급(→RS 도넛 게이지)·12M/3M수익률(→RS 구성성분)·
  // NR7(→ORB와 중복)·볼린저(→온도계/RSI와 상관)·확신도(→점수와 단조)·섹터순위(대부분 '스캔 필요').
  const rows = [
    ['RSI (14)',    rsiVal,  '70↑ 과매수(조정 주의) · 30↓ 과매도(반등 주목)', rsiCol],
    ['ADX',        adxRaw,  '25↑ 추세 존재 · 40↑ 강한 추세 · 25↓ 횡보',     adxCol],
    ['ATR%',       d.ATRPercent != null ? fmt(d.ATRPercent,2)+'%' : '—', '높을수록 변동성 큼 — 위험과 기회 동시', null],
    ['VWAP 거리',  vwapRaw, '양수=평균가 위(강세) · 음수=아래(약세)',         d.VWAPDistance > 0 ? 'var(--success)' : 'var(--destructive)'],
    ['거래량 비율',volRaw,  '1↑ 평소보다 활발 · 2↑ 기관 참여 가능성',        d.VolRatio > 1.5 ? 'var(--success)' : null],
    ['MACD 방향',  macdRaw, '히스토그램 양수=상승 모멘텀 · 음수=하락 모멘텀', macdCol],
    ['ORB 신호',   _orbNr7Label(d.ORBSignal), '시초가 범위 돌파 시 진입 신호',     _orbNr7Color(d.ORBSignal)],
  ];

  el.innerHTML = rows.map(([l, v, s, c]) => _indicatorRowHtml(l, v, s, c)).join('');
}

// ── 온도계·RS·ATR분석·리스크 4종 패널 ────────────────────────────────────
function _renderDetailFeatures(d) {
  // ── 온도계 신호 ──────────────────────────────────────────────
  const marker     = document.getElementById('dp-thermo-marker');
  const thermoLbl  = document.getElementById('dp-thermo-label');
  const thermoAct  = document.getElementById('dp-thermo-action');
  const rsi = d.RSI != null ? Number(d.RSI) : null;
  if (marker && rsi != null) {
    // RSI → 바 위치 정규화: 각 존이 균등 20% 폭을 차지하도록 매핑
    // 라벨(극도공포~극도탐욕)이 space-between 균등 배치이므로 위치를 맞춤
    let pct;
    if      (rsi < 30) pct = (rsi / 30) * 20;
    else if (rsi < 45) pct = 20 + ((rsi - 30) / 15) * 20;
    else if (rsi < 55) pct = 40 + ((rsi - 45) / 10) * 20;
    else if (rsi < 70) pct = 60 + ((rsi - 55) / 15) * 20;
    else               pct = 80 + Math.min((rsi - 70) / 30, 1) * 20;
    pct = Math.max(2, Math.min(98, pct));
    marker.style.left = pct + '%';
    let lbl, act, col;
    if      (rsi >= 70) { lbl = '극도탐욕'; act = '과열 구간';         col = '#DC2626'; }
    else if (rsi >= 55) { lbl = '탐욕';     act = '탐욕 구간';         col = '#F59E0B'; }
    else if (rsi >= 45) { lbl = '중립';     act = '중립 구간';         col = '#6B7280'; }
    else if (rsi >= 30) { lbl = '공포';     act = '공포 구간';         col = '#06B6D4'; }
    else                { lbl = '극도공포'; act = '과매도 구간';       col = '#2563EB'; }
    marker.style.borderColor = col;
    if (thermoLbl) { thermoLbl.textContent = lbl; thermoLbl.style.color = col; }
    if (thermoAct) { thermoAct.textContent = act; thermoAct.style.color = col; }
  }

  // ── RS Rating ─────────────────────────────────────────────────
  const rsVal = document.getElementById('dp-rs-value');
  const rsLbl = document.getElementById('dp-rs-label');
  const rs = d.RSRating != null ? Math.round(Number(d.RSRating)) : null;
  if (rsVal && rsLbl) {
    if (rs != null) {
      let rsCol, rsTxt;
      if      (rs >= 80) { rsCol = '#16A34A'; rsTxt = '주도주'; }
      else if (rs >= 60) { rsCol = '#16A34A'; rsTxt = '강세';  }
      else if (rs >= 40) { rsCol = '#6B7280'; rsTxt = '중립';  }
      else if (rs >= 20) { rsCol = '#F59E0B'; rsTxt = '약세';  }
      else               { rsCol = '#DC2626'; rsTxt = '부진';  }
      rsVal.textContent = String(rs);
      rsVal.style.color = rsCol;
      // 권고1: 같은 지수(시총군) 내 상대강도 — size 베타 제거한 진짜 순위
      const _IXLBL = { SP500: 'S&P500', SP400: '미드캡', SP600: '스몰캡', NDX: '나스닥100', OTHER: '기타' };
      const bkt = d.RSBucket, bktNm = d.RSBucketName;
      const bktSuffix = (bkt != null && bktNm && _IXLBL[bktNm])
        ? ` · ${_IXLBL[bktNm]} 내 ${Math.round(bkt)}` : '';
      rsLbl.textContent = rsTxt + ' / 99' + bktSuffix;
      rsLbl.style.color = rsCol;
      if (bktSuffix) {
        rsLbl.title = `전체 유니버스 RS는 ${rs}점이지만, 같은 ${_IXLBL[bktNm]} 종목들 사이에서는 ${Math.round(bkt)}점(백분위)입니다. ` +
                      `대형주와 소형주를 섞어 비교할 때 생기는 size 왜곡을 제거한 순위예요.`;
      }
    } else {
      rsVal.innerHTML = '—';
      rsLbl.textContent = '/ 99';
    }
  }

  // ── 거래량 배수 · 시총 (Hero 우측 패널) ──────────────────────
  const volEl = document.getElementById('dp-volratio');
  if (volEl) volEl.textContent = (d.VolRatio != null) ? (Number(d.VolRatio).toFixed(1) + '×') : '—';
  const mcEl = document.getElementById('dp-mktcap');
  if (mcEl) {
    const mc = (d.MarketCap != null) ? d.MarketCap : d._MarketCap;
    mcEl.textContent = (mc != null) ? _fmtMarketCap(mc) : '—';
  }

}

// ── 진입 판단 종합 카드 ──────────────────────────────────────────────────
function _renderEntryVerdict(d) {
  const card = document.getElementById('dp-entry-verdict');
  if (!card) return;

  const bf = d.BFScore != null ? Number(d.BFScore) : null;
  const es = d.EntryScore != null ? Number(d.EntryScore) : null;
  const ts = d.TotalScore != null ? Number(d.TotalScore) : null;
  const price = d.Price != null ? Number(d.Price) : null;
  const ep = d.EntryPlan || {};
  const atr = ep.atr_pct != null ? Number(ep.atr_pct) : null;
  const gz = d.GreedZone ? (d.GreedZoneScore || 0) : 0;

  if (bf == null && es == null && ts == null) { card.style.display = 'none'; return; }

  // ── 확신도 계산 (0-100) ──
  let conv = 50;
  const pros = [], cons = [];

  if (bf != null) {
    if (bf >= 60)      { conv += 15; pros.push('저점매수 강력'); }
    else if (bf >= 40) { conv += 8;  pros.push('저점매수 적극'); }
    else if (bf >= 25) { conv += 3;  pros.push('저점매수 관심'); }
  }
  if (es != null) {
    if (es >= 70)      { conv += 15; pros.push('타이밍 우수'); }
    else if (es >= 50) { conv += 8;  pros.push('타이밍 양호'); }
    else if (es < 30)  { conv -= 10; cons.push('타이밍 부적합'); }
  }
  if (ts != null) {
    if (ts >= 70)      { conv += 12; pros.push('종합 우량'); }
    else if (ts >= 55) { conv += 5; }
    else if (ts < 40)  { conv -= 8;  cons.push('종합 점수 낮음'); }
  }
  if (gz >= 70)      { conv -= 15; cons.push('과열 경고'); }
  else if (gz >= 40) { conv -= 8;  cons.push('과열 주의'); }

  const mdd = ep.mdd_current;
  if (mdd != null && mdd < -25) { conv -= 8; cons.push('고 낙폭'); }

  conv = Math.max(5, Math.min(95, conv));

  // ── 신호 매핑 ──
  let label, icon, color;
  if (conv >= 72)      { label = '진입 유리'; icon = '🟢'; color = '#16A34A'; }
  else if (conv >= 58) { label = '관심 구간'; icon = '🔵'; color = '#2563EB'; }
  else if (conv >= 42) { label = '관망';     icon = '🟡'; color = '#D97706'; }
  else                 { label = '보류';     icon = '🔴'; color = '#DC2626'; }

  // ── 분할매수 플랜 ──
  let splitHtml = '';
  if (price != null && price > 0) {
    const a = (atr != null && atr > 0) ? atr : 3.0;
    let w1, w2, w3;
    if (conv >= 72)      { w1 = 40; w2 = 35; w3 = 25; }
    else if (conv >= 58) { w1 = 33; w2 = 34; w3 = 33; }
    else                 { w1 = 25; w2 = 35; w3 = 40; }

    const d2 = a * 1.5, d3 = a * 3;
    const p1 = price, p2 = price * (1 - d2 / 100), p3 = price * (1 - d3 / 100);
    const avg = (p1 * w1 + p2 * w2 + p3 * w3) / 100;
    const fp = v => fmtPrice(v);

    const tranche = (n, w, p, lbl, hi) =>
      `<div class="ev-tr" ${hi ? `style="color:${color};font-weight:700"` : ''}>
        <span class="ev-tr-n">${n}차</span>
        <span class="ev-tr-bar"><span class="ev-tr-fill" style="width:${w}%;background:${hi ? color : 'var(--text-tertiary)'}"></span></span>
        <span class="ev-tr-pct">${w}%</span>
        <span class="ev-tr-price">${fp(p)}</span>
        <span class="ev-tr-lbl">${lbl}</span>
      </div>`;

    splitHtml = `
      <div class="ev-split">
        <div class="ev-split-head">📊 분할매수 플랜 <span style="font-weight:400;color:var(--text-tertiary);font-size:10px;">ATR 기반 3회</span></div>
        ${tranche(1, w1, p1, '시장가', true)}
        ${tranche(2, w2, p2, '−' + d2.toFixed(1) + '%', false)}
        ${tranche(3, w3, p3, '−' + d3.toFixed(1) + '%', false)}
        <div class="ev-split-foot">평균단가 <b>${fp(avg)}</b> · 최대 이격 <b>${d3.toFixed(1)}%</b></div>
      </div>`;
  }

  // ── 팩터 pills ──
  const pills = [
    ...pros.map(t => `<span class="ev-pill ev-pro">${esc(t)}</span>`),
    ...cons.map(t => `<span class="ev-pill ev-con">${esc(t)}</span>`)
  ].join('');

  // ── 판단 포스터 렌더 (conv 단일 소스, 15단계) ──
  let _pgCls, _pvWord, _pvReason, _pvBg, _tmWord, _tmSub;
  // ticker + conv 기반 결정론적 선택 — 동일 종목은 재렌더 시에도 같은 문구 유지
  const _tickerHash = (d.Ticker || '').split('').reduce((a, c) => (a * 31 + c.charCodeAt(0)) | 0, 0);
  const _seed = Math.abs(_tickerHash * 1000 + Math.round(conv));
  const _pick = arr => arr[_seed % arr.length];
  if (conv >= 93) {
    _pgCls = 'dvp-green';
    _pvWord   = _pick(['역대급', '인생 타이밍', '지금 당장', '올인각', '이게 자리다']);
    _pvReason = _pick([
      '지표·수급·모멘텀 삼박자 완벽<br>이런 자리 1년에 몇 번 안 옴<br>망설이면 두고두고 후회함',
      '차트 보고 눈물 날 뻔함<br>모든 조건 동시에 충족된 자리<br>지금 안 담으면 진짜 바보',
      '수급 폭발에 추세 완벽 우상향<br>기술적·기본적 지표 전부 켜짐<br>인생 타이밍 맞음 진짜',
      '이런 신호 놓치면 후회함<br>볼수록 좋은 차트에 수급까지 터짐<br>비중 최대로 ㄱㄱ',
    ]);
    _pvBg = _pick(['인생각', '올인', '역대급']);
    _tmWord = _pick(['🟢 인생 타이밍', '🟢 역대급', '🟢 지금 당장']);
    _tmSub  = '인생 타이밍';
  } else if (conv >= 86) {
    _pgCls = 'dvp-green';
    _pvWord   = _pick(['올인각', '풀매각', '슈팅각', '지금이야', '강력 매수']);
    _pvReason = _pick([
      '지표 다 켜졌고 수급까지 터짐<br>이런 타이밍 자주 안 옴<br>지금 안 담으면 진짜 후회함',
      '차트 완벽하게 살아있음<br>추세 ㄹㅇ 강하고 모멘텀 최상<br>분할 말고 그냥 풀매각',
      '수급 뒷받침에 모멘텀도 완벽<br>기술적·기본적 지표 전부 켜짐<br>올인각 나왔다 진짜',
      '이거 지금 아니면 언제 사냐<br>모든 조건 충족된 자리임<br>소액이라도 반드시 담아봐',
    ]);
    _pvBg = _pick(['올인', '풀매', '슈팅']);
    _tmWord = _pick(['🟢 올인각', '🟢 풀매각', '🟢 슈팅각']);
    _tmSub  = '극강 타이밍';
  } else if (conv >= 79) {
    _pgCls = 'dvp-green';
    _pvWord   = _pick(['풀매각', '적극 매수', '강하게 담아', '지금 담아', '줍줍각']);
    _pvReason = _pick([
      '지금 안 담으면 후회할 수 있음<br>추세 강하고 수급도 뒷받침됨<br>비중 실어서 담아봐',
      '차트 ㄷㄷ하고 모멘텀 살아있음<br>수급 들어오는 게 확인됨<br>눌리면 추가 담기 각',
      '지표 대부분 켜진 강한 자리<br>리스크 낮고 기대수익 높음<br>망설이지 말고 담아',
      '이거 지금 아니면 비싸게 사야 함<br>추세 우상향 확실하고 수급도 좋음<br>분할이라도 지금 시작해',
    ]);
    _pvBg = _pick(['풀매', '적극', '줍줍']);
    _tmWord = _pick(['🟢 풀매각', '🟢 적극 매수', '🟢 줍줍각']);
    _tmSub  = '강한 매수';
  } else if (conv >= 72) {
    _pgCls = 'dvp-green';
    _pvWord   = _pick(['줍줍각', '담아가', '매수각', '나눠서 담아', '비중 실어']);
    _pvReason = _pick([
      '지금 안 담으면 후회함<br>추세 좋고 수급도 받쳐주는 중<br>나눠서 조금씩 담아봐',
      '차트 ㄷㄷ함<br>수급 들어오고 모멘텀 살아있음<br>눌리면 분할 담기 각',
      '추세 우상향 중이고 수급도 좋음<br>리스크 관리하면서 분할로 ㄱㄱ<br>성급한 풀매는 피해',
      '지표 켜져 있고 자리도 좋음<br>욕심 안 부리고 분할로 접근<br>생각보다 좋은 종목임',
    ]);
    _pvBg = _pick(['줍줍', '분할', '담기']);
    _tmWord = _pick(['🟢 줍줍각', '🟢 담아가', '🟢 분할 ㄱㄱ']);
    _tmSub  = '매수 추천';
  } else if (conv >= 65) {
    _pgCls = 'dvp-green';
    _pvWord   = _pick(['분할 ㄱㄱ', '소량 진입', '슬금슬금', '조심스럽게', '나눠서']);
    _pvReason = _pick([
      '나쁘지 않은 자리긴 한데<br>확신이 100%는 아님<br>분할로 리스크 줄여서 접근',
      '긍정 지표 있지만 일부 애매함<br>풀매보단 소량 분할이 맞는 상황<br>더 좋아지면 추가 담기',
      '들어갈 수는 있는 자리인데<br>손실 감당 가능한 비중으로만<br>절대 몰빵은 금지',
      '긍정적인 신호 있지만 리스크도 있음<br>작은 비중으로 먼저 확인해봐<br>차트 좋아지면 추가',
    ]);
    _pvBg = _pick(['소량', '분할', '조심']);
    _tmWord = _pick(['🟢 소량 진입', '🟢 분할 ㄱㄱ', '🟢 슬금슬금']);
    _tmSub  = '신중 매수';
  } else if (conv >= 58) {
    _pgCls = 'dvp-yellow';
    _pvWord   = _pick(['테스트 담기', '발만 살짝', '소액만', '일단 찔러봐', '살짝만']);
    _pvReason = _pick([
      '지표 일부 긍정적이지만 전부는 아님<br>소액으로 먼저 포지션 잡아봐<br>확인되면 비중 늘리는 방식',
      '긍정 신호 있는데 확신이 안 섬<br>물려도 감당 가능한 소액으로만<br>지켜보면서 추가 판단해',
      '진입 가능한 자리긴 한데<br>추가 확인이 필요한 상황<br>소량 테스트 후 결정해봐',
      '애매하긴 한데 아주 나쁘진 않음<br>발만 살짝 담가서 흐름 봐봐<br>좋아지면 그때 비중 추가',
    ]);
    _pvBg = _pick(['테스트', '소액', '찔러봐']);
    _tmWord = _pick(['🟡 테스트 담기', '🟡 소액만', '🟡 발만 살짝']);
    _tmSub  = '테스트 진입';
  } else if (conv >= 51) {
    _pgCls = 'dvp-yellow';
    _pvWord   = _pick(['긍정 눈팅', '좀 더 봐봐', '시그널 대기', '거의 다 왔어', '조금만 기다려']);
    _pvReason = _pick([
      '긍정적인 신호 보이긴 하는데<br>아직 확실히 켜진 건 아님<br>좀 더 지켜보다가 들어가봐',
      '방향성은 긍정적인데 타이밍이 아직<br>조금만 더 기다리면 좋은 자리 나옴<br>서두르지 말고 시그널 확인해',
      '나쁜 종목은 아닌데 자리가 좀 이름<br>추세 확인되면 그때 담는 게 맞음<br>조금만 더 기다려봐',
      '긍정 지표 늘어나는 중인데<br>아직 매수 확신까지는 아님<br>좀 더 보다가 진입 타이밍 잡아봐',
    ]);
    _pvBg = _pick(['대기', '시그널', '곧이야']);
    _tmWord = _pick(['🟡 긍정 눈팅', '🟡 좀 더 봐봐', '🟡 시그널 대기']);
    _tmSub  = '긍정 관망';
  } else if (conv >= 44) {
    _pgCls = 'dvp-yellow';
    _pvWord   = _pick(['눈팅각', '반반임', '모르겠음', '저울질 중', '중립']);
    _pvReason = _pick([
      '좋은 것도 있고 안 좋은 것도 있음<br>확신이 안 서는 자리<br>더 좋은 시그널 나오면 그때 대응',
      '종목 자체는 나쁘지 않은데<br>타이밍이 살짝 애매함<br>좀 더 내려오면 그때 담자',
      '반반임 솔직히<br>지금 들어가기도 애매하고 빠지기도 애매함<br>관망하면서 눈팅해봐',
      '확신이 안 서는 구간<br>섣불리 들어갔다가 멘탈 털릴 수 있음<br>좀 더 지켜보면서 판단해',
    ]);
    _pvBg = _pick(['중립', '눈팅', '저울질']);
    _tmWord = _pick(['🟡 눈팅각', '🟡 반반임', '🟡 중립']);
    _tmSub  = '중립 관망';
  } else if (conv >= 37) {
    _pgCls = 'dvp-yellow';
    _pvWord   = _pick(['관망각', '기다려봐', '아직은 아냐', '타이밍 아님', '좀 더 기다려']);
    _pvReason = _pick([
      '좋긴 한데 지금 들어가면 물릴 수 있음<br>조금만 더 눌리면 그때 담아봐<br>지금은 관망각',
      '부정 신호 하나둘씩 켜지는 중<br>지금 들어가기엔 타이밍이 안 좋음<br>좀 더 기다려봐',
      '살짝 고점 느낌 나기 시작함<br>성급하게 들어갔다가 물릴 수 있음<br>관망하면서 기다려봐',
      '나쁜 종목은 아닌데<br>지금 들어가기엔 리스크가 있음<br>조금 더 내려오면 담자',
    ]);
    _pvBg = _pick(['관망', '대기', '기다려']);
    _tmWord = _pick(['🟡 관망각', '🟡 기다려봐', '🟡 타이밍 아님']);
    _tmSub  = '소극적 관망';
  } else if (conv >= 30) {
    _pgCls = 'dvp-red';
    _pvWord   = _pick(['고점 주의', '아직 일러', '더 눌려야', '서두르지 마', '대기각']);
    _pvReason = _pick([
      '살짝 고점 느낌 남<br>지금 들어가면 물릴 수 있음<br>더 내려오면 그때 검토해봐',
      '지금 들어가기엔 부담스러운 자리<br>좀 더 눌려야 매력 있는 가격 됨<br>서두르지 마셈',
      '지표들 부정 신호 보내는 중<br>리스크 크고 기대수익은 작음<br>더 좋은 자리 기다려봐',
      '차트가 부담스러운 구간<br>수급 빠지기 시작하는 느낌<br>여기서 손댔다가 물리면 고생함',
    ]);
    _pvBg = _pick(['고점', '대기', '주의']);
    _tmWord = _pick(['🟠 고점 주의', '🟠 아직 일러', '🟠 더 눌려야']);
    _tmSub  = '고점 주의';
  } else if (conv >= 23) {
    _pgCls = 'dvp-red';
    _pvWord   = _pick(['진입 부담', '손 빼봐', '위험한 자리', '뇌동 주의', '패스 고려']);
    _pvReason = _pick([
      '지금 들어가면 물릴 각도임<br>지표들 대부분 안 좋은 신호 보내는 중<br>관심종목만 넣고 손 빼셈',
      '차트 안 좋고 수급도 빠지는 중<br>지금 들어가는 건 뇌동매매임<br>더 내려오면 그때 다시 검토',
      '여러 지표가 경고 보내는 중<br>리스크 대비 기대수익 너무 안 나옴<br>더 좋은 종목 찾아봐',
      '지금 자리는 진입하면 안 됨<br>손절라인도 애매하고 지지도 약함<br>완전히 빠질 때까지 기다려',
    ]);
    _pvBg = _pick(['부담', '주의', '패스']);
    _tmWord = _pick(['🟠 진입 부담', '🟠 손 빼봐', '🟠 뇌동 주의']);
    _tmSub  = '진입 부담';
  } else if (conv >= 16) {
    _pgCls = 'dvp-red';
    _pvWord   = _pick(['강한 경고', '진입 금물', '손 빼셈', '위험 구간', '절대 비추']);
    _pvReason = _pick([
      '지금 들어가면 높은 확률로 물림<br>지표들이 전부 경고 보내는 중<br>관심만 해두고 절대 손 대지 마',
      '차트 안 좋고 수급도 완전 빠지는 중<br>지금 들어가면 뇌동매매 확정<br>더 내려가는 거 구경만 해',
      '모멘텀 죽고 수급도 없음<br>여기서 들어가면 물리는 거 거의 확정<br>좋아질 때까지 무시해',
      '지표 전반적으로 매우 안 좋음<br>손절라인 없는 진입은 자살행위<br>절대 추격매수 금지',
    ]);
    _pvBg = _pick(['경고', '금물', '위험']);
    _tmWord = _pick(['🟠 강한 경고', '🟠 진입 금물', '🟠 손 빼셈']);
    _tmSub  = '강한 경고';
  } else if (conv >= 10) {
    _pgCls = 'dvp-red';
    _pvWord   = _pick(['패스각', '손절각', '도망쳐', '버려', '손 빼셈']);
    _pvReason = _pick([
      '지금 들어가면 거의 물릴 각도임<br>지표들이 다 안 좋은 신호<br>관심만 해두고 절대 손대지 마',
      '차트 개못생김 솔직히<br>수급 빠지고 모멘텀도 죽었음<br>그냥 지켜만 봐',
      '이거 손대면 안 됨 진짜<br>모든 지표가 경고 보내는 중<br>관심종목만 넣고 기다려봐',
      '지금 들어가면 뇌동매매임<br>더 좋은 자리 나올 때까지 패스<br>절대 추격매수 금지',
    ]);
    _pvBg = _pick(['패스', '손절', '회피']);
    _tmWord = _pick(['🔴 패스각', '🔴 손절각', '🔴 도망쳐']);
    _tmSub  = '강력 회피';
  } else if (conv >= 5) {
    _pgCls = 'dvp-red';
    _pvWord   = _pick(['탈출각', '청산각', '손절 검토', '빠져나와', '들고 있으면 위험']);
    _pvReason = _pick([
      '이미 갖고 있으면 탈출 검토해야 함<br>지표 전부 최악 신호<br>손절이 장기 버티기보다 나음',
      '차트 완전히 망가진 상황<br>수급 없고 모멘텀 바닥<br>빠르게 나오는 게 맞음',
      '지금 갖고 있다면 매도 고려해봐<br>회복까지 엄청 오래 걸릴 수 있음<br>기회비용 생각해야 함',
      '모든 지표 바닥 신호<br>물타기는 절대 안 됨<br>손실 확정하고 나오는 게 현명함',
    ]);
    _pvBg = _pick(['탈출', '청산', '손절']);
    _tmWord = _pick(['🔴 탈출각', '🔴 청산각', '🔴 손절 검토']);
    _tmSub  = '탈출 권고';
  } else {
    _pgCls = 'dvp-red';
    _pvWord   = _pick(['깡통 주의', '건드리지 마', '최고 위험', '절대 금지', '폭탄이야']);
    _pvReason = _pick([
      '이거 손대면 진짜 깡통 각도임<br>지표 전부 최악 중에 최악<br>존재 자체를 잊어버려',
      '차트 역대급으로 못생겼음<br>수급 제로에 모멘텀 나락<br>절대 건드리지 마',
      '이런 자리에서 들어가면 미련한 거임<br>회복 가능성도 낮고 기다릴 가치도 없음<br>관심종목에서도 삭제해',
      '지금 들어가면 깡통 확정에 가까움<br>어떤 이유로도 진입 금지<br>이거 갖고 있으면 지금 당장 팔아',
    ]);
    _pvBg = _pick(['깡통', '절대금지', '최위험']);
    _tmWord = _pick(['🔴 깡통 주의', '🔴 절대 금지', '🔴 건드리지 마']);
    _tmSub  = '최고 위험';
  }

  const _vpEl = document.getElementById('dp-verdict-poster');
  if (_vpEl) {
    _vpEl.style.display = '';
    _vpEl.className = `dp-verdict-poster ${_pgCls}`;
    _vpEl.innerHTML = `<div class="dvp-main"><div class="dvp-eyebrow">살까? 말까?</div><div class="dvp-word">${_pvWord}</div><div class="dvp-reason">${_pvReason}</div></div><div class="dvp-conf"><div class="dvp-conf-num">${conv}<span class="dvp-conf-pct">%</span></div><div class="dvp-conf-lbl">확신도</div></div><div class="dvp-bg">${_pvBg}</div>`;
  }
  // ── 렌더 (entry-verdict 카드는 내부 데이터 보존용, UI 미노출) ──
  card.style.display = 'none';
  card.style.borderLeft = `3px solid ${color}`;
  card.innerHTML = `
    <div class="ev-head">
      <span class="ev-icon">${icon}</span>
      <span class="ev-label" style="color:${color}">${label}</span>
      <span class="ev-conf" style="background:${color}18;color:${color}">확신도 ${conv}%</span>
    </div>
    ${pills ? `<div class="ev-pills">${pills}</div>` : ''}
  `;

  // 분할매수 플랜은 얼마나? 섹션으로 분리
  const _spEl = document.getElementById('dp-split-plan');
  if (_spEl) _spEl.innerHTML = splitHtml;
}

// Bottom-Fishing Score card
function _renderBFScore(d) {
  const card = document.getElementById('dp-bf-card');
  if (!card) return;
  const bf = d.BFScore != null ? Number(d.BFScore) : null;
  if (bf == null || bf === 0) { card.style.display = 'none'; return; }
  card.style.display = '';
  const col = bf >= 75 ? '#16A34A' : bf >= 60 ? '#2563EB' : bf >= 45 ? '#F59E0B' : bf >= 30 ? '#6B7280' : '#DC2626';
  const sigMap = { STRONG_BUY: '적극 매수', BUY: '매수', WATCH: '관심', NEUTRAL: '중립', AVOID: '회피' };
  const sigEl = document.getElementById('dp-bf-signal');
  if (sigEl) {
    sigEl.textContent = sigMap[d.BFSignal] || d.BFSignal || '';
    sigEl.style.color = col;
    sigEl.style.background = col + '18';
  }
  const scoreEl = document.getElementById('dp-bf-score');
  if (scoreEl) { scoreEl.textContent = Math.round(bf); scoreEl.style.color = col; }
  const fill = document.getElementById('dp-bf-fill');
  if (fill) { fill.style.width = bf + '%'; fill.style.background = col; }
  const a1 = document.getElementById('dp-bf-a1');
  const a2 = document.getElementById('dp-bf-a2');
  const a3 = document.getElementById('dp-bf-a3');
  if (a1) a1.textContent = d.BFAxis1 != null ? Math.round(d.BFAxis1) : 0;
  if (a2) a2.textContent = d.BFAxis2 != null ? Math.round(d.BFAxis2) : 0;
  if (a3) a3.textContent = d.BFAxis3 != null ? Math.round(d.BFAxis3) : 0;
  const tagsEl = document.getElementById('dp-bf-tags');
  if (tagsEl) {
    const tags = d.BFTags || [];
    tagsEl.innerHTML = tags.map(t =>
      `<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:${col}18;color:${col};font-weight:600;">${esc(t)}</span>`
    ).join('');
  }
  const detEl = document.getElementById('dp-bf-detail');
  if (detEl) {
    const parts = [];
    if (d.PiotroskiF != null && d.PiotroskiF > 0) parts.push(`F-Score ${d.PiotroskiF}/9`);
    if (d.AltmanZ != null) parts.push(`Altman Z ${Number(d.AltmanZ).toFixed(1)} (${d.AltmanZone || ''})`);
    detEl.textContent = parts.join(' · ');
  }
}

// P4-P12: 드로다운 리스크 메트릭 카드 (상세 패널)
function _renderDrawdownRisk(d) {
  const host = document.getElementById('dp-drawdown-risk');
  if (!host) return;
  const ep = (d && d.EntryPlan) || {};
  const mdd = ep.mdd_current;
  if (mdd == null && ep.cvar_95 == null) { host.innerHTML = ''; return; }
  const rows = [];
  // 기본 낙폭
  if (mdd != null) {
    const col = mdd < -20 ? '#DC2626' : mdd < -10 ? '#F59E0B' : 'var(--text-primary)';
    rows.push(`<tr><td>현재 MDD</td><td style="color:${col};font-weight:600">${mdd.toFixed(1)}%</td></tr>`);
  }
  // P6: 수면하 체류
  if (ep.underwater_days > 0) {
    const col = ep.underwater_days > 120 ? '#DC2626' : ep.underwater_days > 60 ? '#F59E0B' : 'var(--text-secondary)';
    rows.push(`<tr><td>수면하 체류</td><td style="color:${col}">${ep.underwater_days}일</td></tr>`);
  }
  // P4: 급락 속도
  if (ep.dd_velocity_5d != null && ep.dd_velocity_5d < -2) {
    rows.push(`<tr><td>5일 낙폭 속도</td><td style="color:#DC2626;font-weight:600">${ep.dd_velocity_5d.toFixed(1)}%p</td></tr>`);
  }
  // P7: Calmar
  if (ep.calmar_ratio != null && ep.calmar_ratio !== 0) {
    const col = ep.calmar_ratio > 1.5 ? '#16A34A' : ep.calmar_ratio > 0.5 ? 'var(--text-primary)' : '#F59E0B';
    const lbl = ep.calmar_ratio > 1.5 ? '우수' : ep.calmar_ratio > 0.5 ? '보통' : '부진';
    rows.push(`<tr><td>고통 대비 보상</td><td style="color:${col}">${ep.calmar_ratio.toFixed(2)} (${lbl})</td></tr>`);
  }
  // P8: CVaR 금액 번역
  // P8: CVaR(ES95) — 라벨 정정. '최악의 날'이 아니라 '하위 5% 나쁜 날들의 평균 손실'.
  if (ep.cvar_95 != null && ep.cvar_95 !== 0) {
    const remain = Math.round(1000000 * (1 + ep.cvar_95 / 100));  // 나쁜 날 잔액 (100만원 기준)
    rows.push(`<tr><td title="하위 5% 나쁜 날들의 평균 손실 (CVaR/Expected Shortfall 95%). '가장 나쁜 하루'가 아니라 '나쁜 날이면 평균 이 정도' 입니다. 최근 1년 기준.">나쁜 날 손실 <span style="font-size:10px;color:var(--text-tertiary);">(하위5%·ES)</span></td><td style="color:#DC2626">${ep.cvar_95.toFixed(2)}% <span style="color:var(--text-tertiary);font-size:11px;">(100만원→${remain.toLocaleString()}원)</span></td></tr>`);
  }
  // B1: 진짜 단일 최악일 (최근 1년 중 하루 최대 낙폭) — '최악의 날'은 이쪽이 정확
  if (ep.worst_day != null && ep.worst_day !== 0) {
    const wremain = Math.round(1000000 * (1 + ep.worst_day / 100));  // 최악의 날 잔액 (100만원 기준)
    rows.push(`<tr><td title="최근 1년간 실제 하루 최대 낙폭 (단일 최악일)">최근 1년 최악의 날</td><td style="color:#B91C1C;font-weight:600">${ep.worst_day.toFixed(2)}% <span style="color:var(--text-tertiary);font-size:11px;font-weight:400;">(100만원→${wremain.toLocaleString()}원)</span></td></tr>`);
  }
  // P9: 하방 베타
  if (ep.downside_beta != null) {
    const col = ep.downside_beta > 1.5 ? '#DC2626' : ep.downside_beta > 1.0 ? '#F59E0B' : '#16A34A';
    rows.push(`<tr><td>하방 민감도(β)</td><td style="color:${col}">${ep.downside_beta.toFixed(2)}×</td></tr>`);
  }
  // P12: 체감형 스트레스 시나리오 (Goldman P3)
  if (ep.stress_2008 != null) {
    const inv = 100; // 100만원 기준
    rows.push(`<tr><td colspan="2" style="padding-top:6px;font-size:10px;color:var(--text-tertiary);font-weight:600;">과거 위기 재현 시 (100만원 투자 기준) <span style="font-weight:400;opacity:.75;">※ 하방β 선형 추정치 — 실제 손실은 더 클 수 있음</span></td></tr>`);
    const _stress = (label, v) => {
      const pct = (v * 100).toFixed(0);
      const remain = Math.round(inv * (1 + v));
      const barW = Math.min(100, Math.abs(v) * 200);
      return `<tr><td>${label}</td><td><div style="display:flex;align-items:center;gap:4px;"><div style="width:60px;height:6px;background:var(--bg-secondary);border-radius:3px;overflow:hidden;"><div style="height:100%;width:${barW}%;background:#DC2626;border-radius:3px;"></div></div><span style="color:#DC2626;font-weight:600;">${pct}%</span><span style="font-size:10px;color:var(--text-tertiary);">${remain}만원</span></div></td></tr>`;
    };
    rows.push(_stress('2008 금융위기', ep.stress_2008));
    rows.push(_stress('2020 코로나', ep.stress_2020));
    rows.push(_stress('2022 금리인상', ep.stress_2022));
  }
  if (!rows.length) { host.innerHTML = ''; return; }
  host.innerHTML = `<table style="width:100%;font-size:12px;line-height:1.8;border-collapse:collapse;">${rows.join('')}</table>`;
}

// P1: Composite Risk Score — Hero 카드 내 Dual Display
function _renderRiskGauge(d) {
  const el = document.getElementById('dp-risk-gauge');
  if (!el) return;
  const ep = (d && d.EntryPlan) || {};
  const cr = ep.composite_risk;
  if (cr == null) { el.style.display = 'none'; return; }
  if (cr < 35) { el.style.display = 'none'; return; }  // 양호는 표시 안 함 — 경고만 노출
  const col = cr >= 60 ? '#DC2626' : '#F59E0B';
  const lbl = cr >= 60 ? '손실 위험 높음' : '변동성 주의';
  el.style.display = 'inline-flex';
  el.style.background = col + '18';
  el.style.color = col;
  el.innerHTML = `⚠️ <span style="font-weight:600;">${lbl}</span>`;
}

// (정리됨) _renderRiskSummary·_renderFactorWaterfall·_renderACCard·_renderLiquidityCard 4개 함수 제거.
// 중복/저신호로 컨테이너(dp-risk-summary·dp-factor-waterfall·dp-ac-card·dp-liquidity-card)와 호출부를 삭제.
// 데이터 필드(composite_risk·factor_contrib·ac1·liquidity_score)는 백엔드에 보존 — 되돌리기 가능.

// ── MECE 분석 프레임워크 렌더 함수 (Phase 1/2/3) ──────────────────────

function _renderValuationContext(d) {
  const card = document.getElementById('valuation-context-card');
  if (!card) return;
  const vp = d.ValPctile, srpe = d.SectorRelPE, pil = d.PriceInLevel;
  if (vp == null && srpe == null && pil == null) { card.style.display = 'none'; return; }
  card.style.display = '';

  let html = '<div class="mece-inner">';
  html += '<div class="mece-header"><span class="mece-title">밸류에이션 맥락</span><span class="mece-badge">참고용 시뮬레이션</span></div>';

  // ValPctile 게이지 바
  if (vp != null) {
    const per = d._PER != null ? `PER ${fmt(d._PER, 1)}` : '';
    const zone = vp <= 30 ? '저평가 구간' : vp >= 70 ? '고평가 구간' : '적정 구간';
    const zoneCol = vp <= 30 ? 'var(--success)' : vp >= 70 ? 'var(--destructive)' : 'var(--text-secondary)';
    html += `<div class="val-gauge-wrap">`;
    html += `<div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px;">${esc(per)} &gt; 과거 1년 중 하위 ${Math.round(vp)}% <span style="color:${zoneCol};font-weight:700;">(${zone})</span></div>`;
    html += `<div class="val-gauge"><div class="val-gauge-fill" style="width:${Math.min(100, Math.max(0, vp))}%;"></div><div class="val-gauge-marker" style="left:${Math.min(100, Math.max(0, vp))}%;"></div></div>`;
    html += `<div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-tertiary);margin-top:2px;"><span>0%</span><span>${Math.round(vp)}%</span><span>100%</span></div>`;
    html += `</div>`;
  } else {
    html += `<div style="font-size:12px;color:var(--text-tertiary);padding:8px 0;">ValPctile 데이터 수집 중 (약 2주 후 활성화)</div>`;
  }

  // SectorRelPE
  if (srpe != null) {
    const label = srpe > 0 ? '프리미엄' : srpe < 0 ? '할인' : '적정';
    const col = srpe > 10 ? 'var(--destructive)' : srpe < -10 ? 'var(--success)' : 'var(--text-secondary)';
    html += `<div style="font-size:13px;margin-top:8px;">섹터 대비 <span style="color:${col};font-weight:700;">${srpe > 0 ? '+' : ''}${fmt(srpe, 1)}%</span> <span style="color:var(--text-tertiary);">(${label})</span></div>`;
  }

  // PriceInLevel
  if (pil != null) {
    const pilLabel = pil <= 30 ? '저반영' : pil >= 60 ? '과반영' : '적정';
    const pilCol = pil <= 30 ? 'var(--success)' : pil >= 60 ? 'var(--destructive)' : 'var(--text-secondary)';
    html += `<div style="font-size:13px;margin-top:4px;">선반영도 <span style="color:${pilCol};font-weight:700;">${Math.round(pil)}/100</span> <span style="color:var(--text-tertiary);">(${pilLabel})</span></div>`;
  }

  html += '</div>';
  card.innerHTML = html;
}

function _renderScenarioTable(d) {
  const card = document.getElementById('scenario-table-card');
  if (!card) return;
  const sc = d.Scenarios;
  if (!sc || !sc.scores) { card.style.display = 'none'; return; }
  card.style.display = '';

  const scores = sc.scores;
  const triggers = sc.triggers || {};
  const responses = sc.responses || {};
  const keyVars = scores.key_variables || [];

  let html = '<div class="mece-inner">';
  html += '<div class="mece-header"><span class="mece-title">시나리오 시그널 강도</span><span class="mece-badge">참고용 시뮬레이션</span></div>';

  // 3-way 바
  html += `<div class="scenario-bar-wrap">`;
  html += `<div class="scenario-bar">`;
  html += `<div class="scenario-bar-seg scenario-bull" style="width:${scores.bull}%;" title="강세 ${scores.bull}%"></div>`;
  html += `<div class="scenario-bar-seg scenario-neutral" style="width:${scores.neutral}%;" title="중립 ${scores.neutral}%"></div>`;
  html += `<div class="scenario-bar-seg scenario-bear" style="width:${scores.bear}%;" title="약세 ${scores.bear}%"></div>`;
  html += `</div>`;
  html += `<div style="display:flex;justify-content:space-between;font-size:11px;margin-top:4px;">`;
  html += `<span style="color:#16A34A;font-weight:700;">강세 ${scores.bull}%</span>`;
  html += `<span style="color:#6B7280;font-weight:700;">중립 ${scores.neutral}%</span>`;
  html += `<span style="color:#DC2626;font-weight:700;">약세 ${scores.bear}%</span>`;
  html += `</div></div>`;

  // 시나리오 테이블
  html += `<table class="scenario-table"><thead><tr><th>시나리오</th><th>충족 조건</th><th>대응</th></tr></thead><tbody>`;
  const scenarioData = [
    { key: 'bull', label: '강세', pct: scores.bull, color: '#16A34A' },
    { key: 'neutral', label: '중립', pct: scores.neutral, color: '#6B7280' },
    { key: 'bear', label: '약세', pct: scores.bear, color: '#DC2626' },
  ];
  for (const s of scenarioData) {
    const trigs = (triggers[s.key] || []).map(t => esc(t)).join('<br>') || '-';
    const resp = esc(responses[s.key] || '-');
    html += `<tr><td style="color:${s.color};font-weight:700;">${s.label} ${s.pct}%</td><td>${trigs}</td><td>${resp}</td></tr>`;
  }
  html += `</tbody></table>`;

  // 핵심 변수
  if (keyVars.length) {
    html += `<div style="margin-top:8px;font-size:12px;color:var(--text-secondary);">핵심 변수: `;
    html += keyVars.map((v, i) => {
      const sign = v.impact > 0 ? '+' : '';
      const col = v.impact > 0 ? '#16A34A' : '#DC2626';
      return `<span style="color:${col};font-weight:600;">(${i + 1}) ${esc(v.name)} ${sign}${v.impact}</span>`;
    }).join(' &nbsp; ');
    html += `</div>`;
  }

  html += '</div>';
  card.innerHTML = html;
}

function _renderPriceLevels(d) {
  const card = document.getElementById('price-levels-card');
  if (!card) return;
  const pl = d.PriceLevels;
  if (!pl || !pl.price_levels) { card.style.display = 'none'; return; }
  card.style.display = '';

  const levels = pl.price_levels;
  const action = pl.action_plan || {};
  const vb = pl.vol_band || levels.vol_band || null;
  const ents = (vb && vb.entries) || [];
  const kLabel = i => (ents[i] != null ? ents[i].k.toFixed(1) + 'σ' : '');

  let html = '<div class="mece-inner">';
  html += '<div class="mece-header"><span class="mece-title">가격대별 대응 전략</span><span class="mece-badge">참고용 시뮬레이션</span></div>';

  // 가격 맵
  const pricePoints = [];
  if (levels.target_52w_high) pricePoints.push({ price: levels.target_52w_high, label: '52주 고가', cls: 'pm-target' });
  if (levels.target_analyst) pricePoints.push({ price: levels.target_analyst, label: '애널리스트 목표가', cls: 'pm-target' });
  pricePoints.push({ price: levels.price, label: '현재가', cls: 'pm-current' });
  pricePoints.push({ price: levels.entry_1, label: `1차 (${kLabel(0)})`, cls: 'pm-entry' });
  pricePoints.push({ price: levels.entry_2, label: `2차 (${kLabel(1)})`, cls: 'pm-entry' });
  pricePoints.push({ price: levels.entry_3, label: `3차 (${kLabel(2)})`, cls: 'pm-entry' });
  pricePoints.push({ price: levels.stop_loss, label: `손절 (${vb ? vb.stop_k.toFixed(1) + 'σ' : ''})`, cls: 'pm-stop' });

  // 피보나치 추가
  if (levels.fib_382) pricePoints.push({ price: levels.fib_382, label: 'Fib 38.2%', cls: 'pm-fib' });
  if (levels.fib_500) pricePoints.push({ price: levels.fib_500, label: 'Fib 50%', cls: 'pm-fib' });
  if (levels.fib_618) pricePoints.push({ price: levels.fib_618, label: 'Fib 61.8%', cls: 'pm-fib' });

  // 가격 높은 순 정렬
  pricePoints.sort((a, b) => b.price - a.price);

  html += `<div class="price-map">`;
  for (const pt of pricePoints) {
    html += `<div class="price-map-row ${pt.cls}">`;
    html += `<span class="price-map-val">${fmtPrice(pt.price)}</span>`;
    html += `<span class="price-map-line"></span>`;
    html += `<span class="price-map-label">${esc(pt.label)}${pt.cls === 'pm-current' ? ' *' : ''}</span>`;
    html += `</div>`;
  }
  html += `</div>`;

  // 변동성 소스 정보
  if (vb) {
    const srcLabel = { VKOSPI: 'VKOSPI', VIX: 'VIX', ATR: '종목 ATR' }[vb.source] || vb.source;
    const profLabel = vb.profile === 'deep' ? '깊은 급락 밴드 (1~2σ)' : '얕은 눌림 밴드 (0~1σ)';
    html += `<div style="font-size:12px;color:var(--text-secondary);margin-top:8px;">${esc(srcLabel)} 기준 · 일일 σ <b>${fmt(vb.sigma_daily, 2)}%</b> &middot; ${profLabel}</div>`;
  }

  // 신규 진입자 / 기존 보유자 분기 카드
  const newInv = action.new_investor || {};
  const holder = action.holder || {};
  html += `<div class="action-cards">`;
  // 신규 진입자
  html += `<div class="action-card action-new">`;
  html += `<div class="action-card-title">신규 진입자</div>`;
  html += `<div class="action-card-action">${esc(newInv.action || '-')}</div>`;
  if (newInv.details && newInv.details.length) {
    html += `<ul class="action-card-list">`;
    for (const det of newInv.details) html += `<li>${esc(det)}</li>`;
    html += `</ul>`;
  }
  html += `</div>`;
  // 기존 보유자
  html += `<div class="action-card action-holder">`;
  html += `<div class="action-card-title">기존 보유자</div>`;
  html += `<div class="action-card-action">${esc(holder.action || '-')}</div>`;
  if (holder.details && holder.details.length) {
    html += `<ul class="action-card-list">`;
    for (const det of holder.details) html += `<li>${esc(det)}</li>`;
    html += `</ul>`;
  }
  html += `</div>`;
  html += `</div>`; // .action-cards

  // 변동성 분할매수 인터랙티브 섹션 (vol_band 있을 때만)
  if (vb && ents.length) {
    html += `<div class="volband" id="volband-sec"></div>`;
  }

  html += '</div>';
  card.innerHTML = html;

  // 인터랙티브 와이어링
  if (vb && ents.length) _wireVolBand(pl, d);

  // 디스클레이머 표시
  const disc = document.getElementById('mece-disclaimer');
  if (disc) disc.style.display = '';
}

// ── 변동성 분할매수 인터랙티브 상태/렌더 ──────────────────────────────────
var _vbState = { total: 10000000, mode: 'equal', geoRatio: 1.30, stepMult: 2.5, custom: null };

function _vbWeights(n) {
  const s = _vbState;
  let raw;
  if (s.mode === 'geo') {
    raw = Array.from({ length: n }, (_, i) => Math.pow(s.geoRatio, i));
  } else if (s.mode === 'step') {
    const back = Math.max(1, Math.floor(n / 3));
    raw = Array.from({ length: n }, (_, i) => (i >= n - back ? s.stepMult : 1.0));
  } else if (s.mode === 'custom' && s.custom && s.custom.length === n) {
    raw = s.custom.map(x => Math.max(0, x));
  } else {
    raw = Array(n).fill(1);
  }
  const sum = raw.reduce((a, b) => a + b, 0) || 1;
  return raw.map(v => v / sum);
}

function _wireVolBand(pl, d) {
  const sec = document.getElementById('volband-sec');
  if (!sec) return;
  const vb = pl.vol_band;
  const ents = vb.entries;
  const base = pl.price_levels.price;
  const sigD = vb.sigma_daily;
  const n = ents.length;
  const nfmt = v => Math.round(v).toLocaleString('ko-KR');

  function paint() {
    const w = _vbWeights(n);
    const total = _vbState.total || 0;
    const maxW = Math.max(...w);
    let cum = 0, shares = 0;
    const rows = w.map((wi, i) => {
      cum += wi;
      const amt = total * wi;
      if (ents[i].price > 0) shares += amt / ents[i].price;
      return `<tr>
        <td>${i + 1}회</td>
        <td>${ents[i].k.toFixed(1)}σ</td>
        <td>${fmtPrice(ents[i].price)}</td>
        <td class="vb-acc">${(wi * 100).toFixed(1)}%</td>
        <td>${nfmt(amt)}</td>
        <td><span class="vb-bar"><span style="width:${maxW > 0 ? (wi / maxW * 100).toFixed(0) : 0}%"></span></span></td>
        <td class="vb-dim">${ents[i].prob.toFixed(1)}%</td>
        <td class="vb-dim">${(cum * 100).toFixed(0)}%</td>
      </tr>`;
    }).join('');
    const avg = shares > 0 ? total / shares : 0;
    const disc = base > 0 ? (base - avg) / base * 100 : 0;

    // 리스크 시나리오 (배분 반영 재계산)
    function fill(j) {
      let a = 0, sh = 0, ws = 0;
      for (let i = 0; i <= j; i++) { const amt = total * w[i]; a += amt; ws += w[i]; if (ents[i].price > 0) sh += amt / ents[i].price; }
      return { ac: sh > 0 ? a / sh : 0, ws };
    }
    const mid = Math.max(0, Math.round((n - 1) / 2));
    const s0 = fill(0), sMid = fill(mid), sAll = fill(n - 1);
    const recMid = sMid.ac > 0 ? (base - sMid.ac) / sMid.ac * 100 : 0;
    const recAll = sAll.ac > 0 ? (base - sAll.ac) / sAll.ac * 100 : 0;
    const stressSig = ents[n - 1].k + 1.0;
    const pStress = base * (1 - stressSig * sigD / 100);
    const lossStress = sAll.ac > 0 ? (sAll.ac - pStress) / sAll.ac * 100 : 0;

    const scn = [
      { cls: 'vb-idle', lab: '1회만 체결 후 반등', big: (s0.ws * 100).toFixed(0) + '% 투입', det: `미투입 ${((1 - s0.ws) * 100).toFixed(0)}% — 반등 시 기회손실` },
      { cls: 'vb-up', lab: `중간(${ents[mid].k.toFixed(1)}σ) 회복`, big: '+' + recMid.toFixed(2) + '%', det: `${(sMid.ws * 100).toFixed(0)}% 투입 · 평단 ${fmtPrice(sMid.ac)}` },
      { cls: 'vb-up', lab: '전량 체결 후 회복', big: '+' + recAll.toFixed(2) + '%', det: `100% 투입 · 평단 ${fmtPrice(sAll.ac)}` },
      { cls: 'vb-down', lab: `하방 스트레스 (${stressSig.toFixed(1)}σ)`, big: '−' + lossStress.toFixed(2) + '%', det: `평가손 ${nfmt(total * lossStress / 100)}` },
    ];
    const scnHtml = scn.map(s => `<div class="vb-scn ${s.cls}"><div class="vb-scn-lab">${s.lab}</div><div class="vb-scn-big">${s.big}</div><div class="vb-scn-det">${s.det}</div></div>`).join('');

    sec.querySelector('.vb-tbody').innerHTML = rows;
    sec.querySelector('.vb-summary').innerHTML =
      `<div class="vb-sum-card"><div class="vb-sum-k">평균 매입단가</div><div class="vb-sum-v">${fmtPrice(avg)}</div><div class="vb-sum-s">기준가 대비 −${disc.toFixed(2)}%</div></div>` +
      `<div class="vb-sum-card"><div class="vb-sum-k">총 투입금</div><div class="vb-sum-v">${nfmt(total)}</div><div class="vb-sum-s">${n}회 분할</div></div>`;
    sec.querySelector('.vb-scn-grid').innerHTML = scnHtml;
  }

  // 모드별 가용 옵션 (deep=6구간이면 step 노출)
  const modes = [['equal', '균등'], ['geo', '가중']];
  if (n >= 6) modes.splice(1, 0, ['step', '단계형']);
  modes.push(['custom', '직접']);
  const segBtns = modes.map(([m, lbl]) =>
    `<button class="vb-seg-btn${_vbState.mode === m ? ' on' : ''}" data-m="${m}">${lbl}</button>`).join('');

  sec.innerHTML = `
    <div class="vb-head">📊 변동성 분할매수</div>
    <div class="vb-controls">
      <label class="vb-total-field">총 투입금
        <input type="number" class="vb-total" value="${_vbState.total}" min="0" step="100000">
      </label>
      <div class="vb-seg">${segBtns}</div>
    </div>
    <div class="vb-param vb-param-geo" style="display:${_vbState.mode === 'geo' ? 'flex' : 'none'}">
      <span>공비 r</span><input type="range" class="vb-geo" min="0.5" max="2" step="0.05" value="${_vbState.geoRatio}">
      <span class="vb-geo-v">${_vbState.geoRatio.toFixed(2)}</span>
    </div>
    <div class="vb-param vb-param-step" style="display:${_vbState.mode === 'step' ? 'flex' : 'none'}">
      <span>후반 가중 배수</span><input type="range" class="vb-step" min="1" max="4" step="0.1" value="${_vbState.stepMult}">
      <span class="vb-step-v">${_vbState.stepMult.toFixed(1)}×</span>
    </div>
    <div class="vb-table-wrap"><table class="vb-table">
      <thead><tr><th>회차</th><th>σ</th><th>진입가</th><th>비중</th><th>금액</th><th>분포</th><th>도달</th><th>누적</th></tr></thead>
      <tbody class="vb-tbody"></tbody>
    </table></div>
    <div class="vb-summary"></div>
    <div class="vb-scn-grid"></div>
    <div class="vb-note">도달확률은 1일 정규분포 기준 · 평균단가 = 총투입금 ÷ 총수량 · 실제 체결·갭·세금 미반영</div>`;

  // 이벤트 바인딩
  sec.querySelector('.vb-total').addEventListener('input', e => { _vbState.total = parseFloat(e.target.value) || 0; paint(); });
  sec.querySelectorAll('.vb-seg-btn').forEach(b => b.addEventListener('click', () => {
    _vbState.mode = b.dataset.m;
    sec.querySelectorAll('.vb-seg-btn').forEach(x => x.classList.toggle('on', x === b));
    sec.querySelector('.vb-param-geo').style.display = _vbState.mode === 'geo' ? 'flex' : 'none';
    sec.querySelector('.vb-param-step').style.display = _vbState.mode === 'step' ? 'flex' : 'none';
    paint();
  }));
  const geoEl = sec.querySelector('.vb-geo');
  if (geoEl) geoEl.addEventListener('input', e => { _vbState.geoRatio = parseFloat(e.target.value); sec.querySelector('.vb-geo-v').textContent = _vbState.geoRatio.toFixed(2); paint(); });
  const stepEl = sec.querySelector('.vb-step');
  if (stepEl) stepEl.addEventListener('input', e => { _vbState.stepMult = parseFloat(e.target.value); sec.querySelector('.vb-step-v').textContent = _vbState.stepMult.toFixed(1) + '×'; paint(); });

  paint();
}

function _renderFinanceTab(d) {
  const el = document.getElementById('dp-finance-content');
  if (!el) return;

  // 재무 데이터가 전부 0이면 데이터 없음 메시지
  const hasFinance = d._PER || d._PBR || d._ROE || d._MarketCap;
  if (!hasFinance) {
    el.innerHTML = `<div style="padding:24px 16px;text-align:center;color:var(--text-tertiary);font-size:13px;line-height:1.8;">
      <div style="font-size:22px;margin-bottom:8px;">📊</div>
      Yahoo Finance에서 이 종목의 재무 데이터를<br>제공하지 않아요.<br>
      <span style="font-size:11px;margin-top:4px;display:block;">한국 종목은 네이버 증권을 참고해 주세요.</span>
    </div>`;
    return;
  }

  const roeRaw = d._ROE ? fmt(d._ROE * 100, 1) + '%' + _roeLbl(d._ROE) : '—';
  const epsRaw = (d._EPSGrowth != null && d._EPSGrowth !== 0) ? (d._EPSGrowth >= 0 ? '+' : '') + fmt(d._EPSGrowth*100,1)+'%' + _epsLbl(d._EPSGrowth) : '—';
  const perLbl = d._PER ? (d._PER < 15 ? ' · 저평가 가능' : d._PER > 40 ? ' · 고평가 주의' : '') : '';
  const pbrLbl = d._PBR ? (d._PBR < 1 ? ' · 자산 대비 저평가' : d._PBR > 5 ? ' · 고평가' : '') : '';
  const omLbl  = d._OperatingMargin ? (d._OperatingMargin > 0.20 ? ' · 우수' : d._OperatingMargin > 0.10 ? ' · 양호' : d._OperatingMargin > 0 ? ' · 보통' : ' · 손실') : '';
  const epsAccelRaw = d.EPSAcceleration ? '가속 중 🚀' : (d.EPSAcceleration === false ? '가속 아님' : '—');
  const epsAccelCol = d.EPSAcceleration ? 'var(--success)' : null;

  // 중복/저신호 행 제거: 매출성장(→EPS성장과 상관)·배당(성장주 스캔서 저신호)·부채비율(섹터마다 정상범위 달라 오해)·
  // 시가총액(→테이블에 있음)·밸류/퀄리티 팩터(→CAN SLIM 탭에 분해 표시).
  const rows = [
    ['PER',       d._PER   ? fmt(d._PER, 1) + perLbl  : '—', '주가÷순이익 · 15↓ 저평가 · 40↑ 고평가',    d._PER && d._PER < 15 ? 'var(--success)' : d._PER > 40 ? 'var(--destructive)' : null],
    ['PBR',       d._PBR   ? fmt(d._PBR, 2) + pbrLbl  : '—', '주가÷순자산 · 1↓ 자산 대비 저렴',           null],
    ['ROE',       roeRaw,                                      '자기자본이익률 · 17%↑ 오닐 기준 합격',     d._ROE > 0.15 ? 'var(--success)' : null],
    ['EPS 성장률',epsRaw,                                      '분기 순이익 전년비 · 25%↑ 성장주 기준',    d._EPSGrowth > 0 ? 'var(--success)' : 'var(--destructive)'],
    ['EPS 가속',  epsAccelRaw,                                 '전분기 대비 성장 가속 중인가 (CAN SLIM C원칙)', epsAccelCol],
    ['영업이익률',d._OperatingMargin ? fmt(d._OperatingMargin*100,1)+'%' + omLbl : '—', '매출 대비 영업이익 · 20%↑ 우수',  null],
  ];

  el.innerHTML = rows.map(([l, v, s, c]) => _indicatorRowHtml(l, v, s, c)).join('');
}


// ── RS Rating 카드 ───────────────────────────────────────────────────────
function _renderRsRating(rs) {
  const wrap = document.getElementById('rs-rating-wrap');
  if (!wrap || !rs) return;
  const r = rs.rating || 50;
  // 등급별 색상
  const color = r >= 80 ? '#22c55e' : r >= 70 ? '#84cc16' : r >= 50 ? '#94a3b8' : r >= 30 ? '#f97316' : '#ef4444';
  const bg    = r >= 80 ? 'rgba(34,197,94,.08)' : r >= 70 ? 'rgba(132,204,22,.08)' : r >= 50 ? 'rgba(148,163,184,.08)' : r >= 30 ? 'rgba(249,115,22,.08)' : 'rgba(239,68,68,.08)';
  const leaderBadge = rs.is_leader ? `<span style="font-size:10px;background:rgba(34,197,94,.15);color:#22c55e;padding:2px 7px;border-radius:10px;font-weight:700;margin-left:6px;">LEADER</span>` : '';
  // 기간별 수익률 행
  const retRow = (label, val) => `<div style="display:flex;justify-content:space-between;align-items:center;">
    <span style="color:var(--text-tertiary);font-size:11px;">${label}</span>
    <span style="font-size:12px;font-weight:600;color:${val >= 0 ? '#22c55e' : '#ef4444'};">${val >= 0 ? '+' : ''}${val}%</span>
  </div>`;
  wrap.innerHTML = `
    <div style="background:${bg};border:1px solid ${color}33;border-radius:var(--radius);padding:12px 14px;margin-top:4px;">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">
        <div style="font-size:11px;font-weight:700;color:var(--text-secondary);letter-spacing:.05em;">RS RATING${leaderBadge}</div>
        <div style="font-size:26px;font-weight:800;color:${color};line-height:1;">${r}<span style="font-size:13px;font-weight:500;color:var(--text-tertiary);margin-left:2px;">/99</span></div>
      </div>
      <!-- 게이지 바 -->
      <div style="height:6px;border-radius:3px;background:var(--border);margin-bottom:10px;overflow:hidden;">
        <div style="height:100%;width:${r}%;background:${color};border-radius:3px;transition:width .4s;"></div>
      </div>
      <div style="font-size:12px;font-weight:600;color:${color};margin-bottom:8px;">${rs.label || ''}</div>
      <div style="display:flex;flex-direction:column;gap:3px;">
        ${retRow('1개월 수익률', rs.r1_pct ?? 0)}
        ${retRow('3개월 수익률', rs.r3_pct ?? 0)}
        ${retRow('6개월 수익률', rs.r6_pct ?? 0)}
        ${retRow('12개월 수익률', rs.r12_pct ?? 0)}
      </div>
      <div style="margin-top:8px;font-size:10px;color:var(--text-tertiary);">가중 수익률(1M×25%+3M×40%+6M×20%+12M×15%) 기준 상대강도 지수. 80 이상이면 시장 주도주.</div>
    </div>`;
  wrap.style.display = '';
}

// ── 과열·바닥 신호 카드 ───────────────────────────────────────────────────
function _renderHeatSignal(hs) {
  const wrap = document.getElementById('heat-signal-wrap');
  if (!wrap || !hs) return;
  const colorMap = { hot: '#ef4444', warm: '#f97316', neutral: '#94a3b8', cool: '#60a5fa', cold: '#3b82f6' };
  const bgMap    = { hot: 'rgba(239,68,68,.08)', warm: 'rgba(249,115,22,.08)', neutral: 'rgba(148,163,184,.08)', cool: 'rgba(96,165,250,.08)', cold: 'rgba(59,130,246,.08)' };
  const c = colorMap[hs.color] || '#94a3b8';
  const bg = bgMap[hs.color] || 'rgba(148,163,184,.08)';
  const abs = Math.abs(hs.score);
  const barW = Math.min(100, abs) + '%';
  const barDir = hs.score >= 0 ? 'left' : 'right';
  const fmtComp = (v, lo, hi) => {
    const col = v >= hi ? '#ef4444' : v <= lo ? '#3b82f6' : '#94a3b8';
    return `<span style="color:${col};font-weight:600;">${v}</span>`;
  };
  wrap.innerHTML = `
<div class="card" style="padding:14px 16px; border-left:3px solid ${c}; background:${bg};">
  <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:10px;">
    <span style="font-size:13px; font-weight:700; color:var(--text-primary);">RSI 온도계</span>
    <span style="font-size:12px; font-weight:700; color:${c}; padding:2px 10px; border-radius:100px; background:${bg}; border:1px solid ${c};">${esc(hs.label)}</span>
  </div>
  <div style="display:flex; align-items:center; gap:8px; margin-bottom:10px;">
    <span style="font-size:11px; color:var(--text-tertiary); width:32px;">냉각</span>
    <div style="flex:1; height:6px; border-radius:3px; background:var(--surface-subtle); position:relative; overflow:hidden;">
      <div style="position:absolute; top:0; ${barDir}:50%; width:${barW/2}; height:100%; background:${c}; border-radius:3px;"></div>
      <div style="position:absolute; top:0; left:50%; width:1px; height:100%; background:var(--border);"></div>
    </div>
    <span style="font-size:11px; color:var(--text-tertiary); width:32px; text-align:right;">과열</span>
  </div>
  <div style="display:grid; grid-template-columns:1fr 1fr; gap:4px 16px; font-size:11px; color:var(--text-secondary);">
    <span>RSI ${fmtComp(hs.rsi, 30, 70)}</span>
    <span>BB%B ${fmtComp(hs.bb_b, 20, 80)}</span>
    <span>Stoch ${fmtComp(hs.stoch_k, 20, 80)}</span>
    <span>MFI ${fmtComp(hs.mfi, 20, 80)}</span>
  </div>
  <div style="margin-top:8px; font-size:10px; color:var(--text-tertiary);">종합 점수 ${hs.score > 0 ? '+' : ''}${hs.score} / 100 · 참고용 보조지표</div>
</div>`;
  wrap.style.display = 'block';
}

let _detailSeq = 0;           // openDetail / _loadAqSignal stale-guard
let _lastDetailData = null;   // /api/sentiment lazy-merge용 현재 패널 데이터
let _dpFourAxisLoadedFor = null;
let _dpFourAxisLoadingFor = null;
let _dpFourAxisReqSeq = 0;
let _detailFourAxisReqSeq = 0;
const FOUR_AXIS_FETCH_TIMEOUT_MS = 90000;

/* ── F6: IntersectionObserver lazy load — 차트 섹션이 뷰포트 진입 시 1회 호출 ── */
let _dpFourAxisIO = null;
function _scheduleLoadDpFourAxis(ticker) {
  if (!('IntersectionObserver' in window)) { loadDpFourAxis(ticker); return; }
  // .dp-section-timing은 항상 가시 (chart-wrap은 초기 display:none이라 IO가 fire 안 함)
  const target = document.querySelector('#detail-panel .dp-section-timing')
              || document.getElementById('dp-fa-haiku');
  if (!target) { loadDpFourAxis(ticker); return; }
  if (_dpFourAxisIO) { try { _dpFourAxisIO.disconnect(); } catch(_) {} _dpFourAxisIO = null; }
  const scrollRoot = document.querySelector('#detail-panel .dp-scroll') || null;
  const io = new IntersectionObserver(function(entries) {
    for (const e of entries) {
      if (e.isIntersecting) {
        try { io.disconnect(); } catch(_) {}
        if (_dpFourAxisIO === io) _dpFourAxisIO = null;
        loadDpFourAxis(ticker);
        return;
      }
    }
  }, { root: scrollRoot, rootMargin: '200px 0px', threshold: 0.01 });
  _dpFourAxisIO = io;
  io.observe(target);
}

async function _fetchWithTimeout(url, options = {}, timeoutMs = FOUR_AXIS_FETCH_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (err) {
    if (err?.name === 'AbortError') {
      throw new Error(`요청 시간 초과 (${Math.round(timeoutMs / 1000)}초)`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

async function _readApiError(res) {
  let serverMsg = '';
  try {
    const text = await res.text();
    if (!text) return `HTTP ${res.status}`;
    try {
      const j = JSON.parse(text);
      serverMsg = j?.error || text.slice(0, 200);
    } catch (_) {
      serverMsg = text.slice(0, 200);
    }
  } catch (_) {
    serverMsg = '';
  }
  return `HTTP ${res.status}${serverMsg ? ' - ' + serverMsg : ''}`;
}

function switchDpTab(tabId) {
  document.querySelectorAll('.dp-tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabId);
  });
  document.querySelectorAll('.dp-tab-panel').forEach(p => {
    p.classList.toggle('active', p.id === `dp-tab-${tabId}`);
  });
  // 공시·뉴스 탭 lazy loading (드로어)
  if (tabId === 'dartnews') {
    const tk = (document.getElementById('dp-ticker')?.textContent || '').trim();
    if (tk && tk !== '—' && tk !== '…') loadDpDartNews(tk);
  }
  // US 인사이트 탭 lazy loading (드로어)
  if (tabId === 'usinsight') {
    const tk = (document.getElementById('dp-ticker')?.textContent || '').trim();
    if (tk && tk !== '—' && tk !== '…') loadDpUSInsight(tk);
  }
}

let _dpDartNewsLoaded = false;
let _dpUSInsightLoaded = false;

async function loadDpDartNews(ticker) {
  if (_dpDartNewsLoaded) return;
  _dpDartNewsLoaded = true;

  const container = document.getElementById('dp-dartnews-content');
  if (!container) return;
  container.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-tertiary);font-size:13px;">공시·뉴스 로딩 중...</div>';

  try {
    const p = new URLSearchParams({ market: currentMarket });
    const res = await fetch(`/api/dart-news/${encodeURIComponent(ticker)}?${p}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    _renderDartNews(container, data);
    // 뉴스 한 줄 바 채우기
    _fillNewsBar(data);
  } catch (e) {
    container.innerHTML = `<div class="dn-empty">공시·뉴스를 불러올 수 없습니다: ${e.message}</div>`;
  }
}

function _fillNewsBar(data) {
  const bar = document.getElementById('dp-news-bar');
  const link = document.getElementById('dp-news-bar-link');
  if (!bar || !link) return;
  const news = data.news || {};
  const first = (news.top_positive || []).concat(news.top_negative || []).find(i => i.title);
  if (first) {
    link.textContent = first.title;
    link.href = (first.link && /^https?:\/\//.test(first.link)) ? first.link : '#';
    bar.style.display = 'flex';
  } else if ((data.filings || []).length) {
    const f = data.filings[0];
    link.textContent = f.title || f.report_nm || '공시 확인';
    link.href = f.url || f.rcept_url || '#';
    bar.style.display = 'flex';
  } else {
    bar.style.display = 'none';
  }
}

async function loadDpUSInsight(ticker) {
  if (_dpUSInsightLoaded) return;
  _dpUSInsightLoaded = true;

  const container = document.getElementById('dp-usinsight-content');
  if (!container) return;
  container.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-tertiary);font-size:13px;">Loading US Insight...</div>';

  try {
    const p = new URLSearchParams({ market: 'US' });
    const res = await fetch(`/api/us-insight/${encodeURIComponent(ticker)}?${p}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    _renderUSInsight(container, data);
  } catch (e) {
    container.innerHTML = `<div class="dn-empty">US Insight 로드 실패: ${esc(e.message)}</div>`;
  }
}

async function loadDpFourAxis(ticker) {
  const loading = document.getElementById('dp-fouraxis-loading');
  const errDiv  = document.getElementById('dp-fouraxis-error');
  const header  = document.getElementById('dp-fouraxis-header');
  const obsDiv  = document.getElementById('dp-fouraxis-obs');
  if (!loading) return;
  if (_dpFourAxisLoadingFor === ticker) return;

  header.style.display = 'none';
  obsDiv.style.display = 'none';
  errDiv.style.display = 'none';
  loading.style.display = 'block';
  // 차트 영역 초기화 — 이전 종목 차트 잔상 방지
  const _prevImg = document.getElementById('dp-hd-chart');
  const _prevSkel = document.getElementById('dp-hd-skeleton');
  if (_prevImg) { _prevImg.src = ''; _prevImg.classList.remove('is-loaded'); }
  if (_prevSkel) _prevSkel.style.display = '';
  _dpFourAxisLoadingFor = ticker;
  const reqSeq = ++_dpFourAxisReqSeq;

  try {
    const p = new URLSearchParams({ market: currentMarket });
    const res = await _fetchWithTimeout(`/api/four_axis/${encodeURIComponent(ticker)}?${p}`);
    if (!res.ok) throw new Error(await _readApiError(res));
    const d = await res.json();
    if (d.error) throw new Error(d.error);
    if (reqSeq !== _dpFourAxisReqSeq) return;

    _dpFourAxisLoadedFor = ticker;
    _dpFourAxisLoadingFor = null;

    // ── 핸드드로잉 차트 이미지 렌더 ─────────────────────────────
    try {
      const skeleton = document.getElementById('dp-hd-skeleton');
      const chartImg = document.getElementById('dp-hd-chart');
      if (chartImg && d.chart) {
        chartImg.onload = () => {
          if (skeleton) skeleton.style.display = 'none';
          chartImg.classList.add('is-loaded');
        };
        chartImg.onerror = () => {
          if (skeleton) skeleton.style.display = 'none';
        };
        chartImg.src = 'data:image/png;base64,' + d.chart;
      } else if (skeleton) {
        skeleton.style.display = 'none';
      }
    } catch (e) { console.warn('handdrawn chart render failed:', e); }

    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('dp-fa-phase', d.phase || '');
    // ── 별점 = V5.1 진입 점수 파생 (단일 소스) ──────────────────────
    // 별점과 '진입 타이밍' 점수가 같은 화면에서 모순되지 않도록,
    // 4축 signal_stars 대신 스캔 레코드의 EntryScore(0~100)에서 직접
    // 환산한다. 밴드는 V5.1 상태(STRONG≥50 / NEUTRAL 30~49 /
    // AVOID<30)와 정렬 → 별점·점수·문구가 항상 같은 방향.
    const _rec = _stockMap[ticker];
    const _es  = (_rec && _rec.EntryScore != null) ? Number(_rec.EntryScore) : null;
    let _stars;
    if (_es == null || Number.isNaN(_es)) {
      _stars = d.signal_stars || 0;            // EntryScore 없으면 4축 폴백
    } else if (_es >= 70) _stars = 5;
    else if (_es >= 50) _stars = 4;
    else if (_es >= 40) _stars = 3;
    else if (_es >= 30) _stars = 2;
    else _stars = 1;
    set('dp-fa-stars', '★'.repeat(_stars) + '☆'.repeat(5 - _stars));

    const _starMeaningTbl = {
      5: '진입조건 모두 충족',
      4: '진입조건 대부분 충족',
      3: '추세 확인 필요',
      2: '눌림 대기 구간',
      1: '진입조건 미충족',
      0: '데이터 부족',
    };
    // 별점 테이블 기준 — EntryScore와 항상 일관되게 유지
    const _meaningText = _starMeaningTbl[_stars] || '';
    set('dp-fa-stars-meaning', _meaningText ? `· ${_meaningText}` : '');
    // 주도주 배지: RS Rating 80+ + EPS 가속 동시 충족
    const _leaderBadge = document.getElementById('dp-leader-badge');
    if (_leaderBadge) {
      if ((d.RSRating ?? 0) >= 80 && d.EPSAcceleration) {
        _leaderBadge.textContent = '⚡ 주도주 확정';
        _leaderBadge.style.display = 'inline-flex';
      } else {
        _leaderBadge.style.display = 'none';
      }
    }
    // 1008-풀 OneLiner는 populateDetailPanel에서 이미 설정함 — 여기서 덮어쓰지 않음
    set('dp-fa-trend-score',   d.trend?.score    ?? '-');
    set('dp-fa-trend-verdict', d.trend?.verdict  ?? '');
    set('dp-fa-mom-score',     d.momentum?.score ?? '-');
    set('dp-fa-mom-verdict',   d.momentum?.verdict ?? '');
    set('dp-fa-vol-score',     d.volatility?.score ?? '-');
    set('dp-fa-vol-verdict',   d.volatility?.verdict ?? '');
    set('dp-fa-volm-score',    d.volume?.score   ?? '-');
    set('dp-fa-volm-verdict',  d.volume?.verdict ?? '');

    // ── 친절한 이유 목록 ──────────────────────────────────────────
    const _reasonsEl = document.getElementById('dp-fa-reasons');
    if (_reasonsEl) {
      const _axDef = [
        { ax: d.trend,
          pros: { 4: '추세가 강해요 — 이평선이 나란히 올라가고 있어요' },
          cons: { 2: '추세가 꺾이고 있어요 — 방향이 불안정해요', 1: '추세가 꺾이고 있어요 — 방향이 불안정해요' } },
        { ax: d.momentum,
          pros: { 4: '가격 탄력이 좋아요 — 오르는 힘이 있어요' },
          cons: { 2: '모멘텀이 약해요 — 오르는 힘이 부족해요', 1: '모멘텀이 약해요 — 오르는 힘이 부족해요' } },
        { ax: d.volatility,
          pros: { 4: '변동성이 낮아요 — 안정적으로 움직이고 있어요' },
          cons: { 2: '요즘 등락이 좀 심해요 — 갑자기 흔들릴 수 있어요', 1: '요즘 등락이 좀 심해요 — 갑자기 흔들릴 수 있어요' } },
        { ax: d.volume,
          pros: { 4: '거래량이 받쳐줘요 — 사람들이 많이 사고 있어요' },
          cons: { 2: '거래량이 많이 빠졌어요 — 관심이 줄어들고 있어요', 1: '거래량이 많이 빠졌어요 — 관심이 줄어들고 있어요' } },
      ];
      const _pros = [], _warns = [];
      for (const { ax, pros, cons } of _axDef) {
        const sc = ax?.score;
        if (sc == null) continue;
        if (sc >= 4) _pros.push(pros[4]);
        else if (sc <= 2) _warns.push(cons[sc] || cons[2]);
      }
      if (d.momentum?.details?.bull_div) _pros.push('상승 다이버전스 포착 — 바닥 반등 신호예요');
      if (d.momentum?.details?.bear_div) _warns.push('단기 과열 신호가 있어요 — 눌림목 올 수 있어요');
      const _allItems = [
        ..._pros.map(p => `<div class="dp-timing-pro">✅ ${p}</div>`),
        ..._warns.map(w => `<div class="dp-timing-warn">⚠️ ${w}</div>`)
      ];
      _reasonsEl.innerHTML = _allItems.slice(0, 3).join('');
    }

    header.style.display = 'block';

    const obs = d.key_observation || d.structured_analysis || '';
    if (obs) {
      document.getElementById('dp-fa-observation').textContent = obs;
      obsDiv.style.display = 'block';
    }
  } catch (e) {
    if (reqSeq !== _dpFourAxisReqSeq) return;
    _dpFourAxisLoadedFor = null;
    _dpFourAxisLoadingFor = null;
    errDiv.textContent = '분석 로드 실패: ' + e.message;
    errDiv.style.display = 'block';
  } finally {
    if (reqSeq !== _dpFourAxisReqSeq) return;
    loading.style.display = 'none';
  }
}

// Hero 스파크라인 — closes 배열 → 인라인 SVG (viewBox 300x110)
function buildSparklineSVG(closes, color) {
  if (!Array.isArray(closes) || closes.length < 2) return '';
  const W = 300, H = 110, pad = 6;
  const lo = Math.min(...closes), hi = Math.max(...closes);
  const span = (hi - lo) || 1;
  const n = closes.length;
  const x = i => (i / (n - 1)) * W;
  const y = v => pad + (1 - (v - lo) / span) * (H - pad * 2);
  const pts = closes.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const last = closes.length - 1;
  const gid = 'spkg_' + Math.abs(closes.length * 7 + Math.round(hi));
  return (
    `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:100%;display:block;">`
    + `<defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">`
    + `<stop offset="0" stop-color="${color}" stop-opacity=".22"/>`
    + `<stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>`
    + `<polygon points="${pts} ${W},${H} 0,${H}" fill="url(#${gid})"/>`
    + `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>`
    + `<circle cx="${x(last).toFixed(1)}" cy="${y(closes[last]).toFixed(1)}" r="4.5" fill="${color}"/>`
    + `<circle cx="${x(last).toFixed(1)}" cy="${y(closes[last]).toFixed(1)}" r="9" fill="${color}" opacity=".18"/>`
    + `</svg>`
  );
}

// ── 탭 전환 (detail.html onclick) ────────────────────────────────────────

function switchTab(tabId) {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    const active = btn.getAttribute('aria-controls') === `tab-${tabId}`;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-selected', String(active));
  });
  document.querySelectorAll('.tab-panel').forEach(panel => {
    panel.classList.toggle('active', panel.id === `tab-${tabId}`);
  });
  // 공시·뉴스 탭 lazy loading
  if (tabId === 'dartnews' && typeof TICKER !== 'undefined' && TICKER) {
    loadDartNews(TICKER);
  }
  // US 인사이트 탭 lazy loading
  if (tabId === 'usinsight' && typeof TICKER !== 'undefined' && TICKER) {
    loadUSInsight(TICKER);
  }
}


async function loadFourAxis(ticker) {
  const loading = document.getElementById('fouraxis-loading');
  const errDiv  = document.getElementById('fouraxis-error');
  const chartW  = document.getElementById('fouraxis-chart-wrap');
  if (!loading) return;

  const cacheKey = `fouraxis:${ticker}:${currentMarket}`;
  const cached = _clientCache.get(cacheKey);
  if (cached) {
    document.getElementById('fouraxis-chart').src = 'data:image/png;base64,' + cached.chart;
    chartW.style.display = 'block';
    loading.style.display = 'none';
    return;
  }

  loading.style.display = 'block';
  errDiv.style.display  = 'none';
  const reqSeq = ++_detailFourAxisReqSeq;

  try {
    const p = new URLSearchParams({ market: currentMarket });
    const res = await _fetchWithTimeout(`/api/four_axis/${encodeURIComponent(ticker)}?${p}`);
    if (!res.ok) throw new Error(await _readApiError(res));
    const d = await res.json();
    if (d.error) throw new Error(d.error);
    if (!d.chart) throw new Error('Empty chart payload');
    if (reqSeq !== _detailFourAxisReqSeq) return;

    _clientCache.set(cacheKey, d);
    document.getElementById('fouraxis-chart').src = 'data:image/png;base64,' + d.chart;
    chartW.style.display = 'block';
    _renderRsRating(d.rs_rating_data);
    _renderHeatSignal(d.heat_signal);
  } catch (e) {
    if (reqSeq !== _detailFourAxisReqSeq) return;
    errDiv.textContent = '4축 차트 실패: ' + e.message;
    errDiv.style.display = 'block';
  } finally {
    if (reqSeq !== _detailFourAxisReqSeq) return;
    loading.style.display = 'none';
  }
}

if (typeof TICKER !== 'undefined' && TICKER) {
  document.addEventListener('DOMContentLoaded', () => {
    // 4축 차트: 화면에 보일 때만 로드 (IntersectionObserver)
    const fourAxisEl = document.getElementById('fouraxis-loading') || document.getElementById('fouraxis-chart-wrap');
    if (fourAxisEl && 'IntersectionObserver' in window) {
      const obs = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting) {
          obs.disconnect();
          loadFourAxis(TICKER);
        }
      }, { rootMargin: '200px' });
      obs.observe(fourAxisEl);
    } else {
      loadFourAxis(TICKER);
    }
    loadConsensus(TICKER);
  });
}

// ── 공시·뉴스 탭 (DART + Naver News) ─────────────────────────────────

let _dartNewsLoaded = false;

async function loadDartNews(ticker) {
  if (_dartNewsLoaded) return;
  _dartNewsLoaded = true;

  const container = document.getElementById('dartnews-content');
  if (!container) return;
  container.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-tertiary);font-size:13px;">공시·뉴스 로딩 중...</div>';

  try {
    const p = new URLSearchParams({ market: currentMarket });
    const res = await fetch(`/api/dart-news/${encodeURIComponent(ticker)}?${p}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    _renderDartNews(container, data);
  } catch (e) {
    container.innerHTML = `<div class="dn-empty">공시·뉴스를 불러올 수 없습니다: ${e.message}</div>`;
  }
}

function _renderDartNews(container, data) {
  let html = '';
  const news = data.news || {};
  const filings = data.filings || [];

  // 뉴스 감성분석 카드
  if (data.news_available && news.count > 0) {
    const avg = news.avg_sentiment || 0;
    const tone = avg > 0.15 ? 'positive' : avg < -0.15 ? 'negative' : 'neutral';
    const toneKo = tone === 'positive' ? '긍정적' : tone === 'negative' ? '부정적' : '중립';
    const total = (news.positive || 0) + (news.negative || 0) + (news.neutral || 0);
    const posPct = total ? Math.round(((news.positive || 0) / total) * 100) : 0;
    const negPct = total ? Math.round(((news.negative || 0) / total) * 100) : 0;

    html += `<div class="dn-sentiment-card">
      <div class="dn-sentiment-header">
        <span class="dn-sentiment-title">뉴스 감성분석</span>
        <span class="dn-sentiment-badge ${tone}">${toneKo}</span>
      </div>
      <div class="dn-sentiment-bars">
        <span>긍정 ${news.positive || 0}</span>
        <span>중립 ${news.neutral || 0}</span>
        <span>부정 ${news.negative || 0}</span>
      </div>
      <div style="display:flex;gap:2px;margin-bottom:10px;">
        <div class="dn-bar-wrap" style="flex:${posPct || 1}"><div class="dn-bar-pos" style="width:100%"></div></div>
        <div class="dn-bar-wrap" style="flex:${100 - posPct - negPct || 1};background:var(--surface-muted)"></div>
        <div class="dn-bar-wrap" style="flex:${negPct || 1}"><div class="dn-bar-neg" style="width:100%"></div></div>
      </div>
      <div class="dn-summary-text">${esc(news.summary_text || '')}</div>`;

    // 주요 뉴스 헤드라인
    const topItems = [...(news.top_positive || []), ...(news.top_negative || [])].slice(0, 5);
    if (topItems.length) {
      html += '<div style="margin-top:12px;display:flex;flex-direction:column;">';
      for (const item of topItems) {
        const cls = item.sentiment > 0 ? 'positive' : item.sentiment < 0 ? 'negative' : 'neutral';
        const safeTitle = esc(item.title || '');
        const safeHref = (item.link && /^https?:\/\//.test(item.link)) ? esc(item.link) : '';
        const link = safeHref ? `<a href="${safeHref}" target="_blank" rel="noopener">${safeTitle}</a>` : safeTitle;
        html += `<div class="dn-news-item"><span class="dn-news-dot ${cls}"></span><span class="dn-news-title">${link}</span></div>`;
      }
      html += '</div>';
    }
    html += '</div>';
  } else if (data.news_available) {
    html += '<div class="dn-empty">관련 뉴스가 없습니다</div>';
  } else {
    html += '<div class="dn-empty">네이버 뉴스 API 키가 설정되지 않았습니다 (설정 → NAVER_CLIENT_ID/SECRET)</div>';
  }

  // 실적 서프라이즈 (KR)
  const krEHist = data.earnings_history || [];
  if (krEHist.length > 0) {
    html += `<div class="dn-filing-card">
      <div class="dn-filing-header">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
        실적 서프라이즈
        <span style="margin-left:auto;font-size:11px;color:var(--text-tertiary);font-weight:400;">최근 ${krEHist.length}분기</span>
      </div>
      <div style="display:grid;grid-template-columns:repeat(${Math.min(krEHist.length, 4)},1fr);gap:0;border-top:1px solid var(--border);">`;
    for (const q of krEHist) {
      const beat = q.beat === true;
      const miss = q.beat === false;
      const icon = beat ? '✓' : miss ? '✗' : '—';
      const iconColor = beat ? 'var(--success)' : miss ? 'var(--destructive)' : 'var(--text-tertiary)';
      const bgColor = beat ? 'rgba(52,199,89,0.06)' : miss ? 'rgba(255,59,48,0.06)' : '';
      const surpText = q.surprise_pct != null ? `컨센 대비 ${q.surprise_pct >= 0 ? '+' : ''}${q.surprise_pct}%` : '';
      const surpColor = q.surprise_pct != null ? (q.surprise_pct >= 0 ? 'var(--success)' : 'var(--destructive)') : 'var(--text-tertiary)';
      const yoyText = q.yoy_pct != null ? `YoY ${q.yoy_pct >= 0 ? '+' : ''}${q.yoy_pct}%` : '';
      const yoyColor = q.yoy_pct != null ? (q.yoy_pct >= 0 ? 'var(--success)' : 'var(--destructive)') : 'var(--text-tertiary)';
      html += `<div style="padding:12px 10px;text-align:center;border-right:1px solid var(--border);background:${bgColor};">
        <div style="font-size:11px;color:var(--text-tertiary);margin-bottom:6px;">${esc(q.date || '')}</div>
        <div style="font-size:20px;font-weight:800;color:${iconColor};margin-bottom:4px;">${icon}</div>
        <div style="font-size:12px;font-weight:700;color:${surpColor};margin-bottom:2px;">${surpText || '—'}</div>
        ${yoyText ? `<div style="font-size:11px;font-weight:600;color:${yoyColor};margin-bottom:4px;">${yoyText}</div>` : ''}
        <div style="font-size:11px;color:var(--text-secondary);">
          ${q.actual != null ? `<div>EPS ${q.actual.toLocaleString()}원</div>` : ''}
          ${q.estimate != null ? `<div style="color:var(--text-tertiary)">추정 ${q.estimate.toLocaleString()}원</div>` : ''}
        </div>
      </div>`;
    }
    html += '</div></div>';
  }

  // DART 공시 목록
  if (data.dart_available && filings.length > 0) {
    html += '<div class="dn-filing-card"><div class="dn-filing-header"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>최근 공시</div>';
    for (const f of filings) {
      const date = f.date ? `${f.date.slice(0,4)}.${f.date.slice(4,6)}.${f.date.slice(6,8)}` : '';
      const safeFilingTitle = esc(f.title || '');
      const safeFilingHref = (f.url && f.url.startsWith('https://dart.fss.or.kr')) ? esc(f.url) : '';
      const title = safeFilingHref ? `<a href="${safeFilingHref}" target="_blank" rel="noopener">${safeFilingTitle}</a>` : safeFilingTitle;
      html += `<div class="dn-filing-item"><span class="dn-filing-date">${date}</span><span class="dn-filing-title">${title}</span></div>`;
    }
    html += '</div>';
  } else if (data.dart_available) {
    html += '<div class="dn-empty">공시 내역이 없습니다</div>';
  } else {
    html += '<div class="dn-empty">DART API 키가 설정되지 않았습니다 (설정 → DART_API_KEY)</div>';
  }

  if (!html) html = '<div class="dn-empty">데이터가 없습니다</div>';
  container.innerHTML = html;
}

// ── US 인사이트 탭 ──────────────────────────────────────────────────

let _usInsightLoaded = false;

async function loadUSInsight(ticker) {
  if (_usInsightLoaded) return;
  _usInsightLoaded = true;

  const container = document.getElementById('usinsight-content');
  if (!container) return;
  container.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-tertiary);font-size:13px;">Loading US Insight...</div>';

  try {
    const p = new URLSearchParams({ market: 'US' });
    const res = await fetch(`/api/us-insight/${encodeURIComponent(ticker)}?${p}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    _renderUSInsight(container, data);
  } catch (e) {
    container.innerHTML = `<div class="dn-empty">US Insight 로드 실패: ${esc(e.message)}</div>`;
  }
}

function _renderUSInsight(container, data) {
  let html = '';

  // 1) 어닝 캘린더
  if (data.earnings_available) {
    const ear = data.earnings || {};
    const chipBg = ear.chip_bg || 'var(--surface-subtle)';
    const chipFg = ear.chip_fg || 'var(--text-primary)';
    const epsEst = ear.eps_estimate != null ? `EPS Est. $${ear.eps_estimate}` : '';
    const revEst = ear.revenue_estimate ? `Rev Est. ${ear.revenue_estimate}` : '';
    const metaItems = [epsEst, revEst].filter(Boolean);
    html += `<div class="us-earnings-card">
      ${ear.chip_show ? `<span class="us-earnings-chip" style="background:${esc(chipBg)};color:${esc(chipFg)}">${esc(ear.chip_text)}</span>` : `<span class="us-earnings-chip" style="background:var(--surface-subtle);color:var(--text-secondary)">📅</span>`}
      <div class="us-earnings-info">
        <div class="us-earnings-date">${esc(ear.date || 'TBD')}</div>
        <div class="us-earnings-sub">${ear.when ? esc(ear.when) : 'Earnings Date'}${metaItems.length ? ' · ' + metaItems.join(' · ') : ''}</div>
      </div>
    </div>`;
  }

  // 1-b) 실적 서프라이즈 히스토리
  const eHist = data.earnings_history || [];
  if (eHist.length > 0) {
    html += `<div class="dn-filing-card">
      <div class="dn-filing-header">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
        Earnings Surprise
        <span style="margin-left:auto;font-size:11px;color:var(--text-tertiary);font-weight:400;">최근 ${eHist.length}분기</span>
      </div>
      <div style="display:grid;grid-template-columns:repeat(${Math.min(eHist.length, 4)},1fr);gap:0;border-top:1px solid var(--border);">`;
    for (const q of eHist) {
      const beat = q.beat === true;
      const miss = q.beat === false;
      const icon = beat ? '✓' : miss ? '✗' : '—';
      const iconColor = beat ? 'var(--success)' : miss ? 'var(--destructive)' : 'var(--text-tertiary)';
      const bgColor = beat ? 'rgba(52,199,89,0.06)' : miss ? 'rgba(255,59,48,0.06)' : '';
      const surpText = q.surprise_pct != null ? `vs Est ${q.surprise_pct >= 0 ? '+' : ''}${q.surprise_pct}%` : '';
      const surpColor = q.surprise_pct != null ? (q.surprise_pct >= 0 ? 'var(--success)' : 'var(--destructive)') : 'var(--text-tertiary)';
      const yoyText = q.yoy_pct != null ? `YoY ${q.yoy_pct >= 0 ? '+' : ''}${q.yoy_pct}%` : '';
      const yoyColor = q.yoy_pct != null ? (q.yoy_pct >= 0 ? 'var(--success)' : 'var(--destructive)') : 'var(--text-tertiary)';
      html += `<div style="padding:12px 10px;text-align:center;border-right:1px solid var(--border);background:${bgColor};">
        <div style="font-size:11px;color:var(--text-tertiary);margin-bottom:6px;">${esc(q.date || '')}</div>
        <div style="font-size:20px;font-weight:800;color:${iconColor};margin-bottom:4px;">${icon}</div>
        <div style="font-size:12px;font-weight:700;color:${surpColor};margin-bottom:2px;">${surpText || '—'}</div>
        ${yoyText ? `<div style="font-size:11px;font-weight:600;color:${yoyColor};margin-bottom:4px;">${yoyText}</div>` : ''}
        <div style="font-size:11px;color:var(--text-secondary);">
          ${q.actual != null ? `<div>EPS $${q.actual}</div>` : ''}
          ${q.estimate != null ? `<div style="color:var(--text-tertiary)">Est $${q.estimate}</div>` : ''}
        </div>
      </div>`;
    }
    html += '</div></div>';
  }

  // 2) 기관보유 / 공매도
  if (data.holders_available) {
    const h = data.holders || {};
    const fmtPct = v => v != null ? (v * 100).toFixed(1) + '%' : 'N/A';
    html += `<div class="us-metric-grid">
      <div class="us-metric-card">
        <div class="us-metric-label">Institutional</div>
        <div class="us-metric-value">${fmtPct(h.institutional_pct)}</div>
      </div>
      <div class="us-metric-card">
        <div class="us-metric-label">Insider</div>
        <div class="us-metric-value">${fmtPct(h.insider_pct)}</div>
      </div>
      <div class="us-metric-card">
        <div class="us-metric-label">Short % Float</div>
        <div class="us-metric-value">${fmtPct(h.short_pct)}</div>
        ${h.short_ratio != null ? `<div class="us-metric-sub">Short Ratio ${h.short_ratio.toFixed(1)}</div>` : ''}
      </div>
    </div>`;
  }

  // 3) 뉴스 감성분석
  if (data.news_available) {
    const news = data.news || {};
    const avg = news.avg_sentiment || 0;
    const tone = avg > 0.15 ? 'positive' : avg < -0.15 ? 'negative' : 'neutral';
    const toneLabel = tone === 'positive' ? 'Positive' : tone === 'negative' ? 'Negative' : 'Neutral';
    const total = (news.positive || 0) + (news.negative || 0) + (news.neutral || 0);
    const posPct = total ? Math.round(((news.positive || 0) / total) * 100) : 0;
    const negPct = total ? Math.round(((news.negative || 0) / total) * 100) : 0;

    html += `<div class="dn-sentiment-card">
      <div class="dn-sentiment-header">
        <span class="dn-sentiment-title">News Sentiment</span>
        <span class="dn-sentiment-badge ${tone}">${toneLabel}</span>
      </div>
      <div class="dn-sentiment-bars">
        <span>Positive ${news.positive || 0}</span>
        <span>Neutral ${news.neutral || 0}</span>
        <span>Negative ${news.negative || 0}</span>
      </div>
      <div style="display:flex;gap:2px;margin-bottom:10px;">
        <div class="dn-bar-wrap" style="flex:${posPct || 1}"><div class="dn-bar-pos" style="width:100%"></div></div>
        <div class="dn-bar-wrap" style="flex:${100 - posPct - negPct || 1};background:var(--surface-muted)"></div>
        <div class="dn-bar-wrap" style="flex:${negPct || 1}"><div class="dn-bar-neg" style="width:100%"></div></div>
      </div>
      <div class="dn-summary-text">${esc(news.summary_text || '')}</div>`;

    const topItems = [...(news.top_positive || []), ...(news.top_negative || [])].slice(0, 5);
    if (topItems.length) {
      html += '<div style="margin-top:12px;display:flex;flex-direction:column;">';
      for (const item of topItems) {
        const cls = item.sentiment > 0 ? 'positive' : item.sentiment < 0 ? 'negative' : 'neutral';
        const safeTitle = esc(item.title || '');
        const safeHref = (item.link && /^https?:\/\//.test(item.link)) ? esc(item.link) : '';
        const link = safeHref ? `<a href="${safeHref}" target="_blank" rel="noopener">${safeTitle}</a>` : safeTitle;
        html += `<div class="dn-news-item"><span class="dn-news-dot ${cls}"></span><span class="dn-news-title">${link}</span></div>`;
      }
      html += '</div>';
    }
    html += '</div>';
  }

  // 4) 애널리스트 추천
  const recs = data.recommendations || [];
  if (recs.length > 0) {
    html += `<div class="dn-filing-card">
      <div class="dn-filing-header">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4-4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 00-3-3.87"/><path d="M16 3.13a4 4 0 010 7.75"/></svg>
        애널리스트 투자의견 변경 이력
        <span style="margin-left:auto;font-size:11px;color:var(--text-tertiary);font-weight:400;">최근 ${recs.length}건</span>
      </div>`;
    for (const rec of recs) {
      const action = (rec.action || '').toLowerCase();
      const actionCls = action.includes('up') ? 'upgrade' : action.includes('down') ? 'downgrade' : action.includes('init') ? 'init' : 'reiterated';
      const tgtStr = rec.target != null ? `$${Number(rec.target).toLocaleString()}` : '';
      const priorStr = rec.prior_target != null ? `$${Number(rec.prior_target).toLocaleString()}` : '';
      const tgtHtml = tgtStr ? `<span style="font-size:12px;font-weight:600;margin-left:auto;white-space:nowrap;color:${actionCls === 'upgrade' ? 'var(--success)' : actionCls === 'downgrade' ? 'var(--destructive)' : 'var(--text-secondary)'}">${priorStr ? esc(priorStr) + ' → ' : ''}${esc(tgtStr)}</span>` : '';
      const dateHtml = rec.date ? `<span style="font-size:10px;color:var(--text-tertiary);white-space:nowrap;">${esc(rec.date)}</span>` : '';
      html += `<div class="us-rec-item">
        <span class="us-rec-action ${actionCls}">${esc(rec.action || '')}</span>
        <span class="us-rec-firm">${esc(rec.firm || '')}</span>
        <span class="us-rec-grade">${esc(rec.grade || '')}</span>
        ${dateHtml}
        ${tgtHtml}
      </div>`;
    }
    html += '</div>';
  }

  // 5) SEC 공시
  const filings = data.sec_filings || [];
  if (data.sec_available && filings.length > 0) {
    html += `<div class="dn-filing-card">
      <div class="dn-filing-header">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
        SEC Filings
        <span style="margin-left:auto;font-size:11px;color:var(--text-tertiary);font-weight:400;">${filings.length}건</span>
      </div>`;
    for (const f of filings) {
      const safeDesc = esc(f.description || f.form || '');
      const safeUrl = (f.url && /^https?:\/\//.test(f.url)) ? esc(f.url) : '';
      const formColor = (f.form || '').includes('10-K') ? 'var(--success)' : (f.form || '').includes('10-Q') ? 'var(--brand)' : (f.form || '').includes('8-K') ? 'var(--warning)' : 'var(--text-secondary)';
      if (safeUrl) {
        html += `<a href="${safeUrl}" target="_blank" rel="noopener" class="dn-filing-item" style="text-decoration:none;cursor:pointer;transition:background 0.15s;" onmouseenter="this.style.background='var(--surface-subtle)'" onmouseleave="this.style.background=''">
          <span class="dn-filing-date">${esc(f.date || '')}</span>
          <span style="font-weight:700;color:${formColor};min-width:50px;font-size:11px;">${esc(f.form || '')}</span>
          <span class="dn-filing-title" style="color:var(--text-primary);flex:1;">${safeDesc}</span>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--text-tertiary)" stroke-width="2" style="flex-shrink:0;"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
        </a>`;
      } else {
        html += `<div class="dn-filing-item">
          <span class="dn-filing-date">${esc(f.date || '')}</span>
          <span style="font-weight:700;color:${formColor};min-width:50px;font-size:11px;">${esc(f.form || '')}</span>
          <span class="dn-filing-title" style="flex:1;">${safeDesc}</span>
        </div>`;
      }
    }
    html += '</div>';
  }

  if (!html) html = '<div class="dn-empty">No data available</div>';
  container.innerHTML = html;
}



// ── 증권사 컨센서스 상세 로딩 ─────────────────────────────────────────

async function loadConsensus(ticker, wrapId = 'consensus-wrap', prefix = 'cons') {
  const wrap = document.getElementById(wrapId);
  if (!wrap) return;
  const p = new URLSearchParams({ market: currentMarket });
  const cacheKey = `consensus:${ticker}:${currentMarket}`;
  const cached = _clientCache.get(cacheKey);
  if (cached) { _renderConsensusData(wrap, cached, prefix); return; }
  try {
    const res = await fetch(`/api/consensus/${ticker}?${p}`);
    if (!res.ok) { console.warn(`consensus HTTP ${res.status} for ${ticker}`); return; }
    const data = await res.json();
    _clientCache.set(cacheKey, data);
    _renderConsensusData(wrap, data, prefix);
  } catch (e) {
    console.warn('loadConsensus failed:', ticker, e.message || e);
  }
}

function _renderConsensusData(wrap, data, prefix = 'cons') {
  const s = data.summary || {};
  const reports = data.reports || [];

  if (!s.mean && !s.high && reports.length === 0) return;
  wrap.style.display = '';

  const badge = document.getElementById(`${prefix}-opinion-badge`);
  if (badge && s.opinion) {
    badge.textContent = s.opinion;
    badge.style.display = '';
  } else if (badge) {
    badge.style.display = 'none';
  }

  if (s.low)  setText(`${prefix}-low`,  fmtPrice(s.low));
  if (s.mean) setText(`${prefix}-mean`, fmtPrice(s.mean));
  if (s.high) setText(`${prefix}-high`, fmtPrice(s.high));

  const countWrap = document.getElementById(`${prefix}-count-wrap`);
  if (countWrap && s.count) {
    countWrap.textContent = `(${s.count}개 증권사)`;
  }

  const reportsEl = document.getElementById(`${prefix}-reports`);
  if (reportsEl && reports.length > 0) {
    reportsEl.innerHTML = reports.map(r => `
      <div style="display:flex; align-items:center; padding:8px 0; border-top:1px solid var(--border); font-size:12px;">
        <span style="flex:1; font-weight:600; color:var(--text-primary);">${esc(r.firm)}</span>
        <span style="font-weight:700; color:var(--text-primary); margin-right:8px;">${r.target ? fmtPrice(r.target) : '—'}</span>
        <span style="color:${r.opinion && /긍정|관심|강한/.test(r.opinion) ? 'var(--success)' : 'var(--text-tertiary)'}; font-weight:600; width:32px; text-align:center;">${esc(r.opinion || '')}</span>
        <span style="color:var(--text-tertiary); margin-left:8px; min-width:60px; text-align:right;">${esc(r.date || '')}</span>
      </div>
    `).join('');
  }
}

// ── 관심 종목 토글 ───────────────────────────────────────────────────────


function toggleBookmark(btn) {
  const icon = document.getElementById('bookmarkIcon');
  if (!icon) return;
  const filled = icon.getAttribute('fill') !== 'none';
  icon.setAttribute('fill',   filled ? 'none' : '#3182F6');
  icon.setAttribute('stroke', '#3182F6');
}

// ── TopReason 태그 렌더링 ────────────────────────────────────────────

function _reasonShorthand(text) {
  let m;
  if (/52주.*신고가/.test(text))            return { ico: '\u{1F4C8}', short: '신고가' };
  if ((m = text.match(/거래량\s*([\d.]+)x/))) return { ico: '\u{1F50A}', short: m[1] + 'x' };
  if ((m = text.match(/RS\s*(\d+)\s*주도주/))) return { ico: '\u{1F3C6}', short: 'RS' + m[1] };
  if (/EPS\s*가속/.test(text))              return { ico: '\u{1F4CA}', short: 'EPS\u2191' };
  if ((m = text.match(/ROE\s*([\d.]+%?)/)))  return { ico: '\u{1F4B0}', short: 'ROE' + m[1] };
  if ((m = text.match(/RSI\s*(\d+)\s*과매도/))) return { ico: '\u{1F4C9}', short: 'RSI' + m[1] };
  if ((m = text.match(/RSI\s*(\d+)\s*과열/)))  return { ico: '\u{1F525}', short: 'RSI' + m[1] };
  if ((m = text.match(/DCF\s*([+\-]?\d+%?)/))) return { ico: '\u{1F3AF}', short: m[1] };
  if (/⛔.*EPS/.test(text))                 return { ico: '\u26D4', short: 'EPS' };
  if ((m = text.match(/⛔RS(\d+)/)))         return { ico: '\u26D4', short: 'RS' + m[1] };
  return null;
}

function _renderReasonTags(topReason) {
  if (!topReason || topReason === '-') return '<span style="color:var(--text-tertiary)">—</span>';
  try {
    if (typeof topReason !== 'string') return '<span style="color:var(--text-tertiary)">—</span>';
    const parts = topReason.replace(/\s*[·]\s*/g, ' · ').split(' · ').filter(p => p.trim()).slice(0, 4);
    return parts.map(p => {
      const neg = /⛔|과열|AVOID|SELL|적자/.test(p);
      const pos = /신고가|돌파|주도주|EPS|ROE|과매도/.test(p);
      const cls = neg ? ' negative' : pos ? ' positive' : '';
      const sh = _reasonShorthand(p);
      if (sh) return `<span class="reason-tag${cls}" title="${esc(p)}">${sh.ico}${esc(sh.short)}</span>`;
      return `<span class="reason-tag${cls}" title="${esc(p)}">${esc(p)}</span>`;
    }).join('');
  } catch (_) { return '<span style="color:var(--text-tertiary)">—</span>'; }
}

// ── 테이블 정렬 ─────────────────────────────────────────────────────

function initFilterChips() {
  document.querySelectorAll('#filter-chips .chip[data-filter]').forEach(chip => {
    chip.addEventListener('click', () => {
      const f = chip.dataset.filter || 'all';
      if (f === 'all') {
        _activeFilters.clear();
      } else if (_activeFilters.has(f)) {
        _activeFilters.delete(f);
      } else {
        _activeFilters.add(f);
      }
      if (f === 'score_surge') {
        if (_activeFilters.has('score_surge')) {
          _sortKey = 'ScoreDelta'; _sortDir = -1;
        } else if (_sortKey === 'ScoreDelta') {
          _sortKey = 'TotalScore'; _sortDir = -1;
        }
        _syncSortArrowUI();
      }
      _syncFilterChipUI();
      if (_searchBaseStocks().length) _refreshFilteredView();
    });
  });
}

function _syncFilterChipUI() {
  document.querySelectorAll('#filter-chips .chip[data-filter]').forEach(c => {
    const f = c.dataset.filter || 'all';
    if (f === 'all') {
      c.classList.toggle('active', _activeFilters.size === 0);
    } else {
      c.classList.toggle('active', _activeFilters.has(f));
    }
  });
}

// ── 지수별 보기 바 ────────────────────────────────────────────────────────
function initIndexBar() {
  document.querySelectorAll('#index-bar .index-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      _activeIndex = chip.dataset.index || 'all';
      _syncIndexBarUI();
      _toggleMidcapCol(_activeIndex === 'SP400');
      if (_searchBaseStocks().length) _refreshFilteredView();
    });
  });
}

function _toggleMidcapCol(show) {
  document.querySelectorAll('.midcap-col-hdr').forEach(el => {
    el.style.display = show ? '' : 'none';
  });
}


function _syncIndexBarUI() {
  document.querySelectorAll('#index-bar .index-chip').forEach(c => {
    c.classList.toggle('active', (c.dataset.index || 'all') === _activeIndex);
  });
}

// 지수 바: 미국에서만 표시하고 각 지수 편입 종목 수를 갱신한다.
function _updateIndexBar(stocks) {
  const bar = document.getElementById('index-bar');
  if (!bar) return;
  const list = Array.isArray(stocks) ? stocks : [];
  // KR 마켓이거나 지수 데이터가 전혀 없으면 바를 숨기고 전체 보기로 리셋.
  const anyTagged = list.some(s => Array.isArray(s.Indices));
  if (currentMarket !== 'US' || !anyTagged) {
    bar.hidden = true;
    if (_activeIndex !== 'all') { _activeIndex = 'all'; _syncIndexBarUI(); }
    return;
  }
  bar.hidden = false;
  // 버킷별 종목 수 + 유동성(권고2): 중앙 거래대금·저유동성 종목 수
  const counts = { all: list.length, SP500: 0, SP400: 0, SP600: 0, NDX: 0, OTHER: 0 };
  const advs   = { all: [], SP500: [], SP400: [], SP600: [], NDX: [], OTHER: [] };
  const lowliq = { all: 0, SP500: 0, SP400: 0, SP600: 0, NDX: 0, OTHER: 0 };
  const _push = (k, s) => {
    counts[k]++;
    const a = Number(s._AvgDollarVol20);
    if (isFinite(a) && a > 0) advs[k].push(a);
    if (s.LowLiquidity) lowliq[k]++;
  };
  list.forEach(s => {
    _push('all', s);
    const ix = Array.isArray(s.Indices) ? s.Indices : [];
    if (!ix.length) { _push('OTHER', s); return; }
    ix.forEach(k => { if (k in counts) _push(k, s); });
  });
  bar.querySelectorAll('.index-chip').forEach(chip => {
    const k = chip.dataset.index;
    const cnt = chip.querySelector('.ix-count');
    if (cnt) cnt.textContent = counts[k] != null ? counts[k] : '';
    if (k && k !== 'all' && counts[k] != null) {
      const med = _median(advs[k]);
      const liqTxt = med != null ? `중앙 거래대금 ${_fmtMarketCap(med)}` : '거래대금 정보 부족';
      const lowTxt = lowliq[k] ? ` · 유동성↓ ${lowliq[k]}종목` : '';
      const base = (chip.dataset.baseTitle || (chip.dataset.baseTitle = chip.title || ''));
      chip.title = `${base}\n${counts[k]}종목 · ${liqTxt}${lowTxt}`;
    }
  });
  _renderIndexBarMeta();
}

// 정렬 없이 중앙값 (빈 배열이면 null)
function _median(arr) {
  if (!arr || !arr.length) return null;
  const a = arr.slice().sort((x, y) => x - y);
  const m = a.length >> 1;
  return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
}

// 명단 기준일 + 가격 캐시 신선도 표시 (권고3 surface)
function _renderIndexBarMeta() {
  const el = document.getElementById('index-bar-meta');
  if (!el) return;
  const parts = [];
  if (_indexMeta && _indexMeta.generated) {
    const day = String(_indexMeta.generated).slice(0, 10);
    parts.push(`명단 ${day}${_indexMeta.is_stale ? ' ⚠️갱신요망' : ''}`);
  }
  if (_scanCacheAgeMin != null && isFinite(_scanCacheAgeMin)) {
    const h = _scanCacheAgeMin / 60;
    const fresh = h < 24 ? `${Math.round(_scanCacheAgeMin)}분 전` :
                  `${(h / 24).toFixed(1)}일 전`;
    const warn = h >= 24 ? ' ⚠️' : '';
    parts.push(`시세 ${fresh}${warn}`);
  }
  el.textContent = parts.join('  ·  ');
}

async function loadIndexMeta() {
  try {
    const res = await fetch('/api/index-meta');
    _indexMeta = await res.json();
    _renderIndexBarMeta();
  } catch (e) { /* 비치명적 — 메타 없이도 동작 */ }
}

function initOneLinerFilter() {
  const tbody = document.getElementById('stock-list');
  if (tbody) {
    tbody.addEventListener('click', (e) => {
      const tagEl = e.target.closest('.stock-oneliner[data-tag]');
      if (!tagEl) return;
      e.preventDefault();
      e.stopPropagation();
      const tag = tagEl.dataset.tag || '';
      if (tag) applyOneLinerFilter(tag);
    }, true);
  }

  const chip = document.getElementById('oneliner-filter-chip');
  if (chip) {
    chip.addEventListener('click', (e) => {
      if (!e.target.closest('.oneliner-filter-chip-clear')) return;
      e.preventDefault();
      clearOneLinerFilter();
    });
  }
}

// 현재 _sortKey/_sortDir 에 맞춰 테이블 헤더 정렬 화살표를 동기화.
// ScoreDelta 처럼 열 헤더가 없는 키면 모든 화살표가 비워진다.
function _syncSortArrowUI() {
  document.querySelectorAll('.stock-table th.sortable').forEach(th => {
    th.classList.remove('asc', 'desc');
    if (th.dataset.sort === _sortKey && _sortDir !== 0) {
      th.classList.add(_sortDir === 1 ? 'asc' : 'desc');
    }
  });
}

function initSort() {
  // 초기 정렬 화살표 표시 (기본: Scores.BALANCED desc)
  document.querySelectorAll('.stock-table th.sortable').forEach(th => {
    if (th.dataset.sort === _sortKey) {
      th.classList.add(_sortDir === 1 ? 'asc' : 'desc');
    }
  });
  document.querySelectorAll('.stock-table th.sortable').forEach(th => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      if (_sortKey === key) {
        _sortDir = _sortDir === -1 ? 1 : _sortDir === 1 ? 0 : -1;
      } else {
        _sortKey = key;
        _sortDir = -1; // 기본 내림차순
      }
      // 화살표 UI 갱신
      document.querySelectorAll('.stock-table th.sortable').forEach(h => {
        h.classList.remove('asc', 'desc');
      });
      if (_sortDir === 1)  th.classList.add('asc');
      if (_sortDir === -1) th.classList.add('desc');

      if (_sortDir === 0 || !allStocks.length) {
        if (_searchBaseStocks().length) _refreshFilteredView();
        return;
      }
      const sorted = [...allStocks].sort((a, b) => {
        const va = _getByPath(a, key); const vb = _getByPath(b, key);
        // 문자열(섹터·티커 등) ↔ 숫자 분기: 한글 로케일 비교 적용
        if (typeof va === 'string' || typeof vb === 'string') {
          const sa = (va == null ? '' : String(va));
          const sb = (vb == null ? '' : String(vb));
          if (!sa && sb) return 1;          // 빈 값은 항상 뒤로
          if (sa && !sb) return -1;
          return _sortDir * sa.localeCompare(sb, 'ko');
        }
        const aa = va == null ? -Infinity : va;
        const bb = vb == null ? -Infinity : vb;
        return _sortDir * (aa > bb ? 1 : aa < bb ? -1 : 0);
      });
      _refreshFilteredView();
    });
  });
}

// ── CSV 내보내기 ─────────────────────────────────────────────────────

function exportCSV() {
  if (!allStocks.length) {
    alert('내보낼 데이터가 없습니다. 먼저 스캔을 실행해주세요.');
    return;
  }
  const cols = [
    ['Ticker','티커'], ['Name','종목명'], ['Sector','섹터'], ['TotalScore','점수'],
    ['Signal','시그널'], ['Price','현재가'], ['DayChg','등락률'],
    ['RSI','RSI'], ['TargetPrice','메인목표가'], ['TargetSource','메인목표가출처'], ['TargetUpside','메인목표가상승여력'],
    ['TargetView','컨센서스'], ['Conviction','확신도'], ['VolRatio','거래량비율'],
    ['TopReason','핵심이유'], ['MomentumScore','모멘텀'], ['ValueScore','밸류'],
    ['QualityScore','퀄리티'], ['RSRating','RS등급'],
    ['Industry','업종'], ['Desc','설명'],
  ];
  const header = cols.map(c => c[1]).join(',');
  const rows = allStocks.map(s =>
    cols.map(([k]) => {
      let v = s[k] ?? '';
      if (k === 'DayChg' || k === 'TargetUpside')
        v = v ? (v * 100).toFixed(2) + '%' : '';
      v = String(v).replace(/"/g, '""');
      if (String(v).includes(',') || String(v).includes('"') || String(v).includes('\n'))
        v = `"${v}"`;
      return v;
    }).join(',')
  );
  const csv = '\uFEFF' + header + '\n' + rows.join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  const date = new Date().toISOString().slice(0, 10);
  a.download = `분석기_${currentMarket}_${currentStrategy}_${date}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ── 레짐 배지 ─────────────────────────────────────────────────────────────

async function fetchRegimeBadge() {
  const el = document.getElementById('regime-badge');
  if (!el) return;
  try {
    const r = await fetch('/api/regime');
    if (!r.ok) return;
    const d = await r.json();
    if (d.error) return;

    const cls = d.state === 'BULL' ? 'bull' : d.state === 'BEAR' ? 'bear' : 'chop';
    const conf = Math.round(d.confidence * 100);
    let tip = `${d.desc} · 확신도 ${conf}%\n내일 Bull ${Math.round(d.p_next.bull*100)}% / Bear ${Math.round(d.p_next.bear*100)}% / Chop ${Math.round(d.p_next.chop*100)}%\n권고: ${d.position}`;
    if (d.early_exit) tip += '\n⚡ Bear 압력 감지 — 포지션 축소 검토';
    if (d.early_long) tip += '\n⚡ Bull 전환 가능 — 진입 준비';

    el.className = `regime-badge ${cls}${d.early_exit ? ' early-exit' : d.early_long ? ' early-long' : ''}`;
    el.title     = tip;
    el.textContent = `${d.emoji} KOSPI ${d.label} ${conf}%`;
    el.removeAttribute('hidden');
  } catch(e) {}
}

// ── 초기화 ───────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  // URL 파라미터에서 market/strategy 읽기
  const sp = new URLSearchParams(window.location.search);
  if (sp.has('market'))   currentMarket   = sp.get('market');
  if (sp.has('strategy')) currentStrategy = sp.get('strategy');

  // 오늘 날짜 표시
  const dateEl = document.getElementById('topbar-date');
  if (dateEl) {
    dateEl.textContent = new Date().toLocaleDateString('ko-KR', {
      year: 'numeric', month: '2-digit', day: '2-digit'
    });
  }

  // 레짐 배지
  fetchRegimeBadge();

  // 시장 셀렉터 동기화
  const mSel = document.getElementById('market-select');
  if (mSel) mSel.value = currentMarket;
  const sSel = document.getElementById('strategy-select');
  if (sSel) sSel.value = currentStrategy;

  // ESC로 드로어 닫기
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    closeDetailBtn();
  });

  if (typeof COMPARE_MARKET !== 'undefined' && COMPARE_MARKET) {
    currentMarket = COMPARE_MARKET;
  }

  if (typeof COMPARE_TICKERS !== 'undefined' && Array.isArray(COMPARE_TICKERS)) {
    loadComparePage(COMPARE_TICKERS);
  } else if (typeof TICKER !== 'undefined' && TICKER) {
    // ── 디테일 페이지
    loadDetail(TICKER);
    // KR 마켓일 때만 공시·뉴스 탭 표시 (currentMarket은 위에서 URL params로 설정 완료)
    if (currentMarket === 'KR') {
      const dnBtn = document.getElementById('btn-dartnews');
      if (dnBtn) dnBtn.style.display = '';
    }
    // US 마켓일 때만 US 인사이트 탭 표시
    if (currentMarket === 'US') {
      const usBtn = document.getElementById('btn-usinsight');
      if (usBtn) usBtn.style.display = '';
    }
  } else {
    // ── 스캐너 페이지
    initFilterChips();
    initIndexBar();
    loadIndexMeta();
    initSearch();
    initOneLinerFilter();
    initSort();
    updateCompareActions();
    document.getElementById('btn-scan')?.addEventListener('click', runScan);
    // 핵심 fetch를 최우선 실행: runScan → scan 완료 후 나머지 로드
    loadSectors();
    runScan().then(() => {
      loadWatchlist();
      loadMacro();
      loadScoreEval();
      if (typeof _loadMacroStrip === 'function') _loadMacroStrip(currentMarket);
    });
    setInterval(loadMacro, 15 * 60 * 1000);
    // 장중에만 3분 갱신, 장 외에는 30분 간격 — 불필요한 네트워크/서버 부하 방지
    setInterval(() => {
      if (document.hidden) return;
      const h = new Date().getHours();
      const isKrOpen = (h >= 9 && h < 16);       // KST 09:00-15:30
      const isUsOpen = (h >= 22 || h < 7);        // KST 22:00-07:00
      const interval = (isKrOpen || isUsOpen) ? 3 : 30;
      if ((Date.now() - (_lastAutoScanTs || 0)) >= interval * 60 * 1000) {
        _lastAutoScanTs = Date.now();
        runScan();
      }
    }, 60 * 1000);  // 1분마다 체크, 실제 갱신은 조건부
  }
});


const _MACRO_DEFS = [
  { key: 'vix',     label: 'VIX',         fixed: 2, invert: true  },
  { key: 'sp500',   label: 'S&P500',      fixed: 2, invert: false },
  { key: 'nasdaq',  label: '나스닥',      fixed: 2, invert: false },
  { key: 'kospi',   label: 'KOSPI',       fixed: 2, invert: false },
  { key: 'usdkrw',  label: '원/달러',     fixed: 1, invert: true  },
  { key: 'dxy',     label: 'DXY',         fixed: 2, invert: true  },
  { key: 'us10y',   label: '美10Y',       fixed: 2, invert: false, suffix: '%' },
  { key: 'gold',    label: '금',          fixed: 1, invert: false },
  { key: 'wti',     label: 'WTI',         fixed: 2, invert: false },
  { key: 'btc',     label: 'BTC',         fixed: 0, invert: false },
  { key: 'us_rate', label: '美기준금리',  fixed: 2, invert: false, suffix: '%' },
  { key: 'kr_rate', label: '韓기준금리',  fixed: 2, invert: false, suffix: '%' },
];

// ── 점수 신뢰도 배지 (표본외 IC 검증 결과) ───────────────────────────────────
async function loadScoreEval() {
  const stat = document.getElementById('score-eval-stat');
  const badge = document.getElementById('score-eval-badge');
  if (!stat || !badge) return;  // 스캐너 페이지에만 존재
  try {
    const res = await fetch('/api/score-eval?market=' + encodeURIComponent(currentMarket));
    const d = await res.json();
    const b = (d && d.badge) ? d.badge : { level: 'none', label: '검증 데이터 없음' };
    badge.className = 'score-eval-badge ' + (b.level || 'none');
    if (!b || b.level === 'none' || b.ic == null) {
      badge.textContent = (b && b.label) ? b.label : '데이터 없음';
      badge.removeAttribute('title');
      stat.hidden = false;
      return;
    }
    const icTxt = (b.ic > 0 ? '+' : '') + Number(b.ic).toFixed(2);
    badge.textContent = 'IC ' + icTxt + ' · ' + b.label;
    const tTxt = (b.t_stat == null) ? '—' : ((b.t_stat > 0 ? '+' : '') + Number(b.t_stat).toFixed(2));
    badge.title = b.horizon + '거래일 포워드 IC ' + icTxt + ' (t ' + tTxt + ', 표본 ' + b.n_dates + '일). ' +
      (b.level === 'valid' ? '통계적으로 유의한 예측력.' :
       b.level === 'negative' ? '점수와 미래수익이 역방향 — 주의.' :
       '표본이 작아 아직 검증 중(통계적 유의성 부족).');
    stat.hidden = false;
  } catch (e) {
    console.warn('loadScoreEval failed', e);
  }
}

async function loadMacro() {
  const strip = document.getElementById('macro-strip');
  if (!strip) return;  // 스캐너 페이지에만 존재
  try {
    const res = await fetch('/api/macro');
    const d = await res.json();
    renderMacro(d);
  } catch (e) {
    console.warn('loadMacro failed', e);
  }
}

function renderMacro(d) {
  const sigEl = document.getElementById('macro-signal');
  const itemsEl = document.getElementById('macro-items');
  const metaEl = document.getElementById('macro-meta');
  const leadEl = document.getElementById('macro-leading');
  if (!sigEl || !itemsEl || !metaEl) return;

  const sig = d.signal || { level: 'unknown', emoji: '⚪', label: '정보없음' };
  const trend = sig.trend || 'stable';
  const trendArrow = trend === 'deteriorating' ? '<span class="trend-arrow">↗악화</span>'
                   : trend === 'improving' ? '<span class="trend-arrow">↘개선</span>' : '';
  sigEl.className = 'macro-signal ' + (sig.level || 'unknown');
  sigEl.innerHTML = `${sig.emoji || '⚪'} <span>${esc(sig.label || '')}${trendArrow}</span>`;

  // 종가베팅 선행 지표
  if (leadEl) {
    const lead = d.leading;
    if (lead && lead.safety) {
      const lbl = lead.safety === 'safe' ? '종가베팅 안전'
                : lead.safety === 'caution' ? '종가베팅 주의'
                : '종가베팅 위험';
      const ico = lead.safety === 'safe' ? '🟢'
                : lead.safety === 'caution' ? '🟡' : '🔴';
      leadEl.className = 'macro-leading ' + lead.safety;
      leadEl.innerHTML = `${ico} ${esc(lbl)}`;
      leadEl.title = (lead.reasons && lead.reasons.length)
        ? lead.reasons.join('\n')
        : '선행 지표 이상 없음';
      const details = [];
      if (lead.vix_term != null) details.push('VIX텀 ' + lead.vix_term.toFixed(2));
      if (lead.skew != null) details.push('SKEW ' + lead.skew);
      if (lead.hy_spread_chg != null) details.push('HY ' + (lead.hy_spread_chg > 0 ? '+' : '') + lead.hy_spread_chg + '%p');
      if (details.length) leadEl.title += '\n\n' + details.join(' | ');
    } else {
      leadEl.className = 'macro-leading hidden';
    }
  }

  const ind = d.indicators || {};
  const parts = [];
  _MACRO_DEFS.forEach((def, i) => {
    const cell = ind[def.key];
    if (i > 0) parts.push('<span class="macro-sep"></span>');
    if (!cell || cell.value == null) {
      parts.push(
        `<span class="macro-item"><span class="mi-label">${esc(def.label)}</span>` +
        `<span class="mi-val">—</span></span>`
      );
      return;
    }
    const val = Number(cell.value).toLocaleString('ko-KR', {
      minimumFractionDigits: def.fixed, maximumFractionDigits: def.fixed,
    }) + (def.suffix || '');
    let chgHtml = '';
    if (cell.change_pct != null) {
      const c = Number(cell.change_pct);
      const dir = c > 0.005 ? 'up' : (c < -0.005 ? 'down' : 'flat');
      const arrow = dir === 'up' ? '▲' : (dir === 'down' ? '▼' : '­');
      chgHtml = `<span class="mi-chg ${dir}">${arrow}${Math.abs(c).toFixed(2)}%</span>`;
    }
    parts.push(
      `<span class="macro-item"><span class="mi-label">${esc(def.label)}</span>` +
      `<span class="mi-val">${esc(val)}</span>${chgHtml}</span>`
    );
  });
  itemsEl.innerHTML = parts.join('');

  let meta = '';
  if (d.ts) {
    const t = new Date(d.ts);
    if (!isNaN(t)) {
      const mins = Math.max(0, Math.round((Date.now() - t.getTime()) / 60000));
      meta = mins <= 0 ? '방금 갱신' : `${mins}분 전 갱신`;
    }
  }
  metaEl.innerHTML =
    (d.stale ? '<span class="macro-stale-badge">⚠ 이전 값</span>' : '') +
    (meta ? `<span>${esc(meta)}</span>` : '');
}

// ── 공유 카드 ──────────────────────────────────────────────────────────────

function _compareMetricRow(label, value) {
  return `
    <div class="compare-row">
      <span class="compare-label">${esc(label)}</span>
      <span class="compare-value">${esc(value)}</span>
    </div>`;
}

async function loadComparePage(tickers) {
  const grid = document.getElementById('compare-grid');
  if (!grid) return;

  const cleanTickers = (Array.isArray(tickers) ? tickers : []).map(t => String(t || '').trim()).filter(Boolean).slice(0, 4);
  if (cleanTickers.length < 2) {
    grid.innerHTML = '<div class="compare-empty">비교하려면 2개 이상 종목이 필요합니다.</div>';
    return;
  }

  grid.innerHTML = cleanTickers.map(ticker => `
    <div class="compare-card">
      <div class="compare-card-header">
        <div class="compare-name">${esc(ticker)}</div>
        <div class="compare-ticker">로딩 중...</div>
      </div>
      <div class="compare-body">
        <div class="compare-chart"><div class="compare-chart-empty">4축 차트 불러오는 중...</div></div>
        <div class="compare-metrics">${_compareMetricRow('상태', '로딩 중')}</div>
      </div>
    </div>
  `).join('');

  const cards = grid.querySelectorAll('.compare-card');
  await Promise.all(cleanTickers.map(async (ticker, index) => {
    const card = cards[index];
    if (!card) return;

    try {
      const p = new URLSearchParams({ market: currentMarket, strategy: currentStrategy });
      const [detailRes, axisRes] = await Promise.all([
        fetch(`/api/ticker/${encodeURIComponent(ticker)}?${p}`),
        fetch(`/api/four_axis/${encodeURIComponent(ticker)}?${new URLSearchParams({ market: currentMarket })}`),
      ]);
      const detail = await detailRes.json();
      const axis = axisRes.ok ? await axisRes.json() : { error: `HTTP ${axisRes.status}` };
      if (!detailRes.ok || detail.error) throw new Error(detail.error || `HTTP ${detailRes.status}`);

      const { base } = _splitSignal(detail.Signal);
      const score = detail.TotalScore != null ? Math.round(detail.TotalScore) : '—';
      const rsi = detail.RSI != null ? fmt(detail.RSI, 1) : '—';
      const mom12 = detail.Mom12M != null ? `${detail.Mom12M >= 0 ? '+' : ''}${fmt(detail.Mom12M * 100, 1)}%` : '—';
      const per = detail._PER != null ? fmt(detail._PER, 1) : '—';
      const roe = detail._ROE != null ? `${fmt(detail._ROE * 100, 1)}%` : '—';
      const entry = detail.EntryStatus || '—';

      card.innerHTML = `
        <div class="compare-card-header">
          <div class="compare-name">${esc(detail.Name || detail.Ticker || ticker)}</div>
          <div class="compare-ticker">${esc(detail.Ticker || ticker)}</div>
          <span class="compare-signal" style="color:${signalColor(base)};background:${signalBg(base)}">${esc(_trKo(base || '—'))}</span>
        </div>
        <div class="compare-body">
          <div class="compare-chart">
            ${axis && axis.chart ? `<img src="data:image/png;base64,${axis.chart}" alt="${esc(ticker)} 4축 차트">` : '<div class="compare-chart-empty">4축 차트를 불러오지 못했습니다.</div>'}
          </div>
          <div class="compare-metrics">
            ${_compareMetricRow('TotalScore', score)}
            ${_compareMetricRow('RSI', rsi)}
            ${_compareMetricRow('Mom12M', mom12)}
            ${_compareMetricRow('PER', per)}
            ${_compareMetricRow('ROE', roe)}
            ${_compareMetricRow('EntryStatus', entry)}
          </div>
          <div class="compare-oneliner">${olHtml(detail.OneLiner || '요약 코멘트 없음')}${detail.OneLinerSub ? `<span class="oneliner-sub">→ ${esc(detail.OneLinerSub)}</span>` : ''}${detail.OneLinerData ? `<span class="oneliner-data">${esc(detail.OneLinerData)}</span>` : ''}</div>
        </div>
      `;
    } catch (err) {
      card.innerHTML = `
        <div class="compare-card-header">
          <div class="compare-name">${esc(ticker)}</div>
          <div class="compare-ticker">불러오기 실패</div>
        </div>
        <div class="compare-body">
          <div class="compare-chart"><div class="compare-chart-empty">${esc(err.message || '오류')}</div></div>
        </div>
      `;
    }
  }));
}



function updateCompareActions() {
  const group = document.getElementById('compare-fab-group');
  const openBtn = document.getElementById('compare-open-btn');
  const clearBtn = document.getElementById('compare-clear-btn');
  if (!group || !openBtn || !clearBtn) return;
  const size = _selectedStocks.size;
  group.hidden = size === 0;
  openBtn.hidden = size < 2;
  openBtn.disabled = size < 2;
  openBtn.textContent = `비교 (${size})`;
  clearBtn.hidden = size === 0;
  clearBtn.disabled = size === 0;
}

function toggleCompareStock(ticker, ev) {
  if (ev) ev.stopPropagation();
  if (_compareSet.has(ticker)) {
    _compareSet.delete(ticker);
  } else {
    if (_compareSet.size >= 4) {
      alert('비교는 최대 4개 종목까지 가능합니다.');
      if (ev?.target) ev.target.checked = false;
      return;
    }
    _compareSet.add(ticker);
  }
  updateCompareActions();
}

function clearCompareSelection() {
  _compareSet.clear();
  _selectedStocks.clear();
  document.querySelectorAll('.stock-table tbody input[type=checkbox]').forEach(cb => { cb.checked = false; });
  const allCb = document.getElementById('select-all-cb');
  if (allCb) allCb.checked = false;
  _updateShareCount();
  updateCompareActions();
}

function openCompareView() {
  if (_compareSet.size < 2) return;
  const tickers = [..._compareSet].join(',');
  const p = new URLSearchParams({ tickers, market: currentMarket, strategy: currentStrategy });
  location.href = `/compare?${p.toString()}`;
}

function _updateShareCount() {
  const el = document.getElementById('share-count');
  if (el) el.textContent = _selectedStocks.size;
}

function toggleSelectStock(ticker, ev) {
  ev.stopPropagation();
  if (_selectedStocks.has(ticker)) { _selectedStocks.delete(ticker); _compareSet.delete(ticker); }
  else {
    if (_compareSet.size >= 4) { alert('비교는 최대 4개 종목까지 가능합니다.'); if (ev?.target) ev.target.checked = false; return; }
    _selectedStocks.add(ticker); _compareSet.add(ticker);
  }
  _updateShareCount();
  updateCompareActions();
  const allCb = document.getElementById('select-all-cb');
  if (allCb) allCb.checked = allStocks.length > 0 && _selectedStocks.size === allStocks.length;
}

function toggleSelectAll() {
  const allCb = document.getElementById('select-all-cb');
  if (!allCb) return;
  if (allCb.checked) {
    allStocks.forEach(s => { _selectedStocks.add(s.Ticker); _compareSet.add(s.Ticker); });
  } else {
    _selectedStocks.clear(); _compareSet.clear();
  }
  _updateShareCount();
  updateCompareActions();
  document.querySelectorAll('.stock-table tbody input[type=checkbox]').forEach(cb => {
    cb.checked = allCb.checked;
  });
}

function _shareCardSignalColor(signal) {
  const s = (signal || '').toUpperCase();
  if (s.includes('BREAKOUT') || s.includes('STRONG') || s.includes('MOMENTUM')) return { color: '#00C073', bg: 'rgba(0,192,115,0.12)' };
  if (s.includes('LEADER') || s.includes('WATCH') || s.includes('ACCUMULATE')) return { color: '#3182F6', bg: 'rgba(49,130,246,0.12)' };
  if (s.includes('HOLD') || s.includes('NEUTRAL')) return { color: '#FF9200', bg: 'rgba(255,146,0,0.12)' };
  if (s.includes('SELL') || s.includes('AVOID') || s.includes('LAGGARD') || s.includes('BEAR')) return { color: '#F04452', bg: 'rgba(240,68,82,0.12)' };
  return { color: '#8B95A1', bg: '#F2F3F6' };
}

function _shareCardScoreColor(score) {
  if (score >= 70) return '#00C073';
  if (score >= 50) return '#FF9200';
  return '#F04452';
}

async function generateShareCard() {
  if (_selectedStocks.size === 0) {
    alert('공유할 종목을 체크박스로 선택해주세요.');
    return;
  }
  const selected = allStocks.filter(s => _selectedStocks.has(s.Ticker));
  if (selected.length === 0) { alert('선택된 종목 데이터가 없습니다.'); return; }
  if (selected.length > 20) { alert('최대 20종목까지 공유 카드에 넣을 수 있습니다.'); return; }

  const now = new Date();
  const dateStr = `${now.getFullYear()}.${String(now.getMonth()+1).padStart(2,'0')}.${String(now.getDate()).padStart(2,'0')} ${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`;
  const mkt = currentMarket === 'KR' ? '한국' : '미국';

  let rowsHtml = '';
  selected.forEach((s, i) => {
    const score = Math.round(s.TotalScore || 0);
    const sc = _shareCardScoreColor(score);
    const barW = Math.min(100, Math.max(0, score));
    const dayChg = s.DayChg || 0;
    const chgPct = (dayChg * 100).toFixed(2);
    const chgSign = dayChg > 0 ? '+' : '';
    const chgColor = dayChg > 0 ? '#F04452' : dayChg < 0 ? '#3182F6' : '#8B95A1';
    const { base } = _splitSignal(s.Signal);
    const sigText = _trKo(base || '—');
    const sig = _shareCardSignalColor(s.Signal);
    const name = (s.Name || s.Ticker || '').slice(0, 16);
    const ticker = s.Ticker || '';

    rowsHtml += `
    <div style="display:flex;align-items:center;gap:10px;padding:12px 16px;${i > 0 ? 'border-top:1px solid #EAEBEE;' : ''}">
      <div style="width:24px;text-align:center;font-size:13px;font-weight:700;color:${i < 3 ? '#3182F6' : '#8B95A1'};">${i + 1}</div>
      <div style="flex:1;min-width:0;">
        <div style="font-size:14px;font-weight:600;color:#191F28;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(name)}</div>
        <div style="font-size:11px;color:#8B95A1;margin-top:1px;">${esc(ticker)}</div>
      </div>
      <div style="width:80px;">
        <div style="display:flex;align-items:center;gap:4px;">
          <span style="font-size:15px;font-weight:700;color:${sc};">${score}</span>
          <span style="font-size:10px;color:#8B95A1;">/ 100</span>
        </div>
        <div style="height:4px;background:#EAEBEE;border-radius:2px;margin-top:3px;">
          <div style="height:100%;width:${barW}%;background:${sc};border-radius:2px;"></div>
        </div>
      </div>
      <div style="min-width:64px;text-align:center;">
        <span style="font-size:11px;font-weight:600;color:${sig.color};background:${sig.bg};padding:3px 8px;border-radius:6px;white-space:nowrap;">${esc(sigText)}</span>
      </div>
      <div style="min-width:70px;text-align:right;">
        <div style="font-size:13px;font-weight:600;color:#191F28;">${fmtPrice(s.Price)}</div>
        <div style="font-size:11px;font-weight:500;color:${chgColor};">${chgSign}${chgPct}%</div>
      </div>
    </div>`;
  });

  const cardHtml = `
  <div style="width:580px;background:#ffffff;border-radius:16px;overflow:hidden;font-family:-apple-system,'Pretendard','Noto Sans KR',system-ui,sans-serif;">
    <div style="background:linear-gradient(135deg,#3182F6,#1B64DA);padding:20px 20px 16px;">
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <div>
          <div style="font-size:18px;font-weight:800;color:#ffffff;letter-spacing:-0.5px;">${SafeMode.name()}</div>
          <div style="font-size:12px;color:rgba(255,255,255,0.75);margin-top:4px;">${mkt} · ${dateStr}</div>
        </div>
        <div style="background:rgba(255,255,255,0.2);border-radius:8px;padding:6px 12px;">
          <span style="font-size:13px;font-weight:700;color:#ffffff;">${selected.length}종목</span>
        </div>
      </div>
    </div>
    <div style="padding:4px 0;">
      ${rowsHtml}
    </div>
    <div style="padding:12px 16px;background:#F5F6F8;border-top:1px solid #EAEBEE;">
      <div style="font-size:10px;color:#8B95A1;text-align:center;">알고리즘 스크리닝 결과일 뿐임. 판단과 손익은 본인 책임임.</div>
    </div>
  </div>`;

  const renderArea = document.getElementById('share-card-render');
  renderArea.innerHTML = cardHtml;

  await _ensureHtml2Canvas();
  html2canvas(renderArea.firstElementChild, {
    scale: 2,
    backgroundColor: '#ffffff',
    useCORS: true,
    logging: false,
  }).then(canvas => {
    renderArea.innerHTML = '';
    const img = canvas.toDataURL('image/png');
    const preview = document.getElementById('share-card-preview');
    preview.innerHTML = `<img src="${img}" style="max-width:100%;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,0.1);">`;
    preview.dataset.dataUrl = img;
    const modal = document.getElementById('share-modal');
    modal.style.display = 'flex';
  }).catch(err => {
    renderArea.innerHTML = '';
    alert('카드 생성 실패: ' + err.message);
  });
}

function downloadShareCard() {
  const preview = document.getElementById('share-card-preview');
  const dataUrl = preview?.dataset?.dataUrl;
  if (!dataUrl) return;
  const a = document.createElement('a');
  a.href = dataUrl;
  a.download = preview.dataset.fileName || `share_card_${new Date().toISOString().slice(0,10).replace(/-/g,'')}.png`;
  a.click();
}

// ── 캡쳐모드 토글 (핵심 컬럼만 표시) ──────────────────────────────────
function toggleCaptureMode() {
  const wrap = document.querySelector('.stock-table-wrap');
  if (!wrap) return;
  const on = wrap.classList.toggle('capture-mode');
  // 토글 버튼 시각 피드백
  const btn = document.querySelector('[data-action="toggleCaptureMode"]');
  if (btn) btn.classList.toggle('active-item', on);
}

// ── 종목 목록 캡쳐 ─────────────────────────────────────────────
async function captureStockList() {
  try { await _ensureHtml2Canvas(); } catch { alert('html2canvas 라이브러리 로드 실패'); return; }
  if (typeof html2canvas !== 'function') {
    return;
  }
  // 모바일이면 카드 리스트, 데스크탑이면 테이블 캡쳐
  const isMobile = window.matchMedia('(max-width: 768px)').matches;
  const target = isMobile
    ? document.getElementById('mobile-stock-list')
    : document.querySelector('.stock-table-wrap');
  if (!target || target.children.length === 0) {
    alert('캡쳐할 종목 목록이 없습니다. 먼저 스캔을 실행하세요.');
    return;
  }

  const btn = document.getElementById('btn-capture-list');
  const origText = btn ? btn.innerHTML : '';
  if (btn) { btn.disabled = true; btn.innerHTML = '캡쳐 중...'; }

  try {
    // 클론 후 오프스크린에 렌더 — 라이브 DOM/스크롤 영향 X
    const stage = document.getElementById('share-card-render') || (() => {
      const s = document.createElement('div');
      s.id = 'share-card-render';
      s.style.cssText = 'position:fixed;left:-99999px;top:0;';
      document.body.appendChild(s);
      return s;
    })();
    stage.innerHTML = '';

    const clone = target.cloneNode(true);
    clone.style.maxHeight = 'none';
    clone.style.overflow = 'visible';
    // 캡쳐모드 활성 시 클론에도 적용 — 좁은 폭으로 글자 크게
    const isCaptureMode = target.classList.contains('capture-mode');
    if (isCaptureMode) clone.classList.add('capture-mode');
    const defaultWidth = isCaptureMode ? 800 : 1600;
    // 원본 사이즈 보장 — 좁은 화면에서도 데스크탑급 보장 (붙여넣기 시 흐릿하지 않게)
    clone.style.width = isMobile ? '480px' : Math.max(target.scrollWidth, defaultWidth) + 'px';

    // 헤더 박스(브랜드 + 메타데이터) 추가
    const now = new Date();
    const dateStr = `${now.getFullYear()}.${String(now.getMonth()+1).padStart(2,'0')}.${String(now.getDate()).padStart(2,'0')} ${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`;
    const mkt = currentMarket === 'KR' ? '🇰🇷 한국' : '🇺🇸 미국';
    const total = (_currentResults || []).length;
    const sectorLbl = (typeof _currentSector !== 'undefined' && _currentSector) ? _currentSector : '전체';

    const wrap = document.createElement('div');
    wrap.style.cssText = 'background:#ffffff;padding:0;font-family:-apple-system,Pretendard,Noto Sans KR,system-ui,sans-serif;';
    wrap.innerHTML = `
      <div style="background:linear-gradient(135deg,#7C3AED,#3182F6);padding:16px 20px;color:#fff;">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;">
          <div>
            <div style="font-size:16px;font-weight:800;letter-spacing:-0.4px;">${SafeMode.name()} — 종목 목록</div>
            <div style="font-size:11px;opacity:0.85;margin-top:3px;">${mkt} · ${esc(sectorLbl)} · ${dateStr}</div>
          </div>
          <div style="background:rgba(255,255,255,0.2);border-radius:8px;padding:6px 12px;font-size:12px;font-weight:700;">${total}종목</div>
        </div>
      </div>
    `;
    wrap.appendChild(clone);

    const footer = document.createElement('div');
    footer.style.cssText = 'padding:10px 16px;background:#F5F6F8;border-top:1px solid #EAEBEE;font-size:10px;color:#8B95A1;text-align:center;';
    footer.textContent = '알고리즘 스크리닝 결과일 뿐임. 판단과 손익은 본인 책임임.';
    wrap.appendChild(footer);

    stage.appendChild(wrap);

    // 고해상도 캡쳐 — DPR 반영, 최소 scale 3 보장 (4K/레티나 모니터에서도 선명)
    const dpr = window.devicePixelRatio || 1;
    const captureScale = Math.max(3, Math.ceil(dpr * 1.5));
    const canvas = await html2canvas(wrap, {
      scale: captureScale,
      backgroundColor: '#ffffff',
      useCORS: true,
      logging: false,
      windowWidth: wrap.scrollWidth,
    });
    stage.innerHTML = '';

    const dataUrl = canvas.toDataURL('image/png');
    const fileName = `종목목록_${currentMarket}_${new Date().toISOString().slice(0,10).replace(/-/g,'')}_${canvas.width}x${canvas.height}.png`;

    // 공유 모달 재사용
    const preview = document.getElementById('share-card-preview');
    const modal = document.getElementById('share-modal');
    if (preview && modal) {
      preview.innerHTML = `<img src="${dataUrl}" style="max-width:100%;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,0.1);">`;
      preview.dataset.dataUrl = dataUrl;
      preview.dataset.fileName = fileName;
      modal.style.display = 'flex';
    } else {
      // 모달 없으면 바로 다운로드
      const a = document.createElement('a');
      a.href = dataUrl;
      a.download = fileName;
      a.click();
    }
  } catch (err) {
    alert('캡쳐 실패: ' + (err && err.message ? err.message : err));
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = origText; }
  }
}

async function copyShareCard() {
  const dataUrl = document.getElementById('share-card-preview')?.dataset?.dataUrl;
  if (!dataUrl) return;
  try {
    const res = await fetch(dataUrl);
    const blob = await res.blob();
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
    alert('클립보드에 복사되었습니다!');
  } catch {
    alert('복사 실패 — 브라우저가 이미지 복사를 지원하지 않습니다. 다운로드를 이용해주세요.');
  }
}

function closeShareModal() {
  document.getElementById('share-modal').style.display = 'none';
}

// ── 디테일 패널 캡쳐 ───────────────────────────────────────────────────────

async function captureDetail() {
  try { await _ensureHtml2Canvas(); } catch { alert('html2canvas 로드 실패'); return; }
  const panel = document.getElementById('detail-panel');
  if (!panel) return;

  const ticker = (document.getElementById('dp-ticker')?.textContent || 'stock').trim();
  const dateStr = new Date().toISOString().slice(0, 10).replace(/-/g, '');

  // ── 1) 모든 탭 컨텐츠 사전 로드 (라이브 DOM에 데이터 채워둠) ──
  try {
    if (typeof loadDpDartNews === 'function') {
      await loadDpDartNews(ticker);
    }
  } catch (_) { /* 로드 실패해도 캡쳐 진행 */ }

  // ── 2) 라이브 패널을 오프스크린에 클론. 라이브 UI는 절대 건드리지 않음 ──
  const stage = document.getElementById('share-card-render');
  if (!stage) {
    alert('캡쳐 영역(#share-card-render)을 찾을 수 없습니다.');
    return;
  }
  stage.innerHTML = '';

  const clone = panel.cloneNode(true);
  // 클론에 라이브 ID가 그대로 복사되면 충돌 — 모든 id 접두사 부여
  clone.id = '__cap-clone-detail-panel';
  clone.querySelectorAll('[id]').forEach(el => { el.id = '__cap_' + el.id; });

  // 라이브 패널과 동일한 너비로 렌더 (보통 사이드 드로어 너비)
  const liveRect = panel.getBoundingClientRect();
  const captureWidth = Math.max(liveRect.width || 0, 720);

  // 오프스크린 위치/사이즈 강제
  clone.style.position    = 'static';
  clone.style.left        = 'auto';
  clone.style.right       = 'auto';
  clone.style.top         = 'auto';
  clone.style.transform   = 'none';
  clone.style.display     = 'block';
  clone.style.visibility  = 'visible';
  clone.style.width       = captureWidth + 'px';
  clone.style.maxWidth    = 'none';
  clone.style.height      = 'auto';
  clone.style.maxHeight   = 'none';
  clone.style.overflow    = 'visible';
  clone.style.boxShadow   = 'none';

  // 내부 스크롤·바디 제약 해제 (클론에 한정)
  const cScroll = clone.querySelector('.dp-scroll');
  if (cScroll) {
    cScroll.style.overflow  = 'visible';
    cScroll.style.maxHeight = 'none';
    cScroll.style.height    = 'auto';
    cScroll.style.flex      = 'none';
    cScroll.style.minHeight = 'auto';
  }
  // 섹션 제약 해제 (클론에 한정)
  clone.querySelectorAll('.dp-section-conclusion,.dp-section-timing,.dp-section-company').forEach(s => {
    if (s) { s.style.minHeight = 'auto'; s.style.height = 'auto'; s.style.overflow = 'visible'; }
  });

  // 클론의 탭 네비 숨기고 모든 탭 패널 펼침 + 섹션 헤더 주입
  const cTabNav = clone.querySelector('.dp-tab-nav');
  if (cTabNav) cTabNav.style.display = 'none';

  const tabMeta = [
    { suffix: 'canslim',  label: 'CAN SLIM 분석' },
    { suffix: 'tech',     label: '기술 지표' },
    { suffix: 'finance',  label: '재무 지표' },
    { suffix: 'dartnews', label: '공시·뉴스',
      visibleOnly: () => currentMarket === 'KR' },
    { suffix: 'usinsight', label: 'US 인사이트',
      visibleOnly: () => currentMarket !== 'KR' },
  ];
  for (const t of tabMeta) {
    const pane = clone.querySelector('#__cap_dp-tab-' + t.suffix);
    if (!pane) continue;
    if (t.visibleOnly && !t.visibleOnly()) {
      pane.style.display = 'none';
      continue;
    }
    pane.style.display = 'block';
    // CSS .dp-tab-panel.active 강제
    pane.classList.add('active');

    const hdr = document.createElement('div');
    hdr.textContent = t.label;
    hdr.style.cssText = 'margin:18px 16px 6px;padding:8px 12px;font-size:14px;font-weight:700;color:#1A1F36;background:#F2F4F8;border-left:3px solid #00C073;border-radius:6px;';
    pane.parentNode.insertBefore(hdr, pane);
  }

  // 클론을 오프스크린 스테이지에 마운트 (좌측 -9999px)
  stage.appendChild(clone);

  // 클론 내 이미지 디코딩 대기 (4축 차트 등 base64 src)
  try {
    const imgs = clone.querySelectorAll('img');
    await Promise.all(Array.from(imgs).map(im => {
      if (im.complete && im.naturalWidth > 0) return Promise.resolve();
      return new Promise(res => {
        im.onload = im.onerror = () => res();
        // 안전망: 6초 후 강제 진행
        setTimeout(res, 6000);
      });
    }));
  } catch (_) {}

  // 레이아웃 안정화 대기
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));

  function cleanup() {
    try { stage.removeChild(clone); } catch (_) {}
  }

  try {
    // 고해상도 — DPR 반영, 최소 scale 3 (붙여넣기 시 원본 사이즈 가독성 확보)
    const _dpr = window.devicePixelRatio || 1;
    const _capScale = Math.max(3, Math.ceil(_dpr * 1.5));
    const canvas = await html2canvas(clone, {
      scale: _capScale,
      backgroundColor: '#ffffff',
      useCORS: true,
      logging: false,
      width: clone.scrollWidth,
      height: clone.scrollHeight,
      windowWidth: clone.scrollWidth,
      windowHeight: clone.scrollHeight,
    });
    const img = canvas.toDataURL('image/png');
    const preview = document.getElementById('share-card-preview');
    preview.innerHTML = `<img src="${img}" style="max-width:100%;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,0.1);">`;
    preview.dataset.dataUrl = img;
    preview.dataset.fileName = `${ticker.replace(/[^A-Za-z0-9가-힣]/g, '_')}_${dateStr}_${canvas.width}x${canvas.height}.png`;
    document.getElementById('share-modal').style.display = 'flex';
  } catch (err) {
    alert('캡쳐 실패: ' + err.message);
  } finally {
    cleanup();
  }
}

/* ── Hover Sparkline (desktop only) ───────────────────────────
 * 종목 행/카드에 마우스를 올리면 30일 점수 추이 미니차트 표시.
 * - 이벤트 위임: #stock-list, #mobile-stock-list
 * - 220ms 지연 후 fetch (스쳐 지나가는 호버 무시)
 * - 티커별 메모리 캐시
 * - 모바일/터치 환경에서는 자동 비활성화
 */
(function initHoverSparkline() {
  if (typeof window === 'undefined') return;
  // [DISABLED] 사용자 요청으로 호버 스파크라인 비활성화 (2026-05-25)
  return;
  // 터치 디바이스에서는 비활성 (mobile에서는 onclick이 우선)
  const isTouch = window.matchMedia && window.matchMedia('(hover: none)').matches;
  if (isTouch) return;

  const cache = new Map();        // ticker -> { points } | 'loading' | 'empty'
  let tip = null;
  let hoverTimer = null;
  let currentTicker = null;
  let lastEv = null;              // 가장 최근 마우스 이벤트 — 첫 hover 위치 잡기용

  function ensureTip() {
    if (tip) return tip;
    tip = document.createElement('div');
    tip.id = 'hover-sparkline-tip';
    tip.setAttribute('role', 'tooltip');
    tip.innerHTML = `
      <div class="hst-head">
        <span class="hst-ticker"></span>
        <span class="hst-delta"></span>
      </div>
      <svg class="hst-svg" width="220" height="56" viewBox="0 0 220 56" preserveAspectRatio="none"></svg>
      <div class="hst-meta"></div>`;
    document.body.appendChild(tip);
    return tip;
  }

  function position(ev) {
    if (!tip) return;
    const W = 240, H = 100;
    const pad = 12;
    let x = ev.clientX + 16;
    let y = ev.clientY + 16;
    if (x + W > window.innerWidth - pad)  x = ev.clientX - W - 16;
    if (y + H > window.innerHeight - pad) y = ev.clientY - H - 16;
    tip.style.left = Math.max(pad, x) + 'px';
    tip.style.top  = Math.max(pad, y) + 'px';
  }

  function render(ticker, points) {
    // 유효 포인트가 2개 미만이면 미리보기 자체를 띄우지 않음
    const valid = (points || []).filter(p => p && p.score != null && p.date);
    if (valid.length < 2) {
      if (tip) tip.classList.remove('show');
      return;
    }
    const t = ensureTip();
    // .show 추가 전에 마지막 마우스 위치로 선배치 — 첫 hover 시 (0,0) 노출 방지
    if (lastEv) position(lastEv);
    // 회사정보 팝업과 동시에 뜨면 가려져 보임 → sparkline 표시 시 팝업 즉시 숨김
    try { hideStockPopup(); } catch (_) {}

    const scores = valid.map(p => p.score);
    const W = 220, H = 56, PAD = 4;
    const minS = Math.min(...scores), maxS = Math.max(...scores);
    const range = maxS - minS || 1;
    // 날짜 → epoch-days
    const dayOf = (d) => {
      const [y, m, dd] = d.split('-').map(Number);
      return Date.UTC(y, m - 1, dd) / 86400000;
    };
    // X축: 인덱스 기반(균등 분포)이 아닌 "실제 날짜" 기반.
    //  → 12일 풀 적재와 3일 희소 적재가 시각적으로 명확히 구분됨.
    const days = valid.map(p => dayOf(p.date));
    const tFirst = days[0];
    const tLast  = days[days.length - 1];
    const span   = Math.max(1, tLast - tFirst);
    const toX = i => PAD + ((days[i] - tFirst) / span) * (W - PAD * 2);
    const toY = s => H - PAD - ((s - minS) / range) * (H - PAD * 2);
    const segSolid = [];   // 연속 구간 polyline 들
    const segDash  = [];   // 결손 구간 polyline 들
    let cur = [[toX(0), toY(scores[0])]];
    for (let i = 1; i < valid.length; i++) {
      const gap = days[i] - days[i - 1];
      const px = [toX(i), toY(scores[i])];
      if (gap <= 1) {
        cur.push(px);
      } else {
        // 끊김 — 직전 점과 현재 점을 점선으로 잇고, 새 연속 구간 시작
        if (cur.length >= 2) segSolid.push(cur);
        segDash.push([cur[cur.length - 1], px]);
        cur = [px];
      }
    }
    if (cur.length >= 2) segSolid.push(cur);

    const fmtSeg = (seg) => seg.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
    const allPtsStr = valid.map((_, i) => `${toX(i).toFixed(1)},${toY(scores[i]).toFixed(1)}`).join(' ');
    const areaPts = `${PAD.toFixed(1)},${H} ${allPtsStr} ${(W-PAD).toFixed(1)},${H}`;

    const first = scores[0], last = scores[scores.length - 1];
    const delta = last - first;
    const up = delta >= 0;
    const color = up ? 'var(--success, #00C073)' : 'var(--destructive, #F04452)';
    const fillId = `hst-grad-${up ? 'u' : 'd'}`;

    const dateFirst = valid[0].date;
    const dateLast  = valid[valid.length - 1].date;
    const sign = up ? '+' : '';
    const gapCount = segDash.length;

    t.querySelector('.hst-ticker').textContent = ticker;
    const dEl = t.querySelector('.hst-delta');
    dEl.textContent = `${sign}${delta.toFixed(1)}pt`;
    dEl.style.color = color;

    const solidSvg = segSolid.map(seg =>
      `<polyline points="${fmtSeg(seg)}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>`
    ).join('');
    const dashSvg = segDash.map(seg =>
      `<polyline points="${fmtSeg(seg)}" fill="none" stroke="${color}" stroke-width="1.4" stroke-linecap="round" stroke-dasharray="3 3" opacity="0.55"/>`
    ).join('');

    t.querySelector('.hst-svg').innerHTML = `
      <defs>
        <linearGradient id="${fillId}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"  stop-color="${up ? '#00C073' : '#F04452'}" stop-opacity="0.28"/>
          <stop offset="100%" stop-color="${up ? '#00C073' : '#F04452'}" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <polygon points="${areaPts}" fill="url(#${fillId})" opacity="0.7"/>
      ${dashSvg}
      ${solidSvg}
      <circle cx="${toX(scores.length-1).toFixed(1)}" cy="${toY(last).toFixed(1)}" r="2.8" fill="${color}"/>`;
    t.querySelector('.hst-meta').textContent =
      gapCount > 0
        ? `${dateFirst} ~ ${dateLast} · ${scores.length}일 (${gapCount}곳 결손) · ${Math.round(last)}점`
        : `${dateFirst} ~ ${dateLast} · ${scores.length}일 · ${Math.round(last)}점`;
    t.classList.add('show');
  }

  async function load(ticker) {
    const market = (typeof currentMarket !== 'undefined' && currentMarket) ? currentMarket : 'KR';
    const key = `${market}:${ticker}`;
    if (cache.has(key)) {
      const v = cache.get(key);
      if (v !== 'loading' && currentTicker === ticker) render(ticker, v);
      return;
    }
    cache.set(key, 'loading');
    try {
      const res = await fetch(`/api/score-history/${encodeURIComponent(ticker)}?market=${encodeURIComponent(market)}&days=30`);
      if (!res.ok) {
        // 실패는 영구캐시하지 않음 — 30초 뒤 재시도 허용
        cache.delete(key);
        setTimeout(() => cache.delete(key), 30000);
        return;
      }
      const { points } = await res.json();
      const pts = points || [];
      cache.set(key, pts);
      if (currentTicker === ticker) render(ticker, pts);
    } catch (e) {
      // 네트워크 에러도 재시도 가능하도록 캐시 해제
      cache.delete(key);
    }
  }

  function findTickerEl(target) {
    if (!target) return null;
    return target.closest('[data-ticker]');
  }

  function onOver(ev) {
    const el = findTickerEl(ev.target);
    if (!el) return;
    // 검색 자동완성·기타 컴포넌트 제외
    if (el.classList.contains('search-suggest-item')) return;
    const ticker = el.getAttribute('data-ticker');
    if (!ticker || ticker === currentTicker) return;
    currentTicker = ticker;
    lastEv = ev;  // 첫 hover 위치 기록 (mousemove 없이도 위치 잡기)
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(() => {
      if (currentTicker !== ticker) return;
      load(ticker);
    }, 220);
  }

  function onOut(ev) {
    const el = findTickerEl(ev.target);
    const next = findTickerEl(ev.relatedTarget);
    if (el && next && el === next) return;
    clearTimeout(hoverTimer);
    currentTicker = null;
    if (tip) tip.classList.remove('show');
  }

  function onMove(ev) {
    lastEv = ev;
    if (!tip || !tip.classList.contains('show')) {
      if (currentTicker) position(ev);
      return;
    }
    position(ev);
  }

  // 컨테이너 위임 — 동적 갱신에도 자동 적용
  document.addEventListener('mouseover', onOver, true);
  document.addEventListener('mouseout',  onOut,  true);
  document.addEventListener('mousemove', onMove);
  // 스크롤 시 숨김 — 위치 어긋남 방지
  window.addEventListener('scroll', () => { if (tip) tip.classList.remove('show'); currentTicker = null; }, true);
})();

// ── TopBar ⋯ 오버플로 메뉴 토글 ────────────────────────────────────────────
(function () {
  const btn = document.getElementById('topbar-more-btn');
  const menu = document.getElementById('topbar-more-menu');
  if (!btn || !menu) return;
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    menu.hidden = !menu.hidden;
  });
  document.addEventListener('click', () => { menu.hidden = true; });
})();
