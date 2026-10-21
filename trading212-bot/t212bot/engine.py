"""Die Handels-Engine: verbindet Broker, Marktdaten, Nachrichten, Scoring und Risikomanagement."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable

from .broker import AccountSummary, Order, Position, Trading212Client, Trading212Error
from .config import Config
from .indicators import snapshot
from .market_data import MarketData
from .market_hours import MarketClock
from .news import NewsProvider
from .risk import evaluate_exit, floor_to, initial_levels, position_size
from .scoring import Candidate, buy_decision, combine, filter_reason, technical_score
from .sentiment import NewsAnalyzer
from .state import BotState, TradeState, journal
from .universe import InstrumentMap

log = logging.getLogger(__name__)

PENDING_GRACE_CYCLES = 3


class TradingBot:
    def __init__(
        self,
        cfg: Config,
        broker: Trading212Client,
        market: MarketData,
        news: NewsProvider,
        analyzer: NewsAnalyzer,
        instruments: InstrumentMap,
        clock: MarketClock,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.cfg = cfg
        self.broker = broker
        self.market = market
        self.news = news
        self.analyzer = analyzer
        self.instruments = instruments
        self.clock = clock
        self.now = now
        self.state = BotState.load(cfg.paths.state_file)
        self._sold_this_cycle: set[str] = set()

    # ------------------------------------------------------------------ Schleife
    def run_forever(self) -> None:
        log.info("Bot gestartet (Umgebung=%s, dry_run=%s)", self.cfg.environment, self.cfg.dry_run)
        next_scan = 0.0
        while True:
            try:
                if self.clock.is_open(self.now()):
                    do_scan = time.monotonic() >= next_scan
                    self.run_cycle(scan=do_scan)
                    if do_scan:
                        next_scan = time.monotonic() + self.cfg.scan.interval_minutes * 60
                else:
                    log.debug("Markt geschlossen – warte")
            except Trading212Error as exc:
                log.error("%s", exc)
            except Exception:  # der Bot soll bei einzelnen Fehlern weiterlaufen
                log.exception("Unerwarteter Fehler im Zyklus")
            time.sleep(self.cfg.scan.manage_interval_minutes * 60)

    def run_cycle(self, scan: bool = True, force: bool = False) -> list[Candidate]:
        """Ein Durchlauf: Abgleich, Ausstiege prüfen, ggf. neue Kandidaten suchen und kaufen."""
        if not force and not self.clock.is_open(self.now()):
            log.info("Markt geschlossen – kein Handel")
            return []
        self._sold_this_cycle = set()
        summary = self.broker.account_summary()
        positions = {p.ticker: p for p in self.broker.positions()}
        orders = self.broker.open_orders()
        self._update_day(summary)
        self._reconcile(positions, orders)
        self._manage_exits(positions, orders)
        candidates: list[Candidate] = []
        if scan:
            if self.cfg.strategy.exit_on_negative_news:
                self._check_news_exits(positions, orders)
            if self._may_open_positions(summary):
                candidates = self._scan_and_buy(summary, positions, orders)
            self.state.last_scan = self.now().isoformat(timespec="seconds")
        self.state.save(self.cfg.paths.state_file)
        return candidates

    # ------------------------------------------------------------- Verwaltung
    def _update_day(self, summary: AccountSummary) -> None:
        today = self.clock.local_date(self.now())
        if self.state.day != today:
            self.state.day = today
            self.state.day_start_equity = summary.total_value
            log.info("Neuer Handelstag %s – Depotwert %.2f %s", today, summary.total_value, summary.currency)

    def _may_open_positions(self, summary: AccountSummary) -> bool:
        start = self.state.day_start_equity
        limit = self.cfg.risk.daily_loss_limit_pct
        if start > 0 and summary.total_value < start * (1 - limit):
            log.warning("Tagesverlustlimit erreicht (%.2f -> %.2f) – heute keine neuen Käufe",
                        start, summary.total_value)
            return False
        return True

    def _reconcile(self, positions: dict[str, Position], orders: list[Order]) -> None:
        """Gleicht den gespeicherten Zustand mit dem tatsächlichen Depot ab."""
        open_buys = {o.ticker for o in orders if o.side == "BUY"}
        for ticker, trade in list(self.state.trades.items()):
            pos = positions.get(ticker)
            if pos is not None and pos.quantity > 0:
                if trade.pending:
                    # Stop/Ziel auf den tatsächlichen Ausführungskurs verschieben
                    shift = pos.average_price - trade.entry_price
                    trade.entry_price = pos.average_price
                    trade.initial_stop = round(trade.initial_stop + shift, 4)
                    trade.stop = round(trade.stop + shift, 4)
                    trade.take_profit = round(trade.take_profit + shift, 4)
                    trade.highest_price = max(pos.average_price, pos.current_price)
                    trade.pending = False
                    log.info("Kauf bestätigt: %s %.4f @ %.2f (Stop %.2f, Ziel %.2f)", ticker,
                             pos.quantity, pos.average_price, trade.stop, trade.take_profit)
                trade.quantity = pos.quantity
                continue
            if trade.pending and (ticker in open_buys or trade.pending_cycles < PENDING_GRACE_CYCLES):
                trade.pending_cycles += 1
                continue
            log.info("%s ist nicht mehr im Depot – entferne aus der Verwaltung", ticker)
            del self.state.trades[ticker]

        unmanaged = sorted(set(positions) - set(self.state.trades))
        if unmanaged:
            log.debug("Nicht vom Bot verwaltete Positionen (werden ignoriert): %s", ", ".join(unmanaged))

    def _manage_exits(self, positions: dict[str, Position], orders: list[Order]) -> None:
        open_sells = {o.ticker for o in orders if o.side == "SELL"}
        for ticker, trade in list(self.state.trades.items()):
            pos = positions.get(ticker)
            if trade.pending or pos is None or ticker in open_sells:
                continue
            signal = evaluate_exit(trade, pos.current_price, self.cfg.risk, self.now())
            if signal is None:
                continue
            if signal.fraction >= 1:
                qty = pos.quantity_available
            else:
                qty = floor_to(pos.quantity_available * signal.fraction, self.cfg.risk.quantity_decimals)
                if qty <= 0 or pos.quantity_available - qty <= 0:
                    qty = pos.quantity_available
            if qty <= 0:
                continue
            if self._sell(trade, pos, qty, signal.reason):
                self._after_sell(trade, pos, qty)

    def _check_news_exits(self, positions: dict[str, Position], orders: list[Order]) -> None:
        """Verkauft, wenn nach dem Einstieg deutlich negative Nachrichten auftauchen."""
        open_sells = {o.ticker for o in orders if o.side == "SELL"}
        for ticker, trade in list(self.state.trades.items()):
            pos = positions.get(ticker)
            if trade.pending or pos is None or ticker in open_sells or ticker in self._sold_this_cycle:
                continue
            articles = [
                a for a in self.news.fetch(trade.symbol, self.cfg.news.lookback_days, self.cfg.news.max_articles)
                if a.published >= trade.opened_dt
            ]
            if not articles:
                continue
            inst = self.instruments.by_ticker.get(ticker)
            assessment = self.analyzer.assess(trade.symbol, inst.name if inst else trade.symbol, articles)
            bad = assessment.red_flags or (
                assessment.sentiment <= self.cfg.strategy.negative_news_exit_sentiment
                and assessment.confidence >= 0.5
            )
            if bad and pos.quantity_available > 0:
                log.warning("Negative Nachrichten zu %s: %s", ticker, assessment.summary)
                if self._sell(trade, pos, pos.quantity_available, "negative_news"):
                    self._after_sell(trade, pos, pos.quantity_available)

    def _after_sell(self, trade: TradeState, pos: Position, qty: float) -> None:
        if self.cfg.dry_run:
            return
        remaining = pos.quantity - qty
        if remaining <= 1e-9:
            self.state.trades.pop(trade.ticker, None)
            self.state.cooldowns[trade.ticker] = self.now().isoformat(timespec="seconds")
        else:
            trade.quantity = remaining
            trade.partial_taken = True
            trade.stop = max(trade.stop, trade.entry_price)  # Rest auf Einstand absichern
            log.info("Teilgewinn %s mitgenommen – Stop für Rest auf Einstand %.2f", trade.ticker, trade.stop)

    # -------------------------------------------------------------- Auswahl
    def scan(self, exclude: set[str] | None = None) -> list[Candidate]:
        """Bewertet die Watchlist und gibt alle Kandidaten sortiert nach Gesamt-Score zurück."""
        exclude = exclude or set()
        cfg = self.cfg
        evaluated: list[Candidate] = []
        rejected: list[Candidate] = []
        for symbol in cfg.universe.watchlist:
            if symbol in cfg.universe.exclude:
                continue
            inst = self.instruments.resolve(symbol)
            if inst is None:
                log.info("%s: bei Trading 212 nicht handelbar/gefunden", symbol)
                continue
            if inst.ticker in exclude:
                continue
            snap = snapshot(self.market.history(symbol, cfg.scan.history_days))
            if snap is None:
                log.info("%s: zu wenige Kursdaten", symbol)
                continue
            c = Candidate(symbol=symbol, t212_ticker=inst.ticker, company=inst.name or symbol,
                          currency=inst.currency, tech=snap)
            reason = filter_reason(snap, cfg.scan, cfg.strategy)
            if reason:
                c.decision = reason
                rejected.append(c)
                continue
            c.tech_score, c.tech_reasons = technical_score(snap)
            evaluated.append(c)

        evaluated.sort(key=lambda c: c.tech_score, reverse=True)
        top = evaluated[: cfg.scan.max_candidates_for_news]
        for c in top:
            articles = self.news.fetch(c.symbol, cfg.news.lookback_days, cfg.news.max_articles)
            c.news = self.analyzer.assess(c.symbol, c.company, articles)
            combine(c, cfg.strategy)
            c.buy, c.decision = buy_decision(c, cfg.strategy)
        for c in evaluated[cfg.scan.max_candidates_for_news:]:
            combine(c, cfg.strategy)
            c.decision = "technisch nicht unter den besten – keine News-Prüfung"

        ranked = sorted(evaluated, key=lambda c: (c.buy, c.total_score), reverse=True)
        return ranked + rejected

    def _scan_and_buy(self, summary: AccountSummary, positions: dict[str, Position],
                      orders: list[Order]) -> list[Candidate]:
        risk = self.cfg.risk
        pending_only = [t for t in self.state.trades if t not in positions]
        slots = risk.max_positions - len(positions) - len(pending_only)
        if slots <= 0:
            log.info("Maximale Anzahl Positionen erreicht (%d)", risk.max_positions)
            return []
        now = self.now()
        blocked = set(positions) | set(self.state.trades) | {o.ticker for o in orders}
        blocked |= {t for t in self.state.cooldowns if self.state.in_cooldown(t, risk.reentry_cooldown_days, now)}

        candidates = self.scan(exclude=blocked)
        buys = [c for c in candidates if c.buy][: min(slots, risk.max_new_positions_per_cycle)]
        if not buys:
            log.info("Keine Aktie erfüllt aktuell alle Kaufkriterien")
        free_cash = summary.free_cash
        for c in buys:
            try:
                fx = self.market.fx_rate(c.currency, summary.currency)
            except Exception as exc:
                log.warning("Kein Wechselkurs %s->%s (%s) – überspringe %s", c.currency, summary.currency, exc, c.symbol)
                continue
            sizing = position_size(equity=summary.total_value, free_cash=free_cash, price=c.tech.price,
                                   atr=c.tech.atr14, fx_to_account=fx, cfg=risk)
            if sizing.quantity <= 0:
                log.info("%s: kein Kauf – %s", c.symbol, sizing.reason)
                continue
            stop, target = initial_levels(c.tech.price, c.tech.atr14, risk)
            order, qty = self._buy(c, sizing.quantity, stop, target)
            if order is None:
                continue
            free_cash -= qty * c.tech.price * fx
            if self.cfg.dry_run:
                continue
            self.state.trades[c.t212_ticker] = TradeState(
                ticker=c.t212_ticker, symbol=c.symbol, quantity=qty, entry_price=c.tech.price,
                initial_stop=stop, stop=stop, take_profit=target, atr=c.tech.atr14,
                highest_price=c.tech.price, opened_at=now.isoformat(timespec="seconds"),
                order_id=order.id or None, entry_score=c.total_score,
                entry_note=(c.news.summary if c.news else "")[:300],
            )
        return candidates

    # ----------------------------------------------------------------- Orders
    def _buy(self, c: Candidate, qty: float, stop: float, target: float) -> tuple[Order | None, float]:
        news = c.news.summary if c.news else ""
        log.info("KAUF %s (%s) %.4f Stk. @ ~%.2f %s | Stop %.2f | Ziel %.2f | Score %.2f | %s",
                 c.symbol, c.t212_ticker, qty, c.tech.price, c.currency, stop, target, c.total_score, news)
        if self.cfg.dry_run:
            self._journal("BUY", c.t212_ticker, c.symbol, qty, c.tech.price, c.currency, c.decision, c.total_score)
            return Order(0, c.t212_ticker, "BUY", "MARKET", "DRY_RUN", qty, 0.0), qty
        try:
            order = self.broker.place_market_order(c.t212_ticker, qty)
        except Trading212Error as exc:
            whole = float(int(qty))
            if exc.status == 400 and whole >= 1 and whole != qty:
                log.info("Bruchstück abgelehnt – versuche %d ganze Aktien", whole)
                try:
                    order, qty = self.broker.place_market_order(c.t212_ticker, whole), whole
                except Trading212Error as exc2:
                    log.error("Kauf %s fehlgeschlagen: %s", c.symbol, exc2)
                    return None, 0.0
            else:
                log.error("Kauf %s fehlgeschlagen: %s", c.symbol, exc)
                return None, 0.0
        self._journal("BUY", c.t212_ticker, c.symbol, qty, c.tech.price, c.currency, c.decision, c.total_score)
        return order, qty

    def _sell(self, trade: TradeState, pos: Position, qty: float, reason: str) -> bool:
        pnl = pos.current_price / trade.entry_price - 1 if trade.entry_price else 0.0
        log.info("VERKAUF %s %.4f Stk. @ ~%.2f | Grund: %s | Ergebnis %+.2f%%",
                 trade.ticker, qty, pos.current_price, reason, pnl * 100)
        if not self.cfg.dry_run:
            try:
                self.broker.place_market_order(trade.ticker, -qty)
            except Trading212Error as exc:
                log.error("Verkauf %s fehlgeschlagen: %s", trade.ticker, exc)
                return False
        self._sold_this_cycle.add(trade.ticker)
        self._journal("SELL", trade.ticker, trade.symbol, qty, pos.current_price, pos.currency or "",
                      reason, trade.entry_score, round(pnl * 100, 2))
        return True

    def _journal(self, action: str, ticker: str, symbol: str, qty: float, price: float, currency: str,
                 reason: str, score: float, pnl_pct: float | str = "") -> None:
        journal(self.cfg.paths.journal_file, action=action, ticker=ticker, symbol=symbol, quantity=qty,
                price=round(price, 4), currency=currency, reason=reason, score=round(score, 4),
                realized_pnl_pct=pnl_pct, dry_run=self.cfg.dry_run)
