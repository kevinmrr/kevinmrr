"""Nachrichtenquellen: Finnhub (empfohlen, kostenloser Key) oder Yahoo Finance."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import requests

log = logging.getLogger(__name__)


@dataclass
class Article:
    headline: str
    summary: str
    source: str
    url: str
    published: datetime


class NewsProvider(Protocol):
    def fetch(self, symbol: str, lookback_days: int, max_articles: int) -> list[Article]: ...


def _dedupe_sort(articles: list[Article], max_articles: int) -> list[Article]:
    seen: set[str] = set()
    unique = []
    for a in sorted(articles, key=lambda a: a.published, reverse=True):
        key = a.headline.strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(a)
    return unique[:max_articles]


class FinnhubNews:
    """https://finnhub.io/docs/api/company-news (Free-Tier: 60 Aufrufe/Minute)."""

    URL = "https://finnhub.io/api/v1/company-news"

    def __init__(self, api_key: str, session: requests.Session | None = None, min_interval: float = 1.1):
        if not api_key:
            raise ValueError("FINNHUB_API_KEY fehlt – setze ihn in der .env oder nutze news.provider: yahoo")
        self.session = session or requests.Session()
        self.session.headers.update({"X-Finnhub-Token": api_key})
        self.min_interval = min_interval
        self._last = 0.0

    def fetch(self, symbol: str, lookback_days: int, max_articles: int) -> list[Article]:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        today = datetime.now(timezone.utc).date()
        params = {
            "symbol": symbol,
            "from": (today - timedelta(days=lookback_days)).isoformat(),
            "to": today.isoformat(),
        }
        try:
            resp = self.session.get(self.URL, params=params, timeout=15)
            resp.raise_for_status()
            items = resp.json() or []
        except (requests.RequestException, ValueError) as exc:
            log.warning("Finnhub-News für %s nicht abrufbar: %s", symbol, exc)
            return []
        articles = [
            Article(
                headline=str(i.get("headline", "")),
                summary=str(i.get("summary", "")),
                source=str(i.get("source", "")),
                url=str(i.get("url", "")),
                published=datetime.fromtimestamp(int(i.get("datetime", 0)), tz=timezone.utc),
            )
            for i in items
            if i.get("headline")
        ]
        return _dedupe_sort(articles, max_articles)


class YahooNews:
    """Nachrichten über yfinance – ohne Key, aber weniger vollständig."""

    def __init__(self):
        import yfinance as yf

        self._yf = yf

    def fetch(self, symbol: str, lookback_days: int, max_articles: int) -> list[Article]:
        try:
            items = self._yf.Ticker(symbol).news or []
        except Exception as exc:
            log.warning("Yahoo-News für %s nicht abrufbar: %s", symbol, exc)
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        articles = [a for a in (self._parse(i) for i in items) if a and a.published >= cutoff]
        return _dedupe_sort(articles, max_articles)

    @staticmethod
    def _parse(item: dict[str, Any]) -> Article | None:
        content = item.get("content")
        if isinstance(content, dict):  # neues Format
            pub = content.get("pubDate") or content.get("displayTime")
            try:
                published = datetime.fromisoformat(str(pub).replace("Z", "+00:00"))
            except ValueError:
                return None
            url = (content.get("canonicalUrl") or {}).get("url", "")
            return Article(
                headline=str(content.get("title", "")),
                summary=str(content.get("summary", "")),
                source=str((content.get("provider") or {}).get("displayName", "")),
                url=url,
                published=published,
            )
        if item.get("title"):  # altes Format
            return Article(
                headline=str(item["title"]),
                summary="",
                source=str(item.get("publisher", "")),
                url=str(item.get("link", "")),
                published=datetime.fromtimestamp(int(item.get("providerPublishTime", 0)), tz=timezone.utc),
            )
        return None
