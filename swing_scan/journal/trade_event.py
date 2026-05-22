"""journal/trade_event.py

Canonical TradeEvent DTO — Codex US-04 (Fix #5 stage 1-2).

원자적 매매 이벤트 표현. 기존 trading_journal 테이블과 별도로
신규 trade_events 테이블에 dual-write 하여 후속 단계의 dashboard
COALESCE 정렬(US-05)에 활용한다.

기존 trades / trading_journal 테이블 스키마는 절대 변경하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class TradeEvent:
    """단일 매매 이벤트 (BUY 또는 SELL).

    Attributes:
        event_ts: ISO 포맷 타임스탬프 'YYYY-MM-DD HH:MM:SS' (이벤트 발생 시각).
        ticker:   종목 코드.
        side:     "BUY" | "SELL".
        entry_price: 진입가 (BUY 인 경우 자기 가격, SELL 인 경우 원래 진입가).
        exit_price:  청산가 (SELL 시 체결가; BUY 시 None).
        qty:      수량 (BUY/SELL 모두 양수).
        pnl:      실현 손익 (KRW; SELL 시만 채움. None 가능).
        pnl_pct:  실현 수익률 (% — return_rate; SELL 시만. None 가능).
        reason:   사유 (e.g. "tp1" | "full_stop" | "swing_mom").
        source:   이벤트 발생 소스 (e.g. "swing_mom_kr" | "auto" | "manual").
    """
    event_ts: str
    ticker: str
    side: str
    entry_price: Optional[float]
    exit_price: Optional[float]
    qty: int
    pnl: Optional[float]
    pnl_pct: Optional[float]
    reason: str
    source: str

    def to_dict(self) -> Dict[str, Any]:
        """DB 직렬화용 dict 변환."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradeEvent":
        """DB row dict 에서 역직렬화."""
        return cls(
            event_ts=str(data["event_ts"]),
            ticker=str(data["ticker"]),
            side=str(data["side"]).upper(),
            entry_price=_to_float_or_none(data.get("entry_price")),
            exit_price=_to_float_or_none(data.get("exit_price")),
            qty=int(data.get("qty") or 0),
            pnl=_to_float_or_none(data.get("pnl")),
            pnl_pct=_to_float_or_none(data.get("pnl_pct")),
            reason=str(data.get("reason") or ""),
            source=str(data.get("source") or ""),
        )

    @staticmethod
    def now_ts() -> str:
        """현재 시각 ISO 문자열 헬퍼."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_float_or_none(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
