/**
 * app.js — (.)(.)분석기 웹 프론트엔드
 * scanner.html (데스크탑 테이블) / detail.html 공용 스크립트
 */

// ── 상태 ─────────────────────────────────────────────────────────────────
let currentMarket   = 'US';
let currentStrategy = 'BALANCED';
let currentSector   = '';   // '' = 전체
let allStocks       = [];   // 마지막 스캔 결과 캐시
let _scanStocks     = [];   // 검색/필터 기준이 되는 전체 스캔 결과
let _cachedGroups   = {};   // loadSectors 결과 보관 (sidebar 재렌더용)
let _cachedSectors  = {};   // loadSectors 결과 보관
let _sortKey        = 'TotalScore';  // 현재 정렬 키 (기본: 복합 점수)
let _sortDir        = -1;   // 0=없음, 1=asc, -1=desc
let _stockMap       = {};   // ticker → stock data (팝업용)
let _currentFilter  = 'all'; // 활성 퀵필터: all/watchlist/strong/near_high/oversold/intraday
let _watchlist      = new Set(); // 현재 마켓의 워치리스트 티커 집합
let _selectedStocks = new Set(); // 공유 카드용 선택 종목

// ── 워치리스트 (localStorage) ───────────────────────────────────────────
let _currentResults = [];   // search/view basis
let _oneLinerFilter = null; // OneLinerTag filter
let _compareSet = new Set();

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

