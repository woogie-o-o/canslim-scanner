
"""
journal/trading_journal.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TradingJournal — 매매 일지 조회 클래스

[역할 구분] 매매 일지 "조회/통계/기록" 클래스.
  - TradingJournal: DB + 인메모리 소스 통합 조회, KPI 계산, record_trade() 저장
  - dashboard.py, server.py, api_routes/journal.py, api_routes/account.py 에서 사용
  - ※ trade_journal.py (루트): 매매 "판단 근거" 기록 + Streamlit 탭 렌더링 — 별도 시스템

dashboard.py가 필요로 하는 인터페이스:
  · get_recent_entries(limit)   → 최근 매매 내역 (list[dict])
  · get_trade_history()         → 전체 완료 거래 (list[dict])
  · get_statistics()            → 종합 KPI (dict)
  · get_statistics_by_channel() → AUTO/MANUAL 채널별 KPI (dict)

데이터 소스 (우선순위):
  1. trading.db  → trading_journal 테이블 (auto_loop 기록)
  2. _LOOP_STATE["trade_log"] → 인메모리 실시간 로그 (오늘 거래)
  3. ScalpingEngine._trade_history → 엔진 인메모리 히스토리
"""
from __future__ import annotations

import os
import sqlite3
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# trading.db 기본 경로
_DEFAULT_DB = os.path.join(os.path.dirname(__file__), "..", "trading.db")


