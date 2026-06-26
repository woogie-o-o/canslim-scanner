from __future__ import annotations

import logging
import re
import time
import yfinance as yf
from tradingkey_api import is_kr_ticker, get_tradingkey_data

logger = logging.getLogger(__name__)


# ── yfinance 일괄 수집 ───────────────────────────────────────────────────────

def _fetch_yf(ticker: str) -> dict:
    """yfinance 데이터를 한 번에 수집해 dict로 반환."""
    t = yf.Ticker(ticker)
    bs  = t.balance_sheet
    inc = t.income_stmt
    cf  = t.cashflow
    info = {}
    try:
        info = t.info or {}
    except Exception:
        pass

    def _g(df, key, col=0, default=0.0):
        try:
            return float(df.loc[key].iloc[col])
        except Exception:
            return float(default)

    curr = {
        "Net Income":             _g(inc, "Net Income"),
        "Total Assets":           _g(bs,  "Total Assets"),
        "Operating Cash Flow":    _g(cf,  "Operating Cash Flow"),
        "Long Term Debt":         _g(bs,  "Long Term Debt"),
        "Current Assets":         _g(bs,  "Current Assets"),
        "Current Liabilities":    _g(bs,  "Current Liabilities"),
        "Ordinary Shares Number": _g(bs,  "Ordinary Shares Number"),
        "Total Revenue":          _g(inc, "Total Revenue"),
        "Gross Profit":           _g(inc, "Gross Profit"),
    }
    prev = {
        "Net Income":             _g(inc, "Net Income",             col=1),
        "Total Assets":           _g(bs,  "Total Assets",           col=1),
        "Long Term Debt":         _g(bs,  "Long Term Debt",         col=1),
        "Current Assets":         _g(bs,  "Current Assets",         col=1),
        "Current Liabilities":    _g(bs,  "Current Liabilities",    col=1),
        "Ordinary Shares Number": _g(bs,  "Ordinary Shares Number", col=1),
        "Total Revenue":          _g(inc, "Total Revenue",          col=1),
        "Gross Profit":           _g(inc, "Gross Profit",           col=1),
    }

    # EBIT
    try:
        ebit = float(inc.loc["EBIT"].iloc[0])
    except Exception:
        try:
            ebit = float(inc.loc["Operating Income"].iloc[0])
        except Exception:
            ebit = 0.0

    # Retained Earnings
    try:
        retained = float(bs.loc["Retained Earnings"].iloc[0])
    except Exception:
        retained = 0.0

    # Net PPE
    try:
        ppe_curr = float(bs.loc["Net PPE"].iloc[0])
        ppe_prev = float(bs.loc["Net PPE"].iloc[1])
    except Exception:
        ppe_curr = ppe_prev = 0.0

    # Depreciation
    try:
        depr = float(cf.loc["Depreciation And Amortization"].iloc[0])
    except Exception:
        depr = 0.0

    # Other Non Current Assets
    try:
        lt_curr = float(bs.loc["Other Non Current Assets"].iloc[0])
        lt_prev = float(bs.loc["Other Non Current Assets"].iloc[1])
    except Exception:
        lt_curr = lt_prev = 0.0

    # SGA
    try:
        sga_curr = float(inc.loc["Selling General And Administration"].iloc[0])
        sga_prev = float(inc.loc["Selling General And Administration"].iloc[1])
    except Exception:
        sga_curr = sga_prev = 0.0

    return {
        "curr": curr,
        "prev": prev,
        "market_cap": float(info.get("marketCap", 0) or 0),
        "ebit": ebit,
        "retained": retained,
        "ppe_curr": ppe_curr,
        "ppe_prev": ppe_prev,
        "depr": depr,
        "lt_curr": lt_curr,
        "lt_prev": lt_prev,
        "sga_curr": sga_curr,
        "sga_prev": sga_prev,
    }


