
"""
utils/telegram_notifier.py
텔레그램 알림 시스템

지원 알림 유형:
  - 리밸런싱 완료 (종목명, 수량, 비중 변화)
  - 섹터 전환 감지 (상위 섹터 변경)
  - 이벤트 드리븐 포지션 (공시 기반 진입/청산)
  - 일반 시스템 알림

설정:
  환경변수 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  또는 생성자 인자로 직접 전달
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class TelegramNotifier:
    """
    텔레그램 Bot API 기반 알림 발송기.

    사용 예:
        notifier = TelegramNotifier()
        notifier.send_rebalance_report(result)
        notifier.send_sector_rotation(top_sectors, prev_sectors)
        notifier.send_event_trade(event_info)
    """

    TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

    # 재시도 설정
    MAX_RETRY   = 3
    RETRY_DELAY = 2  # seconds

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id:   Optional[str] = None,
    ):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id   = chat_id   or os.getenv("TELEGRAM_CHAT_ID",   "")
        self._enabled  = bool(self.bot_token and self.chat_id)

        if not self._enabled:
            logger.info(
                "[Telegram] 비활성: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정"
            )
        elif not self.chat_id:
            logger.warning("[Notify] telegram disabled_invalid_chat — chat_id 미설정")

    # ── 공개 인터페이스 ──────────────────────────────────────────────

    def send_rebalance_report(self, result) -> bool:
        """
        리밸런싱 완료 알림.

        Parameters
        ----------
        result : RebalanceResult (engine/rebalancer.py)
                 .executed_at, .actions, .total_drift, .rebalanced 속성 필요
        """
        if not self._enabled or not getattr(result, 'rebalanced', False):
            return False

        actions = getattr(result, 'actions', [])
        drift   = getattr(result, 'total_drift', 0.0)
        ts      = getattr(result, 'executed_at', datetime.now().strftime("%Y-%m-%d %H:%M"))

        lines = [
            "◆ ━━━━━━━━━━━━━━━ ◆",
            "",
            f"  ⚖️  <b>리밸런싱 완료</b>",
            "",
            f"  「 <b>{len(actions)}종목</b> 」 드리프트 {drift:.2%}",
        ]

        for act in actions[:5]:
            code   = act.get('code', '')
            name   = act.get('name', code)
            action = act.get('action', '')
            qty    = act.get('qty', 0)
            w_from = act.get('weight_before', 0)
            w_to   = act.get('weight_after',  0)
            w_diff = (w_to - w_from) * 100
            sign   = "+" if w_diff >= 0 else ""
            direction = "매수" if action == "BUY" else "매도"
            d_emoji = "🟢" if action == "BUY" else "🔴"
            lines.append(
                f"\n  {d_emoji} <b>{name}</b>  {direction} {qty:,}주"
                f"\n     {w_from:.1%} → {w_to:.1%}  {sign}{w_diff:.1f}%p"
            )

        if len(actions) > 5:
            lines.append(f"\n     외 {len(actions) - 5}건")

        lines += [
            "",
            "◆ ━━━━━━━━━━━━━━━ ◆",
            f"  ⏱ {ts}",
        ]

        return self._send("\n".join(lines))

    def send_sector_rotation(
        self,
        top_sectors:  List[str],
        prev_sectors: List[str],
        scores:       Optional[Dict[str, float]] = None,
    ) -> bool:
        """
        섹터 로테이션 전환 알림.

        Parameters
        ----------
        top_sectors  : 신규 상위 2개 섹터
        prev_sectors : 이전 상위 2개 섹터
        scores       : {섹터명: 모멘텀 점수} 딕셔너리
        """
        if not self._enabled:
            return False

        changed = set(top_sectors) != set(prev_sectors)
        status  = "섹터 전환" if changed else "섹터 유지"

        rotate_emoji = "🔄" if changed else "📌"
        lines = [
            "◆ ━━━━━━━━━━━━━━━ ◆",
            "",
            f"  {rotate_emoji}  <b>{status}</b>",
        ]

        if changed:
            exited  = [s for s in prev_sectors if s not in top_sectors]
            entered = [s for s in top_sectors  if s not in prev_sectors]
            lines.append("")
            for s in exited:
                sc = f"  {scores[s]:.2f}" if scores and s in scores else ""
                lines.append(f"  🔴 − {s}{sc}")
            for s in entered:
                sc = f"  {scores[s]:.2f}" if scores and s in scores else ""
                lines.append(f"  🟢 + {s}{sc}")
        else:
            lines.append("")
            for s in top_sectors:
                sc = f"  {scores[s]:.2f}" if scores and s in scores else ""
                lines.append(f"     · {s}{sc}")

        if scores:
            lines.append("")
            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            for i, (sec, sc) in enumerate(ranked[:6], 1):
                marker = "  ▸" if i <= 2 else "   "
                lines.append(f"{marker} {i}. {sec}  {sc:.3f}")

        lines += [
            "",
            "◆ ━━━━━━━━━━━━━━━ ◆",
            f"  ⏱ {datetime.now().strftime('%m/%d %H:%M')}",
        ]

        return self._send("\n".join(lines))

    def send_event_trade(
        self,
        action:       str,   # "ENTER" | "EXIT"
        code:         str,
        name:         str,
        disclosure:   Dict,
        ai_analysis:  Optional[Dict] = None,
        pnl_pct:      Optional[float] = None,
    ) -> bool:
        """
        공시 이벤트 드리븐 매매 알림.

        Parameters
        ----------
        action      : "ENTER" 진입 | "EXIT" 청산
        code        : 종목코드
        name        : 종목명
        disclosure  : {title, type, datetime}
        ai_analysis : OpenAI AI 분석 결과 {sentiment, confidence, rationale}
        pnl_pct     : 청산 수익률 (EXIT 시)
        """
        if not self._enabled:
            return False

        title_emoji = "📢" if action == "ENTER" else "🔔"
        title   = "공시 진입" if action == "ENTER" else "공시 청산"
        disc_dt = disclosure.get('datetime', '')

        lines = [
            "◆ ━━━━━━━━━━━━━━━ ◆",
            "",
            f"  {title_emoji}  <b>{name}</b>  {code}",
            "",
            f"  「 <b>{title}</b> 」",
            "",
            f"  ▎ 📄 {disclosure.get('title', '')[:50]}",
            f"  ▎ 📎 {disclosure.get('type', '')}  {disc_dt}",
        ]

        if ai_analysis:
            sentiment  = ai_analysis.get('sentiment', 'NEUTRAL')
            confidence = ai_analysis.get('confidence', 0.0)
            rationale  = ai_analysis.get('rationale', '')
            s_map = {"POSITIVE": "긍정", "NEGATIVE": "부정", "NEUTRAL": "중립"}
            s_emoji = {"POSITIVE": "🟢", "NEGATIVE": "🔴", "NEUTRAL": "⚪"}
            lines += [
                "",
                f"  {s_emoji.get(sentiment, '⚪')} AI  {s_map.get(sentiment, sentiment)} · 확신 {confidence:.0%}",
                f"     {rationale[:80]}",
            ]

        if action == "EXIT" and pnl_pct is not None:
            sign = "+" if pnl_pct >= 0 else ""
            r_emoji = "🟢" if pnl_pct >= 0 else "🔴"
            lines.append(f"\n  {r_emoji} 수익률  <b>{sign}{pnl_pct:.2f}%</b>")

        lines += [
            "",
            "◆ ━━━━━━━━━━━━━━━ ◆",
            f"  ⏱ {datetime.now().strftime('%H:%M')}",
        ]

        return self._send("\n".join(lines))

    def send_portfolio_report(
        self,
        balance:   Dict,
        positions: Dict,
        season:    Optional[Dict] = None,
        mode:      str = "실전투자",
    ) -> bool:
        """
        장마감 포트폴리오 리포트 발송.

        Parameters
        ----------
        balance   : api.get_balance() 반환값
                    {cash, total_equity, total_value, total_profit, profit_rate,
                     holdings: [{code,name,qty,avg_price,current_price,eval_amount,pnl,pnl_pct}]}
        positions : engine.positions  {code: position_dict}
        season    : {name, start_date, start_amount}  — None이면 섹션 생략
        mode      : "모의투자" | "실전투자"
        """
        if not self._enabled:
            return False

        now      = datetime.now()
        ts       = now.strftime("%m/%d %H:%M")
        mode_tag = "실전" if mode == "실전투자" else "모의"

        cash         = float(balance.get("cash", 0))
        total_equity = float(balance.get("total_equity", balance.get("total_value", cash)))
        total_profit = float(balance.get("total_profit", 0))
        profit_rate  = float(balance.get("profit_rate", 0))
        holdings     = balance.get("holdings", []) or []

        eval_pnl = sum(
            float(h.get("pnl", 0) or h.get("eval_profit", 0))
            for h in holdings
        )
        eval_pnl_pct = (eval_pnl / (total_equity - eval_pnl) * 100) if (total_equity - eval_pnl) > 0 else 0.0
        cash_pct = (cash / total_equity * 100) if total_equity > 0 else 100.0

        eval_sign = "+" if eval_pnl >= 0 else ""

        eval_emoji = "🟢" if eval_pnl >= 0 else "🔴"
        lines = [
            "◆ ━━━━━━━━━━━━━━━ ◆",
            "",
            f"  💼  <b>포트폴리오</b>  {mode_tag}",
            "",
            f"  「 <b>{total_equity:,.0f}원</b> 」",
            f"  {eval_emoji} {eval_sign}{eval_pnl:,.0f}원  {eval_sign}{eval_pnl_pct:.2f}%",
            "",
            f"  ▎ 💵 현금  {cash:,.0f}원  {cash_pct:.1f}%",
        ]

        if season:
            s_name    = season.get("name", "시즌")
            s_amount  = float(season.get("start_amount", 0) or 0)
            if s_amount > 0:
                s_profit  = total_equity - s_amount
                s_pct     = (s_profit / s_amount * 100)
                s_sign    = "+" if s_profit >= 0 else ""
                lines.append(f"  ▎ 📅 {s_name}  {s_sign}{s_profit:,.0f}원  {s_sign}{s_pct:.2f}%")

        kr_holdings = [
            h for h in holdings
            if not h.get("market", "").upper().startswith("US")
               and not h.get("currency", "KRW").upper().startswith("USD")
        ]
        if kr_holdings:
            lines.append(f"\n  📋 보유 {len(kr_holdings)}종목")
            for h in kr_holdings[:5]:
                name      = h.get("name", h.get("code", ""))
                qty       = int(h.get("qty", h.get("hldg_qty", 0)) or 0)
                cur       = float(h.get("current_price", 0) or 0)
                pnl       = float(h.get("pnl", 0) or 0)
                pnl_pct   = float(h.get("pnl_pct", 0) or 0)
                pnl_sign  = "+" if pnl >= 0 else ""
                h_emoji   = "🟢" if pnl >= 0 else "🔴"
                lines.append(
                    f"  {h_emoji} <b>{name}</b>  {qty:,}주"
                    f"\n     {cur * qty:,.0f}원  {pnl_sign}{pnl_pct:.2f}%"
                )
            if len(kr_holdings) > 5:
                lines.append(f"     외 {len(kr_holdings) - 5}종목")

        buys = sum(1 for p in positions.values() if p.get("source") == "auto")
        if buys:
            lines.append(f"\n  🤖 자동매매  {buys}종목 보유중")

        lines += [
            "",
            "◆ ━━━━━━━━━━━━━━━ ◆",
            f"  ⏱ {ts}",
        ]

        return self._send("\n".join(lines))

    def send_daily_closing_report(
        self,
        balance: dict,
        trades_today: list = None,
        engine_state: dict = None,
        holdings: list = None,
    ) -> bool:
        """장마감 일일 성과 리포트 발송."""
        if not self._enabled:
            return False
        now = datetime.now().strftime("%m/%d %H:%M")
        trades = trades_today or []
        holdings = holdings or balance.get("holdings", []) or []
        engine_state = engine_state or {}

        total_equity = float(
            balance.get("total_equity")
            or balance.get("total_value")
            or balance.get("eval_amount")
            or 0
        )
        cash = float(balance.get("cash", 0) or 0)

        raw_daily_pnl = engine_state.get("daily_pnl", None)
        if raw_daily_pnl is None:
            pnl_amount = sum(float(t.get("pnl", 0) or 0) for t in trades)
            pnl_pct = (pnl_amount / total_equity * 100) if total_equity > 0 else 0.0
        else:
            raw_daily_pnl = float(raw_daily_pnl or 0)
            if abs(raw_daily_pnl) < 10 and total_equity > 0:
                pnl_amount = raw_daily_pnl * total_equity
                pnl_pct = raw_daily_pnl * 100
            else:
                pnl_amount = raw_daily_pnl
                pnl_pct = (pnl_amount / total_equity * 100) if total_equity > 0 else 0.0

        total = len(trades)
        wins = sum(1 for t in trades if float(t.get("pnl", 0) or 0) >= 0)
        losses = max(total - wins, 0)
        win_rate = (wins / total * 100) if total else 0.0
        avg_pct = (
            sum(float(t.get("pnl_pct", 0) or 0) for t in trades) / total
            if total else 0.0
        )

        pnl_sign = "+" if pnl_amount >= 0 else ""
        pct_sign = "+" if pnl_pct >= 0 else ""
        avg_sign = "+" if avg_pct >= 0 else ""

        pnl_emoji = "🟢" if pnl_amount >= 0 else "🔴"
        lines = [
            "◆ ━━━━━━━━━━━━━━━ ◆",
            "",
            f"  📊  <b>장마감 리포트</b>",
            "",
            f"  {pnl_emoji} 「 <b>{pnl_sign}{pnl_amount:,.0f}원</b> 」 {pct_sign}{pnl_pct:.2f}%",
            "",
            f"  ▎ 📋 {total}건  {wins}승 {losses}패",
            f"  ▎ 📈 승률  {win_rate:.0f}%",
            f"  ▎ 📊 평균  {avg_sign}{avg_pct:.2f}%",
        ]

        if trades:
            best = max(trades, key=lambda t: float(t.get("pnl_pct", 0) or 0))
            worst = min(trades, key=lambda t: float(t.get("pnl_pct", 0) or 0))

            def _trade_brief(emoji: str, label: str, trade: dict) -> str:
                tname = trade.get("name") or trade.get("ticker") or "-"
                pct = float(trade.get("pnl_pct", 0) or 0)
                amount = float(trade.get("pnl", 0) or 0)
                s = "+" if amount >= 0 else ""
                ps = "+" if pct >= 0 else ""
                return f"  {emoji} {label}  {tname}  {ps}{pct:.2f}%  {s}{amount:,.0f}원"

            lines += ["", _trade_brief("▲", "최고", best), _trade_brief("▼", "최저", worst)]

        lines += [
            "",
            "◆ ━━━━━━━━━━━━━━━ ◆",
            f"  💰 보유 {len(holdings)}종목 · 예수금 {cash:,.0f}원",
            f"  📊 평가 <b>{total_equity:,.0f}원</b> · {now}",
        ]
        return self._send("\n".join(lines))

    def send_message(self, text: str) -> bool:
        """일반 텍스트 메시지 발송."""
        return self._send(text)

    def send_emergency(self, daily_pnl: float, positions_closed: int, reason: str = "EMERGENCY_STOP"):
        """
        Emergency Stop 발동 시 긴급 알림 전송

        Args:
            daily_pnl: 일일 PnL (소수점, 예: -0.02 = -2%)
            positions_closed: 강제 청산된 포지션 수
            reason: 중단 사유
        """
        msg = (
            f"◆ ━━━━━━━━━━━━━━━ ◆\n"
            f"\n"
            f"  🚨  <b>긴급 정지</b>\n"
            f"\n"
            f"  ▎ 📉 일일 손실  <b>{daily_pnl:.2%}</b>\n"
            f"  ▎ 🔒 강제 청산  {positions_closed}건\n"
            f"\n"
            f"  ⛔ 당일 자동매매 중단\n"
            f"\n"
            f"◆ ━━━━━━━━━━━━━━━ ◆\n"
            f"  ⏱ {datetime.now().strftime('%H:%M:%S')}"
        )
        try:
            self._send(msg)
        except Exception as e:
            logger.error(f"[TelegramNotifier] Emergency 알림 전송 실패: {e}")

    def send_trade_entry(
        self,
        strategy: str,
        ticker: str,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        qty: int,
        mode_str: str = "",
        reason: str = "",
        atr: float = 0.0,
        rr: float = 0.0,
        tp2_price: float = 0.0,
    ) -> bool:
        """매수 체결 알림 (MR4, SWING-MOM 공용)."""
        if not self._enabled:
            return False
        now = datetime.now().strftime("%H:%M")
        total = entry_price * qty

        sl_pct = (sl_price / entry_price - 1) * 100 if entry_price else 0
        tp_pct = (tp_price / entry_price - 1) * 100 if entry_price else 0

        lines = [
            "◆ ━━━━━━━━━━━━━━━ ◆",
            "",
            f"  📈  <b>{ticker}</b>  매수",
            "",
            f"  「 <b>{entry_price:,.0f}원</b> 」 × {qty:,}주",
            f"     총 {total:,.0f}원",
            "",
            f"  ▎ 🛡 손절  {sl_price:,.0f}원   {sl_pct:+.1f}%",
        ]
        if tp2_price > 0:
            tp2_pct = (tp2_price / entry_price - 1) * 100 if entry_price else 0
            lines.append(f"  ▎ 🎯 목표  {tp_price:,.0f}원   {tp_pct:+.1f}%")
            lines.append(f"  ▎ 🎯 목표2  {tp2_price:,.0f}원  {tp2_pct:+.1f}%")
        else:
            lines.append(f"  ▎ 🎯 목표  {tp_price:,.0f}원   {tp_pct:+.1f}%")
        footer = f"  ⏱ {strategy} · {now}"
        if mode_str:
            footer += f" · {mode_str}"
        lines += [
            "",
            "◆ ━━━━━━━━━━━━━━━ ◆",
            footer,
        ]
        if reason:
            lines.append(f"  {reason}")
        return self._send("\n".join(lines))

    def send_trade_exit(
        self,
        strategy: str,
        ticker: str,
        exit_reason: str,
        entry_price: float,
        exit_price: float,
        qty: int,
        pnl: float,
        pnl_pct: float,
        bars_held: int = 0,
        mode_str: str = "",
        partial: bool = False,
        total_pnl: float = None,
        total_pnl_pct: float = None,
    ) -> bool:
        """청산 알림 (MR4, SWING-MOM 공용)."""
        if not self._enabled:
            return False
        now = datetime.now().strftime("%H:%M")

        # 분할매도 최종 청산: 합산 PnL을 메인으로 표시
        _has_total = total_pnl is not None and total_pnl_pct is not None
        if _has_total:
            display_pnl = total_pnl
            display_pnl_pct = total_pnl_pct
        else:
            display_pnl = pnl
            display_pnl_pct = pnl_pct

        pnl_sign = "+" if display_pnl_pct >= 0 else ""
        partial_str = "분할 " if partial else ""

        type_map = {
            "SL":             "손절",
            "full_stop":      "손절",
            "TP":             "목표 도달",
            "full_tp":        "목표 도달",
            "tp1":            "1차 목표",
            "tp2":            "2차 목표",
            "recovery_tp":    "회복 청산",
            "trail_stop":     "추적 청산",
            "TimeStop":       "시간 청산",
            "time_stop":      "시간 청산",
            "time_stop_l2":   "시간 청산",
            "EOD":            "장마감",
            "eod":            "장마감",
            "eod_l2":         "장마감",
            "breakeven_stop": "본전 청산",
        }
        type_label = type_map.get(exit_reason, str(exit_reason or "청산"))
        result_emoji = "🟢" if display_pnl_pct >= 0 else "🔴"
        sell_emoji = "📈" if display_pnl_pct >= 0 else "📉"
        footer = f"  ⏱ {strategy} · {now}"
        if mode_str:
            footer += f" · {mode_str}"
        lines = [
            "◆ ━━━━━━━━━━━━━━━ ◆",
            "",
            f"  {sell_emoji}  <b>{ticker}</b>  {partial_str}매도",
            "",
            f"  {result_emoji} 「 <b>{pnl_sign}{display_pnl_pct:.2f}%</b> 」 {pnl_sign}{display_pnl:,.0f}원",
        ]
        if _has_total:
            lines.append(f"  ↳ 합산 (분할매도 포함)")
        lines.extend([
            "",
            f"  ▎ 📥 진입  {entry_price:,.0f}원",
            f"  ▎ 📤 청산  {exit_price:,.0f}원",
            "",
            f"  {result_emoji} {type_label} · {bars_held}봉 보유",
            "",
            "◆ ━━━━━━━━━━━━━━━ ◆",
            footer,
        ])
        return self._send("\n".join(lines))

    def is_enabled(self) -> bool:
        return self._enabled

    def send_scan_results(
        self,
        picks: list,
        regime: str = "",
        regime_conf: float = 0.0,
        universe_size: int = 0,
        engine_state: dict = None,
        sector_info: dict = None,
        pipeline_stats: dict = None,
    ) -> bool:
        """S2 스캔 결과 종합 브리핑 텔레그램 발송 (v2).

        Args:
            picks: 스캔 결과 종목 리스트
            regime: 현재 시장 국면 (BULL_TREND, BEAR_TREND 등)
            regime_conf: 국면 신뢰도 (0~1)
            universe_size: 스캔 대상 유니버스 크기
            engine_state: 엔진 상태 (daily_pnl, positions 등)
            sector_info: 섹터 정보 (top_sectors, scores 등)
            pipeline_stats: 파이프라인 통계 (s2_pipeline 정보)
        """
        if not self._enabled:
            return False
        if not picks:
            return False

        try:
            from utils.report_formatter import format_s2_briefing
            # 종목별 + 시장 뉴스 조회
            news = None
            try:
                from utils.naver_news import fetch_news_for_picks, fetch_market_news
                news = fetch_news_for_picks(picks, max_per_stock=1, max_total=3)
                market = fetch_market_news(display=2)
                if market:
                    news["_market"] = market
            except Exception:
                pass
            text = format_s2_briefing(
                picks=picks,
                regime=regime,
                regime_conf=regime_conf,
                universe_size=universe_size,
                engine_state=engine_state,
                sector_info=sector_info,
                pipeline_stats=pipeline_stats,
                news=news,
            )
        except Exception:
            now = datetime.now().strftime("%H:%M")
            text = (
                f"◆ ━━━━━━━━━━━━━━━ ◆\n"
                f"\n"
                f"  🔍  <b>스캔 완료</b>\n"
                f"\n"
                f"  ▎ 🌐 {regime} · 신뢰 {regime_conf:.0%}\n"
                f"  ▎ 📊 유니버스 {universe_size} → 후보 <b>{len(picks)}</b>\n"
                f"\n"
                f"◆ ━━━━━━━━━━━━━━━ ◆\n"
                f"  ⏱ {now}"
            )

        return self._send(text)

    def send_watchlist_briefing(
        self,
        candidates: List[Dict],
        strategy: str = "SWING-MOM",
        open_positions: int = 0,
        max_positions: int = 6,
    ) -> bool:
        """30분 주기 매수 고려 종목 브리핑.

        Parameters
        ----------
        candidates : [{ticker, name, rank_score, m_score, s_score}, ...]
        strategy   : 전략명
        open_positions : 현재 보유 종목 수
        max_positions  : 최대 보유 종목 수
        """
        if not self._enabled:
            return False
        if not candidates:
            return False

        now = datetime.now().strftime("%H:%M")
        slots = max(max_positions - open_positions, 0)

        lines = [
            "◆ ━━━━━━━━━━━━━━━ ◆",
            "",
            f"  🔭  <b>매수 후보 브리핑</b>",
            "",
            f"  「 <b>{len(candidates)}종목</b> 」 대기중",
            f"  ▎ 📂 보유 {open_positions}/{max_positions}  ▎ 🎰 슬롯 {slots}",
            "",
        ]

        for i, c in enumerate(candidates[:8], 1):
            name   = c.get("name") or c.get("ticker", "?")
            ticker = c.get("ticker", "")
            rank   = float(c.get("rank_score", 0))
            m_sc   = float(c.get("m_score", 0))
            s_sc   = float(c.get("s_score", 0))

            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f" {i}."
            lines.append(
                f"  {medal} <b>{name}</b>"
                f"\n     R={rank:.2f}  M={m_sc:.2f}  S={s_sc:.2f}"
            )

        if len(candidates) > 8:
            lines.append(f"\n     외 {len(candidates) - 8}종목")

        lines += [
            "",
            "◆ ━━━━━━━━━━━━━━━ ◆",
            f"  ⏱ {strategy} · {now}",
        ]

        return self._send("\n".join(lines))

    def update_credentials(self, bot_token: str, chat_id: str):
        """런타임 중 자격증명 업데이트 (설정 탭 저장 시 호출)."""
        self.bot_token = bot_token
        self.chat_id   = chat_id
        self._enabled  = bool(bot_token and chat_id)
        if self._enabled:
            logger.info("[Telegram] 자격증명 업데이트 완료 — 알림 활성화")

    # ── 내부 발송 ────────────────────────────────────────────────────

    def _send(self, text: str) -> bool:
        """
        텔레그램 API 호출 (최대 MAX_RETRY 재시도).
        실패 시 예외 대신 False 반환 (매매 루프 블로킹 방지).
        """
        if not self._enabled:
            return False
        if not HAS_REQUESTS:
            logger.warning("[Telegram] requests 미설치 — pip install requests")
            return False

        url     = self.TELEGRAM_API.format(token=self.bot_token)
        payload = {
            "chat_id":    self.chat_id,
            "text":       text,
            "parse_mode": "HTML",
        }

        for attempt in range(1, self.MAX_RETRY + 1):
            try:
                resp = requests.post(url, json=payload, timeout=10)
                if resp.status_code == 200:
                    logger.info(f"[Telegram] 발송 성공 ({len(text)}자)")
                    return True
                # Rate limit (429) → 지연 후 재시도
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", self.RETRY_DELAY * attempt))
                    logger.warning(f"[Telegram] Rate limit — {retry_after}초 대기")
                    time.sleep(retry_after)
                    continue
                logger.warning(
                    f"[Telegram] 발송 실패 (시도 {attempt}/{self.MAX_RETRY}): "
                    f"HTTP {resp.status_code} — {resp.text[:200]}"
                )
            except Exception as exc:
                logger.warning(
                    f"[Telegram] 네트워크 오류 (시도 {attempt}/{self.MAX_RETRY}): {exc}"
                )
            if attempt < self.MAX_RETRY:
                time.sleep(self.RETRY_DELAY * attempt)

        logger.error(f"[Telegram] {self.MAX_RETRY}회 재시도 후 최종 실패")
        return False


# ── 전역 싱글톤 (auto_loop, rebalancer에서 import해서 사용) ──────────
_notifier: Optional[TelegramNotifier] = None


def get_notifier() -> TelegramNotifier:
    """전역 TelegramNotifier 싱글톤 반환."""
    global _notifier
    if _notifier is None:
        _notifier = TelegramNotifier()
    return _notifier


def configure_notifier(bot_token: str, chat_id: str) -> TelegramNotifier:
    """설정 탭에서 자격증명 업데이트 시 호출."""
    global _notifier
    if _notifier is None:
        _notifier = TelegramNotifier(bot_token=bot_token, chat_id=chat_id)
    else:
        _notifier.update_credentials(bot_token, chat_id)
    return _notifier
