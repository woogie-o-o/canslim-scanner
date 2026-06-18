"""Toss Securities Open API helper.

Phase 1 usage in this app is deliberately narrow: use Toss as the primary
source for KR realtime last price, while keeping Naver/DART for fields Toss
does not currently provide here (change %, market cap, fundamentals).
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Iterable

import requests


BASE_URL = os.environ.get("TOSSINVEST_BASE_URL", "https://openapi.tossinvest.com").rstrip("/")
TOKEN_PATH = "/oauth2/token"
PRICES_PATH = "/api/v1/prices"
CANDLES_PATH = "/api/v1/candles"
STOCKS_PATH = "/api/v1/stocks"

_SESSION = requests.Session()
_TOKEN_LOCK = threading.Lock()
_TOKEN: str | None = None
_TOKEN_EXP: float = 0.0
_LAST_ERROR: str = ""
_STOCKS_LOCK = threading.Lock()
_STOCKS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _env(*names: str) -> str:
    for name in names:
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return ""


def _credentials() -> tuple[str, str]:
    client_id = _env(
        "TOSSINVEST_API_KEY",
        "TOSSINVEST_CLIENT_ID",
        "TOSS_INVEST_CLIENT_ID",
        "TOSS_CLIENT_ID",
    )
    client_secret = _env(
        "TOSSINVEST_SECRET_KEY",
        "TOSSINVEST_CLIENT_SECRET",
        "TOSS_INVEST_CLIENT_SECRET",
        "TOSS_CLIENT_SECRET",
    )
    return client_id, client_secret


def _set_last_error(message: str) -> None:
    global _LAST_ERROR
    _LAST_ERROR = str(message or "")[:240]


def get_last_error() -> str:
    return _LAST_ERROR


def _http_error(label: str, resp) -> str:
    detail = ""
    try:
        payload = resp.json() or {}
        if isinstance(payload, dict):
            parts = [payload.get("error"), payload.get("error_description"), payload.get("code"), payload.get("message")]
            detail = " ".join(str(p).strip() for p in parts if p)
    except Exception:
        detail = ""
    status = getattr(resp, "status_code", "unknown")
    return f"{label}_http_{status}" + (f": {detail[:160]}" if detail else "")


def is_available() -> bool:
    client_id, client_secret = _credentials()
    return bool(client_id and client_secret)


def _timeout() -> float:
    try:
        return max(1.0, float(os.environ.get("TOSSINVEST_TIMEOUT_SEC", "5")))
    except (TypeError, ValueError):
        return 5.0


def _stocks_ttl_sec() -> float:
    try:
        return max(60.0, float(os.environ.get("TOSSINVEST_STOCKS_TTL_SEC", "86400")))
    except (TypeError, ValueError):
        return 86400.0


def _request(method: str, path: str, **kwargs):
    return _SESSION.request(method, BASE_URL + path, timeout=_timeout(), **kwargs)


def _access_token(force: bool = False) -> str | None:
    global _TOKEN, _TOKEN_EXP
    if not is_available():
        _set_last_error("credentials_missing")
        return None
    now = time.time()
    with _TOKEN_LOCK:
        if not force and _TOKEN and now < _TOKEN_EXP - 30:
            return _TOKEN

        client_id, client_secret = _credentials()
        try:
            resp = _request(
                "POST",
                TOKEN_PATH,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
        except requests.RequestException as exc:
            _set_last_error(f"token_request_error: {exc.__class__.__name__}")
            raise
        if resp.status_code >= 400:
            _set_last_error(_http_error("token", resp))
        resp.raise_for_status()
        data = resp.json() or {}
        token = str(data.get("access_token") or "").strip()
        if not token:
            _set_last_error("token_missing")
            return None
        try:
            expires_in = float(data.get("expires_in") or 3600)
        except (TypeError, ValueError):
            expires_in = 3600.0
        _TOKEN = token
        _TOKEN_EXP = now + max(60.0, expires_in)
        _set_last_error("")
        return _TOKEN


def _normalize_symbol(ticker: str) -> str | None:
    raw = str(ticker or "").strip().upper()
    if not raw:
        return None
    raw = raw.replace(".KS", "").replace(".KQ", "")
    if raw.isdigit():
        return raw.zfill(6)
    return raw


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _authed_get(path: str, params: dict[str, str], *, label: str = "request"):
    token = _access_token()
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        resp = _request("GET", path, headers=headers, params=params)
    except requests.RequestException as exc:
        _set_last_error(f"{label}_request_error: {exc.__class__.__name__}")
        raise
    if resp.status_code == 401:
        token = _access_token(force=True)
        if not token:
            return None
        headers["Authorization"] = f"Bearer {token}"
        resp = _request("GET", path, headers=headers, params=params)
    if resp.status_code == 429:
        try:
            wait = min(2.0, max(0.2, float(resp.headers.get("Retry-After", "0.5"))))
        except (TypeError, ValueError):
            wait = 0.5
        time.sleep(wait)
        resp = _request("GET", path, headers=headers, params=params)
    if resp.status_code >= 400:
        _set_last_error(_http_error(label, resp))
    resp.raise_for_status()
    data = resp.json()
    _set_last_error("")
    return data


def get_prices(symbols: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Return Toss realtime prices keyed by normalized symbol.

    Toss supports up to 200 symbols per request; this helper chunks larger
    inputs and returns only rows with a valid positive last price.
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        s = _normalize_symbol(symbol)
        if s and s not in seen:
            normalized.append(s)
            seen.add(s)
    if not normalized:
        return {}
    if not is_available():
        _set_last_error("credentials_missing")
        return {}

    out: dict[str, dict[str, Any]] = {}
    for idx in range(0, len(normalized), 200):
        chunk = normalized[idx : idx + 200]
        data = _authed_get(PRICES_PATH, {"symbols": ",".join(chunk)}, label="prices")
        if not isinstance(data, dict):
            continue
        rows = data.get("result") or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            sym = _normalize_symbol(str(row.get("symbol") or ""))
            price = _to_float(row.get("lastPrice"))
            if not sym or price is None or price <= 0:
                continue
            out[sym] = {
                "ticker": sym,
                "code": sym,
                "price": price,
                "currency": row.get("currency"),
                "timestamp": row.get("timestamp"),
                "source": "tossinvest",
            }
    return out


def get_quote(ticker: str) -> dict[str, Any] | None:
    symbol = _normalize_symbol(ticker)
    if not symbol:
        return None
    return get_prices([symbol]).get(symbol)


def get_stocks(symbols: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Return Toss stock master rows keyed by normalized symbol."""
    normalized: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        s = _normalize_symbol(symbol)
        if s and s not in seen:
            normalized.append(s)
            seen.add(s)
    if not normalized:
        return {}
    if not is_available():
        _set_last_error("credentials_missing")
        return {}

    out: dict[str, dict[str, Any]] = {}
    now = time.time()
    ttl = _stocks_ttl_sec()
    to_fetch: list[str] = []
    with _STOCKS_LOCK:
        for symbol in normalized:
            cached = _STOCKS_CACHE.get(symbol)
            if cached and now - cached[0] < ttl:
                out[symbol] = dict(cached[1])
            else:
                to_fetch.append(symbol)
    if not to_fetch:
        return out

    for idx in range(0, len(to_fetch), 200):
        chunk = to_fetch[idx : idx + 200]
        data = _authed_get(STOCKS_PATH, {"symbols": ",".join(chunk)}, label="stocks")
        if not isinstance(data, dict):
            continue
        rows = data.get("result") or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            sym = _normalize_symbol(str(row.get("symbol") or ""))
            if not sym:
                continue
            mapped = {
                "ticker": sym,
                "code": sym,
                "name": str(row.get("name") or "").strip(),
                "english_name": str(row.get("englishName") or "").strip(),
                "isin_code": row.get("isinCode"),
                "market": row.get("market"),
                "security_type": row.get("securityType"),
                "is_common_share": row.get("isCommonShare"),
                "status": row.get("status"),
                "currency": row.get("currency"),
                "list_date": row.get("listDate"),
                "delist_date": row.get("delistDate"),
                "shares_outstanding": _to_float(row.get("sharesOutstanding")),
                "source": "tossinvest",
            }
            out[sym] = mapped
            with _STOCKS_LOCK:
                _STOCKS_CACHE[sym] = (time.time(), dict(mapped))
    return out