def _fetch_yf_kr(ticker: str) -> tuple:
    """KR 종목 yfinance 재무제표 + 1개월 수익률. .KS → .KQ 폴백."""
    base = re.sub(r'\.(KS|KQ)$', '', ticker.upper(), flags=re.IGNORECASE)
    yf_data = None
    rev_1m = 0.0
    for suffix in ('.KS', '.KQ'):
        yf_sym = base + suffix
        try:
            t = yf.Ticker(yf_sym)
            bs = t.balance_sheet
            if bs is None or bs.empty:
                continue
            inc = t.income_stmt
            cf  = t.cashflow
            info = {}
            try:
                info = t.info or {}
            except Exception:
                pass

            def _g(df, key, col=0, default=0.0):
                try:
                    return float(df.loc[key].iloc[col])
                except Exception:
                    return float(default)

            curr = {
                "Net Income":             _g(inc, "Net Income"),
                "Total Assets":           _g(bs,  "Total Assets"),
                "Operating Cash Flow":    _g(cf,  "Operating Cash Flow"),
                "Long Term Debt":         _g(bs,  "Long Term Debt"),
                "Current Assets":         _g(bs,  "Current Assets"),
                "Current Liabilities":    _g(bs,  "Current Liabilities"),
                "Ordinary Shares Number": _g(bs,  "Ordinary Shares Number"),
                "Total Revenue":          _g(inc, "Total Revenue"),
                "Gross Profit":           _g(inc, "Gross Profit"),
            }
            prev = {
                "Net Income":             _g(inc, "Net Income",             col=1),
                "Total Assets":           _g(bs,  "Total Assets",           col=1),
                "Long Term Debt":         _g(bs,  "Long Term Debt",         col=1),
                "Current Assets":         _g(bs,  "Current Assets",         col=1),
                "Current Liabilities":    _g(bs,  "Current Liabilities",    col=1),
                "Ordinary Shares Number": _g(bs,  "Ordinary Shares Number", col=1),
                "Total Revenue":          _g(inc, "Total Revenue",          col=1),
                "Gross Profit":           _g(inc, "Gross Profit",           col=1),
            }
            try:
                ebit = float(inc.loc["EBIT"].iloc[0])
            except Exception:
                try:
                    ebit = float(inc.loc["Operating Income"].iloc[0])
                except Exception:
                    ebit = 0.0
            try:
                retained = float(bs.loc["Retained Earnings"].iloc[0])
            except Exception:
                retained = 0.0
            try:
                ppe_curr = float(bs.loc["Net PPE"].iloc[0])
                ppe_prev = float(bs.loc["Net PPE"].iloc[1])
            except Exception:
                ppe_curr = ppe_prev = 0.0
            try:
                depr = float(cf.loc["Depreciation And Amortization"].iloc[0])
            except Exception:
                depr = 0.0
            try:
                lt_curr = float(bs.loc["Other Non Current Assets"].iloc[0])
                lt_prev = float(bs.loc["Other Non Current Assets"].iloc[1])
            except Exception:
                lt_curr = lt_prev = 0.0
            try:
                sga_curr = float(inc.loc["Selling General And Administration"].iloc[0])
                sga_prev = float(inc.loc["Selling General And Administration"].iloc[1])
            except Exception:
                sga_curr = sga_prev = 0.0

            yf_data = {
                "curr": curr, "prev": prev,
                "market_cap": float(info.get("marketCap", 0) or 0),
                "ebit": ebit, "retained": retained,
                "ppe_curr": ppe_curr, "ppe_prev": ppe_prev, "depr": depr,
                "lt_curr": lt_curr, "lt_prev": lt_prev,
                "sga_curr": sga_curr, "sga_prev": sga_prev,
            }
            try:
                h = t.history(period="35d", interval="1d", auto_adjust=True)
                if h is not None and len(h) >= 2:
                    p_now = float(h["Close"].iloc[-1])
                    p_1m  = float(h["Close"].iloc[0])
                    rev_1m = round((p_now / p_1m - 1) * 100, 2) if p_1m else 0.0
            except Exception:
                pass
            break
        except Exception as _e:
            logger.debug("_fetch_yf_kr %s failed: %s", yf_sym, _e)
            time.sleep(1.0)
    return yf_data, rev_1m


