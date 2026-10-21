from datetime import datetime, timedelta, timezone

import pytest

from conftest import (AlwaysOpenClock, FakeAnalyzer, FakeBroker, FakeMarket, FakeNews, article, instrument,
                      make_trend_df)
from t212bot.broker import Order, Position
from t212bot.config import Config, LIVE_CONFIRM_VALUE, Secrets
from t212bot.engine import TradingBot
from t212bot.sentiment import NewsAssessment
from t212bot.state import BotState
from t212bot.universe import InstrumentMap

NOW = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)


def make_bot(cfg, broker=None, analyzer=None, news=None):
    frames = {"AAA": make_trend_df(drift=0.002, seed=3), "BBB": make_trend_df(drift=-0.005, seed=5)}
    analyzer = analyzer or FakeAnalyzer({
        "AAA": NewsAssessment(0.8, 0.8, 0.9, summary="Prognose angehoben", article_count=2),
        "BBB": NewsAssessment(0.8, 0.8, 0.9, summary="gut", article_count=2),
    })
    news = news or FakeNews({"AAA": [article("AAA raises guidance")], "BBB": [article("BBB news")]})
    return TradingBot(cfg, broker or FakeBroker(), FakeMarket(frames), news, analyzer,
                      InstrumentMap([instrument("AAA"), instrument("BBB")]), AlwaysOpenClock(), now=lambda: NOW)


@pytest.fixture
def loose_cfg(cfg):
    cfg.strategy.buy_threshold = 0.5
    cfg.strategy.min_volatility = 0.05
    return cfg


def test_scan_ranks_uptrend_with_good_news_as_buy(loose_cfg):
    bot = make_bot(loose_cfg)
    candidates = bot.scan()
    aaa = next(c for c in candidates if c.symbol == "AAA")
    bbb = next(c for c in candidates if c.symbol == "BBB")
    assert aaa.buy, aaa.decision
    assert not bbb.buy and "Aufwärtstrend" in bbb.decision
    assert candidates[0].symbol == "AAA"


def test_cycle_buys_and_persists_trade(loose_cfg):
    broker = FakeBroker()
    bot = make_bot(loose_cfg, broker)
    bot.run_cycle()
    assert len(broker.placed) == 1 and broker.placed[0][0] == "AAA_US_EQ" and broker.placed[0][1] > 0
    state = BotState.load(loose_cfg.paths.state_file)
    trade = state.trades["AAA_US_EQ"]
    assert trade.pending and trade.stop < trade.entry_price < trade.take_profit
    assert state.day_start_equity == 10_000


def test_dry_run_places_no_orders(loose_cfg):
    loose_cfg.dry_run = True
    broker = FakeBroker()
    bot = make_bot(loose_cfg, broker)
    bot.run_cycle()
    assert broker.placed == [] and bot.state.trades == {}


def test_fractional_rejection_retries_with_whole_shares(loose_cfg):
    broker = FakeBroker(total=100_000, cash=100_000)
    broker.fail_fractional = True
    bot = make_bot(loose_cfg, broker)
    bot.run_cycle()
    assert broker.placed and broker.placed[0][1] == int(broker.placed[0][1])


def test_reconcile_confirms_fill_then_stop_loss_sells(loose_cfg):
    broker = FakeBroker()
    bot = make_bot(loose_cfg, broker)
    bot.run_cycle()
    trade = bot.state.trades["AAA_US_EQ"]
    qty, fill = trade.quantity, trade.entry_price + 1
    distance = trade.entry_price - trade.stop

    broker.positions_list = [Position("AAA_US_EQ", qty, qty, fill, fill, "USD", 0.0)]
    bot.run_cycle(scan=False)
    trade = bot.state.trades["AAA_US_EQ"]
    assert not trade.pending and trade.entry_price == fill
    assert trade.stop == pytest.approx(fill - distance)

    crash = trade.stop - 0.5
    broker.positions_list = [Position("AAA_US_EQ", qty, qty, fill, crash, "USD", -50.0)]
    bot.run_cycle(scan=False)
    assert broker.placed[-1] == ("AAA_US_EQ", -qty)
    assert "AAA_US_EQ" not in bot.state.trades
    assert bot.state.in_cooldown("AAA_US_EQ", 3, NOW)


