"""Aktienauswahl: technische Bewertung + Nachrichtenbewertung -> Gesamt-Score."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import ScanConfig, StrategyConfig
from .indicators import TechnicalSnapshot
from .sentiment import NewsAssessment


@dataclass
class Candidate:
    symbol: str
    t212_ticker: str
    company: str
    currency: str
    tech: TechnicalSnapshot
    tech_score: float = 0.0
    tech_reasons: list[str] = field(default_factory=list)
    news: NewsAssessment | None = None
    news_score: float = 0.0
    total_score: float = 0.0
    buy: bool = False
    decision: str = ""


def _ok(x: float) -> bool:
    return x is not None and not math.isnan(x)


def filter_reason(snap: TechnicalSnapshot, scan: ScanConfig, strategy: StrategyConfig) -> str | None:
    """Gibt einen Ablehnungsgrund zurück oder None, wenn die Aktie ins Risikoprofil passt."""
    if snap.price < scan.min_price:
        return f"Kurs unter {scan.min_price}"
    if not _ok(snap.avg_dollar_volume) or snap.avg_dollar_volume < scan.min_avg_dollar_volume:
        return "zu geringe Liquidität"
    if not _ok(snap.volatility):
        return "Volatilität unbekannt"
    if snap.volatility < strategy.min_volatility:
        return f"Volatilität {snap.volatility:.0%} zu niedrig (wenig Gewinnpotenzial)"
    if snap.volatility > strategy.max_volatility:
        return f"Volatilität {snap.volatility:.0%} zu hoch (Risiko)"
    if not _ok(snap.atr14) or snap.atr14 <= 0:
        return "ATR unbekannt"
    return None


def technical_score(snap: TechnicalSnapshot) -> tuple[float, list[str]]:
    """0..1 – bevorzugt intakte Aufwärtstrends mit Momentum, ohne überkauft zu sein."""
    reasons: list[str] = []

    # Trend (30 %)
    trend = 0.0
    if snap.price > snap.sma50:
        trend += 0.4
        reasons.append("Kurs über 50-Tage-Linie")
    if snap.sma20 > snap.sma50:
        trend += 0.3
    if _ok(snap.sma200):
        if snap.sma50 > snap.sma200:
            trend += 0.3
            reasons.append("50- über 200-Tage-Linie")
    else:
        trend += 0.15

    # Momentum (30 %)
    m3 = snap.return_3m if _ok(snap.return_3m) else 0.0
    m1 = snap.return_1m if _ok(snap.return_1m) else 0.0
    momentum = 0.6 * min(1.0, max(0.0, 0.5 + m3 / 0.5)) + 0.4 * min(1.0, max(0.0, 0.5 + m1 / 0.25))
    if m3 > 0.15:
        reasons.append(f"3-Monats-Performance {m3:+.0%}")

    # RSI (20 %) – gesunder Bereich 50-65
    r = snap.rsi14
    if not _ok(r):
        rsi_s = 0.3
    elif 50 <= r <= 65:
        rsi_s = 1.0
    elif 65 < r <= 72:
        rsi_s = 0.7
    elif 45 <= r < 50:
        rsi_s = 0.6
    elif 72 < r <= 78:
        rsi_s = 0.35
    elif r > 78:
        rsi_s = 0.05
        reasons.append(f"überkauft (RSI {r:.0f})")
    else:
        rsi_s = 0.2

    # Volumen (10 %) – steigendes Interesse
    vr = snap.volume_ratio if _ok(snap.volume_ratio) else 1.0
    volume_s = min(1.0, max(0.0, (vr - 0.7) / 0.8))
    if vr >= 1.3:
        reasons.append(f"Volumen {vr:.1f}x über Durchschnitt")

    # Nähe zum 52-Wochen-Hoch (10 %) – relative Stärke
    d = snap.distance_to_high if _ok(snap.distance_to_high) else 0.5
    high_s = min(1.0, max(0.0, 1 - d / 0.3))

    score = 0.30 * trend + 0.30 * momentum + 0.20 * rsi_s + 0.10 * volume_s + 0.10 * high_s
    return round(min(1.0, max(0.0, score)), 4), reasons


def news_score(a: NewsAssessment | None) -> float:
    """0..1 – 0.5 = neutral. Warnsignale führen zu 0."""
    if a is None or a.article_count == 0:
        return 0.5
    if a.red_flags:
        return 0.0
    weight = 0.5 + 0.5 * a.confidence
    score = 0.5 + 0.4 * a.sentiment * weight
    score += 0.1 * a.catalyst_strength * (1 if a.sentiment > 0 else -1 if a.sentiment < 0 else 0)
    return round(min(1.0, max(0.0, score)), 4)


def combine(c: Candidate, strategy: StrategyConfig) -> float:
    total_w = strategy.weight_technical + strategy.weight_news
    c.news_score = news_score(c.news)
    c.total_score = round(
        (strategy.weight_technical * c.tech_score + strategy.weight_news * c.news_score) / total_w, 4
    )
    return c.total_score


def buy_decision(c: Candidate, strategy: StrategyConfig) -> tuple[bool, str]:
    snap, news = c.tech, c.news
    if snap.price <= snap.sma50:
        return False, "kein Aufwärtstrend (Kurs unter 50-Tage-Linie)"
    if _ok(snap.rsi14) and snap.rsi14 > 78:
        return False, f"überkauft (RSI {snap.rsi14:.0f})"
    if news is None or news.article_count < strategy.min_news_articles:
        return False, "zu wenige aktuelle Nachrichten"
    if news.red_flags:
        return False, "Warnsignale: " + ", ".join(news.red_flags)
    if news.sentiment < strategy.min_news_sentiment:
        return False, f"Nachrichtenlage nicht positiv genug ({news.sentiment:+.2f})"
    if c.total_score < strategy.buy_threshold:
        return False, f"Score {c.total_score:.2f} unter Schwelle {strategy.buy_threshold:.2f}"
    return True, f"Kaufsignal (Score {c.total_score:.2f})"
