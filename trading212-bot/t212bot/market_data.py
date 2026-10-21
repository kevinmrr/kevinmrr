"""Kursdaten und Wechselkurse über Yahoo Finance (yfinance, kein API-Key nötig).

Trading 212 liefert über die API keine Kurshistorie, daher kommt sie von hier.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

log = logging.getLogger(__name__)


class MarketData:
    def __init__(self, cache_minutes: int = 15):
        import yfinance as yf  # Import hier, damit Tests ohne Netzwerk laufen

        self._yf = yf
        self._cache_ttl = timedelta(minutes=cache_minutes)
        self._history_cache: dict[tuple[str, int], tuple[datetime, pd.DataFrame]] = {}
        self._fx_cache: dict[str, tuple[datetime, float]] = {}

    def history(self, symbol: str, days: int = 400) -> pd.DataFrame:
        """Tageskerzen (Open, High, Low, Close, Volume), dividenden-/split-bereinigt."""
        key = (symbol, days)
        now = datetime.now(timezone.utc)
        cached = self._history_cache.get(key)
        if cached and now - cached[0] < self._cache_ttl:
            return cached[1]
        try:
            df = self._yf.Ticker(symbol).history(
                start=(now - timedelta(days=days)).date().isoformat(), interval="1d", auto_adjust=True
            )
        except Exception as exc:  # yfinance wirft sehr unterschiedliche Fehler
            log.warning("Keine Kursdaten für %s: %s", symbol, exc)
            return pd.DataFrame()
        if df is None or df.empty:
            return pd.DataFrame()
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        self._history_cache[key] = (now, df)
        return df

    def fx_rate(self, from_ccy: str, to_ccy: str) -> float:
        """Umrechnungskurs: 1 Einheit from_ccy = x Einheiten to_ccy."""
        from_ccy, to_ccy = from_ccy.upper(), to_ccy.upper()
        if from_ccy == to_ccy:
            return 1.0
        # Trading 212 führt britische Aktien in Pence (GBX)
        factor = 1.0
        if from_ccy == "GBX":
            from_ccy, factor = "GBP", 0.01
        pair = f"{from_ccy}{to_ccy}"
        now = datetime.now(timezone.utc)
        cached = self._fx_cache.get(pair)
        if cached and now - cached[0] < timedelta(hours=1):
            return cached[1] * factor
        df = self._yf.Ticker(f"{pair}=X").history(period="5d", interval="1d")
        if df is None or df.empty:
            raise RuntimeError(f"Kein Wechselkurs für {pair} verfügbar")
        rate = float(df["Close"].dropna().iloc[-1])
        self._fx_cache[pair] = (now, rate)
        return rate * factor