class TradingJournal:
    """
    매매 일지 조회 클래스.

    dashboard.py session_state에 저장되어 탭 전환 시 재사용됨.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        engine=None,
    ):
        """
        Args:
            db_path: trading.db 경로 (None이면 프로젝트 루트 기본값)
            engine:  ScalpingEngine 인스턴스 (선택 — 인메모리 히스토리 참조)
        """
        self.db_path = db_path or os.getenv("JOURNAL_DB_PATH", _DEFAULT_DB)
        self.engine  = engine  # 선택적 엔진 참조

    # ─────────────────────────────────────────────────────────────
    #  퍼블릭 인터페이스
    # ─────────────────────────────────────────────────────────────

    def get_recent_entries(self, limit: int = 20) -> List[Dict]:
        """
        최근 매매 내역 반환 (dashboard tab_journal 용).

        DB → 인메모리 순으로 데이터를 합쳐 최신 순 정렬.
        """
        rows = []

        # ① DB 조회
        rows += self._read_db_entries(limit * 2)

        # ② 인메모리 trade_log (오늘 실시간 거래)
        rows += self._read_loop_state_log()

        # ③ 엔진 _trade_history
        rows += self._read_engine_history()

        # 중복 제거 + 최신 순 정렬 + limit 적용
        # 체결 완료(sell)된 거래만 — buy/미체결 제외
        seen = set()
        deduped = []
        for r in sorted(rows, key=lambda x: x.get("timestamp", ""), reverse=True):
            if r.get("trade_type") != "sell":
                continue
            key = (r.get("code", ""), r.get("timestamp", ""))
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        return deduped[:limit]

    def get_trade_history(self) -> List[Dict]:
        """완료된 전체 거래 반환 (MetaLabeler 학습용)."""
        rows = self._read_db_entries(limit=5000)
        rows += self._read_engine_history()
        # ★ [PATCH] sell 완료 거래만 — buy 레코드는 quant_score가 return_rate로 오염돼 통계 왜곡
        return [r for r in rows if r.get("trade_type") == "sell" and r.get("return_rate") is not None]

    def get_statistics(self) -> Dict:
        """종합 KPI 계산."""
        trades = self.get_trade_history()
        return self._calc_stats(trades)

    def get_statistics_by_channel(self) -> Dict[str, Dict]:
        """AUTO / MANUAL 채널별 KPI."""
        trades = self.get_trade_history()
        auto_trades   = [t for t in trades if t.get("source", "").upper() in ("AUTO", "AGENT", "FALLBACK")]
        manual_trades = [t for t in trades if t.get("source", "").upper() in ("MANUAL", "HAND", "")]
        return {
            "AUTO":   self._calc_stats(auto_trades),
            "MANUAL": self._calc_stats(manual_trades),
        }

    def get_today_stats(self) -> Dict:
        """오늘 거래 KPI — /api/auto/state wins_today, pnl_today 용."""
        today = datetime.now().strftime("%Y-%m-%d")
        all_trades = self.get_trade_history()
        today_trades = [t for t in all_trades if str(t.get("timestamp", t.get("decision_date", "")))[:10] == today]
        wins   = sum(1 for t in today_trades if float(t.get("return_rate") or 0) > 0)
        losses = sum(1 for t in today_trades if float(t.get("return_rate") or 0) <= 0)
        pnl_pct = sum(float(t.get("return_rate") or 0) for t in today_trades)
        pnl_amt = sum(
            float(next((t.get(k) for k in ("pnl_amt", "profit", "pnl") if t.get(k) is not None), 0))
            for t in today_trades
        )
        return {
            "wins":     wins,
            "losses":   losses,
            "total":    len(today_trades),
            "pnl_pct":  round(pnl_pct, 4),
            "pnl_amt":  round(pnl_amt, 0),
            "win_rate": round(wins / len(today_trades), 4) if today_trades else 0.0,
        }

    # ─────────────────────────────────────────────────────────────
    #  TradeEvent — Codex US-04 (Fix #5 stage 1-2): canonical DTO
    #  기존 trades / trading_journal 테이블은 변경하지 않음.
    #  신규 trade_events 테이블만 추가하여 dual-write.
    # ─────────────────────────────────────────────────────────────

    def _create_trade_events_table(self, conn: sqlite3.Connection) -> None:
        """trade_events 테이블 생성 (idempotent)."""
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_ts    TEXT NOT NULL,
                ticker      TEXT NOT NULL,
                side        TEXT NOT NULL,
                entry_price REAL,
                exit_price  REAL,
                qty         INTEGER NOT NULL DEFAULT 0,
                pnl         REAL,
                pnl_pct     REAL,
                reason      TEXT,
                source      TEXT,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            )
            """
        )
        # 정렬/조회 성능 인덱스
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_trade_events_event_ts ON trade_events(event_ts)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_trade_events_ticker ON trade_events(ticker)"
        )

    def record_trade_event(self, event) -> None:
        """Canonical TradeEvent 를 trade_events 테이블에 기록.

        Args:
            event: journal.trade_event.TradeEvent 인스턴스.
        실패해도 raise 하지 않음 — record_trade 와 동일하게 logger.warning 만.
        """
        from journal.trade_event import TradeEvent  # 지연 import 로 순환 회피

        if not isinstance(event, TradeEvent):
            logger.warning(f"[TradeEvent] invalid type: {type(event).__name__}")
            return

        conn = None
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._create_trade_events_table(conn)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO trade_events (
                    event_ts, ticker, side, entry_price, exit_price,
                    qty, pnl, pnl_pct, reason, source
                ) VALUES (
                    :event_ts, :ticker, :side, :entry_price, :exit_price,
                    :qty, :pnl, :pnl_pct, :reason, :source
                )
                """,
                event.to_dict(),
            )
            conn.commit()
            logger.debug(
                f"[TradeEvent] insert OK ticker={event.ticker} side={event.side} "
                f"qty={event.qty} pnl_pct={event.pnl_pct}"
            )
        except Exception as e:
            logger.warning(f"[TradeEvent] record_trade_event 실패: {e}")
        finally:
            if conn:
                conn.close()

    # ─────────────────────────────────────────────────────────────
    #  기록 (ScalpingEngine → 매도 기록 저장)
    # ─────────────────────────────────────────────────────────────

    def record_trade(self, data: Dict, exit_type: Optional[str] = None, hold_minutes: Optional[int] = None) -> None:
        """
        ScalpingEngine에서 호출 — 매수/매도 기록을 trading_journal DB에 저장.
        매도 기록의 경우 return_rate, sell_rationale 등이 포함됨.

        Args:
            data:         매매 데이터 dict
            exit_type:    청산 유형 (SL_HIT / TP_HIT / TRAILING / ONEIL_HARD /
                          GAP_DOWN / NEWS_SELL / TIME_STOP / MANUAL), nullable
            hold_minutes: 보유 시간 (분), nullable
        """
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trading_journal (
                    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker                  TEXT NOT NULL,
                    stock_name              TEXT,
                    decision_date           TEXT,
                    decision_time           TEXT,
                    price                   REAL,
                    qty                     INTEGER DEFAULT 0,
                    quant_score             REAL,
                    trigger_type            TEXT,
                    decision                TEXT,
                    rejection_reason        TEXT,
                    stop_loss               REAL,
                    target_price            REAL,
                    technical_trend         TEXT,
                    volume_analysis         TEXT,
                    market_condition_impact TEXT,
                    agent_source            TEXT,
                    regime                  TEXT,
                    full_json_data          TEXT,
                    prefetch_keys           TEXT,
                    return_rate             REAL,
                    sell_rationale          TEXT,
                    buy_rationale           TEXT,
                    created_at              TEXT DEFAULT (datetime('now','localtime'))
                )
            """)
            # 마이그레이션: 누락 컬럼 추가
            for col, coltype in [
                ("qty", "INTEGER DEFAULT 0"),
                ("return_rate", "REAL"),
                ("sell_rationale", "TEXT"),
                ("buy_rationale", "TEXT"),
                ("note", "TEXT"),
                ("tags", "TEXT"),
                ("exit_type", "TEXT"),
                ("hold_minutes", "INTEGER"),
                ("buy_price", "REAL"),
                ("ai_confidence",   "REAL"),
                ("news_sentiment",  "REAL"),
                ("ai_decision_raw", "TEXT"),
                ("atr_pct",          "REAL DEFAULT 1.0"),
                ("session_code",     "INTEGER DEFAULT 2"),
                ("meta_probability", "REAL DEFAULT 0.0"),
                ("label_v1",         "INTEGER DEFAULT -1"),
                ("label_v2",         "INTEGER DEFAULT -1"),
                ("is_synthetic",     "INTEGER DEFAULT 0"),
                ("entry_reason_summary", "TEXT"),
                ("entry_reason_detail",  "TEXT"),
                ("exit_reason_summary",  "TEXT"),
                ("exit_reason_detail",   "TEXT"),
                ("entry_tags",           "TEXT"),
                ("exit_tags",            "TEXT"),
                ("market",           "TEXT DEFAULT 'KR'"),
            ]:
                try:
                    cur.execute(f"ALTER TABLE trading_journal ADD COLUMN {col} {coltype}")
                except sqlite3.OperationalError:
                    pass

            # market 컬럼 추가 (KR 기본값)
            for tbl in ["trades", "positions", "scan_history"]:
                try:
                    cur.execute(f"ALTER TABLE {tbl} ADD COLUMN market TEXT DEFAULT 'KR'")
                except Exception:
                    pass  # 컬럼이 이미 존재하거나 테이블이 없으면 무시

            now_dt = datetime.now()
            trade_type = data.get("type", data.get("trade_type", "buy"))
            # decision: trade_type 우선. 단, 명시적 decision="rejected" 는 보존하여
            # 거절/실패 이력도 사후 분석·게이트 평가에 남긴다 (rejection_reason 과 쌍으로).
            explicit_decision = (data.get("decision") or "").strip().lower()
            if explicit_decision == "rejected":
                decision = "rejected"
            else:
                decision = "sell" if trade_type == "sell" else "buy"
            # rejection_reason 컬럼은 schema 에 이미 존재 — INSERT 에도 반영해야 실제 저장된다.
            rejection_reason = data.get("rejection_reason") or ""
            if not rejection_reason and decision == "rejected":
                # rationale 에 "REJECTED: <msg>" 관행이 있으면 추출해 정규화.
                for key in ("sell_rationale", "buy_rationale"):
                    val = data.get(key, "") or ""
                    if val.startswith("REJECTED:"):
                        rejection_reason = val[len("REJECTED:"):].strip()
                        break

            record = {
                "ticker":        data.get("code", ""),
                "stock_name":    data.get("name", ""),
                "decision_date": now_dt.strftime("%Y-%m-%d"),
                "decision_time": data.get("timestamp", now_dt.strftime("%H:%M:%S")),
                "price":         float(data.get("price", 0) or 0),
                "qty":           int(data.get("qty", 0) or 0),
                "quant_score":   float(data.get("quant_score") or data.get("score") or 0),
                "trigger_type":  data.get("trigger_type", ""),
                "decision":      decision,
                "rejection_reason": rejection_reason,
                "return_rate":   float(data.get("return_rate", 0) or 0) if trade_type == "sell" else None,
                "sell_rationale": data.get("sell_rationale", "") if trade_type == "sell" else "",
                "buy_rationale":  data.get("buy_rationale", ""),
                "agent_source":  data.get("source", "engine"),
                "regime":        data.get("regime", ""),
                "exit_type":     exit_type or data.get("exit_type"),
                "hold_minutes":  hold_minutes if hold_minutes is not None else data.get("hold_minutes"),
                "buy_price":     float(data.get("buy_price", 0) or 0),
                "ai_confidence":   float(data.get("ai_confidence", 0.5) or 0.5),
                "news_sentiment":  float(data.get("news_sentiment", 0.0) or 0.0),
                "ai_decision_raw": str(data.get("ai_decision_raw", ""))[:2000],
                "entry_reason_summary": data.get("entry_reason_summary", "") or "",
                "entry_reason_detail":  data.get("entry_reason_detail", "") or "",
                "exit_reason_summary":  data.get("exit_reason_summary", "") or "",
                "exit_reason_detail":   data.get("exit_reason_detail", "") or "",
                "entry_tags":           data.get("entry_tags", "") or "",
                "exit_tags":            data.get("exit_tags", "") or "",
            }

            cur.execute("""
                INSERT INTO trading_journal (
                    ticker, stock_name, decision_date, decision_time, price, qty,
                    quant_score, trigger_type, decision, rejection_reason, return_rate,
                    sell_rationale, buy_rationale, agent_source, regime,
                    exit_type, hold_minutes, buy_price,
                    ai_confidence, news_sentiment, ai_decision_raw,
                    entry_reason_summary, entry_reason_detail,
                    exit_reason_summary, exit_reason_detail,
                    entry_tags, exit_tags
                ) VALUES (
                    :ticker, :stock_name, :decision_date, :decision_time, :price, :qty,
                    :quant_score, :trigger_type, :decision, :rejection_reason, :return_rate,
                    :sell_rationale, :buy_rationale, :agent_source, :regime,
                    :exit_type, :hold_minutes, :buy_price,
                    :ai_confidence, :news_sentiment, :ai_decision_raw,
                    :entry_reason_summary, :entry_reason_detail,
                    :exit_reason_summary, :exit_reason_detail,
                    :entry_tags, :exit_tags
                )
            """, record)
            conn.commit()
            logger.debug(f"[TradingJournal] record_trade 저장: {record['stock_name']}({record['ticker']}) {decision}")

            # 매도 완료 시 PerformanceTracker 피드백 루프 갱신
            if trade_type == "sell":
                try:
                    from journal.performance_tracker import PerformanceTracker as _PT
                    _pt = _PT(self.db_path)
                    _pt.record_outcome(
                        ticker=record["ticker"],
                        trigger_type=record["trigger_type"] or "",
                        return_pct=float(record["return_rate"] or 0),
                        hold_days=int(data.get("hold_days", 0) or 0),
                        lesson=record.get("sell_rationale", ""),
                    )
                except Exception as _pe:
                    logger.debug(f"[TradingJournal] PerformanceTracker 갱신 실패 (무시): {_pe}")
        except Exception as e:
            logger.warning(f"[TradingJournal] record_trade 저장 오류: {e}")
        finally:
            if conn:
                conn.close()

    # ─────────────────────────────────────────────────────────────
    #  내부 데이터 수집
    # ─────────────────────────────────────────────────────────────

    def _read_db_entries(self, limit: int = 200) -> List[Dict]:
        """trading.db의 trading_journal 테이블 조회."""
        if not os.path.exists(self.db_path):
            return []
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # trading_journal 테이블 존재 여부 확인
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='trading_journal'"
            )
            if not cur.fetchone():
                return []

            # [v14.1] 추가 컬럼 존재 여부 확인
            cur.execute("PRAGMA table_info(trading_journal)")
            cols = cur.fetchall()
            columns = {row[1] for row in cols}
            has_qty = "qty" in columns
            has_return = "return_rate" in columns
            has_sell_rat = "sell_rationale" in columns
            has_buy_rat_col = "buy_rationale" in columns
            has_full_json = "full_json_data" in columns
            has_buy_price = "buy_price" in columns
            has_exit_type = "exit_type" in columns
            has_hold_min = "hold_minutes" in columns
            has_entry_reason_summary = any(c[1] == 'entry_reason_summary' for c in cols)
            has_entry_reason_detail  = any(c[1] == 'entry_reason_detail'  for c in cols)
            has_exit_reason_summary  = any(c[1] == 'exit_reason_summary'  for c in cols)
            has_exit_reason_detail   = any(c[1] == 'exit_reason_detail'   for c in cols)
            has_entry_tags_new       = any(c[1] == 'entry_tags'           for c in cols)
            has_exit_tags_new        = any(c[1] == 'exit_tags'            for c in cols)

            select_cols = [
                "ticker          AS code",
                "stock_name      AS name",
                "decision_date || ' ' || decision_time AS timestamp",
                "decision        AS trade_type",
                "price",
                "qty" if has_qty else "0 AS qty",
                "quant_score",
                "trigger_type",
                "rejection_reason",
                "regime",
                "agent_source    AS source",
                "technical_trend",
                "volume_analysis",
                "market_condition_impact",
            ]
            if has_return:          select_cols.append("return_rate AS db_return_rate")
            if has_sell_rat:        select_cols.append("sell_rationale AS db_sell_rationale")
            if has_buy_rat_col:     select_cols.append("buy_rationale AS db_buy_rationale")
            if has_full_json:       select_cols.append("full_json_data AS db_full_json")
            if has_buy_price:       select_cols.append("buy_price")
            if has_exit_type:       select_cols.append("exit_type")
            if has_hold_min:        select_cols.append("hold_minutes")
            if has_entry_reason_summary: select_cols.append("entry_reason_summary")
            if has_entry_reason_detail:  select_cols.append("entry_reason_detail")
            if has_exit_reason_summary:  select_cols.append("exit_reason_summary")
            if has_exit_reason_detail:   select_cols.append("exit_reason_detail")
            if has_entry_tags_new:       select_cols.append("entry_tags")
            if has_exit_tags_new:        select_cols.append("exit_tags")

            # Codex US-05 (Fix #5 stage 3 REVISE): row-level COALESCE 정렬.
            # 기존 MAX(event_ts) WHERE ticker+date 매칭은 같은 종목 BUY/SELL 모든
            # row 가 동일 sort_ts 를 받아 정렬이 무너지는 버그가 있었음.
            # ROW_NUMBER() 윈도우로 (ticker, side) 그룹 내 순서대로 양쪽을 페어링하여
            # row-level 매칭. trade_events 가 일부 ticker 만 갖거나 페어 수가
            # 다르더라도 매칭되지 않는 row 는 fallback (decision_date+time) 으로 정렬.
            # trade_events 가 없는 (이전 세션) DB 도 graceful — 테이블 존재 확인 후 분기.
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='trade_events'"
            )
            _has_te = cur.fetchone() is not None

            if _has_te:
                # ROW_NUMBER() 페어링:
                #   trading_journal.decision (buy/sell) → uppercase 로 side 매칭.
                #   같은 (ticker, side) 그룹 내 시간순 순서가 같은 row 끼리 매칭.
                # SQLite 3.25+ 의 윈도우 함수 사용. 매칭 실패 시 LEFT JOIN 으로 NULL.
                # JOIN 후 ambiguous column 회피를 위해 derived table 컬럼은 _x suffix.
                cur.execute(
                    f"""
                    WITH te_ranked AS (
                        SELECT
                            ticker         AS ticker_x,
                            UPPER(side)    AS side_x,
                            event_ts       AS event_ts_x,
                            ROW_NUMBER() OVER (
                                PARTITION BY ticker, UPPER(side)
                                ORDER BY event_ts ASC, id ASC
                            ) AS rn_x
                        FROM trade_events
                    ),
                    tj_ranked AS (
                        SELECT
                            id             AS tj_id,
                            ticker         AS tj_ticker,
                            UPPER(decision) AS tj_side,
                            ROW_NUMBER() OVER (
                                PARTITION BY ticker, UPPER(decision)
                                ORDER BY decision_date ASC, decision_time ASC, id ASC
                            ) AS tj_rn
                        FROM trading_journal
                    )
                    SELECT
                        {', '.join(select_cols)},
                        COALESCE(
                            te.event_ts_x,
                            trading_journal.decision_date || ' ' || trading_journal.decision_time,
                            trading_journal.created_at
                        ) AS sort_ts
                    FROM trading_journal
                    LEFT JOIN tj_ranked
                           ON tj_ranked.tj_id = trading_journal.id
                    LEFT JOIN te_ranked te
                           ON te.ticker_x = tj_ranked.tj_ticker
                          AND te.side_x   = tj_ranked.tj_side
                          AND te.rn_x     = tj_ranked.tj_rn
                    WHERE trading_journal.decision != 'rejected'
                    ORDER BY sort_ts DESC, trading_journal.id DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            else:
                # 이전 세션 호환 — trade_events 없으면 기존 ORDER BY id DESC 유지.
                cur.execute(
                    f"""
                    SELECT
                        {', '.join(select_cols)}
                    FROM trading_journal
                    WHERE decision != 'rejected'
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            rows = [dict(r) for r in cur.fetchall()]
            # sort_ts 는 정렬용 보조 컬럼 — dict 에서 제거 (호환).
            for _r in rows:
                _r.pop("sort_ts", None)

            # trade_type 정규화: DB는 decision 컬럼(소문자 buy/sell) 사용
            for r in rows:
                tt = (r.get("decision") or r.get("trade_type") or "").lower()
                if tt in ("buy", "진입", "enter", "b"):
                    r["trade_type"] = "buy"
                elif tt in ("sell", "청산", "exit", "s"):
                    r["trade_type"] = "sell"
                else:
                    r["trade_type"] = "skip"

                # [v14.1 FIX] quant_score → 별도 필드로 보존, return_rate는 DB 값 우선
                r["score"] = r.pop("quant_score", 0)
                # 매도 기록은 DB에 저장된 실현 수익률 사용, 매수는 None
                db_ret = r.pop("db_return_rate", None)
                if r["trade_type"] == "sell" and db_ret is not None:
                    r["return_rate"] = float(db_ret)
                else:
                    r["return_rate"] = None  # ★ [PATCH] buy는 return_rate 없음 (quant_score 덮어쓰기 제거)

                # qty 기본값
                r["qty"] = int(r.get("qty", 0) or 0)

                # rationale 합성
                _db_buy_rat = r.pop("db_buy_rationale", "") or ""
                _db_sell_rat = r.pop("db_sell_rationale", "") or ""
                _db_full_json = r.pop("db_full_json", "") or ""
                _trend = r.get("technical_trend", "") or ""
                _vol   = r.get("volume_analysis", "") or ""
                _mkt   = r.get("market_condition_impact", "") or ""

                # ── [v14.2 FIX] full_json_data에서 AI 판단 정보 복원 ──
                _ai_comment = ""
                _confidence = None
                _entry_price = None
                _stop_price = None
                _target_price = None
                if _db_full_json:
                    try:
                        import json as _j
                        _fj = _j.loads(_db_full_json)
                        _confidence = _fj.get("confidence")
                        _entry_price = _fj.get("entry_price")
                        _stop_price = _fj.get("stop_loss") or _fj.get("stop_price")
                        _target_price = _fj.get("target_price")
                        # buy_rationale(클린 분석 텍스트) 우선, raw_report(JSON 포함)는 최후
                        _ai_comment = _fj.get("buy_rationale", "")
                        if not _ai_comment:
                            # raw_report에서 JSON 블록 이전만 추출
                            _raw = _fj.get("raw_report", "")
                            if _raw:
                                import re as _re2
                                _cb = _re2.search(r'```(?:json)?\s*\{', _raw)
                                _js = _raw.find('{')
                                _cp = _cb.start() if _cb else (_js if _js > 0 else len(_raw))
                                _ai_comment = _raw[:_cp].rstrip('`').strip()
                    except Exception as _e:
                        logger.debug(f"[Journal] AI 원문 파싱 실패: {_e}")

                # AI 분석 원문이 없으면 DB buy_rationale 사용
                if not _ai_comment:
                    _ai_comment = _db_buy_rat

                # 매도 기록은 DB에 저장된 rationale 우선
                if r["trade_type"] == "sell" and _db_sell_rat:
                    r["sell_rationale"] = ""  # [v13 FIX] 판단 근거와 중복 방지 → 청산 이유 비움
                    r["buy_rationale"] = _db_buy_rat
                    r["rationale"] = _db_sell_rat  # 판단 근거 = 청산 사유
                else:
                    # 매수 기록: buy_rationale 우선, 없으면 기술분석 필드 합성
                    if _db_buy_rat:
                        r["rationale"] = _db_buy_rat
                    else:
                        parts = []
                        if _trend and _trend not in ("", "N/A (fallback)"):
                            parts.append(f"추세: {_trend}")
                        if _vol and _vol not in ("", "N/A (fallback)"):
                            parts.append(f"거래량: {_vol}")
                        if _mkt and _mkt not in ("", "N/A (fallback)"):
                            parts.append(f"시장: {_mkt}")
                        r["rationale"] = " | ".join(parts) if parts else ""
                    r["buy_rationale"] = r["rationale"]
                    r["sell_rationale"] = ""

                # ── [v13 FIX] AI 판단 상세: 구조화된 요약만 표시 (buy_rationale 중복 제거) ──
                # buy_rationale 전문은 "진입 이유" 섹션에서 이미 표시되므로,
                # AI 판단 영역에는 가격·신뢰도·기술분석 요약만 표시
                _ai_parts = []
                if _confidence is not None:
                    _ai_parts.append(f"신뢰도: {float(_confidence):.0%}")
                if _entry_price:
                    _ai_parts.append(f"진입가: {float(_entry_price):,.0f}")
                if _stop_price:
                    _ai_parts.append(f"손절가: {float(_stop_price):,.0f}")
                if _target_price:
                    _ai_parts.append(f"목표가: {float(_target_price):,.0f}")
                _rej = r.get("rejection_reason", "")
                if _rej:
                    _ai_parts.append(f"사유: {_rej}")

                # 기술분석 3필드는 buy_rationale과 별개이므로 AI 판단에 포함
                _tech_parts = []
                if _trend and _trend not in ("", "N/A (fallback)"):
                    _tech_parts.append(f"📈 추세: {_trend}")
                if _vol and _vol not in ("", "N/A (fallback)"):
                    _tech_parts.append(f"📊 거래량: {_vol}")
                if _mkt and _mkt not in ("", "N/A (fallback)"):
                    _tech_parts.append(f"🌐 시장: {_mkt}")

                if _ai_parts or _tech_parts:
                    _summary = " | ".join(_ai_parts) if _ai_parts else ""
                    _tech_block = "\n".join(_tech_parts) if _tech_parts else ""
                    r["ai_comment"] = (
                        (_summary + "\n\n" if _summary else "")
                        + _tech_block
                    ).strip()
                else:
                    r["ai_comment"] = ""

                # ── [FIX] 프론트엔드(render.js) 호환 필드 별칭 추가 ──────
                # render.js journal()이 읽는 필드명과 DB 필드명 불일치 해소
                # pnl_pct: return_rate 별칭
                rr = r.get("return_rate")  # db_return_rate는 line 553에서 pop 후 return_rate로 이미 세팅됨
                r["pnl_pct"] = float(rr) if rr is not None else 0.0

                # pnl_amt: 근사치 (price × qty × return_rate / 100)
                _price = float(r.get("price", 0) or 0)
                _qty = int(r.get("qty", 0) or 0)
                _rr_val = float(rr) if rr is not None else 0.0
                r["pnl_amt"] = round(_price * _qty * _rr_val / 100, 0) if _price > 0 and _qty > 0 else 0

                # exit_date / date: timestamp에서 날짜 부분
                _ts = r.get("timestamp", "")
                r["exit_date"] = str(_ts)[:10] if _ts else ""
                r["date"] = r["exit_date"]

                # exit_reason: sell_rationale 또는 exit_type
                _exit_type = r.get("exit_type", "")
                r["exit_reason"] = (
                    r.get("sell_rationale")
                    or _exit_type
                    or r.get("rationale")
                    or ""
                )

                # trigger: trigger_type 별칭
                r["trigger"] = r.get("trigger_type", "")

                # hold_min: hold_minutes 별칭
                _hm = r.get("hold_minutes")
                r["hold_min"] = int(_hm) if _hm else None

                # entry_price: buy_price 또는 price
                r["entry_price"] = float(r.get("buy_price", 0) or 0) or _price

                # ticker: DB는 "ticker AS code"로 SELECT하므로 code → ticker 동기화
                r["ticker"] = r.get("ticker") or r.get("code", "")

                # exit_price: sell 레코드에서 price가 청산가
                if not r.get("exit_price"):
                    r["exit_price"] = _price if r.get("trade_type") == "sell" else 0.0

            return rows

        except Exception as e:
            logger.debug(f"[TradingJournal] DB 조회 오류: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def _read_loop_state_log(self) -> List[Dict]:
        """auto_loop 인메모리 _LOOP_STATE['trade_log'] 조회."""
        try:
            # auto_loop 모듈이 로드되어 있을 경우에만 접근
            import sys
            al_mod = sys.modules.get("engine.auto_loop") or sys.modules.get("auto_loop")
            if al_mod is None:
                return []
            loop_state = getattr(al_mod, "_LOOP_STATE", {})
            raw_log = loop_state.get("trade_log", [])

            result = []
            for entry in raw_log:
                action = (entry.get("action") or "").upper()
                trade_type = "buy" if action == "BUY" else "sell" if action == "SELL" else "unknown"
                _reason = entry.get("reason", "")
                result.append({
                    "code":        entry.get("code", ""),
                    "name":        entry.get("name", entry.get("code", "")),
                    "timestamp":   entry.get("time", datetime.now().strftime("%H:%M:%S")),
                    "trade_type":  trade_type,
                    "price":       float(entry.get("price", 0) or 0),
                    "qty":         int(entry.get("qty", 0) or 0),
                    "reason":      _reason,
                    "source":      "auto" if "AUTO" in (_reason).upper() else "manual",
                    "return_rate": None,  # 실시간 로그엔 수익률 없음
                    "score":       float(entry.get("quant_score", 0) or 0),
                    "trigger_type": entry.get("trigger_type", ""),
                    "rationale":   _reason,
                    "buy_rationale": _reason if trade_type == "buy" else "",
                    "sell_rationale": _reason if trade_type == "sell" else "",
                    "ai_comment":  "",
                })
            return result

        except Exception as e:
            logger.debug(f"[TradingJournal] trade_log 조회 오류: {e}")
            return []

    def _read_engine_history(self) -> List[Dict]:
        """ScalpingEngine._trade_history 조회 (엔진이 연결된 경우)."""
        if self.engine is None:
            return []
        try:
            hist = getattr(self.engine, "_trade_history", [])
            result = []
            for entry in hist:
                ret = float(entry.get("return_pct", 0) or 0)
                source = entry.get("source", "auto")
                _sell_rat = entry.get("sell_rationale", entry.get("reason", ""))
                result.append({
                    "code":          entry.get("code", ""),
                    "name":          entry.get("name", entry.get("code", "")),
                    "timestamp":     entry.get("sell_time",
                                               datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    "trade_type":    "sell",   # _trade_history는 완료 거래만 기록
                    "price":         float(entry.get("sell_price", 0) or 0),
                    "qty":           int(entry.get("quantity", 0) or 0),
                    "return_rate":   ret,
                    "score":         0,
                    "source":        source,
                    "reason":        entry.get("reason", ""),
                    "trigger_type":  entry.get("trigger", ""),
                    "rationale":     _sell_rat,
                    "buy_rationale": entry.get("buy_rationale", ""),
                    "sell_rationale":_sell_rat,
                    "ai_comment":    "",
                })
            return result
        except Exception as e:
            logger.debug(f"[TradingJournal] engine history 오류: {e}")
            return []

    # ─────────────────────────────────────────────────────────────
    #  통계 계산
    # ─────────────────────────────────────────────────────────────

    def _calc_stats(self, trades: List[Dict]) -> Dict:
        """주어진 거래 리스트에서 KPI 계산."""
        completed = [
            t for t in trades
            if t.get("return_rate") is not None and t.get("trade_type") == "sell"
        ]
        total = len(completed)
        if total == 0:
            return {
                "total_trades":    0,
                "win_rate":        0.0,
                "avg_profit_rate": 0.0,
                "total_profit":    0.0,
                "max_profit":      0.0,
                "max_loss":        0.0,
                "profit_factor":   0.0,
            }

        returns = [float(t.get("return_rate", 0) or 0) for t in completed]
        wins    = [r for r in returns if r > 0]
        losses  = [r for r in returns if r <= 0]

        gross_win  = sum(wins)   if wins   else 0.0
        gross_loss = sum(abs(l) for l in losses) if losses else 0.0
        pf         = round(gross_win / gross_loss, 3) if gross_loss > 0 else float("inf")

        # total_profit: return_rate 기반 (원화 profit 없으면 비율 합산)
        total_profit_val = sum(
            float(t.get("profit", t.get("return_rate", 0)) or 0) for t in completed
        )

        return {
            "total_trades":    total,
            "win_rate":        round(len(wins) / total * 100, 2),
            "avg_profit_rate": round(sum(returns) / total, 4),
            "total_profit":    round(total_profit_val, 0),
            "max_profit":      round(max(returns), 4) if returns else 0.0,
            "max_loss":        round(min(returns), 4) if returns else 0.0,
            "profit_factor":   pf,
        }

    # ─────────────────────────────────────────────────────────────
    #  엔진 연결 / 해제
    # ─────────────────────────────────────────────────────────────

    def attach_engine(self, engine) -> None:
        """실행 중인 ScalpingEngine 연결 (실시간 히스토리 조회용)."""
        self.engine = engine
        logger.info("[TradingJournal] ScalpingEngine 연결됨")

    def detach_engine(self) -> None:
        self.engine = None
