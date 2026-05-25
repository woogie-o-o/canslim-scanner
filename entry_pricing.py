"""EG-002~004: entry_price 계산 핵심 헬퍼.

quant_nexus_v20.py 6048-6066 의 STRONG/NEUTRAL/AVOID 분기에서 사용.
순수 함수로 분리해 단위 테스트가 가능하게 한다.
"""

from __future__ import annotations


def strong_entry_floor(cur: float, vwap: float, atr_abs: float) -> float:
    """STRONG 분기의 풀백 진입가.

    floor:
      - cur*0.97 (절대값 3% 하한)
      - cur - 0.5*ATR (ATR 정규화 하한)
    둘 중 더 보수적인(높은) 값을 사용.

    이유: ATR이 cur 의 6%+ 인 고변동성 종목에서 -3% floor 만 쓰면
    entry 가 cur-0.3*ATR 보다 위로 밀려 사실상 시장가 매수가 됨.
    역으로 저변동성 종목(ATR<1%)에서는 -3% 가 충분한 풀백.
    """
    base = min(
        cur,
        vwap if vwap > 0 else cur,
        cur - 0.3 * atr_abs,
    )
    return max(base, cur * 0.97, cur - 0.5 * atr_abs)
