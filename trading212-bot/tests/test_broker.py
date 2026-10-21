import base64

import pytest

from t212bot.broker import Trading212Client, Trading212Error


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.content = b"x" if payload is not None else b""
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.headers = {}
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, timeout=None, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def client(responses, **kw):
    session = FakeSession(responses)
    c = Trading212Client("key", "secret", "demo", session=session, sleep=lambda s: None, **kw)
    return c, session


def test_basic_auth_header_and_base_url():
    c, session = client([])
    expected = base64.b64encode(b"key:secret").decode()
    assert session.headers["Authorization"] == f"Basic {expected}"
    assert c.base_url == "https://demo.trading212.com/api/v0"


def test_legacy_key_without_secret():
    session = FakeSession([])
    Trading212Client("legacy", "", "live", session=session)
    assert session.headers["Authorization"] == "legacy"


def test_account_summary_parsing():
    c, _ = client([FakeResponse(payload={
        "currency": "EUR", "totalValue": 1234.5,
        "cash": {"availableToTrade": 200.0, "inPies": 0, "reservedForOrders": 0},
        "investments": {"currentValue": 1034.5, "unrealizedProfitLoss": 34.5},
    })])
    s = c.account_summary()
    assert (s.currency, s.total_value, s.free_cash, s.unrealized_pnl) == ("EUR", 1234.5, 200.0, 34.5)


def test_positions_new_and_legacy_format():
    c, _ = client([FakeResponse(payload=[
        {"instrument": {"ticker": "AMD_US_EQ", "currency": "USD"}, "quantity": 2.5,
         "quantityAvailableForTrading": 2.5, "averagePricePaid": 150.0, "currentPrice": 160.0,
         "walletImpact": {"unrealizedProfitLoss": 22.0}, "createdAt": "2026-10-01T14:00:00Z"},
        {"ticker": "AAPL_US_EQ", "quantity": 1, "averagePrice": 200, "currentPrice": 210, "ppl": 9.1},
    ])])
    new, legacy = c.positions()
    assert new.ticker == "AMD_US_EQ" and new.average_price == 150 and new.unrealized_pnl == 22
    assert legacy.ticker == "AAPL_US_EQ" and legacy.average_price == 200 and legacy.quantity_available == 1


def test_market_sell_sends_negative_quantity():
    c, session = client([FakeResponse(payload={"id": 7, "ticker": "AMD_US_EQ", "quantity": -1.5,
                                               "status": "NEW", "type": "MARKET"})])
    order = c.place_market_order("AMD_US_EQ", -1.5)
    method, url, kwargs = session.calls[0]
    assert method == "POST" and url.endswith("/equity/orders/market")
    assert kwargs["json"] == {"ticker": "AMD_US_EQ", "quantity": -1.5, "extendedHours": False}
    assert order.side == "SELL" and order.id == 7


def test_retries_on_rate_limit_then_succeeds():
    c, session = client([FakeResponse(429, {"message": "slow down"}), FakeResponse(payload=[])])
    assert c.positions() == []
    assert len(session.calls) == 2


def test_error_raises_with_message():
    c, _ = client([FakeResponse(400, {"code": "InsufficientFunds"})])
    with pytest.raises(Trading212Error) as exc:
        c.place_market_order("AMD_US_EQ", 1)
    assert exc.value.status == 400 and "InsufficientFunds" in str(exc.value)


def test_throttle_waits_between_calls():
    waits = []
    now = [100.0]
    session = FakeSession([FakeResponse(payload=[]), FakeResponse(payload=[])])
    c = Trading212Client("k", "s", session=session, sleep=waits.append, clock=lambda: now[0])
    c.open_orders()
    c.open_orders()
    assert waits and waits[0] == pytest.approx(5.5)
