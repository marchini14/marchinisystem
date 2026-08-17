from marchini.indicators import Candle
from marchini.strategy import evaluate


def series(closes):
    return [
        Candle(ts=i * 3600_000, open=c, high=c + 0.5, low=c - 0.5, close=c, volume=100.0)
        for i, c in enumerate(closes)
    ]


def test_no_signal_without_enough_history():
    sig = evaluate(series([10.0] * 10))
    assert sig.side is None
    assert "nedovoljno" in sig.reason


def test_long_on_breakout_in_uptrend():
    # Postojan uzlazni trend, pa skok koji probija kanal.
    closes = [100.0 + i * 0.5 for i in range(80)] + [200.0]
    sig = evaluate(series(closes))
    assert sig.side == "long"
    assert sig.atr_value > 0


def test_short_on_breakdown_in_downtrend():
    closes = [200.0 - i * 0.5 for i in range(80)] + [100.0]
    sig = evaluate(series(closes))
    assert sig.side == "short"


def test_short_suppressed_when_disabled():
    closes = [200.0 - i * 0.5 for i in range(80)] + [100.0]
    assert evaluate(series(closes), allow_short=False).side is None


def test_no_signal_in_flat_range():
    closes = [100.0, 101.0, 99.0, 100.5, 99.5] * 20
    assert evaluate(series(closes)).side is None


def test_breakout_against_trend_is_filtered_out():
    # Dugi silazni trend; skok probija kanal ali je jos ispod EMA50 -> bez ulaza.
    closes = [300.0 - i for i in range(80)] + [235.0]
    sig = evaluate(series(closes))
    assert sig.side != "long"