# ── 내부 계산 함수 (pre-fetched data 사용) ──────────────────────────────────

def _piotroski_from(d: dict) -> int:
    curr, prev = d["curr"], d["prev"]
    ta = curr["Total Assets"]
    prev_ta = prev["Total Assets"]
    if ta == 0:
        return 0
    score = 0

    roa = curr["Net Income"] / ta
    if roa > 0:                                               score += 1
    if curr["Operating Cash Flow"] > 0:                       score += 1
    prev_roa = prev["Net Income"] / prev_ta if prev_ta else 0
    if roa > prev_roa:                                        score += 1
    if curr["Operating Cash Flow"] / ta > roa:                score += 1

    curr_lev = curr["Long Term Debt"] / ta
    prev_lev = prev["Long Term Debt"] / prev_ta if prev_ta else 0
    if curr_lev < prev_lev:                                   score += 1

    curr_cr = curr["Current Assets"] / curr["Current Liabilities"] if curr["Current Liabilities"] else 0
    prev_cr = prev["Current Assets"] / prev["Current Liabilities"] if prev["Current Liabilities"] else 0
    if curr_cr > prev_cr:                                     score += 1
    if curr["Ordinary Shares Number"] <= prev["Ordinary Shares Number"]: score += 1

    curr_gm = curr["Gross Profit"] / curr["Total Revenue"] if curr["Total Revenue"] else 0
    prev_gm = prev["Gross Profit"] / prev["Total Revenue"] if prev["Total Revenue"] else 0
    if curr_gm > prev_gm:                                     score += 1

    curr_at = curr["Total Revenue"] / ta
    prev_at = prev["Total Revenue"] / prev_ta if prev_ta else 0
    if curr_at > prev_at:                                     score += 1

    return score


def _piotroski_breakdown(d: dict) -> dict:
    """Piotroski F-Score 9개 기준 개별 결과를 dict로 반환."""
    curr, prev = d["curr"], d["prev"]
    ta = curr["Total Assets"]
    prev_ta = prev["Total Assets"]
    if ta == 0:
        return {k: False for k in ["roa_positive","ocf_positive","roa_improved","accrual_quality","leverage_down","liquidity_up","no_dilution","gm_improved","at_improved"]}

    roa = curr["Net Income"] / ta
    prev_roa = prev["Net Income"] / prev_ta if prev_ta else 0
    curr_lev = curr["Long Term Debt"] / ta
    prev_lev = prev["Long Term Debt"] / prev_ta if prev_ta else 0
    curr_cr = curr["Current Assets"] / curr["Current Liabilities"] if curr["Current Liabilities"] else 0
    prev_cr = prev["Current Assets"] / prev["Current Liabilities"] if prev["Current Liabilities"] else 0
    curr_gm = curr["Gross Profit"] / curr["Total Revenue"] if curr["Total Revenue"] else 0
    prev_gm = prev["Gross Profit"] / prev["Total Revenue"] if prev["Total Revenue"] else 0
    curr_at = curr["Total Revenue"] / ta
    prev_at = prev["Total Revenue"] / prev_ta if prev_ta else 0

    return {
        "roa_positive":    roa > 0,
        "ocf_positive":    curr["Operating Cash Flow"] > 0,
        "roa_improved":    roa > prev_roa,
        "accrual_quality": curr["Operating Cash Flow"] / ta > roa,
        "leverage_down":   curr_lev < prev_lev,
        "liquidity_up":    curr_cr > prev_cr,
        "no_dilution":     curr["Ordinary Shares Number"] <= prev["Ordinary Shares Number"],
        "gm_improved":     curr_gm > prev_gm,
        "at_improved":     curr_at > prev_at,
    }


