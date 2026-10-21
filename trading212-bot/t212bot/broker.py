"""Client für die Trading 212 Public API (v0).

Doku: https://docs.trading212.com/api
- Auth: HTTP Basic mit API-Key (Benutzer) und API-Secret (Passwort).
  Ältere Schlüssel ohne Secret werden direkt im Authorization-Header gesendet.
- Im Live-Konto unterstützt die API nur Market-Orders. Stop-Loss/Take-Profit
  werden daher vom Bot selbst überwacht und per Market-Order ausgeführt.
- Verkäufe sind Market-Orders mit negativer Stückzahl.
"""
from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

log = logging.getLogger(__name__)

BASE_URLS = {
    "demo": "https://demo.trading212.com/api/v0",
    "live": "https://live.trading212.com/api/v0",
}

# Mindestabstand zwischen zwei Aufrufen je Endpunkt (Sekunden), abgeleitet aus den
# dokumentierten Rate-Limits plus etwas Puffer.
MIN_INTERVAL = {
    "account_summary": 5.5,
    "positions": 1.1,
    "instruments": 51.0,
    "orders": 5.5,
    "order": 1.1,
    "market_order": 1.3,
    "cancel_order": 1.3,
}


class Trading212Error(RuntimeError):
    def __init__(self, status: int, message: str, payload: Any = None):
        super().__init__(f"Trading 212 API Fehler {status}: {message}")
        self.status = status
        self.payload = payload


@dataclass
class AccountSummary:
    currency: str
    total_value: float
    free_cash: float
    invested_value: float
    unrealized_pnl: float


@dataclass
class Position:
    ticker: str
    quantity: float
    quantity_available: float
    average_price: float       # in Instrumentenwährung
    current_price: float       # in Instrumentenwährung
    currency: str | None
    unrealized_pnl: float | None  # in Kontowährung
    opened_at: datetime | None = None


@dataclass
class Instrument:
    ticker: str
    short_name: str
    name: str
    currency: str
    type: str
    isin: str = ""
    max_open_quantity: float | None = None


