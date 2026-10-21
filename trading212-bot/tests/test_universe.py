from t212bot.broker import Instrument
from t212bot.universe import InstrumentMap


def test_resolves_us_stocks_only():
    m = InstrumentMap([
        Instrument("AMD_US_EQ", "AMD", "Advanced Micro Devices", "USD", "STOCK"),
        Instrument("AMDd_EQ", "AMD", "Advanced Micro Devices (DE)", "EUR", "STOCK"),
        Instrument("BRK_B_US_EQ", "BRK.B", "Berkshire Hathaway B", "USD", "STOCK"),
        Instrument("QQQ_US_EQ", "QQQ", "Invesco QQQ", "USD", "ETF"),
    ])
    assert m.resolve("AMD").ticker == "AMD_US_EQ"
    assert m.resolve("BRK-B").ticker == "BRK_B_US_EQ"
    assert m.resolve("QQQ") is None
    assert m.resolve("NOPE") is None