def _altman_z_from(d: dict) -> float | None:
    curr = d["curr"]
    ta = curr["Total Assets"]
    if ta == 0:
        return None
    wc = curr["Current Assets"] - curr["Current Liabilities"]
    tl = curr["Long Term Debt"] + curr["Current Liabilities"]
    x1 = wc / ta
    x2 = d["retained"] / ta
    x3 = d["ebit"] / ta
    x4 = d["market_cap"] / tl if tl else 0
    x5 = curr["Total Revenue"] / ta
    return round(1.2*x1 + 1.4*x2 + 3.3*x3 + 0.6*x4 + 1.0*x5, 3)


def _beneish_m_from(d: dict) -> tuple[float, bool] | None:
    curr, prev = d["curr"], d["prev"]
    ta_c, ta_p = curr["Total Assets"], prev["Total Assets"]
    if ta_c == 0 or ta_p == 0:
        return None

    rev_c, rev_p = curr["Total Revenue"], prev["Total Revenue"]
    gp_c,  gp_p  = curr["Gross Profit"],  prev["Gross Profit"]

    dsri = ((curr["Current Assets"]*0.3 / rev_c) / (prev["Current Assets"]*0.3 / rev_p)
            if rev_c and rev_p else 1)
    gmi  = (gp_p / rev_p) / (gp_c / rev_c) if rev_p and rev_c and gp_c else 1
    aqi_c = (ta_c - curr["Current Assets"] - d["ppe_curr"]) / ta_c
    aqi_p = (ta_p - prev["Current Assets"] - d["ppe_prev"]) / ta_p
    aqi  = aqi_c / aqi_p if aqi_p else 1
    sgi  = rev_c / rev_p if rev_p else 1

    dep_r_p = d["ppe_prev"] / (d["ppe_prev"] + d["depr"]) if (d["ppe_prev"] + d["depr"]) else 0.5
    dep_r_c = d["ppe_curr"] / (d["ppe_curr"] + d["depr"]) if (d["ppe_curr"] + d["depr"]) else 0.5
    depi = dep_r_p / dep_r_c if dep_r_c else 1

    sgai = ((d["sga_curr"] / rev_c) / (d["sga_prev"] / rev_p)
            if d["sga_prev"] and rev_p and rev_c else 1)
    lvgi = (curr["Long Term Debt"] / ta_c) / (prev["Long Term Debt"] / ta_p) if prev["Long Term Debt"] else 1
    tata = (curr["Net Income"] - curr["Operating Cash Flow"]) / ta_c

    m = (-4.84 + 0.92*dsri + 0.528*gmi + 0.404*aqi + 0.892*sgi
         + 0.115*depi - 0.172*sgai + 4.679*tata - 0.327*lvgi)
    return round(float(m), 3), m > -1.78


# ── 공개 API (단독 호출용 — 테스트 호환성 유지) ────────────────────────────

def _get_financials(ticker: str) -> tuple[dict, dict]:
    d = _fetch_yf(ticker)
    return d["curr"], d["prev"]


def _get_market_cap(ticker: str) -> float:
    try:
        return float(yf.Ticker(ticker).info.get("marketCap", 0))
    except Exception:
        return 0.0


def _get_ebit(ticker: str) -> float:
    try:
        return float(yf.Ticker(ticker).income_stmt.loc["EBIT"].iloc[0])
    except Exception:
        try:
            return float(yf.Ticker(ticker).income_stmt.loc["Operating Income"].iloc[0])
        except Exception:
            return 0.0


def _get_retained_earnings(ticker: str) -> float:
    try:
        return float(yf.Ticker(ticker).balance_sheet.loc["Retained Earnings"].iloc[0])
    except Exception:
        return 0.0


def _get_ppe(ticker: str) -> tuple[float, float]:
    try:
        bs = yf.Ticker(ticker).balance_sheet
        return float(bs.loc["Net PPE"].iloc[0]), float(bs.loc["Net PPE"].iloc[1])
    except Exception:
        return 0.0, 0.0