def get_stock(ticker: str) -> dict[str, Any] | None:
    symbol = _normalize_symbol(ticker)
    if not symbol:
        return None
    return get_stocks([symbol]).get(symbol)


def get_daily_candles(ticker: str, count: int = 200) -> list[dict[str, Any]]:
    """Return latest daily candles in chronological order."""
    symbol = _normalize_symbol(ticker)
    if not symbol:
        return []
    try:
        n = max(1, min(200, int(count)))
    except (TypeError, ValueError):
        n = 200
    data = _authed_get(
        CANDLES_PATH,
        {
            "symbol": symbol,
            "interval": "1d",
            "count": str(n),
            "adjusted": "true",
        },
        label="candles",
    )
    if not isinstance(data, dict):
        return []
    result = data.get("result") or {}
    rows = result.get("candles") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        close = _to_float(row.get("closePrice"))
        high = _to_float(row.get("highPrice"))
        low = _to_float(row.get("lowPrice"))
        open_ = _to_float(row.get("openPrice"))
        volume = _to_float(row.get("volume"))
        if close is None or high is None or low is None:
            continue
        out.append({
            "timestamp": row.get("timestamp"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "currency": row.get("currency"),
            "source": "tossinvest",
        })
    out.sort(key=lambda x: str(x.get("timestamp") or ""))
    return out
