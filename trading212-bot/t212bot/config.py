"""Konfiguration: YAML-Datei + Umgebungsvariablen (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

LIVE_CONFIRM_VALUE = "I_ACCEPT_THE_RISK"


@dataclass
class UniverseConfig:
    watchlist: list[str] = field(default_factory=lambda: ["AAPL", "MSFT", "NVDA", "AMD", "AMZN"])
    exclude: list[str] = field(default_factory=list)


@dataclass
class ScanConfig:
    interval_minutes: int = 30
    manage_interval_minutes: int = 5
    history_days: int = 400
    max_candidates_for_news: int = 12
    min_price: float = 5.0
    min_avg_dollar_volume: float = 20_000_000


@dataclass
class StrategyConfig:
    min_volatility: float = 0.22
    max_volatility: float = 0.70
    weight_technical: float = 0.55
    weight_news: float = 0.45
    buy_threshold: float = 0.62
    min_news_sentiment: float = 0.15
    min_news_articles: int = 1
    exit_on_negative_news: bool = True
    negative_news_exit_sentiment: float = -0.5


@dataclass
class RiskConfig:
    max_positions: int = 8
    max_position_pct: float = 0.12
    risk_per_trade_pct: float = 0.01
    cash_reserve_pct: float = 0.10
    stop_atr_multiple: float = 2.0
    take_profit_r_multiple: float = 3.0
    partial_take_profit_fraction: float = 0.5
    trailing_activation_r: float = 1.0
    trailing_atr_multiple: float = 2.5
    max_holding_days: int = 20
    min_progress_pct: float = 0.02
    daily_loss_limit_pct: float = 0.03
    reentry_cooldown_days: int = 3
    max_new_positions_per_cycle: int = 2
    min_order_value: float = 25.0
    quantity_decimals: int = 2


@dataclass
class NewsConfig:
    provider: str = "finnhub"
    lookback_days: int = 5
    max_articles: int = 15
    analyzer: str = "claude"
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "medium"


@dataclass
class MarketHoursConfig:
    timezone: str = "America/New_York"
    open: str = "09:35"
    close: str = "15:50"
    holidays: list[str] = field(default_factory=list)


@dataclass
class PathsConfig:
    state_file: str = "data/state.json"
    journal_file: str = "data/trades.csv"
    instruments_cache: str = "data/instruments.json"
    log_file: str = "logs/bot.log"


@dataclass
class Secrets:
    t212_api_key: str = ""
    t212_api_secret: str = ""
    finnhub_api_key: str = ""
    live_confirm: str = ""


@dataclass
class Config:
    environment: str = "demo"
    dry_run: bool = False
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    scan: ScanConfig = field(default_factory=ScanConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    news: NewsConfig = field(default_factory=NewsConfig)
    market_hours: MarketHoursConfig = field(default_factory=MarketHoursConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    secrets: Secrets = field(default_factory=Secrets)

    def validate(self) -> None:
        if self.environment not in ("demo", "live"):
            raise ValueError("environment muss 'demo' oder 'live' sein")
        if self.environment == "live" and not self.dry_run and self.secrets.live_confirm != LIVE_CONFIRM_VALUE:
            raise ValueError(
                "Echtgeld-Modus gesperrt: setze T212_LIVE_CONFIRM="
                f"{LIVE_CONFIRM_VALUE} in der .env, wenn du das Risiko bewusst akzeptierst."
            )
        if not 0 < self.risk.max_position_pct <= 1:
            raise ValueError("risk.max_position_pct muss zwischen 0 und 1 liegen")
        if not 0 < self.risk.risk_per_trade_pct <= 0.05:
            raise ValueError("risk.risk_per_trade_pct muss zwischen 0 und 0.05 liegen")
        if self.strategy.min_volatility >= self.strategy.max_volatility:
            raise ValueError("strategy.min_volatility muss kleiner als max_volatility sein")
        if self.news.provider not in ("finnhub", "yahoo"):
            raise ValueError("news.provider muss 'finnhub' oder 'yahoo' sein")
        if self.news.analyzer not in ("claude", "lexicon"):
            raise ValueError("news.analyzer muss 'claude' oder 'lexicon' sein")


def _build(cls: type, data: dict[str, Any] | None) -> Any:
    data = data or {}
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ValueError(f"Unbekannte Konfigurationsschlüssel in {cls.__name__}: {sorted(unknown)}")
    kwargs = {}
    for name, value in data.items():
        default = known[name].default_factory() if callable(known[name].default_factory) else None  # type: ignore[misc]
        if is_dataclass(default):
            kwargs[name] = _build(type(default), value)
        else:
            kwargs[name] = value
    return cls(**kwargs)


def load_config(path: str | Path | None = "config.yaml", dry_run: bool | None = None) -> Config:
    """Lädt die YAML-Konfiguration (falls vorhanden) und die Secrets aus der Umgebung."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # pragma: no cover - optional
        pass

    data: dict[str, Any] = {}
    if path and Path(path).exists():
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    data.pop("secrets", None)  # Secrets nie aus der YAML-Datei lesen
    cfg: Config = _build(Config, data)
    if dry_run is not None:
        cfg.dry_run = dry_run
    cfg.secrets = Secrets(
        t212_api_key=os.getenv("T212_API_KEY", ""),
        t212_api_secret=os.getenv("T212_API_SECRET", ""),
        finnhub_api_key=os.getenv("FINNHUB_API_KEY", ""),
        live_confirm=os.getenv("T212_LIVE_CONFIRM", ""),
    )
    cfg.validate()
    return cfg
