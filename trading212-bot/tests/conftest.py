from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from t212bot.broker import AccountSummary, Instrument, Order, Position  # noqa: E402
from t212bot.config import Config  # noqa: E402
from t212bot.news import Article  # noqa: E402
from t212bot.sentiment import NewsAssessment  # noqa: E402


def make_trend_df(days: int = 300, start: float = 100.0, drift: float = 0.0015, vol: float = 0.02,
                  seed: int = 1, volume: float = 5_000_000) -> pd.DataFrame:
    """Synthetische Tageskerzen mit Aufwärtstrend."""
    rng = np.random.default_rng(seed)
    rets = drift + vol * rng.standard_normal(days)
    close = start * np.exp(np.cumsum(rets))
    high = close * (1 + np.abs(rng.normal(0, 0.008, days)))
    low = close * (1 - np.abs(rng.normal(0, 0.008, days)))
    idx = pd.bdate_range(end=datetime(2026, 10, 20), periods=days)
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close,
                         "Volume": np.full(days, volume)}, index=idx)


class FakeBroker:
    def __init__(self, total=10_000.0, cash=10_000.0, currency="EUR"):
        self.summary = AccountSummary(currency, total, cash, total - cash, 0.0)
        self.positions_list: list[Position] = []
        self.orders: list[Order] = []
        self.placed: list[tuple[str, float]] = []
        self.fail_fractional = False

    def account_summary(self):
        return self.summary

    def positions(self):
        return list(self.positions_list)

    def open_orders(self):
        return list(self.orders)

    def place_market_order(self, ticker, quantity, extended_hours=False):
        from t212bot.broker import Trading212Error

        if self.fail_fractional and quantity != int(quantity):
            raise Trading212Error(400, "fractional not supported")
        self.placed.append((ticker, quantity))
        return Order(len(self.placed), ticker, "BUY" if quantity > 0 else "SELL", "MARKET", "NEW", quantity, 0)


class FakeMarket:
    def __init__(self, frames: dict[str, pd.DataFrame], fx: float = 0.9):
        self.frames = frames
        self.fx = fx

    def history(self, symbol, days=400):
        return self.frames.get(symbol, pd.DataFrame())

    def fx_rate(self, a, b):
        return 1.0 if a == b else self.fx


class FakeNews:
    def __init__(self, articles: dict[str, list[Article]] | None = None):
        self.articles = articles or {}

    def fetch(self, symbol, lookback_days, max_articles):
        return self.articles.get(symbol, [])[:max_articles]


class FakeAnalyzer:
    def __init__(self, by_symbol: dict[str, NewsAssessment] | None = None):
        self.by_symbol = by_symbol or {}
        self.calls: list[str] = []

    def assess(self, symbol, company, articles):
        self.calls.append(symbol)
        return self.by_symbol.get(symbol, NewsAssessment(0.0, 0.0, 0.0, article_count=len(articles)))


class AlwaysOpenClock:
    def is_open(self, now=None):
        return True

    def local_date(self, now=None):
        return (now or datetime.now(timezone.utc)).date().isoformat()


def article(headline: str, hours_ago: float = 2, summary: str = "") -> Article:
    return Article(headline, summary, "Test", "https://example.com",
                   datetime.now(timezone.utc) - timedelta(hours=hours_ago))


def instrument(symbol: str) -> Instrument:
    return Instrument(f"{symbol}_US_EQ", symbol, f"{symbol} Inc.", "USD", "STOCK")


@pytest.fixture
def cfg(tmp_path) -> Config:
    c = Config()
    c.paths.state_file = str(tmp_path / "state.json")
    c.paths.journal_file = str(tmp_path / "trades.csv")
    c.paths.instruments_cache = str(tmp_path / "instruments.json")
    c.universe.watchlist = ["AAA", "BBB"]
    c.scan.min_avg_dollar_volume = 1_000_000
    return c