@dataclass
class Order:
    id: int
    ticker: str
    side: str
    type: str
    status: str
    quantity: float
    filled_quantity: float


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class Trading212Client:
    def __init__(
        self,
        api_key: str,
        api_secret: str = "",
        environment: str = "demo",
        session: requests.Session | None = None,
        timeout: float = 20.0,
        max_retries: int = 4,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        if not api_key:
            raise ValueError("T212_API_KEY fehlt (siehe .env.example)")
        if environment not in BASE_URLS:
            raise ValueError(f"Unbekannte Umgebung: {environment}")
        self.environment = environment
        self.base_url = BASE_URLS[environment]
        self.session = session or requests.Session()
        if api_secret:
            token = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
            auth = f"Basic {token}"
        else:
            auth = api_key
        self.session.headers.update({"Authorization": auth, "Content-Type": "application/json"})
        self.timeout = timeout
        self.max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._last_call: dict[str, float] = {}

    # ------------------------------------------------------------------ intern
    def _throttle(self, key: str) -> None:
        interval = MIN_INTERVAL.get(key, 1.0)
        last = self._last_call.get(key)
        if last is not None:
            wait = interval - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_call[key] = self._clock()

    def _request(self, method: str, path: str, limit_key: str, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        for attempt in range(self.max_retries + 1):
            self._throttle(limit_key)
            try:
                resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise
                backoff = 2 ** attempt
                log.warning("Netzwerkfehler bei %s %s (%s) – neuer Versuch in %ss", method, path, exc, backoff)
                self._sleep(backoff)
                continue

            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= self.max_retries:
                    break
                wait = self._retry_after(resp, attempt)
                log.warning("HTTP %s bei %s %s – warte %.1fs", resp.status_code, method, path, wait)
                self._sleep(wait)
                continue
            break

        if resp.status_code >= 400:
            try:
                payload = resp.json()
            except ValueError:
                payload = resp.text
            if isinstance(payload, dict):
                message = payload.get("message") or payload.get("code") or payload
            else:
                message = payload
            raise Trading212Error(resp.status_code, str(message)[:300], payload)
        if not resp.content:
            return None
        return resp.json()

    @staticmethod
    def _retry_after(resp: requests.Response, attempt: int) -> float:
        reset = resp.headers.get("x-ratelimit-reset")
        if reset:
            try:
                return max(1.0, min(60.0, float(reset) - time.time()))
            except ValueError:
                pass
        return float(min(60, 2 ** (attempt + 1)))

    # ------------------------------------------------------------- Endpunkte
    def account_summary(self) -> AccountSummary:
        data = self._request("GET", "/equity/account/summary", "account_summary")
        cash = data.get("cash") or {}
        inv = data.get("investments") or {}
        return AccountSummary(
            currency=data.get("currency", ""),
            total_value=_f(data.get("totalValue")),
            free_cash=_f(cash.get("availableToTrade")),
            invested_value=_f(inv.get("currentValue")),
            unrealized_pnl=_f(inv.get("unrealizedProfitLoss")),
        )

    def positions(self) -> list[Position]:
        data = self._request("GET", "/equity/positions", "positions") or []
        return [self._parse_position(p) for p in data]

    @staticmethod
    def _parse_position(p: dict[str, Any]) -> Position:
        instrument = p.get("instrument") or {}
        wallet = p.get("walletImpact") or {}
        qty = _f(p.get("quantity"))
        return Position(
            ticker=instrument.get("ticker") or p.get("ticker", ""),
            quantity=qty,
            quantity_available=_f(p.get("quantityAvailableForTrading"), qty),
            average_price=_f(p.get("averagePricePaid", p.get("averagePrice"))),
            current_price=_f(p.get("currentPrice")),
            currency=instrument.get("currency"),
            unrealized_pnl=_f(wallet.get("unrealizedProfitLoss", p.get("ppl")), 0.0),
            opened_at=_dt(p.get("createdAt") or p.get("initialFillDate")),
        )

    def instruments(self) -> list[Instrument]:
        data = self._request("GET", "/equity/metadata/instruments", "instruments") or []
        return [
            Instrument(
                ticker=i.get("ticker", ""),
                short_name=i.get("shortName", ""),
                name=i.get("name", ""),
                currency=i.get("currencyCode", ""),
                type=i.get("type", ""),
                isin=i.get("isin", ""),
                max_open_quantity=_f(i.get("maxOpenQuantity"), 0.0) or None,
            )
            for i in data
        ]

    def open_orders(self) -> list[Order]:
        data = self._request("GET", "/equity/orders", "orders") or []
        return [self._parse_order(o) for o in data]

    def get_order(self, order_id: int) -> Order:
        return self._parse_order(self._request("GET", f"/equity/orders/{order_id}", "order"))

    @staticmethod
    def _parse_order(o: dict[str, Any]) -> Order:
        instrument = o.get("instrument") or {}
        qty = _f(o.get("quantity"))
        side = o.get("side") or ("SELL" if qty < 0 else "BUY")
        return Order(
            id=int(o.get("id", 0)),
            ticker=o.get("ticker") or instrument.get("ticker", ""),
            side=side,
            type=o.get("type", ""),
            status=o.get("status", ""),
            quantity=qty,
            filled_quantity=_f(o.get("filledQuantity")),
        )

    def place_market_order(self, ticker: str, quantity: float, extended_hours: bool = False) -> Order:
        """Kauf (quantity > 0) oder Verkauf (quantity < 0) zum Marktpreis."""
        if quantity == 0:
            raise ValueError("quantity darf nicht 0 sein")
        body = {"ticker": ticker, "quantity": quantity, "extendedHours": extended_hours}
        data = self._request("POST", "/equity/orders/market", "market_order", json=body)
        return self._parse_order(data or {})

    def cancel_order(self, order_id: int) -> None:
        self._request("DELETE", f"/equity/orders/{order_id}", "cancel_order")