def _get_depreciation(ticker: str) -> float:
    try:
        return float(yf.Ticker(ticker).cashflow.loc["Depreciation And Amortization"].iloc[0])
    except Exception:
        return 0.0


def _get_long_term_assets(ticker: str) -> tuple[float, float]:
    try:
        bs = yf.Ticker(ticker).balance_sheet
        return float(bs.loc["Other Non Current Assets"].iloc[0]), float(bs.loc["Other Non Current Assets"].iloc[1])
    except Exception:
        return 0.0, 0.0


def _get_sga(ticker: str) -> tuple[float, float]:
    try:
        inc = yf.Ticker(ticker).income_stmt
        return float(inc.loc["Selling General And Administration"].iloc[0]), float(inc.loc["Selling General And Administration"].iloc[1])
    except Exception:
        return 0.0, 0.0


def calculate_piotroski(ticker: str) -> int | None:
    """Piotroski F-Score (0~9) 계산."""
    if is_kr_ticker(ticker):
        return None
    try:
        d = _fetch_yf(ticker)
        return _piotroski_from(d)
    except Exception as e:
        logger.warning("Piotroski failed for %s: %s", ticker, e)
        return None


def calculate_altman_z(ticker: str) -> float | None:
    """Altman Z-Score. >2.99=안전, 1.81~2.99=회색, <1.81=위험."""
    if is_kr_ticker(ticker):
        return None
    try:
        d = _fetch_yf(ticker)
        return _altman_z_from(d)
    except Exception as e:
        logger.warning("Altman Z failed for %s: %s", ticker, e)
        return None


def calculate_beneish_m(ticker: str) -> tuple[float, bool] | None:
    """Beneish M-Score. > -1.78: 분식 의심 경고."""
    if is_kr_ticker(ticker):
        return None
    try:
        d = _fetch_yf(ticker)
        return _beneish_m_from(d)
    except Exception as e:
        logger.warning("Beneish M failed for %s: %s", ticker, e)
        return None


# ── 스코어 계산 ──────────────────────────────────────────────────────────────

def _calc_quantitative_score(tk_data: dict, piotroski: int) -> int:
    tk_score = tk_data.get("score", {})
    inst     = tk_data.get("institutional", {})
    perf     = tk_data.get("performance", {})

    # TradingKey overall → 80점 환산
    overall = tk_score.get("overall", 0)
    score   = int(overall / 100 * 80)

    # Piotroski → 10점
    score += int((piotroski / 9) * 10) if piotroski else 0

    # QoQ 모멘텀 → 10점
    qoq = inst.get("holding_qoq", 0)
    if qoq > 5:    score += 4
    elif qoq > 0:  score += 2

    rev_1m = perf.get("1m", 0)
    if rev_1m > 5:    score += 6
    elif rev_1m > 0:  score += 3
    elif rev_1m > -5: score += 1

    return min(100, max(0, score))


def _calc_score_breakdown(tk_data: dict, piotroski: int) -> dict:
    """점수 기여도를 세부 항목별로 분해해 반환."""
    tk_score = tk_data.get("score", {})
    inst     = tk_data.get("institutional", {})
    perf     = tk_data.get("performance", {})

    overall  = tk_score.get("overall", 0)
    tk_contribution = int(overall / 100 * 80)

    pio_contribution = int((piotroski / 9) * 10) if piotroski else 0

    qoq = inst.get("holding_qoq", 0)
    if qoq > 5:    qoq_contribution = 4
    elif qoq > 0:  qoq_contribution = 2
    else:          qoq_contribution = 0

    rev_1m = perf.get("1m", 0)
    if rev_1m > 5:    mom_contribution = 6
    elif rev_1m > 0:  mom_contribution = 3
    elif rev_1m > -5: mom_contribution = 1
    else:             mom_contribution = 0

    return {
        "tk_overall":                overall,
        "tk_contribution":           tk_contribution,
        "piotroski_contribution":    pio_contribution,
        "qoq_contribution":          qoq_contribution,
        "momentum_1m_contribution":  mom_contribution,
        "rev_1m":                    rev_1m,
    }


