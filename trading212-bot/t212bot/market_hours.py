"""Handelszeiten der US-Börsen (NYSE/Nasdaq)."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from .config import MarketHoursConfig

# Ganztägige Feiertage der NYSE (Quelle: nyse.com, Stand 2026). Weitere Tage lassen sich
# über market_hours.holidays in der config.yaml ergänzen.
NYSE_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}


class MarketClock:
    def __init__(self, cfg: MarketHoursConfig):
        self.tz = ZoneInfo(cfg.timezone)
        self.open = time.fromisoformat(cfg.open)
        self.close = time.fromisoformat(cfg.close)
        self.holidays = NYSE_HOLIDAYS | set(cfg.holidays)

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d.isoformat() not in self.holidays

    def is_open(self, now: datetime | None = None) -> bool:
        local = (now or datetime.now(timezone.utc)).astimezone(self.tz)
        return self.is_trading_day(local.date()) and self.open <= local.time() < self.close

    def local_date(self, now: datetime | None = None) -> str:
        return (now or datetime.now(timezone.utc)).astimezone(self.tz).date().isoformat()
