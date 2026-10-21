"""Kommandozeile: python -m t212bot {run,once,scan,status}"""
from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .broker import Trading212Client
from .config import Config, load_config
from .engine import TradingBot
from .market_data import MarketData
from .market_hours import MarketClock
from .news import FinnhubNews, YahooNews
from .sentiment import ClaudeAnalyzer, LexiconAnalyzer
from .universe import InstrumentMap

DISCLAIMER = (
    "HINWEIS: Automatisierter Handel ist riskant. Es gibt keine Gewinngarantie – Verluste bis zum "
    "Totalverlust des eingesetzten Kapitals sind möglich. Teste zuerst im Demo-Konto."
)


def setup_logging(cfg: Config, verbose: bool) -> None:
    Path(cfg.paths.log_file).parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    file = RotatingFileHandler(cfg.paths.log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    file.setFormatter(fmt)
    root.handlers[:] = [console, file]
    for noisy in ("urllib3", "yfinance", "peewee", "httpx", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def build_bot(cfg: Config) -> TradingBot:
    broker = Trading212Client(cfg.secrets.t212_api_key, cfg.secrets.t212_api_secret, cfg.environment)
    news = FinnhubNews(cfg.secrets.finnhub_api_key) if cfg.news.provider == "finnhub" else YahooNews()
    if cfg.news.analyzer == "claude":
        analyzer = ClaudeAnalyzer(model=cfg.news.claude_model, effort=cfg.news.claude_effort)
    else:
        analyzer = LexiconAnalyzer()
    instruments = InstrumentMap.load(broker, cfg.paths.instruments_cache)
    return TradingBot(cfg, broker, MarketData(), news, analyzer, instruments, MarketClock(cfg.market_hours))


def print_candidates(candidates) -> None:
    if not candidates:
        print("Keine Kandidaten.")
        return
    print(f"\n{'Symbol':<7} {'Kurs':>9} {'Vola':>5} {'RSI':>4} {'3M':>6} {'Tech':>5} {'News':>5} "
          f"{'Gesamt':>6}  Entscheidung")
    print("-" * 100)
    for c in candidates:
        t = c.tech
        mark = "KAUF " if c.buy else ""
        print(f"{c.symbol:<7} {t.price:>9.2f} {t.volatility:>5.0%} {t.rsi14:>4.0f} {t.return_3m:>+6.0%} "
              f"{c.tech_score:>5.2f} {c.news_score:>5.2f} {c.total_score:>6.2f}  {mark}{c.decision}")
        if c.news and c.news.article_count:
            print(f"        News ({c.news.article_count}, {c.news.analyzer}): "
                  f"Sentiment {c.news.sentiment:+.2f}, Katalysator {c.news.catalyst_strength:.2f} – "
                  f"{c.news.summary}")
    print()


def cmd_status(bot: TradingBot) -> None:
    s = bot.broker.account_summary()
    print(f"\nKonto ({bot.cfg.environment}): Gesamtwert {s.total_value:,.2f} {s.currency} | "
          f"frei {s.free_cash:,.2f} | investiert {s.invested_value:,.2f} | "
          f"unrealisiert {s.unrealized_pnl:+,.2f}")
    positions = bot.broker.positions()
    if not positions:
        print("Keine offenen Positionen.\n")
        return
    print(f"\n{'Ticker':<14} {'Stück':>10} {'Einstand':>10} {'Kurs':>10} {'G/V':>10}  Bot-Verwaltung")
    for p in positions:
        trade = bot.state.trades.get(p.ticker)
        managed = (f"Stop {trade.stop:.2f} / Ziel {trade.take_profit:.2f}" if trade else "manuell")
        print(f"{p.ticker:<14} {p.quantity:>10.4f} {p.average_price:>10.2f} {p.current_price:>10.2f} "
              f"{(p.unrealized_pnl or 0):>+10.2f}  {managed}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="t212bot", description="Trading-212-Trading-Bot")
    parser.add_argument("command", choices=["run", "once", "scan", "status"],
                        help="run = Dauerbetrieb, once = ein Zyklus, scan = nur Analyse, status = Konto")
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("--force", action="store_true", help="once: auch außerhalb der Handelszeiten (Orders würden erst zur Börseneröffnung "
                             "ausgeführt – am besten mit --dry-run kombinieren)")
    parser.add_argument("--dry-run", action="store_true", help="keine Orders senden")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    cfg = load_config(args.config, dry_run=True if args.dry_run else None)
    setup_logging(cfg, args.verbose)
    log = logging.getLogger("t212bot")
    log.warning(DISCLAIMER)
    if cfg.environment == "live" and not cfg.dry_run:
        log.warning("ECHTGELD-MODUS AKTIV – der Bot platziert echte Orders.")

    bot = build_bot(cfg)
    if args.command == "status":
        cmd_status(bot)
    elif args.command == "scan":
        print_candidates(bot.scan(exclude=set(bot.state.trades)))
    elif args.command == "once":
        print_candidates(bot.run_cycle(scan=True, force=args.force))
    else:
        try:
            bot.run_forever()
        except KeyboardInterrupt:
            log.info("Bot beendet.")
    return 0
