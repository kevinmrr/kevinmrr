"""Technische Kennzahlen auf Basis von Tageskerzen."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI nach Wilder."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range nach Wilder."""
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [df["High"] - df["Low"], (df["High"] - prev_close).abs(), (df["Low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def annualized_volatility(close: pd.Series, window: int = 60) -> float:
    returns = np.log(close / close.shift(1)).dropna().tail(window)
    if len(returns) < 2:
        return float("nan")
    return float(returns.std() * np.sqrt(TRADING_DAYS))


def pct_return(close: pd.Series, days: int) -> float:
    if len(close) <= days:
        return float("nan")
    return float(close.iloc[-1] / close.iloc[-1 - days] - 1)


@dataclass
class TechnicalSnapshot:
    price: float
    sma20: float
    sma50: float
    sma200: float          # NaN bei zu kurzer Historie
    rsi14: float
    atr14: float
    return_1m: float
    return_3m: float
    volatility: float      # annualisiert, 60 Tage
    volume_ratio: float    # Ø-Volumen 5 Tage / 50 Tage
    avg_dollar_volume: float
    distance_to_high: float  # Abstand zum 52-Wochen-Hoch (0 = am Hoch)
    bars: int


def snapshot(df: pd.DataFrame) -> TechnicalSnapshot | None:
    """Berechnet alle Kennzahlen; None, wenn zu wenig Daten vorliegen."""
    if df is None or len(df) < 60:
        return None
    close, volume = df["Close"], df["Volume"]
    price = float(close.iloc[-1])
    vol50 = float(volume.tail(50).mean())
    high_52w = float(df["High"].tail(TRADING_DAYS).max())
    return TechnicalSnapshot(
        price=price,
        sma20=float(sma(close, 20).iloc[-1]),
        sma50=float(sma(close, 50).iloc[-1]),
        sma200=float(sma(close, 200).iloc[-1]) if len(close) >= 200 else float("nan"),
        rsi14=float(rsi(close).iloc[-1]),
        atr14=float(atr(df).iloc[-1]),
        return_1m=pct_return(close, 21),
        return_3m=pct_return(close, 63),
        volatility=annualized_volatility(close),
        volume_ratio=float(volume.tail(5).mean() / vol50) if vol50 > 0 else float("nan"),
        avg_dollar_volume=float((close * volume).tail(20).mean()),
        distance_to_high=1 - price / high_52w if high_52w > 0 else float("nan"),
        bars=len(df),
    )
