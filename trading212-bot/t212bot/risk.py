"""Risikomanagement: Positionsgröße, Stop-Loss, Gewinnmitnahme, Trailing-Stop, Zeit-Stop."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import RiskConfig
from .state import TradeState


def floor_to(value: float, decimals: int) -> float:
    factor = 10 ** decimals
    return math.floor(value * factor + 1e-9) / factor


@dataclass
class SizingResult:
    quantity: float
    value: float          # in Kontowährung
    reason: str


def position_size(
    *,
    equity: float,
    free_cash: float,
    price: float,
    atr: float,
    fx_to_account: float,
    cfg: RiskConfig,
) -> SizingResult:
    """Stückzahl so, dass ein Stop-Loss-Treffer höchstens risk_per_trade_pct kostet.

    price und atr sind in Instrumentenwährung, equity/free_cash in Kontowährung.
    """
    if price <= 0 or atr <= 0 or equity <= 0:
        return SizingResult(0.0, 0.0, "ungültige Eingaben")
    price_acc = price * fx_to_account
    stop_distance_acc = cfg.stop_atr_multiple * atr * fx_to_account

    qty_by_risk = (equity * cfg.risk_per_trade_pct) / stop_distance_acc
    qty_by_cap = (equity * cfg.max_position_pct) / price_acc
    spendable = free_cash - equity * cfg.cash_reserve_pct
    qty_by_cash = max(0.0, spendable) / price_acc

    limits = {"Risiko": qty_by_risk, "Positionslimit": qty_by_cap, "Cash": qty_by_cash}
    binding = min(limits, key=limits.get)  # type: ignore[arg-type]
    qty = floor_to(limits[binding], cfg.quantity_decimals)
    value = qty * price_acc
    if value < cfg.min_order_value:
        return SizingResult(0.0, 0.0, f"Ordervolumen zu klein (begrenzt durch {binding})")
    return SizingResult(qty, round(value, 2), f"begrenzt durch {binding}")


def initial_levels(entry: float, atr: float, cfg: RiskConfig) -> tuple[float, float]:
    """(Stop-Loss, Kursziel) in Instrumentenwährung."""
    stop = entry - cfg.stop_atr_multiple * atr
    target = entry + cfg.take_profit_r_multiple * (entry - stop)
    return round(max(stop, 0.01), 4), round(target, 4)


@dataclass
class ExitSignal:
    reason: str
    fraction: float  # Anteil der Position, der verkauft werden soll (0..1]


def evaluate_exit(trade: TradeState, price: float, cfg: RiskConfig, now: datetime | None = None) -> ExitSignal | None:
    """Prüft die Ausstiegsregeln und zieht dabei den Trailing-Stop nach (verändert trade)."""
    now = now or datetime.now(timezone.utc)
    if price <= 0:
        return None
    trade.highest_price = max(trade.highest_price, price)
    risk_unit = trade.entry_price - trade.initial_stop

    # Trailing-Stop nachziehen, sobald der Trade ausreichend im Gewinn war
    if risk_unit > 0 and trade.highest_price >= trade.entry_price + cfg.trailing_activation_r * risk_unit:
        trail = trade.highest_price - cfg.trailing_atr_multiple * trade.atr
        trade.stop = round(max(trade.stop, trail), 4)

    if price <= trade.stop:
        reason = "stop_loss" if trade.stop <= trade.initial_stop else "trailing_stop"
        return ExitSignal(reason, 1.0)

    if price >= trade.take_profit and not trade.partial_taken:
        fraction = cfg.partial_take_profit_fraction
        return ExitSignal("take_profit", 1.0 if fraction >= 1 else fraction)

    held_days = (now - trade.opened_dt).total_seconds() / 86400
    if held_days >= cfg.max_holding_days and price / trade.entry_price - 1 < cfg.min_progress_pct:
        return ExitSignal("time_stop", 1.0)
    return None
