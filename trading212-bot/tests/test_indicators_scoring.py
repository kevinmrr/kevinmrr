import math

import pandas as pd

from conftest import make_trend_df
from t212bot.config import ScanConfig, StrategyConfig
from t212bot.indicators import atr, rsi, snapshot
from t212bot.scoring import Candidate, buy_decision, combine, filter_reason, news_score, technical_score
from t212bot.sentiment import NewsAssessment


def test_rsi_rising_series_is_100():
    s = pd.Series(range(1, 40), dtype=float)
    assert rsi(s).iloc[-1] == 100.0


def test_rsi_falling_series_is_low():
    s = pd.Series(range(40, 1, -1), dtype=float)
    assert rsi(s).iloc[-1] < 1


def test_atr_constant_range():
    df = pd.DataFrame({"High": [11.0] * 30, "Low": [9.0] * 30, "Close": [10.0] * 30})
    assert math.isclose(atr(df).iloc[-1], 2.0)


def test_snapshot_requires_history():
    assert snapshot(make_trend_df(days=30)) is None
    snap = snapshot(make_trend_df(days=300))
    assert snap is not None and snap.bars == 300
    assert 0 < snap.volatility < 1
    assert snap.atr14 > 0


def test_uptrend_scores_higher_than_downtrend():
    up = snapshot(make_trend_df(drift=0.002, seed=3))
    down = snapshot(make_trend_df(drift=-0.002, seed=3))
    assert technical_score(up)[0] > technical_score(down)[0]


def test_filter_rejects_low_and_high_volatility():
    strategy = StrategyConfig(min_volatility=0.22, max_volatility=0.70)
    scan = ScanConfig(min_avg_dollar_volume=1)
    calm = snapshot(make_trend_df(vol=0.003))
    wild = snapshot(make_trend_df(vol=0.08))
    ok = snapshot(make_trend_df(vol=0.02))
    assert "zu niedrig" in filter_reason(calm, scan, strategy)
    assert "zu hoch" in filter_reason(wild, scan, strategy)
    assert filter_reason(ok, scan, strategy) is None


def test_news_score_red_flags_zero_and_positive_above_neutral():
    assert news_score(NewsAssessment(0.9, 1, 1, red_flags=["Betrug"], article_count=3)) == 0.0
    assert news_score(NewsAssessment(0.8, 0.8, 0.9, article_count=3)) > 0.8
    assert news_score(None) == 0.5


def _candidate(sentiment=0.7, flags=None, articles=3):
    snap = snapshot(make_trend_df(drift=0.002, seed=3))
    c = Candidate("AAA", "AAA_US_EQ", "AAA Inc.", "USD", snap)
    c.tech_score, _ = technical_score(snap)
    c.news = NewsAssessment(sentiment, 0.8, 0.9, red_flags=flags or [], article_count=articles)
    return c


def test_buy_decision_requires_positive_news_without_red_flags():
    strategy = StrategyConfig(buy_threshold=0.5)
    good = _candidate()
    combine(good, strategy)
    if good.tech.rsi14 <= 78:
        assert buy_decision(good, strategy)[0]

    flagged = _candidate(flags=["Ermittlungen"])
    combine(flagged, strategy)
    ok, reason = buy_decision(flagged, strategy)
    assert not ok and "Warnsignale" in reason

    negative = _candidate(sentiment=-0.2)
    combine(negative, strategy)
    assert not buy_decision(negative, strategy)[0]

    no_news = _candidate(articles=0)
    combine(no_news, strategy)
    assert not buy_decision(no_news, strategy)[0]