def _score_to_grade(score: int) -> str:
    if score >= 90: return "A+"
    if score >= 75: return "A"
    if score >= 55: return "B"
    if score >= 35: return "C"
    return "D"


def _grade_to_rating(grade: str) -> str:
    return {"A+": "최우량", "A": "우량", "B": "양호",
            "C": "불량", "D": "최하"}.get(grade, "양호")


def _fetch_naver_kr_supplement(ticker: str) -> dict:
    """네이버 금융에서 1M 수익률 + 기관/외인 수급 보강."""
    result = {"rev_1m": 0.0, "inst_net_5d": 0.0, "foreign_net_5d": 0.0}
    try:
        from naver_finance import get_price_return_1m, get_investor_flow
        rev = get_price_return_1m(ticker)
        if rev is not None:
            result["rev_1m"] = rev
        flow = get_investor_flow(ticker)
        result["inst_net_5d"]    = float(flow.get("inst_net_5d")    or 0)
        result["foreign_net_5d"] = float(flow.get("foreign_net_5d") or 0)
    except Exception as e:
        logger.debug("_fetch_naver_kr_supplement %s: %s", ticker, e)
    return result


def _calc_quantitative_score_kr(piotroski: int, altman_z, beneish_m, rev_1m: float,
                                  inst_net_5d: float = 0.0, foreign_net_5d: float = 0.0) -> int:
    """KR 종목용 종합 스코어 (TradingKey 없이 100점 만점).
    Piotroski 40 · Altman Z 20 · Beneish M 10 · 1M 수익률 20 · 기관/외인 수급 10.
    """
    score = 0
    score += int((piotroski / 9) * 40) if piotroski else 0
    if altman_z is not None:
        if altman_z >= 2.9:    score += 20
        elif altman_z >= 1.23: score += 10
    if beneish_m is not None:
        if beneish_m < -2.22:  score += 10
        elif beneish_m < -1.78: score += 5
    if rev_1m > 5:    score += 20
    elif rev_1m > 0:  score += 10
    elif rev_1m > -5: score += 4
    inst_pos   = inst_net_5d > 0
    frgn_pos   = foreign_net_5d > 0
    if inst_pos and frgn_pos: score += 10
    elif inst_pos or frgn_pos: score += 5
    return min(100, max(0, score))


def _calc_score_breakdown_kr(piotroski: int, altman_z, beneish_m, rev_1m: float,
                               inst_net_5d: float = 0.0, foreign_net_5d: float = 0.0) -> dict:
    """KR 점수 기여도 분해 (US 스키마 호환)."""
    pio_c = int((piotroski / 9) * 40) if piotroski else 0
    az_c  = (20 if (altman_z is not None and altman_z >= 2.9)
             else (10 if (altman_z is not None and altman_z >= 1.23) else 0))
    bm_c  = (10 if (beneish_m is not None and beneish_m < -2.22)
             else (5  if (beneish_m is not None and beneish_m < -1.78) else 0))
    mom_c = 20 if rev_1m > 5 else (10 if rev_1m > 0 else (4 if rev_1m > -5 else 0))
    inst_pos = inst_net_5d > 0
    frgn_pos = foreign_net_5d > 0
    inst_c   = 10 if (inst_pos and frgn_pos) else (5 if (inst_pos or frgn_pos) else 0)
    return {
        "piotroski_contribution":   pio_c,
        "altman_z_contribution":    az_c,
        "beneish_contribution":     bm_c,
        "momentum_1m_contribution": mom_c,
        "inst_contribution":        inst_c,
        "rev_1m":                   round(rev_1m, 2),
        "tk_overall":               0,
        "tk_contribution":          0,
        "qoq_contribution":         0,
    }


