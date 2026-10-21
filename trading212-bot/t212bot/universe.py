"""Zuordnung Yahoo-Symbol <-> Trading-212-Instrument (mit Tages-Cache)."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .broker import Instrument, Trading212Client

log = logging.getLogger(__name__)


def _norm(symbol: str) -> str:
    return symbol.upper().replace("-", ".").replace("/", ".")


class InstrumentMap:
    def __init__(self, instruments: list[Instrument]):
        self.by_ticker = {i.ticker: i for i in instruments}
        self._by_symbol: dict[str, Instrument] = {}
        for i in instruments:
            if i.type != "STOCK" or not i.ticker.endswith("_US_EQ"):
                continue
            # Bevorzugt exakte Ticker wie "AMD_US_EQ"; daneben über shortName zuordnen
            base = i.ticker[: -len("_US_EQ")]
            for key in {_norm(i.short_name), _norm(base)}:
                if key and (key not in self._by_symbol or base == key):
                    self._by_symbol[key] = i

    def resolve(self, symbol: str) -> Instrument | None:
        return self._by_symbol.get(_norm(symbol))

    @classmethod
    def load(cls, client: Trading212Client, cache_path: str | Path, max_age_hours: int = 24) -> "InstrumentMap":
        p = Path(cache_path)
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(raw.get("fetched_at", "1970-01-01T00:00:00+00:00"))
            if datetime.now(timezone.utc) - fetched < timedelta(hours=max_age_hours):
                return cls([Instrument(**i) for i in raw.get("instruments", [])])
        log.info("Lade Instrumentenliste von Trading 212 …")
        instruments = client.instruments()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(),
                        "instruments": [asdict(i) for i in instruments]}),
            encoding="utf-8",
        )
        return cls(instruments)
