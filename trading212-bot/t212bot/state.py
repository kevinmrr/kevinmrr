"""Persistenter Zustand des Bots (offene Trades, Tagesstartwert, Cooldowns) + Trade-Journal."""
from __future__ import annotations

import csv
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class TradeState:
    ticker: str               # Trading-212-Ticker, z. B. AMD_US_EQ
    symbol: str               # Yahoo-Symbol, z. B. AMD
    quantity: float
    entry_price: float        # Instrumentenwährung
    initial_stop: float
    stop: float
    take_profit: float
    atr: float
    highest_price: float
    opened_at: str            # ISO-Zeitstempel (UTC)
    partial_taken: bool = False
    pending: bool = True      # Kauforder noch nicht im Depot bestätigt
    order_id: int | None = None
    pending_cycles: int = 0
    entry_score: float = 0.0
    entry_note: str = ""

    @property
    def opened_dt(self) -> datetime:
        return datetime.fromisoformat(self.opened_at)


@dataclass
class BotState:
    trades: dict[str, TradeState] = field(default_factory=dict)
    day: str = ""
    day_start_equity: float = 0.0
    cooldowns: dict[str, str] = field(default_factory=dict)   # ticker -> ISO-Zeitpunkt des Verkaufs
    last_scan: str = ""

    @classmethod
    def load(cls, path: str | Path) -> "BotState":
        p = Path(path)
        if not p.exists():
            return cls()
        raw: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        trades = {k: TradeState(**v) for k, v in raw.get("trades", {}).items()}
        return cls(
            trades=trades,
            day=raw.get("day", ""),
            day_start_equity=float(raw.get("day_start_equity", 0.0)),
            cooldowns=dict(raw.get("cooldowns", {})),
            last_scan=raw.get("last_scan", ""),
        )

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        # atomar schreiben, damit ein Absturz die Datei nicht beschädigt
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".state-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, p)

    def in_cooldown(self, ticker: str, days: int, now: datetime | None = None) -> bool:
        ts = self.cooldowns.get(ticker)
        if not ts:
            return False
        now = now or datetime.now(timezone.utc)
        return (now - datetime.fromisoformat(ts)).total_seconds() < days * 86400


JOURNAL_FIELDS = [
    "time", "action", "ticker", "symbol", "quantity", "price", "currency",
    "reason", "score", "realized_pnl_pct", "dry_run",
]


def journal(path: str | Path, **row: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    row.setdefault("time", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    with open(p, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=JOURNAL_FIELDS, extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerow(row)