def _get_nomura_score_kr(ticker: str) -> dict | None:
    """KR 종목 노무라式 스코어. 재무제표 기반, TradingKey 미사용."""
    try:
        naver = _fetch_naver_kr_supplement(ticker)
        rev_1m       = naver["rev_1m"]
        inst_net_5d  = naver["inst_net_5d"]
        frgn_net_5d  = naver["foreign_net_5d"]

        yf_data, yf_rev = _fetch_yf_kr(ticker)
        if rev_1m == 0.0 and yf_rev != 0.0:
            rev_1m = yf_rev

        if yf_data is not None:
            try:
                piotroski = _piotroski_from(yf_data)
            except Exception:
                piotroski = 0
            try:
                altman_z = _altman_z_from(yf_data)
            except Exception:
                altman_z = None
            try:
                beneish_result = _beneish_m_from(yf_data)
            except Exception:
                beneish_result = None
        else:
            piotroski, altman_z, beneish_result = 0, None, None

        beneish_m    = beneish_result[0] if beneish_result else None
        beneish_warn = beneish_result[1] if beneish_result else False

        q_score = _calc_quantitative_score_kr(piotroski, altman_z, beneish_m, rev_1m,
                                               inst_net_5d, frgn_net_5d)
        grade   = _score_to_grade(q_score)
        rating  = _grade_to_rating(grade)

        return {
            "quantitative_score": q_score,
            "grade":              grade,
            "piotroski":          piotroski,
            "altman_z":           altman_z,
            "beneish_m":          beneish_m,
            "beneish_warning":    beneish_warn,
            "nomura_rating":      rating,
            "nomura_target":      0.0,
            "nomura_upside":      0.0,
            "score_breakdown":    _calc_score_breakdown_kr(piotroski, altman_z, beneish_m, rev_1m,
                                                            inst_net_5d, frgn_net_5d),
            "piotroski_detail":   _piotroski_breakdown(yf_data) if yf_data else {},
        }
    except Exception as e:
        logger.warning("_get_nomura_score_kr failed for %s: %s", ticker, e)
        return None


# ── 메인 공개 API ────────────────────────────────────────────────────────────

def get_nomura_score(ticker: str) -> dict | None:
    """노무라式 종합 스코어 반환. yfinance를 1회만 호출한다."""
    if is_kr_ticker(ticker):
        return _get_nomura_score_kr(ticker)
    try:
        tk_data = get_tradingkey_data(ticker)  # None이어도 계속 진행

        # yfinance 1회 수집
        try:
            yf_data = _fetch_yf(ticker)
        except Exception as e:
            logger.warning("yfinance fetch failed for %s: %s", ticker, e)
            yf_data = None

        if yf_data is not None:
            try:
                piotroski = _piotroski_from(yf_data)
            except Exception:
                piotroski = 0
            try:
                altman_z = _altman_z_from(yf_data)
            except Exception:
                altman_z = None
            try:
                beneish_result = _beneish_m_from(yf_data)
            except Exception:
                beneish_result = None
        else:
            piotroski, altman_z, beneish_result = 0, None, None

        beneish_m    = beneish_result[0] if beneish_result else None
        beneish_warn = beneish_result[1] if beneish_result else False

        td      = tk_data or {}
        q_score = _calc_quantitative_score(td, piotroski)
        grade   = _score_to_grade(q_score)
        rating  = _grade_to_rating(grade)

        analyst = td.get("analyst", {})
        return {
            "quantitative_score": q_score,
            "grade":              grade,
            "piotroski":          piotroski,
            "altman_z":           altman_z,
            "beneish_m":          beneish_m,
            "beneish_warning":    beneish_warn,
            "nomura_rating":      rating,
            "nomura_target":      float(analyst.get("target_price", 0.0)),
            "nomura_upside":      float(analyst.get("upside_pct",   0.0)),
            "score_breakdown":    _calc_score_breakdown(td, piotroski),
            "piotroski_detail":   _piotroski_breakdown(yf_data) if yf_data else {},
        }
    except Exception as e:
        logger.warning("get_nomura_score failed for %s: %s", ticker, e)
        return None