def test_partial_take_profit_keeps_rest_with_breakeven_stop(loose_cfg):
    broker = FakeBroker()
    bot = make_bot(loose_cfg, broker)
    bot.run_cycle()
    trade = bot.state.trades["AAA_US_EQ"]
    trade.pending = False
    trade.quantity = 10
    price = trade.take_profit + 1
    broker.positions_list = [Position("AAA_US_EQ", 10, 10, trade.entry_price, price, "USD", 100.0)]
    bot.run_cycle(scan=False)
    assert broker.placed[-1] == ("AAA_US_EQ", -5.0)
    trade = bot.state.trades["AAA_US_EQ"]
    assert trade.partial_taken and trade.quantity == 5 and trade.stop >= trade.entry_price


def test_pending_trade_dropped_if_never_filled(loose_cfg):
    broker = FakeBroker()
    bot = make_bot(loose_cfg, broker)
    bot.run_cycle()
    assert "AAA_US_EQ" in bot.state.trades
    for _ in range(4):
        bot.run_cycle(scan=False)
    assert "AAA_US_EQ" not in bot.state.trades


def test_daily_loss_limit_blocks_new_buys(loose_cfg):
    broker = FakeBroker()
    bot = make_bot(loose_cfg, broker)
    bot.state.day = NOW.date().isoformat()
    bot.state.day_start_equity = 11_000  # heute schon > 3 % verloren
    bot.run_cycle()
    assert broker.placed == []


def test_max_positions_respected(loose_cfg):
    loose_cfg.risk.max_positions = 1
    broker = FakeBroker()
    broker.positions_list = [Position("XYZ_US_EQ", 1, 1, 10, 10, "USD", 0)]
    bot = make_bot(loose_cfg, broker)
    bot.run_cycle()
    assert broker.placed == []


def test_negative_news_after_entry_triggers_exit(loose_cfg):
    broker = FakeBroker()
    analyzer = FakeAnalyzer({"AAA": NewsAssessment(0.8, 0.8, 0.9, article_count=1)})
    news = FakeNews({"AAA": [article("AAA raises guidance")]})
    bot = make_bot(loose_cfg, broker, analyzer, news)
    bot.run_cycle()
    trade = bot.state.trades["AAA_US_EQ"]
    trade.pending = False
    trade.opened_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    broker.positions_list = [Position("AAA_US_EQ", 3, 3, trade.entry_price, trade.entry_price, "USD", 0)]
    news.articles["AAA"] = [article("SEC opens fraud investigation into AAA")]
    analyzer.by_symbol["AAA"] = NewsAssessment(-0.8, 0.9, 0.9, red_flags=["Ermittlungen"], article_count=1)
    bot.run_cycle()
    assert ("AAA_US_EQ", -3) in broker.placed


def test_open_sell_order_prevents_duplicate_exit(loose_cfg):
    broker = FakeBroker()
    bot = make_bot(loose_cfg, broker)
    bot.run_cycle()
    trade = bot.state.trades["AAA_US_EQ"]
    trade.pending = False
    broker.positions_list = [Position("AAA_US_EQ", 3, 3, trade.entry_price, trade.stop - 1, "USD", 0)]
    broker.orders = [Order(9, "AAA_US_EQ", "SELL", "MARKET", "NEW", -3, 0)]
    placed_before = len(broker.placed)
    bot.run_cycle(scan=False)
    assert len(broker.placed) == placed_before


def test_live_mode_requires_confirmation():
    c = Config(environment="live")
    with pytest.raises(ValueError):
        c.validate()
    c.secrets = Secrets(live_confirm=LIVE_CONFIRM_VALUE)
    c.validate()
    Config(environment="live", dry_run=True).validate()
