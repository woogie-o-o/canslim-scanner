# -*- coding: utf-8 -*-
"""utils/report_scheduler.py — 정기 리포트 스케줄러 (Telegram)"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional


class ReportScheduler:
    """08:50 / 매시 정각 / 12:00 / 15:40 에 Telegram 리포트를 전송한다."""

    REPORT_TIMES = {"08:50", "12:00", "15:40"}

    def __init__(self, name: str, notifier, state, prefix: str = ""):
        self._name = name
        self._notifier = notifier
        self._state = state
        self._prefix = prefix
        self._trades: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_fired: str = ""

    # ------------------------------------------------------------------
    def record_trade(self, *, ticker: str, name: str, entry_price: float,
                     exit_price: float, qty: int, pnl: float,
                     pnl_pct: float, exit_reason: str) -> None:
        with self._lock:
            self._trades.append({
                "ticker": ticker, "name": name,
                "entry": entry_price, "exit": exit_price,
                "qty": qty, "pnl": pnl, "pnl_pct": pnl_pct,
                "reason": exit_reason,
                "time": datetime.now().strftime("%H:%M:%S"),
            })

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name=f"ReportScheduler-{self._name}")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop_event.is_set():
            now = datetime.now()
            hhmm = now.strftime("%H:%M")

            # 매시 정각 or 지정 시각
            is_top_of_hour = now.minute == 0
            is_special = hhmm in self.REPORT_TIMES

            if (is_top_of_hour or is_special) and hhmm != self._last_fired:
                self._last_fired = hhmm
                try:
                    self._send_report(hhmm)
                except Exception:
                    pass

            self._stop_event.wait(timeout=30)

    # ------------------------------------------------------------------
    def _send_report(self, hhmm: str) -> None:
        if self._notifier is None:
            return

        with self._lock:
            trades = list(self._trades)
            self._trades.clear()

        state = self._state
        positions = getattr(state, "positions", {})
        history = getattr(state, "history", [])

        lines = [
            f"{self._prefix} [{self._name}] 리포트 {hhmm}",
            f"보유: {len(positions)}종목 | 오늘 체결: {len(history)}건",
        ]

        if trades:
            lines.append("─ 최근 체결 ─")
            for t in trades[-5:]:
                sign = "▲" if t["pnl"] >= 0 else "▼"
                lines.append(
                    f"{sign} {t['name']}({t['ticker']}) "
                    f"{t['pnl_pct']:+.2f}% {t['pnl']:+,.0f}원 [{t['reason']}]"
                )

        try:
            self._notifier.send("\n".join(lines))
        except Exception:
            pass
