/**
 * interactive.js — (.)(.)분석기 마이크로인터랙션 모듈
 * app.js와 분리하여 로드 실패 시에도 핵심 기능에 영향 없음.
 *
 * 기능:
 *  1) 점수 게이지 카운트업 애니메이션
 *  2) 퀵필터 전환 카운트 롤링 + confetti
 *  3) "오늘의 발견" 하이라이트 카드
 */

(function () {
  'use strict';

  /* ── prefers-reduced-motion 존중 ─────────────────────────── */
  const prefersReducedMotion = () =>
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ══════════════════════════════════════════════════════════════
   *  1. 점수 게이지 카운트업 애니메이션
   * ══════════════════════════════════════════════════════════════ */

  function easeOutExpo(t) {
    return t >= 1 ? 1 : 1 - Math.pow(2, -10 * t);
  }

  /**
   * 숫자를 from → to 로 카운트업 애니메이션.
   * @param {HTMLElement} el  — textContent 를 교체할 요소
   * @param {number} to      — 목표 숫자
   * @param {number} dur     — ms (기본 600)
   * @param {string} suffix  — 숫자 뒤에 붙일 텍스트 (선택)
   */
  function animateCount(el, to, dur, suffix) {
    if (!el || prefersReducedMotion()) {
      if (el) el.textContent = to + (suffix || '');
      return;
    }
    dur = dur || 600;
    const from = 0;
    let start = null;
    function step(ts) {
      if (!start) start = ts;
      const t = Math.min((ts - start) / dur, 1);
      const val = Math.round(from + (to - from) * easeOutExpo(t));
      el.textContent = val + (suffix || '');
      if (t < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  /**
   * score-bar-fill 의 width 를 0→목표% 로 애니메이션.
   * CSS transition 을 활용하므로 0% 세팅 후 rAF 로 목표 설정.
   */
  function animateBar(barEl, targetPct) {
    if (!barEl || prefersReducedMotion()) return;
    barEl.style.transition = 'none';
    barEl.style.width = '0%';
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        barEl.style.transition = 'width 0.7s cubic-bezier(0.34, 1.56, 0.64, 1)';
        barEl.style.width = targetPct + '%';
      });
    });
  }

  /**
   * 테이블 렌더 후 뷰포트에 보이는 행의 점수를 카운트업.
   * IntersectionObserver 로 뷰포트 내 행만 애니메이션.
   */
  function observeScoreBars() {
    var tbody = document.getElementById('stock-list');
    if (!tbody || prefersReducedMotion()) return;

    var rows = tbody.querySelectorAll('tr');
    if (!rows.length) return;

    // 이미 관찰 중인 옵저버가 있으면 해제
    if (window._ixScoreObs) {
      window._ixScoreObs.disconnect();
    }

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var row = entry.target;
        observer.unobserve(row);

        var barEl = row.querySelector('.score-bar-fill');
        if (barEl) {
          var w = parseFloat(barEl.style.width) || 0;
          animateBar(barEl, w);
        }
      });
    }, { threshold: 0.1 });

    rows.forEach(function (row) { observer.observe(row); });
    window._ixScoreObs = observer;
  }

  /* ══════════════════════════════════════════════════════════════
   *  2. 퀵필터 전환 카운트 롤링 + Confetti
   * ══════════════════════════════════════════════════════════════ */

  // setStatHTML 을 래핑하여 숫자 변화 시 롤링 애니메이션
  var _prevStatTotal = null;
  var _prevStatStrong = null;

  function animateStatRoll(id, newVal) {
    var el = document.getElementById(id);
    if (!el || prefersReducedMotion()) return;

    // newVal 에서 숫자 추출
    var num = parseInt(newVal, 10);
    if (isNaN(num)) return;

    // 이전 값과 같으면 스킵
    var prevRef = id === 'stat-total' ? _prevStatTotal : _prevStatStrong;
    if (prevRef === num) return;

    if (id === 'stat-total') _prevStatTotal = num;
    else _prevStatStrong = num;

    // 롤 애니메이션: 위로 슬라이드-아웃 후 새 값 슬라이드-인
    el.classList.add('ix-stat-roll');
    setTimeout(function () {
      el.classList.remove('ix-stat-roll');
    }, 350);
  }

  /**
   * CSS-only Confetti 파티클 효과.
   * @param {HTMLElement} anchor — 파티클이 터질 기준 요소
   * @param {number} count      — 파티클 수 (기본 12)
   */
  function confetti(anchor, count) {
    if (!anchor || prefersReducedMotion()) return;
    count = count || 12;

    var rect = anchor.getBoundingClientRect();
    var cx = rect.left + rect.width / 2;
    var cy = rect.top + rect.height / 2;

    var colors = ['#3182F6', '#00C073', '#FF9200', '#F04452', '#A855F7'];
    var container = document.createElement('div');
    container.className = 'ix-confetti-container';
    container.setAttribute('aria-hidden', 'true');

    for (var i = 0; i < count; i++) {
      var dot = document.createElement('span');
      dot.className = 'ix-confetti-dot';
      var angle = (Math.PI * 2 * i) / count + (Math.random() - 0.5) * 0.5;
      var dist = 30 + Math.random() * 50;
      var dx = Math.cos(angle) * dist;
      var dy = Math.sin(angle) * dist - 20;
      dot.style.setProperty('--ix-dx', dx + 'px');
      dot.style.setProperty('--ix-dy', dy + 'px');
      dot.style.left = cx + 'px';
      dot.style.top = cy + 'px';
      dot.style.background = colors[i % colors.length];
      dot.style.animationDelay = (Math.random() * 80) + 'ms';
      container.appendChild(dot);
    }

    document.body.appendChild(container);
    setTimeout(function () { container.remove(); }, 900);
  }

  /**
   * 강력매수 종목이 많을 때 confetti 트리거.
   * stat-strong 업데이트 시 호출.
   */
  function maybeConfetti(strongCount, totalCount) {
    if (prefersReducedMotion()) return;
    // 전체의 25% 이상이 강력매수이거나 5개 이상
    if (totalCount > 0 && (strongCount >= 5 || strongCount / totalCount >= 0.25)) {
      var el = document.getElementById('stat-strong');
      if (el) confetti(el, 15);
    }
  }

  /* ══════════════════════════════════════════════════════════════
   *  3. "오늘의 발견" 하이라이트 카드
   * ══════════════════════════════════════════════════════════════ */

  function renderDiscoveryCard(stocks) {
    var card = document.getElementById('ix-discovery-card');
    if (!card || !Array.isArray(stocks) || stocks.length === 0) {
      if (card) card.style.display = 'none';
      return;
    }

    // 후보 1: 점수 급등 (ScoreDelta 최고)
    var topDelta = null;
    var topDeltaVal = -Infinity;
    // 후보 2: 신규 진입 (IsNew)
    var newEntry = null;
    // 후보 3: 강한 시그널 + 최고 점수
    var topStrong = null;

    stocks.forEach(function (s) {
      var delta = s.ScoreDelta || 0;
      if (delta > topDeltaVal && delta > 1) {
        topDeltaVal = delta;
        topDelta = s;
      }
      if (s.IsNew && !newEntry) newEntry = s;
      if (!topStrong && typeof s.Signal === 'string' &&
          (s.Signal.toUpperCase().includes('BREAKOUT') ||
           s.Signal.toUpperCase().includes('STRONG LEADER'))) {
        topStrong = s;
      }
    });

    // 우선순위: 점수 급등 > 신규 진입 > 강한 시그널
    var pick = topDelta || newEntry || topStrong;
    if (!pick) {
      card.style.display = 'none';
      return;
    }

    var reason = '';
    var icon = '';
    if (pick === topDelta) {
      icon = '<span class="ix-disc-icon ix-disc-fire">&#x1F525;</span>';
      reason = '점수 &#x25B2;' + topDeltaVal.toFixed(1) + ' 급등';
    } else if (pick === newEntry) {
      icon = '<span class="ix-disc-icon ix-disc-new">NEW</span>';
      reason = '신규 진입 종목';
    } else {
      icon = '<span class="ix-disc-icon ix-disc-breakout">&#x1F4C8;</span>';
      reason = (pick.Signal || '').replace(/_/g, ' ');
    }

    var score = Math.round(pick.TotalScore || 0);
    var sc = score >= 70 ? 'score-high' : score >= 50 ? 'score-mid' : 'score-low';
    var dayChg = pick.DayChg || 0;
    var chgPct = (dayChg * 100).toFixed(2);
    var chgClass = dayChg > 0 ? 'chg-up' : dayChg < 0 ? 'chg-down' : 'chg-flat';
    var chgSign = dayChg > 0 ? '+' : '';

    var nameEsc = (pick.Name || pick.Ticker || '').replace(/[<>&"]/g, function (c) {
      return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c];
    });
    var tickerEsc = (pick.Ticker || '').replace(/[<>&"]/g, function (c) {
      return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c];
    });

    card.innerHTML =
      '<div class="ix-disc-inner" onclick="openDetail(\'' + tickerEsc + '\')" style="cursor:pointer;">' +
        '<div class="ix-disc-badge">' + icon + '<span class="ix-disc-reason">' + reason + '</span></div>' +
        '<div class="ix-disc-main">' +
          '<span class="ix-disc-name">' + nameEsc + '</span>' +
          '<span class="ix-disc-ticker">' + tickerEsc + '</span>' +
          '<span class="ix-disc-score ' + sc + '">' + score + '점</span>' +
          '<span class="ix-disc-chg ' + chgClass + '">' + chgSign + chgPct + '%</span>' +
        '</div>' +
        '<div class="ix-disc-label">오늘의 발견</div>' +
      '</div>';

    card.style.display = '';

    // 카드 슬라이드-인 애니메이션
    if (!prefersReducedMotion()) {
      card.classList.remove('ix-disc-enter');
      void card.offsetWidth; // reflow
      card.classList.add('ix-disc-enter');
    }
  }

  /* ══════════════════════════════════════════════════════════════
   *  4. 워치리스트 마이크로인터랙션 (별 파티클 + 마일스톤 토스트)
   * ══════════════════════════════════════════════════════════════ */

  /**
   * 별 버튼 주변에 미니 별 파티클을 발사.
   */
  function starBurst(btnEl) {
    if (!btnEl || prefersReducedMotion()) return;
    var rect = btnEl.getBoundingClientRect();
    var cx = rect.left + rect.width / 2;
    var cy = rect.top + rect.height / 2;

    var container = document.createElement('div');
    container.className = 'ix-confetti-container';
    container.setAttribute('aria-hidden', 'true');

    for (var i = 0; i < 8; i++) {
      var star = document.createElement('span');
      star.className = 'ix-star-particle';
      var angle = (Math.PI * 2 * i) / 8;
      var dist = 16 + Math.random() * 20;
      star.style.setProperty('--ix-dx', (Math.cos(angle) * dist) + 'px');
      star.style.setProperty('--ix-dy', (Math.sin(angle) * dist) + 'px');
      star.style.left = cx + 'px';
      star.style.top = cy + 'px';
      star.textContent = '\u2605';
      container.appendChild(star);
    }

    document.body.appendChild(container);
    setTimeout(function () { container.remove(); }, 700);
  }

  /**
   * 마일스톤 토스트 표시.
   */
  function showToast(msg, duration) {
    duration = duration || 2500;
    var existing = document.querySelector('.ix-toast');
    if (existing) existing.remove();

    var toast = document.createElement('div');
    toast.className = 'ix-toast';
    toast.textContent = msg;
    document.body.appendChild(toast);

    requestAnimationFrame(function () {
      toast.classList.add('ix-toast-show');
    });

    setTimeout(function () {
      toast.classList.remove('ix-toast-show');
      setTimeout(function () { toast.remove(); }, 300);
    }, duration);
  }

  var _milestones = [5, 10, 20, 50];
  var _milestoneMessages = {
    5:  '5개 종목 관심 등록!',
    10: '10개 도달! 포트폴리오가 탄탄해지고 있어요',
    20: '20개 돌파! 시장을 꿰뚫고 계시네요',
    50: '50개! 당신은 진정한 종목 헌터'
  };

  /* ══════════════════════════════════════════════════════════════
   *  5. 비교 페이지 카드 뒤집기 (3D Flip)
   * ══════════════════════════════════════════════════════════════ */

  function setupCompareFlip() {
    var grid = document.getElementById('compare-grid');
    if (!grid) return;

    // MutationObserver: 카드가 동적으로 렌더링된 후 플립 구조 적용
    var observer = new MutationObserver(function () {
      var cards = grid.querySelectorAll('.compare-card:not(.ix-flip-ready)');
      cards.forEach(function (card) {
        card.classList.add('ix-flip-ready');

        // 기존 콘텐츠를 front/back 으로 분리
        var header = card.querySelector('.compare-card-header');
        var body = card.querySelector('.compare-body');
        if (!header || !body) return;

        var chart = body.querySelector('.compare-chart');
        var metrics = body.querySelector('.compare-metrics');
        var oneliner = body.querySelector('.compare-oneliner');

        // front: header + chart
        var front = document.createElement('div');
        front.className = 'ix-flip-front';
        front.appendChild(header.cloneNode(true));
        if (chart) {
          var chartClone = chart.cloneNode(true);
          chartClone.style.padding = '12px 18px';
          front.appendChild(chartClone);
        }
        // flip hint
        var hint = document.createElement('div');
        hint.className = 'ix-flip-hint';
        hint.textContent = '\u21BB 클릭하면 상세 지표';
        front.appendChild(hint);

        // back: header(compact) + metrics + oneliner
        var back = document.createElement('div');
        back.className = 'ix-flip-back';
        var backHeader = header.cloneNode(true);
        backHeader.style.paddingBottom = '8px';
        back.appendChild(backHeader);
        if (metrics) back.appendChild(metrics.cloneNode(true));
        if (oneliner) back.appendChild(oneliner.cloneNode(true));
        var hintBack = document.createElement('div');
        hintBack.className = 'ix-flip-hint';
        hintBack.textContent = '\u21BB 클릭하면 차트';
        back.appendChild(hintBack);

        // replace card content with flip structure
        var inner = document.createElement('div');
        inner.className = 'ix-flip-inner';
        inner.appendChild(front);
        inner.appendChild(back);

        card.innerHTML = '';
        card.appendChild(inner);
        card.classList.add('ix-flip-card');

        card.addEventListener('click', function () {
          if (prefersReducedMotion()) {
            // 모션 감소 모드: 단순 토글
            var fr = card.querySelector('.ix-flip-front');
            var bk = card.querySelector('.ix-flip-back');
            if (fr && bk) {
              var showing = fr.style.display !== 'none';
              fr.style.display = showing ? 'none' : '';
              bk.style.display = showing ? '' : 'none';
            }
          } else {
            card.classList.toggle('flipped');
          }
        });
      });
    });

    observer.observe(grid, { childList: true, subtree: true });
  }

  /* ══════════════════════════════════════════════════════════════
   *  훅: app.js 의 기존 함수를 래핑
   * ══════════════════════════════════════════════════════════════ */

  function hookIntoApp() {
    // 1) setStatHTML 래핑 → 카운트 롤링 애니메이션
    if (typeof window.setStatHTML === 'function') {
      var _origSetStatHTML = window.setStatHTML;
      window.setStatHTML = function (id, html) {
        _origSetStatHTML(id, html);
        if (id === 'stat-total' || id === 'stat-strong') {
          animateStatRoll(id, html);
        }
      };
    }

    // 2) renderStockTable 래핑 → score 카운트업 + 오늘의 발견
    if (typeof window.renderStockTable === 'function') {
      var _origRenderStockTable = window.renderStockTable;
      window.renderStockTable = function (stocks) {
        _origRenderStockTable(stocks);

        // 점수 바 애니메이션
        requestAnimationFrame(function () {
          observeScoreBars();
        });

        // 오늘의 발견 카드
        var all = window._scanStocks || window.allStocks || stocks || [];
        renderDiscoveryCard(all);

        // confetti 판단
        if (Array.isArray(stocks)) {
          var total = stocks.length;
          var strong = 0;
          stocks.forEach(function (s) {
            if (s.Signal && (
              s.Signal.toUpperCase().includes('BREAKOUT') ||
              s.Signal.toUpperCase().includes('STRONG LEADER') ||
              s.Signal.toUpperCase().includes('MOMENTUM')
            )) strong++;
          });
          maybeConfetti(strong, total);
        }
      };
    }

    // 3) 퀵필터 칩 클릭 시 롤링 효과 강화
    var chips = document.getElementById('filter-chips');
    if (chips) {
      chips.addEventListener('click', function () {
        _prevStatTotal = null;
        _prevStatStrong = null;
      });
    }

    // 4) toggleWatchlist 래핑 → 별 파티클 + 마일스톤 토스트
    if (typeof window.toggleWatchlist === 'function') {
      var _origToggleWatchlist = window.toggleWatchlist;
      window.toggleWatchlist = function (ticker, ev) {
        var wasInList = window._watchlist && window._watchlist.has(ticker);
        _origToggleWatchlist(ticker, ev);
        var isNowInList = window._watchlist && window._watchlist.has(ticker);

        // 추가됐을 때만 효과
        if (!wasInList && isNowInList) {
          // 별 파티클: 클릭된 버튼 찾기
          var btn = ev && ev.currentTarget ? ev.currentTarget :
                    document.querySelector('.star-btn.starred');
          if (btn) starBurst(btn);

          // 마일스톤 체크
          var count = window._watchlist ? window._watchlist.size : 0;
          for (var i = 0; i < _milestones.length; i++) {
            if (count === _milestones[i]) {
              showToast(_milestoneMessages[_milestones[i]]);
              break;
            }
          }
        }
      };
    }

    // 5) 비교 페이지 카드 뒤집기 초기화
    setupCompareFlip();

    // 6) WOW pack: tilt+holo, stagger entrance, top10 spotlight, score pulse, click ripple
    setupWowPack();
  }

  /* ══════════════════════════════════════════════════════════════
   *  WOW PACK — 4종 반응형 액션
   * ══════════════════════════════════════════════════════════════ */
  function setupWowPack() {
    if (prefersReducedMotion()) return;
    injectWowStyles();
    var tbody = document.getElementById('stock-list');
    if (!tbody) return;

    var run = function () {
      var rows = tbody.querySelectorAll('tr');
      applyStaggerEntrance(rows);
      attachClickRipple(rows);
    };
    run();
    // 리스트 재렌더 감지 (innerHTML 교체 시)
    var mo = new MutationObserver(function () {
      // 디바운스
      if (window._wowMoTimer) clearTimeout(window._wowMoTimer);
      window._wowMoTimer = setTimeout(run, 30);
    });
    mo.observe(tbody, { childList: true });
  }

  function injectWowStyles() {
    if (document.getElementById('ix-wow-styles')) return;
    var css = ''
      + '@keyframes ixRowIn{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:translateY(0)}}'
      + '@keyframes ixGoldSweep{0%{background-position:-200% 0}100%{background-position:200% 0}}'
      + '@keyframes ixScorePulse{0%,100%{box-shadow:0 0 0 0 rgba(255,200,60,0)}50%{box-shadow:0 0 16px 4px rgba(255,200,60,0.55)}}'
      + '.ix-row-in{animation:ixRowIn .55s cubic-bezier(0.22,1,0.36,1) both}'
      + '#stock-list tr.ix-top1 .score-num{animation:ixScorePulse 2.2s ease-in-out infinite}'
      + '.ix-ripple{position:fixed;border-radius:50%;pointer-events:none;background:radial-gradient(circle,rgba(120,180,255,0.55),rgba(120,180,255,0) 70%);transform:translate(-50%,-50%) scale(0);animation:ixRippleExpand .55s ease-out forwards;z-index:9999}'
      + '@keyframes ixRippleExpand{to{transform:translate(-50%,-50%) scale(28);opacity:0}}';
    var st = document.createElement('style');
    st.id = 'ix-wow-styles';
    st.textContent = css;
    document.head.appendChild(st);
  }

  function applyStaggerEntrance(rows) {
    rows.forEach(function (r, i) {
      if (r.dataset.ixIn) return;
      r.dataset.ixIn = '1';
      r.style.animationDelay = Math.min(i * 35, 600) + 'ms';
      r.classList.add('ix-row-in');
    });
  }

  function applyTop10Spotlight(rows) {
    rows.forEach(function (r, i) {
      r.classList.remove('ix-top10', 'ix-top1');
      if (i === 0) r.classList.add('ix-top1');
      if (i < 10) r.classList.add('ix-top10');
    });
  }

  function attachTiltHolo(rows) {
    rows.forEach(function (r) {
      if (r.dataset.ixTilt) return;
      r.dataset.ixTilt = '1';
      // 홀로그램 오버레이 삽입 (첫 td 안에 절대위치)
      var firstTd = r.querySelector('td');
      if (firstTd && !r.querySelector('.ix-holo')) {
        var holo = document.createElement('span');
        holo.className = 'ix-holo';
        r.style.position = 'relative';
        r.appendChild(holo);
      }
      r.addEventListener('mousemove', function (e) {
        var rect = r.getBoundingClientRect();
        var px = (e.clientX - rect.left) / rect.width;
        var py = (e.clientY - rect.top) / rect.height;
        var rx = (py - 0.5) * -4;  // 약한 틸트
        var ry = (px - 0.5) * 6;
        r.style.transform = 'perspective(900px) rotateX(' + rx.toFixed(2) + 'deg) rotateY(' + ry.toFixed(2) + 'deg)';
        r.style.setProperty('--mx', (px * 100) + '%');
        r.style.setProperty('--my', (py * 100) + '%');
        r.classList.add('ix-tilt-hover');
      });
      r.addEventListener('mouseleave', function () {
        r.style.transform = '';
        r.classList.remove('ix-tilt-hover');
      });
    });
  }

  function attachClickRipple(rows) {
    rows.forEach(function (r) {
      if (r.dataset.ixRipple) return;
      r.dataset.ixRipple = '1';
      r.addEventListener('click', function (e) {
        // 별 버튼/액션버튼 클릭은 제외
        var t = e.target;
        if (t.closest && (t.closest('.star-btn') || t.closest('button') || t.closest('a'))) return;
        var ripple = document.createElement('span');
        ripple.className = 'ix-ripple';
        ripple.style.left = e.clientX + 'px';
        ripple.style.top = e.clientY + 'px';
        ripple.style.width = ripple.style.height = '24px';
        document.body.appendChild(ripple);
        setTimeout(function () { ripple.remove(); }, 600);
      }, { passive: true });
    });
  }

  function pulseHighScores(rows) {
    rows.forEach(function (r) {
      var num = r.querySelector('.score-num');
      if (!num) return;
      var v = parseFloat((num.textContent || '0').replace(/[^\d.\-]/g, '')) || 0;
      if (v >= 85) r.classList.add('ix-top1'); // 85+는 톱1 펄스 공유
    });
  }

  /* ══════════════════════════════════════════════════════════════
   *  6. 이스터에그 — 날짜·시간 기반 연출
   *     접속 시각(Asia/Seoul)을 보고 특정 날짜·장 시작에 깜짝 연출.
   *     핵심 스캐너 기능과 완전히 격리(전체 try/catch) — 실패해도 무해.
   * ══════════════════════════════════════════════════════════════ */

  // 음력 명절 등은 양력 날짜가 매년 바뀌므로 'MM-DD' 키로 하드코딩.
  // 새 연도가 시작되면 설날·추석·부처님오신날 날짜만 갱신하면 됨.
  var EGG_DATES = {
    '01-01': { effect: 'confetti', msg: '🎉 새해 복 많이 받으세요!' },
    '12-25': { effect: 'snow',     msg: '🎄 메리 크리스마스!' },
    '12-31': { effect: 'confetti', msg: '🎊 올 한 해 수고 많으셨어요' },
    // 2026 음력 명절 (매년 갱신 필요)
    '02-17': { effect: 'confetti', msg: '🎊 설날 복 많이 받으세요!' },
    '09-25': { effect: 'toast',    msg: '🌕 풍요로운 한가위 되세요' }
  };

  /** 현재 시각을 Asia/Seoul 기준으로 분해. PC 시계가 틀려도 그대로 사용. */
  function seoulNow() {
    var parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit',
      day: '2-digit', hour: '2-digit', minute: '2-digit',
      weekday: 'short', hour12: false
    }).formatToParts(new Date());
    var o = {};
    parts.forEach(function (p) { o[p.type] = p.value; });
    if (o.hour === '24') o.hour = '00';  // 일부 환경에서 자정을 24로 표기
    return o;
  }

  /** 세션당 1회만 — 이미 본 연출이면 false. */
  function eggSeen(key) {
    try {
      if (sessionStorage.getItem('ix_egg_' + key)) return true;
      sessionStorage.setItem('ix_egg_' + key, '1');
      return false;
    } catch (e) {
      return false;  // sessionStorage 불가 환경 — 그냥 보여줌
    }
  }

  /** 화면 가득 떨어지는 눈 연출 (약 8초). */
  function snowfall() {
    if (prefersReducedMotion()) return;
    var container = document.createElement('div');
    container.className = 'ix-snow-container';
    container.setAttribute('aria-hidden', 'true');
    var flakes = ['❄', '❅', '❆'];
    for (var i = 0; i < 40; i++) {
      var flake = document.createElement('span');
      flake.className = 'ix-snowflake';
      flake.textContent = flakes[i % flakes.length];
      flake.style.left = (Math.random() * 100) + 'vw';
      flake.style.fontSize = (8 + Math.random() * 14) + 'px';
      flake.style.opacity = (0.4 + Math.random() * 0.6).toFixed(2);
      flake.style.animationDuration = (5 + Math.random() * 5) + 's';
      flake.style.animationDelay = (Math.random() * 5) + 's';
      container.appendChild(flake);
    }
    document.body.appendChild(container);
    setTimeout(function () { container.remove(); }, 13000);
  }

  function runEasterEgg() {
    try {
      var now = seoulNow();
      var mmdd = now.month + '-' + now.day;
      var hour = parseInt(now.hour, 10);
      var minute = parseInt(now.minute, 10);
      var isWeekday = ['Sat', 'Sun'].indexOf(now.weekday) === -1;

      // 1) 날짜 연출
      var egg = EGG_DATES[mmdd];
      if (egg && !eggSeen(now.year + '-' + mmdd)) {
        if (egg.effect === 'confetti') confetti(document.body, 40);
        else if (egg.effect === 'snow') snowfall();
        showToast(egg.msg, 4000);
        return;  // 날짜 연출과 장 시작 연출이 겹치지 않게
      }

      // 2) 장 시작 연출 — 평일 09:00~09:04
      if (isWeekday && hour === 9 && minute < 5 &&
          !eggSeen(now.year + '-' + mmdd + '-open')) {
        showToast('🔔 장이 열렸습니다. 오늘도 좋은 매매 되세요!', 4000);
      }
    } catch (e) {
      /* 이스터에그 실패는 무시 — 핵심 기능에 영향 없음 */
    }
  }

  /* ── 초기화 ──────────────────────────────────────────────── */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', hookIntoApp);
  } else {
    hookIntoApp();
  }

  // 이스터에그는 핵심 훅과 분리하여 별도 실행 (실패 격리)
  setTimeout(runEasterEgg, 1200);

})();
