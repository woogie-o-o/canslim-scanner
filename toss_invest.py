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

_SESSION = requests.Session()
_TOKEN_LOCK = threading.Lock()
_TOKEN: str | None = None
_TOKEN_EXP: float = 0.0


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


def is_available() -> bool:
    client_id, client_secret = _credentials()
    return bool(client_id and client_secret)


def _timeout() -> float:
    try:
        return max(1.0, float(os.environ.get("TOSSINVEST_TIMEOUT_SEC", "5")))
    except (TypeError, ValueError):
        return 5.0


def _request(method: str, path: str, **kwargs):
    return _SESSION.request(method, BASE_URL + path, timeout=_timeout(), **kwargs)


def _access_token(force: bool = False) -> str | None:
    global _TOKEN, _TOKEN_EXP
    if not is_available():
        return None
    now = time.time()
    with _TOKEN_LOCK:
        if not force and _TOKEN and now < _TOKEN_EXP - 30:
            return _TOKEN

        client_id, client_secret = _credentials()
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
        resp.raise_for_status()
        data = resp.json() or {}
        token = str(data.get("access_token") or "").strip()
        if not token:
            return None
        try:
            expires_in = float(data.get("expires_in") or 3600)
        except (TypeError, ValueError):
            expires_in = 3600.0
        _TOKEN = token
        _TOKEN_EXP = now + max(60.0, expires_in)
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


def _authed_get(path: str, params: dict[str, str]):
    token = _access_token()
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    resp = _request("GET", path, headers=headers, params=params)
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
    resp.raise_for_status()
    return resp.json()


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
    if not normalized or not is_available():
        return {}

    out: dict[str, dict[str, Any]] = {}
    for idx in range(0, len(normalized), 200):
        chunk = normalized[idx : idx + 200]
        data = _authed_get(PRICES_PATH, {"symbols": ",".join(chunk)})
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
