from datetime import datetime, timedelta, timezone

from t212bot.config import RiskConfig
from t212bot.risk import evaluate_exit, initial_levels, position_size
from t212bot.state import TradeState

NOW = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)


def trade(entry=100.0, atr=2.0, opened=NOW, cfg=RiskConfig()):
    stop, target = initial_levels(entry, atr, cfg)
    return TradeState("AAA_US_EQ", "AAA", 10, entry, stop, stop, target, atr, entry, opened.isoformat())


def test_initial_levels():
    stop, target = initial_levels(100, 2, RiskConfig(stop_atr_multiple=2, take_profit_r_multiple=3))
    assert stop == 96 and target == 112


def test_position_size_limited_by_risk():
    cfg = RiskConfig(risk_per_trade_pct=0.01, max_position_pct=0.5, stop_atr_multiple=2)
    r = position_size(equity=10_000, free_cash=10_000, price=100, atr=5, fx_to_account=1.0, cfg=cfg)
    # 1 % von 10.000 = 100 Risiko / (2 * 5) = 10 Stück
    assert r.quantity == 10 and "Risiko" in r.reason


def test_position_size_limited_by_cap_and_cash():
    cfg = RiskConfig(risk_per_trade_pct=0.05, max_position_pct=0.12, cash_reserve_pct=0.10)
    capped = position_size(equity=10_000, free_cash=10_000, price=100, atr=0.5, fx_to_account=1.0, cfg=cfg)
    assert capped.quantity == 12 and "Positionslimit" in capped.reason
    cash = position_size(equity=10_000, free_cash=1_500, price=100, atr=0.5, fx_to_account=1.0, cfg=cfg)
    assert cash.quantity == 5 and "Cash" in cash.reason  # 1.500 - 1.000 Reserve
    broke = position_size(equity=10_000, free_cash=1_000, price=100, atr=0.5, fx_to_account=1.0, cfg=cfg)
    assert broke.quantity == 0


def test_position_size_uses_fx():
    cfg = RiskConfig(risk_per_trade_pct=0.01, max_position_pct=1, cash_reserve_pct=0)
    r = position_size(equity=10_000, free_cash=10_000, price=100, atr=5, fx_to_account=0.5, cfg=cfg)
    assert r.quantity == 20


def test_stop_loss_triggers():
    t = trade()
    assert evaluate_exit(t, 95.9, RiskConfig(), NOW).reason == "stop_loss"
    assert evaluate_exit(trade(), 99, RiskConfig(), NOW) is None


def test_trailing_stop_moves_up_and_triggers():
    cfg = RiskConfig(trailing_activation_r=1.0, trailing_atr_multiple=2.5)
    t = trade()
    assert evaluate_exit(t, 108, cfg, NOW) is None  # +2R -> Trailing aktiv
    assert t.stop == 103.0
    signal = evaluate_exit(t, 102.9, cfg, NOW)
    assert signal.reason == "trailing_stop" and signal.fraction == 1.0


def test_partial_take_profit():
    cfg = RiskConfig(partial_take_profit_fraction=0.5)
    t = trade()
    signal = evaluate_exit(t, 112.5, cfg, NOW)
    assert signal.reason == "take_profit" and signal.fraction == 0.5
    t.partial_taken = True
    assert evaluate_exit(t, 113, cfg, NOW) is None


def test_time_stop():
    cfg = RiskConfig(max_holding_days=20, min_progress_pct=0.02)
    old = trade(opened=NOW - timedelta(days=21))
    assert evaluate_exit(old, 101, cfg, NOW).reason == "time_stop"
    assert evaluate_exit(trade(opened=NOW - timedelta(days=21)), 103, cfg, NOW) is None