function _wlKey(market) { return `scanner_watchlist_${market || currentMarket}`; }
function loadWatchlist(market) {
  try {
    const raw = localStorage.getItem(_wlKey(market));
    _watchlist = new Set(raw ? JSON.parse(raw) : []);
  } catch { _watchlist = new Set(); }
}
function saveWatchlist() {
  try { localStorage.setItem(_wlKey(), JSON.stringify([..._watchlist])); } catch {}
}
function toggleWatchlist(ticker, ev) {
  if (ev) { ev.stopPropagation(); }
  if (_watchlist.has(ticker)) _watchlist.delete(ticker);
  else _watchlist.add(ticker);
  saveWatchlist();
  _refreshFilteredView();
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
const _ENTRY_LABEL = { STRONG: '진입적기', NEUTRAL: '눌림대기', AVOID: '부적합',
                       GREEN: '진입적기', YELLOW: '눌림대기', RED: '부적합' };

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

function _renderEntryCard(d) {
  const card = document.getElementById('dp-entry-card');
  if (!card) return;
  card.classList.remove('green', 'yellow', 'red');
  const st = d.EntryStatus || '';
  const cls = _ENTRY_COLOR[st] || '';
  const ico = _ENTRY_ICON[st] || '⚪';
  const score = d.EntryScore != null ? Math.round(d.EntryScore) : null;
  const phrase = d.EntryPhrase || '—';
  const plan = d.EntryPlan || {};
  setText('dp-entry-icon', ico);
  // 액션(헤드라인) ↔ 근거(보조) 분리.
  // 우선 백엔드 파생필드(headline_action/one_reason)를 그대로 표시.
  // 구버전 캐시 스캔(파생필드 없음)만 "관망 · BB 과확장 · …" 문자열을 분해.
  const _pp = phrase.split(' · ');
  // 진입 타이밍 라벨은 리스트 배지와 같은 어휘(진입적기/눌림대기/부적합)로 통일.
  const _headline = _ENTRY_LABEL[st] || plan.headline_action || _pp[0] || phrase;
  const _reason = plan.one_reason || _pp.slice(1).filter(Boolean).slice(0, 2).join(' · ');
  setText('dp-entry-phrase', _headline);
  const _subEl = document.getElementById('dp-entry-subreason');
  if (_subEl) _subEl.textContent = _reason;
  const _phEl = document.getElementById('dp-entry-phrase');
  if (_phEl) _phEl.style.color = cls === 'green' ? 'var(--success)' : cls === 'red' ? 'var(--destructive)' : cls === 'yellow' ? 'var(--brand)' : 'var(--text-primary)';
  setText('dp-entry-score', score != null ? String(score) : '—');
  const fill = document.getElementById('dp-entry-bar-fill');
  if (fill) fill.style.width = (score != null ? Math.max(0, Math.min(100, score)) : 0) + '%';
  if (cls) card.classList.add(cls);
  setText('dp-entry-px-entry', plan.entry != null ? fmtPrice(plan.entry) : '—');
  setText('dp-entry-px-stop',  plan.stop  != null ? fmtPrice(plan.stop)  : '—');
  setText('dp-entry-px-t1',    plan.t1    != null ? fmtPrice(plan.t1)    : '—');
  setText('dp-entry-px-t2',    plan.t2    != null ? fmtPrice(plan.t2)    : '—');
  setText('dp-entry-type', plan.entry_type || '—');
  setText('dp-entry-current', plan.current != null ? fmtPrice(plan.current) : '—');
  // 할인율 표시
  const discEl = document.getElementById('dp-entry-discount');
  if (discEl) {
    const dv = plan.entry_discount;
    if (dv != null) {
      const sign = dv > 0 ? '-' : (dv < 0 ? '+' : '');
      discEl.textContent = `${sign}${Math.abs(dv).toFixed(2)}%`;
      discEl.style.color = dv > 0 ? 'var(--success)' : dv < 0 ? 'var(--destructive)' : 'var(--muted)';
    } else {
      discEl.textContent = '';
    }
  }
  // R:R 비율 (NaN/Infinity 방어)
  const rrEl = document.getElementById('dp-entry-rr');
  if (rrEl) {
    const rr = plan.rr;
    if (rr != null && Number.isFinite(rr) && rr > 0) {
      const rrNow = plan.rr_now;
      const nowTxt = (rrNow != null && Number.isFinite(rrNow) && Math.abs(rrNow - rr) > 0.05) ? ` (현재 ${rrNow.toFixed(1)})` : '';
      rrEl.textContent = `R:R ${rr.toFixed(1)}:1${nowTxt}`;
      rrEl.style.color = rr >= 3 ? 'var(--success)' : rr >= 2 ? 'var(--brand)' : 'var(--destructive)';
    } else {
      rrEl.textContent = '산출 불가';
      rrEl.style.color = 'var(--text-tertiary)';
    }
  }
  // 손절 방법 + 승률
  const smEl = document.getElementById('dp-entry-stop-method');
  if (smEl) {
    if (plan.stop_method) {
      const methodKo = {'지지선': '지지선', 'SWING_LOW': '스윙 저점', 'ATR': 'ATR'}[plan.stop_method] || plan.stop_method;
      const wr = plan.win_rate;
      const wrText = wr != null && wr > 0 ? ` · 승률 ${Math.min(100, Math.max(0, wr)).toFixed(0)}%` : '';
      smEl.innerHTML = methodKo + (wrText ? `<span style="color:${wr >= 60 ? 'var(--success)' : wr >= 40 ? 'var(--brand)' : 'var(--destructive)'};font-weight:700;">${wrText}</span>` : '');
    } else {
      smEl.textContent = '—';
    }
  }
  // 신뢰도 밴드 — 승률 + R:R 괴리를 1개 배지로 추상화 (탭하면 원수치)
  const confEl  = document.getElementById('dp-entry-confidence');
  const confRaw = document.getElementById('dp-entry-conf-raw');
  if (confEl) {
    const wr    = (plan.win_rate != null && plan.win_rate > 0) ? plan.win_rate : null;
    const rr    = (plan.rr     != null && Number.isFinite(plan.rr)     && plan.rr > 0)     ? plan.rr     : null;
    const rrNow = (plan.rr_now != null && Number.isFinite(plan.rr_now) && plan.rr_now > 0) ? plan.rr_now : null;
    let band = null;  // 'hi' | 'mid' | 'lo'
    // 우선 백엔드 파생필드 confidence_band ("낮음"/"보통"/"높음")를 신뢰.
    const _cb = { '낮음': 'lo', '보통': 'mid', '높음': 'hi' }[plan.confidence_band];
    if (_cb) {
      band = _cb;
    } else if (plan.confidence_band == null && (wr != null || rr != null)) {
      // 구버전 캐시 스캔(파생필드 없음)만 클라이언트에서 재계산
      const lowWr = wr != null && wr < 40;
      const lowRr = (rrNow != null && rrNow < 1.5) || (rr != null && rr < 1.5);
      const hiWr  = wr != null && wr >= 55;
      const hiRr  = rr != null && rr >= 2.5 && (rrNow == null || rrNow >= 2.0);
      if (lowWr || lowRr)      band = 'lo';
      else if (hiWr && hiRr)   band = 'hi';
      else                     band = 'mid';
    }
    if (band) {
      confEl.textContent = { hi: '신뢰도 높음', mid: '신뢰도 보통', lo: '신뢰도 낮음' }[band];
      confEl.className = 'ev-conf ' + band;
      confEl.style.display = '';
      const parts = [];
      if (wr != null) parts.push(`승률 ${Math.min(100, Math.max(0, wr)).toFixed(0)}%`);
      if (rr != null) parts.push(`손익비 ${rr.toFixed(1)}:1${(rrNow != null && Math.abs(rrNow - rr) > 0.05) ? ` (현재 ${rrNow.toFixed(1)})` : ''}`);
      if (confRaw) {
        confRaw.textContent = parts.length ? parts.join('   ·   ') : '원수치 없음';
        confRaw.style.display = 'none';
      }
      confEl.onclick = () => { if (confRaw) confRaw.style.display = (confRaw.style.display === 'none' ? '' : 'none'); };
    } else {
      confEl.style.display = 'none';
      if (confRaw) confRaw.style.display = 'none';
    }
  }
  // 점수 분해 바 차트
  // (칩 dp-entry-tags 제거: score_breakdown 파생값이라 아래 breakdown 차트와 100% 중복)
  const bdEl = document.getElementById('dp-entry-breakdown');
  if (bdEl) {
    const bd = plan.score_breakdown || {};
    const keys = Object.keys(bd);
    if (keys.length > 0) {
      const maxAbs = Math.max(16, ...keys.map(k => Math.abs(bd[k].pts)));
      bdEl.innerHTML = keys.map(k => {
        const p = bd[k].pts;
        const pct = Math.abs(p) / maxAbs * 100;
        const cls = p >= 0 ? 'pos' : 'neg';
        const col = p >= 0 ? '#16A34A' : '#DC2626';
        return `<div class="bd-row">
          <span class="bd-label">${esc(bd[k].tag || k)}</span>
          <div class="bd-bar-wrap"><div class="bd-bar ${cls}" style="width:${pct}%;"></div></div>
          <span class="bd-pts" style="color:${col};">${p >= 0 ? '+' : ''}${p}</span>
        </div>`;
      }).join('');
    } else {
      bdEl.innerHTML = '<div style="font-size:11px;color:var(--text-tertiary);">분해 데이터 없음</div>';
    }
  }
  // AgentQuant 융합 신호 (있을 때만)
  const aqRow = document.getElementById('dp-aq-row');
  if (aqRow) {
    if (d.AQ_Verdict || d.AQ_Regime || d.EntryScore_aq != null) {
      aqRow.style.display = '';
      const vEl = document.getElementById('dp-aq-verdict');
      if (vEl) {
        vEl.textContent = d.AQ_Verdict || '—';
        const vc = d.AQ_VerdictCode;
        const col = vc === 'BUY' ? '#16A34A' : vc === 'ACCUMULATE' ? '#F59E0B' : vc === 'AVOID' ? '#DC2626' : 'var(--text-secondary)';
        vEl.style.color = col; vEl.style.background = col + '22';
      }
      setText('dp-aq-regime', d.AQ_Regime ? `시장 ${d.AQ_Regime}` : '');
      const detail = [];
      if (d.EntryScore_engine != null) detail.push(`기존 ${Math.round(d.EntryScore_engine)}`);
      if (d.EntryScore_aq != null)     detail.push(`AQ ${Math.round(d.EntryScore_aq)}`);
      setText('dp-aq-detail', detail.length ? `(융합 ${detail.join(' · ')})` : '');
      const rEl = document.getElementById('dp-aq-reasons');
      if (rEl) {
        rEl.innerHTML = (Array.isArray(d.AQ_Reasons) ? d.AQ_Reasons : []).map(r =>
          `<span style="padding:2px 8px;border:1px solid var(--border);border-radius:100px;background:var(--bg-tertiary);font-size:10px;">${esc(r)}</span>`
        ).join('');
      }
    } else {
      aqRow.style.display = 'none';
    }
  }
  // 밀도 모드 적용 (기본 compact: 더보기존 접힘, 사용자 선택 localStorage 유지)
  _applyEntryDensity();
}

// 진입 카드 밀도 모드 — compact(요약 3존) | full(전체).
// 기본 compact: 다수 사용자는 진입·손절·목표가1 + 결론이면 결정 가능.
// 고급 사용자가 펼치면 그 선택을 기억(= 향후 A/B 기준값).
function _entryDensityMode() {
  try { return localStorage.getItem('entryCardDensity') === 'full' ? 'full' : 'compact'; }
  catch (e) { return 'compact'; }
}
function _applyEntryDensity() {
  const det = document.getElementById('dp-entry-detail');
  if (!det) return;
  const full = _entryDensityMode() === 'full';
  det.classList.toggle('open', full);
  const tog = document.getElementById('dp-entry-density-toggle');
  if (tog) {
    const ar = tog.querySelector('.arrow');
    const lb = tog.querySelector('.dt-label');
    if (ar) ar.textContent = full ? '▲' : '▼';
    if (lb) lb.textContent = full ? '간단히 보기' : '상세 분석';
  }
}
function _toggleEntryDensity() {
  const next = _entryDensityMode() === 'full' ? 'compact' : 'full';
  try { localStorage.setItem('entryCardDensity', next); } catch (e) {}
  _applyEntryDensity();
}

function _entryLight(stock) {
  if (!stock || !stock.EntryStatus) return '';
  const st = stock.EntryStatus;
  const ico = _ENTRY_ICON[st] || '⚪';
  const lbl = _ENTRY_LABEL[st] || '';
  const cls = _ENTRY_COLOR[st] || 'neutral';
  const phr = stock.EntryPhrase || '';
  const sc  = stock.EntryScore != null ? `진입 타이밍 ${stock.EntryScore}/100` : '';
  let tip = phr ? `${phr}${sc ? ' (' + sc + ')' : ''}` : sc;
  let aqBadge = '';
  if (stock.AQ_Verdict || stock.EntryScore_aq != null) {
    const vc = stock.AQ_VerdictCode;
    const col = vc === 'BUY' ? '#16A34A' : vc === 'ACCUMULATE' ? '#F59E0B' : vc === 'AVOID' ? '#DC2626' : '#6b7280';
    const reg = stock.AQ_Regime ? ` · ${stock.AQ_Regime}` : '';
    const aqSc = stock.EntryScore_aq != null ? ` AQ${Math.round(stock.EntryScore_aq)}` : '';
    tip += ` | AgentQuant: ${stock.AQ_Verdict || '—'}${reg}${aqSc}`;
    aqBadge = `<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:${col};margin-left:2px;vertical-align:middle;" title="${esc(stock.AQ_Verdict||'')}"></span>`;
  }
  return `<span class="entry-badge entry-${cls}" title="${esc(tip)}">${ico}${lbl ? `<span class="entry-badge-label">${esc(lbl)}</span>` : ''}${aqBadge}</span>`;
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
  let h = `<div class="signal-row">${_entryLight(stock)}${qualityHtml}</div>`;
  if (tags.length) {
    h += '<div class="signal-tags">';
    for (const t of tags) {
      const clean = t.replace(/[\u{1F525}\u{1F514}]/gu, '').trim();
      const label = _TAG_KO[clean] || clean;
      const cls = /BREAKOUT|EPS|VOL/.test(t) ? 'sig-tag-hot' : /LOW|LIQ/.test(t) ? 'sig-tag-warn' : 'sig-tag-info';
      h += `<span class="sig-tag ${cls}">${esc(label)}</span>`;
    }
    h += '</div>';
  }
  return h;
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
  currentMarket = val;
  currentSector = '';
  allStocks     = [];
  loadWatchlist();
  _setSegActive('market-btn-group', val);
  loadSectors();
  runScan();
  return;
  setStockListMsg('섹터를 선택하거나 스캔 버튼을 눌러주세요.');
  setStatHTML('stat-total',  '—<span class="unit">개</span>');
  setStatHTML('stat-strong', '—<span class="unit">개</span>');
  setText('stat-sector', '전체');
}

// 전략 토글 제거됨 — 5개 전략 점수를 한 번에 표시하고 컬럼 헤더 클릭으로 정렬
function onStrategyChange(_val) { /* deprecated */ }

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

async function runScan() {
  const btn = document.getElementById('btn-scan');
  if (btn) { btn.disabled = true; }

  setStatHTML('stat-total',  '…<span class="unit">개</span>');
  setStatHTML('stat-strong', '…<span class="unit">개</span>');
  showScanLoading();

  try {
    const p = new URLSearchParams({ market: currentMarket, strategy: currentStrategy });
    if (currentSector) p.set('sector', currentSector);

    const res    = await fetch(`/api/scan?${p}`);
    let payload;
    try {
      payload = await res.json();
    } catch (_parseErr) {
      const raw = await res.text().catch(() => '');
      throw new Error(raw || `HTTP ${res.status}`);
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
    _refreshFilteredView();
  } catch (e) {
    console.error('runScan 실패:', e);
    stopScanLoading();
    setStockListMsg('스캔 실패. 서버 상태를 확인하세요.');
  } finally {
    stopScanLoading();
    if (btn) btn.disabled = false;
  }
}

// 퀵필터 적용
function _applyQuickFilter(stocks) {
  switch (_currentFilter) {
    case 'watchlist':
      return stocks.filter(s => _watchlist.has(s.Ticker));
    case 'entry_green':
      return stocks.filter(s => s.EntryStatus === 'STRONG' || s.EntryStatus === 'GREEN');
    case 'strong': {
      return stocks.filter(s => {
        const g = _stockGrade(s.TotalScore);
        return g === 'S' || g === 'A';
      });
    }
    case 'near_high':
      return stocks.filter(s => s.NearHighPass);
    case 'oversold':
      return stocks.filter(s => s.RSI != null && s.RSI < 30);
    case 'intraday':
      return stocks.filter(s =>
        (s.ORBSignal && s.ORBSignal !== 'NEUTRAL' && s.ORBSignal !== '-') ||
        (s.NR7Signal && s.NR7Signal !== 'NEUTRAL' && s.NR7Signal !== '-') ||
        (s.BBSignal  && s.BBSignal  !== 'NEUTRAL' && s.BBSignal  !== '-')
      );
    default: return stocks;
  }
}

// 표가 실제로 보여주는 것과 동일한 필터 결과(검색 → 퀵필터 → 원라이너 버킷)
function _scopedStocks() {
  let scoped = _applySearchFilter(_searchBaseStocks());
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

function renderStockTable(stocks) {
  const tbody = document.getElementById('stock-list');
  if (!tbody) return;
  _currentResults = Array.isArray(stocks) ? stocks : [];

  // 워치리스트 카운트 (전체 기준)
  const wlEl = document.getElementById('chip-watch-count');
  if (wlEl) wlEl.textContent = stocks.filter(s => _watchlist.has(s.Ticker)).length;

  // 퀵필터 적용
  const quickFiltered = _applyQuickFilter(_currentResults);
  _renderOneLinerFilterChip(quickFiltered);
  const filtered = _oneLinerFilter
    ? quickFiltered.filter(s => (s.OneLinerTag || '') === _oneLinerFilter)
    : quickFiltered;

  const strong = filtered.filter(s => _signalTier(s.Signal) === 'strong').length;
  setStatHTML('stat-total',  `${filtered.length}<span class="unit">개</span>`);
  setStatHTML('stat-strong', `${strong}<span class="unit">개</span>`);

  if (filtered.length === 0) {
    const emptyMsg = _currentFilter === 'watchlist'
      ? '워치리스트가 비어있습니다. 표의 ☆ 버튼으로 추가하세요.'
      : '필터 조건에 맞는 종목이 없습니다.';
    tbody.innerHTML = `<tr><td colspan="${_colCount()}" class="state-msg">${esc(_currentFilter === 'all' ? '결과 없음' : emptyMsg)}</td></tr>`;
    return;
  }

  _stockMap = {};
  filtered.forEach(s => { _stockMap[s.Ticker] = s; });
  // 이후 sort 로직에서 사용되는 `stocks` 변수 재할당
  stocks = filtered;

  // 현재 정렬 적용
  let view = stocks;
  if (_sortKey && _sortDir !== 0) {
    view = [...stocks].sort((a, b) => {
      const va = _getByPath(a, _sortKey);
      const vb = _getByPath(b, _sortKey);
      const aa = va == null ? -Infinity : va;
      const bb = vb == null ? -Infinity : vb;
      return _sortDir * (aa > bb ? 1 : aa < bb ? -1 : 0);
    });
  }
  tbody.innerHTML = view.map((s, i) => renderStockRow(s, i + 1)).join('');
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

  const starred = _watchlist.has(stock.Ticker);
  const checked = _selectedStocks.has(stock.Ticker);
  return `
<tr onclick="openDetail('${esc(stock.Ticker)}')" style="cursor:pointer;">
  <td class="center"><input type="checkbox" ${checked ? 'checked' : ''} onclick="toggleSelectStock('${esc(stock.Ticker)}', event)" style="cursor:pointer;width:16px;height:16px;accent-color:#3182F6;"></td>
  <td class="center"><button class="star-btn${starred ? ' starred' : ''}" onclick="toggleWatchlist('${esc(stock.Ticker)}', event)" title="${starred ? '워치리스트에서 제거' : '워치리스트에 추가'}">${starred ? '★' : '☆'}</button></td>
  <td class="center"><span class="rank-cell ${rankClass}">${rank}</span></td>
  <td class="name-cell" onmouseenter="showStockPopup('${esc(stock.Ticker)}', event)" onmouseleave="hideStockPopup()">
    <span class="stock-name">${esc(stock.Name || stock.Ticker)}</span>
    <span class="stock-code">${esc(stock.Ticker)}</span>
  </td>
  <td class="desc-cell">${esc(stock.Desc || '')}</td>
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
  <td class="right">${rsi}</td>
  <td class="right">${avgVol}</td>
  <td class="right">${marketCap}</td>
  <td class="right" title="${stock.BrokerTargetSource ? esc(stock.BrokerTargetSource) : '증권사 컨센서스 없음'}">${stock.BrokerTarget ? (() => { const bUp = stock.Price ? ((stock.BrokerTarget - stock.Price) / stock.Price) * 100 : null; return `<div class="target-price">${fmtPrice(stock.BrokerTarget)}</div><div class="target-upside" style="color:${bUp != null && bUp >= 0 ? 'var(--success)' : 'var(--destructive)'}">${bUp != null ? (bUp >= 0 ? '+' : '') + fmt(bUp, 1) + '%' : ''}</div>`; })() : '<div class="target-empty">컨센서스 없음</div>'}</td>
  <td class="reason-cell">${reasonHtml}</td>
</tr>`;
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
  const ths = document.querySelectorAll('.stock-table thead th');
  return ths.length || 11;
}

function setStockListMsg(msg) {
  const tbody = document.getElementById('stock-list');
  if (tbody) tbody.innerHTML = `<tr><td colspan="${_colCount()}" class="state-msg">${esc(msg)}</td></tr>`;
}

// ── Game-style Loading Screen (쓸데없는 주식 잡학) ─────────────────────
const _STOCK_TRIVIA = [
  '버크셔 해서웨이 A주 한 주 가격은 약 60만 달러. 서울 아파트 한 채 값이다.',
  '워런 버핏은 11살에 첫 주식을 샀다. Cities Service 우선주 3주를 38.25달러에.',
  '뉴욕증권거래소(NYSE)의 개장벨은 원래 중국식 징이었다. 1903년에 놋쇠 벨로 바뀌었다.',
  '"월스트리트"라는 이름은 네덜란드 식민지 시절 원주민 방어용 나무 벽(wall)에서 유래했다.',
  '세계 최초의 주식회사는 1602년 네덜란드 동인도회사(VOC)다. 배당률이 무려 18%였다.',
  '나스닥(NASDAQ)은 약어다: National Association of Securities Dealers Automated Quotations.',
  '코스닥의 "닥"도 약어다: Korea Securities Dealers Automated Quotations.',
  '1929년 대공황 때 제시 리버모어는 공매도로 1억 달러를 벌었다. 현재 가치 약 17억 달러.',
  '일본 닛케이225는 1989년 12월 29일에 최고점을 찍고, 무려 34년 만인 2024년에 회복했다.',
  'S&P 500 기업의 평균 수명은 약 18년이다. 1960년대에는 60년이었다.',
  '코카콜라를 1919년 IPO 때 40달러에 샀으면, 배당 재투자 시 지금 약 1천만 달러.',
  '"검은 월요일"(1987년 10월 19일) 다우존스는 하루에 22.6% 폭락했다. 역대 최대 일일 낙폭.',
  '알고리즘 트레이딩이 미국 주식 거래량의 약 60~73%를 차지한다. 사람이 소수파.',
  '피터 린치는 마젤란 펀드를 13년간 운용하며 연평균 29.2% 수익률을 달성했다.',
  '한국 주식시장의 점심시간 휴장은 2000년 5월 22일에 폐지되었다.',
  '역사상 가장 비싼 주문 실수: 미즈호증권이 "1엔에 610,000주" 주문. 손실 약 4억 달러.',
  '애플이 시가총액 1조 달러를 처음 돌파한 건 2018년 8월 2일이다.',
  '삼성전자는 1975년 상장 당시 주가가 1만 원이었다.',
  '한국 증시에서 외국인 투자 한도가 완전 폐지된 건 IMF 직후인 1998년이다.',
  '주식시장에서 "불(bull)"은 소가 뿔로 위로 들이받는 모습에서, "곰(bear)"은 발톱으로 아래로 할퀴는 모습에서 유래.',
  'NYSE 트레이딩 플로어 바닥에는 1857년에 심은 플라타너스 나무가 있다.',
  '전 세계 주식시장 시가총액 합계는 약 100조 달러. 지구인 1인당 약 1.2만 달러.',
  '테슬라는 2020년 한 해 동안 주가가 743% 올랐다.',
  '워런 버핏의 자산 99%는 50세 이후에 만들어졌다. 복리의 힘.',
  '일본의 트레이더 BNF는 데이트레이딩만으로 160만 엔을 2005년까지 약 153억 엔으로 불렸다.',
  '캔슬림(CAN SLIM)을 만든 윌리엄 오닐은 30살에 NYSE 최연소 회원석을 샀다.',
  'NYSE 개장벨을 울린 유명인 중에는 스누프 독, 마사 스튜어트, 스파이더맨(코스프레)이 있다.',
  '주식 "틱(tick)"의 어원은 시세 표시기(ticker tape)가 "틱틱" 소리를 내며 인쇄하던 것에서 유래.',
  '한국 코스피 역사상 최대 일일 상승률은 2008년 10월 30일의 11.95%다.',
  '"주식을 사면 그 회사의 화장실도 내 것" — 이론적으로는 맞다. 지분율만큼.',
  '세계에서 가장 오래된 현존 주식은 네덜란드 동인도회사(VOC) 주권이다. 1606년 발행, 경매가 약 1억 원.',
  '다우존스 산업평균지수는 원래 12개 종목이었다. 최초 구성 종목 중 지금까지 남은 건 제너럴 일렉트릭뿐이었는데, 그마저도 2018년에 빠졌다.',
  '찰리 멍거는 투자 비결을 한마디로 요약했다: "합리적인 가격에 훌륭한 기업을."',
  '1970년대 월스트리트에서는 거래량이 너무 많아서 매주 수요일에 쉬었다. 서류 처리 때문에.',
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

function showScanLoading() {
  stopScanLoading();
  _shuffled = _shuffledTrivia();
  _triviaIdx = 0;
  const tbody = document.getElementById('stock-list');
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="${_colCount()}" style="padding:0;border:none;">
    <div class="scan-loading">
      <div class="scan-loading-spinner">
        <div class="dot"></div><div class="dot"></div><div class="dot"></div><div class="dot"></div>
      </div>
      <div class="scan-loading-status">종목 분석 중...</div>
      <div class="scan-loading-trivia-wrap">
        <div class="scan-loading-trivia" id="scan-trivia">
          <div class="scan-loading-trivia-label">알고 계셨나요?</div>
          <div class="scan-loading-trivia-text">${esc(_shuffled[0])}</div>
        </div>
      </div>
      <div class="scan-loading-progress"><div class="scan-loading-progress-bar"></div></div>
    </div>
  </td></tr>`;
  _triviaTimer = setInterval(_rotateScanTrivia, 5000);
}

function _rotateScanTrivia() {
  const wrap = document.querySelector('.scan-loading-trivia-wrap');
  const cur = document.getElementById('scan-trivia');
  if (!wrap || !cur) { stopScanLoading(); return; }
  cur.classList.add('fade-out');
  setTimeout(() => {
    _triviaIdx = (_triviaIdx + 1) % _shuffled.length;
    const el = document.createElement('div');
    el.className = 'scan-loading-trivia';
    el.id = 'scan-trivia';
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
      setStockListMsg(`'${esc(ticker)}' 종목을 찾을 수 없습니다.`);
      return;
    }
    const data = await res.json();
    if (!data || data.error || !data.Ticker) {
      setStockListMsg(`'${esc(ticker)}' 종목을 찾을 수 없습니다.`);
      return;
    }
    openDetail(data.Ticker);
  } catch (err) {
    console.error('search lookup failed:', err);
    setStockListMsg('검색 실패. 서버 상태를 확인하세요.');
  }
}

async function _fetchSuggestions(q, box) {
  try {
    const p = new URLSearchParams({ q, market: currentMarket });
    const res = await fetch(`/api/search?${p}`);
    const hits = await res.json();
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

  inp.addEventListener('input', () => {
    const q = inp.value.trim();
    // 스캔 결과 내 필터
    if (_searchBaseStocks().length) _refreshFilteredView();
    // 자동완성 제안
    clearTimeout(_searchTimer);
    if (q.length < 1) { _hideSuggest(); return; }
    _searchTimer = setTimeout(() => _fetchSuggestions(q, box), 250);
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
      ol.innerHTML = olHtml(d.OneLiner) + (d.OneLinerData ? `<span class="oneliner-data">${esc(d.OneLinerData)}</span>` : '');
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
        ? 'color-mix(in srgb, var(--brand) 6%, var(--card))'
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
    const { base } = _splitSignal(d.Signal);
    sigEl.textContent = _trKo(base || '—');
    sigEl.style.color = signalColor(base);
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
const _KALMAN_KO = {'BUY_TREND':'매수 추세','SELL_TREND':'하락 추세 — 진입 회피','POSSIBLE_REVERSAL':'반전 가능','NEUTRAL':'관망','OVERHEATED':'과열 — 진입 주의','OVERSOLD':'과매도 — 매수 후보','STRONG_BUY':'강한 매수','STRONG_SELL':'강한 회피'};
const _MARKET_KO = {'STRONG_BULL':'강한 상승장','BULL':'상승장','SIDEWAYS (Leaning Bull)':'횡보(상승 우위)','SIDEWAYS':'횡보','BEAR':'하락장','STRONG_BEAR':'강한 하락장'};
function _auxTextKo(badge, text, st) {
  if (badge === 'MATH') {
    const hm = text.match(/Hurst\s+([\d.]+)/);
    const km = text.match(/Kalman\s+(\S+)/);
    const h = hm ? hm[1] : '—';
    const k = km ? (_KALMAN_KO[km[1]] || km[1].replace('SELL','진입 회피')) : '—';
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
  '기관 수급 (Institutional)':      ['기관 수급',        '기관 자금의 매수·매도 흐름'],
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
  'C':         '분기 순이익 증가율 — 최근 분기 EPS가 전년 동기 대비 얼마나 성장했는지 측정합니다. 오닐 기준 25% 이상이 목표입니다.',
  'A':         '연간 ROE 기준 — 자기자본으로 얼마나 많은 이익을 내는지 측정합니다. 오닐 기준 17% 이상이 합격선입니다.',
  'N':         '신고가·피벗 돌파 — 52주 최고가 근접 여부와 컵앤핸들 패턴의 피벗 돌파를 측정합니다.',
  'S':         '거래량 수반 돌파 — 기관의 매수를 동반한 거래량 급증으로 진짜 돌파인지 확인합니다.',
  'L':         '시장 주도주 여부 — 시장 대비 상대강도(RS Rating)를 측정합니다. 80점 이상이 주도주 기준입니다.',
  'I':         '기관 자금 수급 — 스마트머니(기관·세력)의 매수/매도 압력을 자금 흐름 지표로 측정합니다.',
  'M':         '시장 방향 — 현재 전체 시장이 상승장(Bull)인지 하락장(Bear)인지 추세와 ADX로 측정합니다.',
  'Quant':     '퀀트 보조 전략 — Fama-French 팩터, 모멘텀, 평균회귀 등 통계 기반 전략 점수입니다.',
  'Math':      '수학적 시계열 분석 — 허스트 지수(추세 지속성), 칼만 필터(노이즈 제거), Z-Score(통계적 위치)를 활용합니다.',
  'Adj':       '변동성 조정 — DE Shaw 방식으로 변동성 대비 수익률 효율성을 평가해 최종 점수를 미세 조정합니다.',
  'Sentiment': '시장 심리 추정 — 뉴스 없이 가격·거래량만으로 투자 심리를 간접 측정합니다.',
  'Scalp':     '단타 시그널 — ORB 돌파, NR7 변동성 압축, 볼린저밴드 반등 등 단기 트레이딩 신호입니다.',
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
  <div class="cs-score-big ${sc}">${(typeof score === 'number' && isFinite(score)) ? fmt(score, 1) : '<span style="font-size:11px;color:var(--text-tertiary);">데이터 부족</span>'}</div>
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

  // 스캔 데이터가 이미 있으면 즉시 렌더링 (빈 드로어 방지)
  const cached = _stockMap[ticker];
  if (cached) _populatePanelDetail(cached, /* skipFourAxis */ true);

  // 4축 차트 + 종목 상세 + AQ 시그널 + 증권사 컨센서스를 모두 병렬로 요청
  loadDpFourAxis(ticker);
  _loadAqSignal(ticker, seq);
  loadConsensus(ticker, 'dp-consensus-card', 'dpcons');

  try {
    const p   = new URLSearchParams({ market: currentMarket, strategy: currentStrategy });
    const res = await fetch(`/api/ticker/${encodeURIComponent(ticker)}?${p}`);
    if (seq !== _detailSeq) return; // 종목 전환됨 — stale 응답 무시
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    // 한줄평은 시드(score/RSI 양자화 + 시간 회전)에 따라 매번 달라질 수 있다.
    // 스캐너에서 본 한줄평이 패널 열 때 잠깐 보였다가 API 응답 도착 시 다른 문구로
    // 바뀌는 깜빡임을 막기 위해, 같은 버킷이면 스캐너 한줄평을 그대로 유지한다.
    if (cached && cached.OneLinerTag && cached.OneLinerTag === data.OneLinerTag) {
      if (cached.OneLiner) data.OneLiner = cached.OneLiner;
      if (cached.OneLinerData != null) data.OneLinerData = cached.OneLinerData;
    }
    _populatePanelDetail(data, /* skipFourAxis */ true);
  } catch (e) {
    if (seq !== _detailSeq) return;
    console.error('openDetail 실패:', e);
    if (!cached) setText('dp-name', '데이터를 불러올 수 없습니다');
  }
}

async function _loadAqSignal(ticker, seq) {
  const aqRow = document.getElementById('dp-aq-row');
  if (!aqRow) return;
  // 로딩 표시
  aqRow.style.display = '';
  const vEl = document.getElementById('dp-aq-verdict');
  if (vEl) { vEl.textContent = '분석 중…'; vEl.style.color = 'var(--text-tertiary)'; vEl.style.background = 'none'; }
  setText('dp-aq-regime', '');
  setText('dp-aq-detail', '');
  const rEl = document.getElementById('dp-aq-reasons');
  if (rEl) rEl.innerHTML = '';
  try {
    const res = await fetch(`/api/aq_signal/${encodeURIComponent(ticker)}?market=${currentMarket}`);
    if (seq != null && seq !== _detailSeq) return; // 종목 전환됨
    const aq = await res.json();
    if (!aq.ok) { aqRow.style.display = 'none'; return; }
    if (vEl) {
      vEl.textContent = aq.AQ_Verdict || '—';
      const vc = aq.AQ_VerdictCode;
      const col = vc === 'BUY' ? '#16A34A' : vc === 'ACCUMULATE' ? '#F59E0B' : vc === 'AVOID' ? '#DC2626' : 'var(--text-secondary)';
      vEl.style.color = col; vEl.style.background = col + '22';
    }
    setText('dp-aq-regime', aq.AQ_Regime ? `시장 ${aq.AQ_Regime}` : '');
    const detail = [];
    if (aq.EntryScore_aq != null) detail.push(`AQ ${Math.round(aq.EntryScore_aq)}`);
    setText('dp-aq-detail', detail.length ? `(${detail.join(' · ')})` : '');
    if (rEl) {
      rEl.innerHTML = (Array.isArray(aq.AQ_Reasons) ? aq.AQ_Reasons : []).map(r =>
        `<span style="padding:1px 7px;border:1px solid var(--border);border-radius:100px;background:var(--bg-tertiary);">${esc(r)}</span>`
      ).join('');
    }
  } catch (e) {
    console.error('AQ signal 로드 실패:', e);
    aqRow.style.display = 'none';
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
  const _lb = document.getElementById('dp-leader-badge');
  if (_lb) _lb.style.display = 'none';
  const _nb = document.getElementById('dp-news-bar');
  if (_nb) _nb.style.display = 'none';
  ['dp-name','dp-ticker','dp-sector','dp-about','dp-score','dp-signal',
   'dp-price','dp-day-chg','dp-target','dp-broker-target','dp-rsi','dp-conviction',
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
}

function _populatePanelDetail(d, skipFourAxis) {
  setText('dp-name',    d.Name   || d.Ticker || '—');
  setText('dp-ticker',  d.Ticker || '—');
  setText('dp-sector',  d.Sector || '—');
  const aboutText = d.Desc || '';
  const aboutEl   = document.getElementById('dp-about');
  const aboutBox  = document.getElementById('dp-about-box');
  if (aboutEl)  aboutEl.textContent = aboutText;
  if (aboutBox) aboutBox.style.display = aboutText ? '' : 'none';
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
  setText('dp-rsi',     d.RSI    != null ? fmt(d.RSI, 1) : '—');
  setText('dp-conviction', _trKo(d.Conviction || '—'));

  const dayChg = d.DayChg || 0;
  const chgEl  = document.getElementById('dp-day-chg');
  if (chgEl) {
    const sign = dayChg > 0 ? '+' : '';
    chgEl.textContent = `${sign}${(dayChg * 100).toFixed(2)}%`;
    chgEl.className = 'dp-metric-val ' + (dayChg > 0 ? 'chg-up' : dayChg < 0 ? 'chg-down' : 'chg-flat');
  }

  // (상승여력은 DCF/증권사 각 행에 인라인 표시됨)

  const scoreEl = document.getElementById('dp-score');
  if (scoreEl) {
    scoreEl.textContent = Math.round(d.TotalScore || 0);
    scoreEl.className   = 'dp-score-num ' + scoreClass(d.TotalScore || 0);
  }

  const sigEl = document.getElementById('dp-signal');
  if (sigEl) {
    const { base } = _splitSignal(d.Signal);
    sigEl.textContent = _trKo(base || '—');
    sigEl.style.color      = signalColor(base);
    sigEl.style.background = signalBg(base);
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
  _renderEntryCard(d);

  // 종합×진입 2축 사분면 배지
  _renderQuadrant(d);

  // 기술·재무 탭
  _renderTechTab(d);
  _renderFinanceTab(d);

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

  // 투자자 동향 카드
  _renderInvestorCard(d);

  // CAN SLIM 탭으로 초기화
  switchDpTab('canslim');

  // 1008-풀 한줄평 포스터 (4축 차트 위 상단)
  const haikuEl = document.getElementById('dp-fa-haiku');
  if (haikuEl) {
    if (d.OneLiner) {
      haikuEl.innerHTML = olHtml(d.OneLiner) + (d.OneLinerData ? `<span class="oneliner-data">${esc(d.OneLinerData)}</span>` : '');
      haikuEl.setAttribute('data-tag', d.OneLinerTag || '');
      haikuEl.style.display = '';
    } else {
      haikuEl.innerHTML = '';
      haikuEl.removeAttribute('data-tag');
      haikuEl.style.display = 'none';
    }
  }

  // 차트 자동 로드 (openDetail에서 병렬 호출 시 skipFourAxis=true)
  if (!skipFourAxis) {
    const tk = (document.getElementById('dp-ticker')?.textContent || '').trim();
    if (tk && tk !== '—' && tk !== '…' && _dpFourAxisLoadedFor !== tk) {
      loadDpFourAxis(tk);
    }
  }
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

  // US: Finnhub
  if (d._FH_Available) {
    const insNet = d._FH_InsiderNet || 0;
    if (d._FH_InsiderCount > 0) {
      items.push({
        label: '내부자 거래',
        value: insNet > 0 ? '순매수' : insNet < 0 ? '순매도' : '중립',
        sub: `${d._FH_InsiderCount}건`,
        color: insNet > 0 ? 'var(--success)' : insNet < 0 ? 'var(--destructive)' : 'var(--text-tertiary)'
      });
    }
    const change = d._FH_RecChange || '';
    if (change && change !== 'stable') {
      items.push({
        label: '추천 변화',
        value: change === 'upgrade' ? '상향' : '하향',
        sub: `매수 ${d._FH_RecBuy || 0} · 매도 ${d._FH_RecSell || 0}`,
        color: change === 'upgrade' ? 'var(--success)' : 'var(--destructive)'
      });
    }
    const surp = d._FH_EarnSurprise || 0;
    if (surp !== 0) {
      items.push({
        label: '실적 서프라이즈',
        value: `${surp >= 0 ? '+' : ''}${surp.toFixed(1)}%`,
        sub: d._FH_EarnStreak >= 2 ? `${d._FH_EarnStreak}Q 연속 비트` : '',
        color: surp > 0 ? 'var(--success)' : 'var(--destructive)'
      });
    }
  }

  // US: yfinance
  if (d._YF_Available) {
    const shortPct = d._YF_ShortPctFloat;
    const instPct = d._YF_InstPct;
    const recKey = d._YF_RecKey || '';
    const tgtGap = d._YF_TargetGapPct;
    if (shortPct != null && shortPct >= 1) {
      items.push({
        label: '공매도 비율',
        value: `${shortPct.toFixed(1)}%`,
        color: shortPct >= 5 ? 'var(--destructive)' : 'var(--text-secondary)'
      });
    }
    if (instPct != null) {
      items.push({ label: '기관 보유', value: `${instPct.toFixed(0)}%`, color: 'var(--text-secondary)' });
    }
    if (recKey) {
      const recKr = {strong_buy:'적극매수', buy:'매수', hold:'보유', sell:'매도', strong_sell:'적극매도'};
      const recCol = recKey.includes('buy') ? 'var(--success)' : recKey.includes('sell') ? 'var(--destructive)' : 'var(--text-secondary)';
      items.push({
        label: '컨센서스',
        value: recKr[recKey] || recKey,
        sub: d._YF_NumAnalysts ? `${d._YF_NumAnalysts}명` : '',
        color: recCol
      });
    }
    if (tgtGap != null && Math.abs(tgtGap) >= 3) {
      items.push({
        label: '목표가 괴리',
        value: `${tgtGap >= 0 ? '+' : ''}${tgtGap.toFixed(0)}%`,
        color: tgtGap > 0 ? 'var(--success)' : 'var(--destructive)'
      });
    }
  }

  if (items.length === 0) { wrap.style.display = 'none'; return; }
  wrap.style.display = '';
  grid.innerHTML = items.map(it => `
    <div style="padding:10px 14px; border-right:1px solid var(--border); border-bottom:1px solid var(--border);">
      <div style="font-size:11px; color:var(--text-tertiary); margin-bottom:4px;">${esc(it.label)}</div>
      <div style="font-size:16px; font-weight:800; color:${it.color};">${esc(it.value)}</div>
      ${it.sub ? `<div style="font-size:10px; color:var(--text-tertiary); margin-top:2px;">${esc(it.sub)}</div>` : ''}
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
  'ACCUMULATE': '매집', 'HOLD': '진입 보류', 'SELL': '진입 회피',
  'AVOID': '회피', 'LAGGARD': '낙후', 'BREAKOUT': '돌파',
  'MOMENTUM': '모멘텀', 'CAUTION': '주의', 'BEAR MARKET': '하락장',
  // ORB/NR7/BB signals
  'BUY': '매수', 'NO SIGNAL': '신호 없음', 'PARTIAL': '부분',
  'OVERSOLD': '과매도', 'OVERBOUGHT': '과매수',
  'NR7 COMPRESSION': 'NR7 압축', 'SQUEEZE': '수축',
  'BUY_TREND': '매수 추세', 'SELL_TREND': '하락 추세',
  'POSSIBLE_REVERSAL': '반전 가능', 'RANGE': '횡보',
  'CONFIRMED': '확인', 'FAILED': '실패',
  // Breakdown desc 번역 (compound terms first)
  'STRONG_BUY': '강력 매수', 'STRONG_BULLISH': '강한 상승', 'STRONG_BEARISH': '강한 하락',
  'MILD_BULLISH': '약한 상승', 'MILD_BEARISH': '약한 하락',
  'STRONG_S_CONFIRMED': '강한 수급 확인', 'S_CONFIRMED': '수급 확인', 'S_WEAK': '수급 약함',
  'STRONG_DISTRIBUTION': '강한 분산', 'MILD_DISTRIBUTION': '약한 분산',
  'UNCONFIRMED_BREAKOUT': '미확인 돌파', 'NO_INTEREST': '관심 부족',
  'STRONG_TREND': '강한 추세', 'MEAN_REVERTING': '평균 회귀', 'RANDOM_WALK': '불규칙',
  'ORB_BREAKOUT': 'ORB 돌파', 'ORB_READY': 'ORB 관찰', 'ORB_WATCH': 'ORB 감시', 'ORB_WEAK': '약한 ORB', 'OVERHEATED': '과열',
  'MODERATE_BUY': '적정 매수', 'SLIGHT_UPSIDE': '소폭 상승 여력',
  'SLIGHT_OVERVALUED': '소폭 고평가', 'AT_TARGET': 'DCF 적정가 도달',
  'ABOVE_STRONG': '강한 상회', 'BELOW_WEAK': '약한 하회',
  'BULLISH': '상승', 'BEARISH': '하락', 'MODERATE': '보통',
  'ACCUMULATION': '매집', 'DISTRIBUTION': '분산',
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
  const rsRaw   = d.RSRating != null ? String(d.RSRating) + _rsLabel(d.RSRating) : '—';
  const rsCol   = d.RSRating >= 80 ? 'var(--success)' : d.RSRating < 40 ? 'var(--destructive)' : 'var(--text-primary)';
  const vwapRaw = d.VWAPDistance != null ? (d.VWAPDistance >= 0 ? '+' : '') + fmt(d.VWAPDistance*100,1)+'%'
                  + (d.VWAPDistance > 0.03 ? ' · VWAP 위' : d.VWAPDistance < -0.03 ? ' · VWAP 아래' : ' · VWAP 근접') : '—';
  const volRaw  = d.VolRatio != null ? fmt(d.VolRatio, 2)+'x'
                  + (d.VolRatio > 2 ? ' · 급증' : d.VolRatio > 1.3 ? ' · 증가' : d.VolRatio < 0.7 ? ' · 감소' : ' · 보통') : '—';

  const macdRaw = d._MACDHist != null
    ? (d._MACDHist > 0 ? '상승 전환 중' : d._MACDHist < 0 ? '하락 중' : '중립')
    : '—';
  const macdCol = d._MACDHist > 0 ? 'var(--success)' : d._MACDHist < 0 ? 'var(--destructive)' : null;
  const sectorRankRaw = d.SectorRank && d.SectorRank !== '-' ? d.SectorRank : (d.SectorRank === '-' ? '스캔 필요' : '—');
  const sectorRankCol = d.SectorRank === 'Top 10%' ? 'var(--success)' : d.SectorRank === 'Bottom' ? 'var(--destructive)' : null;

  const rows = [
    ['RSI (14)',    rsiVal,  '70↑ 과매수(조정 주의) · 30↓ 과매도(매수 기회)', rsiCol],
    ['ADX',        adxRaw,  '25↑ 추세 존재 · 40↑ 강한 추세 · 25↓ 횡보',     adxCol],
    ['ATR%',       d.ATRPercent != null ? fmt(d.ATRPercent*100,2)+'%' : '—', '높을수록 변동성 큼 — 위험과 기회 동시', null],
    ['VWAP 거리',  vwapRaw, '양수=평균가 위(강세) · 음수=아래(약세)',         d.VWAPDistance > 0 ? 'var(--success)' : 'var(--destructive)'],
    ['RS 등급',    rsRaw,   '80↑ 시장 주도주 · 40↓ 부진주',                  rsCol],
    ['12M 수익률', d.Mom12M != null ? (d.Mom12M >= 0 ? '+' : '') + fmt(d.Mom12M*100,1)+'%' : '—',
     '1년간 주가 등락 — 양수면 상승 추세', d.Mom12M > 0 ? 'var(--success)' : 'var(--destructive)'],
    ['3M 수익률',  d._Mom3M != null ? (d._Mom3M >= 0 ? '+' : '') + fmt(d._Mom3M,1)+'%' : '—',
     '3개월간 주가 등락 — 단기 추세 확인', d._Mom3M > 0 ? 'var(--success)' : 'var(--destructive)'],
    ['거래량 비율',volRaw,  '1↑ 평소보다 활발 · 2↑ 기관 참여 가능성',        d.VolRatio > 1.5 ? 'var(--success)' : null],
    ['MACD 방향',  macdRaw, '히스토그램 양수=상승 모멘텀 · 음수=하락 모멘텀', macdCol],
    ['ORB 신호',   _orbNr7Label(d.ORBSignal), '시초가 범위 돌파 시 매수 신호',     _orbNr7Color(d.ORBSignal)],
    ['NR7 압축',   _orbNr7Label(d.NR7Signal), '변동폭 수축 후 큰 움직임 대비',     _orbNr7Color(d.NR7Signal)],
    ['볼린저밴드', _trKo(d.BBSignal   || '—'), '하단 반등=매수 기회 · 상단=과열 주의', null],
    ['확신도',     _trKo(d.Conviction || '—'), '높음=팩터 방향 일치 · 낮음=신호 혼재', null],
    ['섹터 순위',  sectorRankRaw, '스캔 결과 기준 섹터 내 상대 위치 (목록 스캔 후 표시)', sectorRankCol],
  ];

  el.innerHTML = rows.map(([l, v, s, c]) => _indicatorRowHtml(l, v, s, c)).join('');
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
  const dbtLbl = d._DebtRatio ? (d._DebtRatio > 200 ? ' · 위험' : d._DebtRatio > 100 ? ' · 주의' : ' · 안정') : '';
  const omLbl  = d._OperatingMargin ? (d._OperatingMargin > 0.20 ? ' · 우수' : d._OperatingMargin > 0.10 ? ' · 양호' : d._OperatingMargin > 0 ? ' · 보통' : ' · 손실') : '';
  const epsAccelRaw = d.EPSAcceleration ? '가속 중 🚀' : (d.EPSAcceleration === false ? '가속 아님' : '—');
  const epsAccelCol = d.EPSAcceleration ? 'var(--success)' : null;
  const divRaw  = d._DivYield && d._DivYield > 0 ? fmt(d._DivYield * 100, 2) + '%' : '—';
  const revRaw  = d._RevenueGrowth != null && d._RevenueGrowth !== 0
    ? (d._RevenueGrowth >= 0 ? '+' : '') + fmt(d._RevenueGrowth * 100, 1) + '%' : '—';

  const rows = [
    ['PER',       d._PER   ? fmt(d._PER, 1) + perLbl  : '—', '주가÷순이익 · 15↓ 저평가 · 40↑ 고평가',    d._PER && d._PER < 15 ? 'var(--success)' : d._PER > 40 ? 'var(--destructive)' : null],
    ['PBR',       d._PBR   ? fmt(d._PBR, 2) + pbrLbl  : '—', '주가÷순자산 · 1↓ 자산 대비 저렴',           null],
    ['ROE',       roeRaw,                                      '자기자본이익률 · 17%↑ 오닐 기준 합격',     d._ROE > 0.15 ? 'var(--success)' : null],
    ['EPS 성장률',epsRaw,                                      '분기 순이익 전년비 · 25%↑ 성장주 기준',    d._EPSGrowth > 0 ? 'var(--success)' : 'var(--destructive)'],
    ['EPS 가속',  epsAccelRaw,                                 '전분기 대비 성장 가속 중인가 (CAN SLIM C원칙)', epsAccelCol],
    ['매출 성장', revRaw,                                      '전년비 매출 성장률 — 양수=매출 확장 중',   d._RevenueGrowth > 0 ? 'var(--success)' : d._RevenueGrowth < 0 ? 'var(--destructive)' : null],
    ['배당수익률',divRaw,                                      '연간 배당금 ÷ 현재가 (배당주 참고)',        null],
    ['영업이익률',d._OperatingMargin ? fmt(d._OperatingMargin*100,1)+'%' + omLbl : '—', '매출 대비 영업이익 · 20%↑ 우수',  null],
    ['부채비율',  d._DebtRatio ? fmt(d._DebtRatio, 1)+'%' + dbtLbl : '—', '100%↓ 양호 · 200%↑ 위험', d._DebtRatio > 150 ? 'var(--destructive)' : d._DebtRatio < 50 ? 'var(--success)' : null],
    ['시가총액',  d._MarketCap ? _fmtCap(d._MarketCap) : '—',          '기업 규모 — 클수록 안정적',         null],
    ['밸류 팩터', d.ValueScore   ? fmt(d.ValueScore, 1) + '점' : '—', '가치·저평가 매력도 — 양수=저평가', d.ValueScore > 10 ? 'var(--success)' : d.ValueScore < -5 ? 'var(--destructive)' : null],
    ['퀄리티 팩터',d.QualityScore ? fmt(d.QualityScore,1) + '점': '—', '수익성·안정성 종합 — 높을수록 우량', d.QualityScore > 10 ? 'var(--success)' : null],
  ];

  el.innerHTML = rows.map(([l, v, s, c]) => _indicatorRowHtml(l, v, s, c)).join('');
}

function _fmtCap(v) {
  if (!v) return '—';
  if (v >= 1e12) return (v/1e12).toFixed(1) + 'T';
  if (v >= 1e9)  return (v/1e9).toFixed(1)  + 'B';
  if (v >= 1e6)  return (v/1e6).toFixed(1)  + 'M';
  return String(v);
}

let _detailSeq = 0;           // openDetail / _loadAqSignal stale-guard
let _dpFourAxisLoadedFor = null;
let _dpFourAxisLoadingFor = null;
let _dpFourAxisReqSeq = 0;
let _detailFourAxisReqSeq = 0;
const FOUR_AXIS_FETCH_TIMEOUT_MS = 90000;

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
  const chartW  = document.getElementById('dp-fouraxis-chart-wrap');
  const obsDiv  = document.getElementById('dp-fouraxis-obs');
  if (!loading) return;
  if (_dpFourAxisLoadingFor === ticker) return;

  header.style.display = 'none';
  chartW.style.display = 'none';
  obsDiv.style.display = 'none';
  errDiv.style.display = 'none';
  loading.style.display = 'block';
  _dpFourAxisLoadingFor = ticker;
  const reqSeq = ++_dpFourAxisReqSeq;

  try {
    const p = new URLSearchParams({ market: currentMarket });
    const res = await _fetchWithTimeout(`/api/four_axis/${encodeURIComponent(ticker)}?${p}`);
    if (!res.ok) throw new Error(await _readApiError(res));
    const d = await res.json();
    if (d.error) throw new Error(d.error);
    if (!d.chart) throw new Error('Empty chart payload');
    if (reqSeq !== _dpFourAxisReqSeq) return;

    _dpFourAxisLoadedFor = ticker;
    _dpFourAxisLoadingFor = null;
    document.getElementById('dp-fouraxis-chart').src = 'data:image/png;base64,' + d.chart;
    chartW.style.display = 'block';

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
      5: '지금 매수 가능',
      4: '진입 타이밍 양호',
      3: '추세 확인 후 진입',
      2: '눌림 대기 후 진입',
      1: '지금 말고 다음 기회',
      0: '데이터 부족',
    };
    // 백엔드 headline_action(구체적 맥락 포함)을 우선 사용, 없으면 별점 테이블 폴백
    const _haText = _rec?.EntryPlan?.headline_action;
    const _meaningText = _haText || _starMeaningTbl[_stars] || '';
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
    errDiv.textContent = '4축 차트 로드 실패: ' + e.message;
    errDiv.style.display = 'block';
  } finally {
    if (reqSeq !== _dpFourAxisReqSeq) return;
    loading.style.display = 'none';
  }
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
  // 심층 분석 탭: 캐시된 결과가 있으면 자동 표시
  if (tabId === 'deep' && typeof TICKER !== 'undefined' && TICKER) {
    _deepTryAutoLoad();
  }
}

let _deepAutoTried = false;
async function _deepTryAutoLoad() {
  if (_deepAutoTried) return;
  _deepAutoTried = true;
  const mode = (document.getElementById('deep-mode')?.value) || 'standard';
  try {
    const p = new URLSearchParams({ market: currentMarket, mode, cache_only: '1' });
    const r = await fetch(`/api/deep-analysis/${encodeURIComponent(TICKER)}?${p}`);
    if (!r.ok) return;
    const d = await r.json();
    if (d && d.ok && d._cached) _renderDeep(d);
  } catch (_) { /* ignore */ }
}

function _escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function _renderMarkdown(md) {
  // 매우 간단한 Markdown → HTML 변환 (헤딩/볼드/이탤릭/표/리스트/인용/구분선)
  const lines = String(md || '').split(/\r?\n/);
  const out = [];
  let inTable = false, inUL = false, inOL = false, tableHeaderDone = false;
  const closeBlocks = () => {
    if (inTable) { out.push('</tbody></table>'); inTable = false; tableHeaderDone = false; }
    if (inUL) { out.push('</ul>'); inUL = false; }
    if (inOL) { out.push('</ol>'); inOL = false; }
  };
  const inline = (s) => {
    s = _escapeHtml(s);
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    s = s.replace(/`([^`]+)`/g, '<code style="background:var(--surface);padding:1px 5px;border-radius:3px;font-size:.92em;">$1</code>');
    return s;
  };
  for (let raw of lines) {
    const line = raw.replace(/\s+$/, '');
    if (!line.trim()) { closeBlocks(); continue; }
    // 표
    if (/^\s*\|/.test(line)) {
      const cells = line.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map(c => c.trim());
      if (/^[\s:\-|]+$/.test(line.replace(/\|/g,''))) {
        // 구분 행: skip, but mark header done
        if (inTable && !tableHeaderDone) { out.push('</thead><tbody>'); tableHeaderDone = true; }
        continue;
      }
      if (!inTable) { closeBlocks(); out.push('<table class="deep-tbl"><thead>'); inTable = true; tableHeaderDone = false; out.push('<tr>' + cells.map(c => `<th>${inline(c)}</th>`).join('') + '</tr>'); }
      else { out.push('<tr>' + cells.map(c => `<td>${inline(c)}</td>`).join('') + '</tr>'); }
      continue;
    }
    closeBlocks.call ? null : null;
    if (/^#{1,6}\s+/.test(line)) {
      closeBlocks();
      const m = line.match(/^(#{1,6})\s+(.*)$/);
      const lv = Math.min(m[1].length + 1, 6);
      out.push(`<h${lv} style="margin:18px 0 8px;font-weight:700;">${inline(m[2])}</h${lv}>`);
      continue;
    }
    if (/^\s*>/.test(line)) {
      closeBlocks();
      out.push(`<blockquote style="border-left:3px solid var(--accent);padding:6px 12px;margin:8px 0;color:var(--text-secondary);background:var(--surface);">${inline(line.replace(/^\s*>\s?/, ''))}</blockquote>`);
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      if (!inUL) { closeBlocks(); out.push('<ul style="margin:6px 0 6px 20px;">'); inUL = true; }
      out.push(`<li>${inline(line.replace(/^\s*[-*]\s+/, ''))}</li>`);
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      if (!inOL) { closeBlocks(); out.push('<ol style="margin:6px 0 6px 20px;">'); inOL = true; }
      out.push(`<li>${inline(line.replace(/^\s*\d+\.\s+/, ''))}</li>`);
      continue;
    }
    if (/^\s*---+\s*$/.test(line)) {
      closeBlocks();
      out.push('<hr style="border:none;border-top:1px solid var(--border);margin:14px 0;" />');
      continue;
    }
    closeBlocks();
    out.push(`<p style="margin:6px 0;">${inline(line)}</p>`);
  }
  closeBlocks();
  return out.join('\n');
}

function _renderDeep(d) {
  const content = document.getElementById('deep-content');
  const meta = document.getElementById('deep-meta');
  const sourcesDiv = document.getElementById('deep-sources');
  if (!content) return;
  const html = _renderMarkdown(d.text || '');
  content.innerHTML = `<div class="deep-md">${html}</div>`;
  // 표 스타일 보강
  if (!document.getElementById('deep-md-style')) {
    const s = document.createElement('style');
    s.id = 'deep-md-style';
    s.textContent = `
      .deep-md .deep-tbl { border-collapse:collapse; width:100%; margin:10px 0; font-size:12px; }
      .deep-md .deep-tbl th, .deep-md .deep-tbl td { border:1px solid var(--border); padding:6px 8px; text-align:left; vertical-align:top; }
      .deep-md .deep-tbl th { background:var(--surface); font-weight:600; }
    `;
    document.head.appendChild(s);
  }
  // 메타
  const cacheTxt = d._cached ? ` · 캐시(${Math.round((d._cache_age_sec||0)/60)}분 전)` : '';
  const elapsed = d.elapsed_sec ? ` · ${d.elapsed_sec}s` : '';
  if (meta) meta.textContent = `${d.model || 'gemini'}${elapsed}${cacheTxt}`;
  // 출처
  if (sourcesDiv) {
    const srcs = d.sources || [];
    if (srcs.length) {
      const items = srcs.map((s, i) => `<li><a href="${_escapeHtml(s.uri)}" target="_blank" rel="noopener" style="color:var(--accent);font-size:11px;">[${i+1}] ${_escapeHtml(s.title || s.uri)}</a></li>`).join('');
      sourcesDiv.innerHTML = `<div style="font-size:11px;font-weight:700;color:var(--text-tertiary);margin-bottom:6px;letter-spacing:0.03em;">📚 출처 (Google Search Grounding)</div><ol style="margin:0 0 0 18px;padding:0;">${items}</ol>`;
    } else {
      sourcesDiv.innerHTML = '';
    }
  }
}

async function runDeepAnalysis(force) {
  if (typeof TICKER === 'undefined' || !TICKER) return;
  const mode = (document.getElementById('deep-mode')?.value) || 'standard';
  const btn = document.getElementById('deep-run-btn');
  const refreshBtn = document.getElementById('deep-refresh-btn');
  const content = document.getElementById('deep-content');
  const meta = document.getElementById('deep-meta');
  if (btn) { btn.disabled = true; btn.textContent = '분석 중…'; }
  if (refreshBtn) refreshBtn.disabled = true;
  if (meta) meta.textContent = '웹 검색 + 분석 진행 중 (10~30초)…';
  if (content) {
    content.innerHTML = `<div style="padding:24px;text-align:center;color:var(--text-tertiary);font-size:13px;">
      <div style="display:inline-block;width:28px;height:28px;border:3px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin 1s linear infinite;"></div>
      <div style="margin-top:10px;">Gemini가 ${_escapeHtml(TICKER)}의 최신 시장 데이터를 수집하고 있습니다…</div>
    </div>`;
  }
  if (!document.getElementById('deep-spin-style')) {
    const s = document.createElement('style');
    s.id = 'deep-spin-style';
    s.textContent = '@keyframes spin { to { transform: rotate(360deg); } }';
    document.head.appendChild(s);
  }
  try {
    const p = new URLSearchParams({ market: currentMarket, mode });
    if (force) p.set('force', '1');
    const r = await fetch(`/api/deep-analysis/${encodeURIComponent(TICKER)}?${p}`);
    const d = await r.json();
    if (!d.ok) {
      if (content) content.innerHTML = `<div style="padding:20px;color:var(--destructive);font-size:13px;">⚠️ ${_escapeHtml(d.error || '분석 실패')}</div>`;
      if (meta) meta.textContent = '';
    } else {
      _renderDeep(d);
    }
  } catch (e) {
    if (content) content.innerHTML = `<div style="padding:20px;color:var(--destructive);font-size:13px;">⚠️ 네트워크 오류: ${_escapeHtml(e.message || e)}</div>`;
    if (meta) meta.textContent = '';
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '분석 시작'; }
    if (refreshBtn) refreshBtn.disabled = false;
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

// ── AgentQuant 레짐/진입 신호 ────────────────────────────────────────

async function loadAgentQuant(ticker) {
  const wrap = document.getElementById('aq-wrap');
  if (!wrap) return;
  const p = new URLSearchParams({ market: currentMarket });
  try {
    const res = await fetch(`/api/regime/${encodeURIComponent(ticker)}?${p}`);
    if (!res.ok) return;
    const data = await res.json();
    const stock = data.stock || {};
    const market = data.market || {};
    if (!stock.score && !market.label) return;
    wrap.style.display = '';

    setText('aq-score', stock.score != null ? stock.score.toFixed(0) : '—');
    setText('aq-icon', stock.icon || '⚪');

    const badge = document.getElementById('aq-verdict-badge');
    if (badge && stock.verdict_kr) {
      badge.textContent = stock.verdict_kr;
      const color = stock.verdict === 'BUY' ? '#16A34A'
                  : stock.verdict === 'ACCUMULATE' ? '#F59E0B'
                  : stock.verdict === 'AVOID' ? '#DC2626'
                  : 'var(--text-secondary)';
      badge.style.color = color;
      badge.style.background = color === 'var(--text-secondary)' ? 'var(--bg-tertiary)' : (color + '22');
    }

    setText('aq-market-label', market.label || '—');
    setText('aq-vix-pct', market.vix_percentile_252d != null
      ? `${market.vix_percentile_252d.toFixed(0)}%ile (${(market.vix_level||0).toFixed(1)})`
      : '—');
    setText('aq-rsi-macd', stock.rsi != null
      ? `RSI ${stock.rsi.toFixed(0)} · MACDh ${(stock.macd_hist||0).toFixed(3)}`
      : '—');

    const rwrap = document.getElementById('aq-reasons');
    if (rwrap && Array.isArray(stock.reasons)) {
      rwrap.innerHTML = stock.reasons.map(r =>
        `<span style="padding:2px 8px;border:1px solid var(--border);border-radius:100px;background:var(--bg-tertiary);">${esc(r)}</span>`
      ).join('');
    }
  } catch (e) {
    console.debug('loadAgentQuant:', e);
  }
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
    if (!res.ok) return;
    const data = await res.json();
    _clientCache.set(cacheKey, data);
    _renderConsensusData(wrap, data, prefix);
  } catch (e) {
    console.debug('loadConsensus:', e);
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
        <span style="color:${r.opinion && r.opinion.includes('매수') ? 'var(--success)' : 'var(--text-tertiary)'}; font-weight:600; width:32px; text-align:center;">${esc(r.opinion || '')}</span>
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

function _renderReasonTags(topReason) {
  if (!topReason || topReason === '-') return '<span style="color:var(--text-tertiary)">—</span>';
  const parts = topReason.split(' · ').slice(0, 4);
  return parts.map(p => {
    const neg = /⛔|과열|AVOID|SELL|적자/.test(p);
    const pos = /신고가|돌파|주도주|EPS|ROE|과매도/.test(p);
    const cls = neg ? ' negative' : pos ? ' positive' : '';
    return `<span class="reason-tag${cls}">${esc(p)}</span>`;
  }).join('');
}

// ── 테이블 정렬 ─────────────────────────────────────────────────────

function initFilterChips() {
  document.querySelectorAll('#filter-chips .chip').forEach(chip => {
    chip.addEventListener('click', () => {
      const f = chip.dataset.filter || 'all';
      // 활성 칩을 다시 누르면 필터 해제(all)
      const nextFilter = (_currentFilter === f && f !== 'all') ? 'all' : f;
      _currentFilter = nextFilter;
      document.querySelectorAll('#filter-chips .chip').forEach(c => {
        c.classList.toggle('active', (c.dataset.filter || 'all') === nextFilter);
      });
      if (_searchBaseStocks().length) _refreshFilteredView();
    });
  });
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

  // 시장 셀렉터 동기화
  const mSel = document.getElementById('market-select');
  if (mSel) mSel.value = currentMarket;
  const sSel = document.getElementById('strategy-select');
  if (sSel) sSel.value = currentStrategy;

  // ESC로 드로어 닫기
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDetailBtn(); });

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
    loadWatchlist();
    initFilterChips();
    initSearch();
    initOneLinerFilter();
    initSort();
      updateCompareActions();
    document.getElementById('btn-scan')?.addEventListener('click', runScan);
    loadSectors();
    runScan();
    loadMacro();
    setInterval(loadMacro, 15 * 60 * 1000);
  }
});

// ── 매크로 신호등 띠 ────────────────────────────────────────────────────────

const _MACRO_DEFS = [
  { key: 'vix',     label: 'VIX',      fixed: 2, invert: true  },
  { key: 'usdkrw',  label: '원/달러',  fixed: 1, invert: true  },
  { key: 'kospi',   label: 'KOSPI',    fixed: 2, invert: false },
  { key: 'sp500',   label: 'S&P500',   fixed: 2, invert: false },
  { key: 'kr_rate', label: '기준금리', fixed: 2, invert: false, suffix: '%' },
];

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
  if (!sigEl || !itemsEl || !metaEl) return;

  const sig = d.signal || { level: 'unknown', emoji: '⚪', label: '정보없음' };
  sigEl.className = 'macro-signal ' + (sig.level || 'unknown');
  sigEl.innerHTML = `${sig.emoji || '⚪'} <span>${esc(sig.label || '')}</span>`;

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
          <div class="compare-oneliner">${olHtml(detail.OneLiner || '요약 코멘트 없음')}${detail.OneLinerData ? `<span class="oneliner-data">${esc(detail.OneLinerData)}</span>` : ''}</div>
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

function generateShareCard() {
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
          <div style="font-size:18px;font-weight:800;color:#ffffff;letter-spacing:-0.5px;">(.)(.) 분석기</div>
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
      <div style="font-size:10px;color:#8B95A1;text-align:center;">본 자료는 투자 참고용이며 투자 판단의 책임은 본인에게 있습니다.</div>
    </div>
  </div>`;

  const renderArea = document.getElementById('share-card-render');
  renderArea.innerHTML = cardHtml;

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
  const cBody = clone.querySelector('.dp-body');
  if (cBody) {
    cBody.style.minHeight = 'auto';
    cBody.style.height    = 'auto';
    cBody.style.overflow  = 'visible';
  }

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
    const canvas = await html2canvas(clone, {
      scale: 2,
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
    preview.dataset.fileName = `${ticker.replace(/[^A-Za-z0-9가-힣]/g, '_')}_${dateStr}.png`;
    document.getElementById('share-modal').style.display = 'flex';
  } catch (err) {
    alert('캡쳐 실패: ' + err.message);
  } finally {
    cleanup();
  }
}
