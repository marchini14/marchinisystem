import pytest

from marchini.indicators import Candle, atr, donchian, ema, parse_candles, true_range


def make(closes, highs=None, lows=None):
    highs = highs or [c + 1 for c in closes]
    lows = lows or [c - 1 for c in closes]
    return [
        Candle(ts=i * 3600_000, open=c, high=h, low=l, close=c, volume=100.0)
        for i, (c, h, l) in enumerate(zip(closes, highs, lows))
    ]


def test_parse_candles_reads_bitget_row_layout():
    rows = [["1786957200000", "63349.8", "63400", "63261.9", "63290", "731.9", "46345987"]]
    candles = parse_candles(rows)
    assert len(candles) == 1
    c = candles[0]
    assert (c.ts, c.open, c.high, c.low, c.close) == (1786957200000, 63349.8, 63400.0, 63261.9, 63290.0)


def test_parse_candles_skips_malformed_rows():
    assert parse_candles([["1", "2", "3"]]) == []


def test_ema_of_constant_series_is_that_constant():
    assert ema([5.0] * 30, 10)[-1] == pytest.approx(5.0)


def test_ema_tracks_rising_series_below_price():
    values = [float(i) for i in range(1, 51)]
    assert ema(values, 10)[-1] < values[-1]


def test_ema_reacts_faster_with_shorter_period():
    values = [10.0] * 30 + [20.0] * 10
    assert ema(values, 5)[-1] > ema(values, 20)[-1]


def test_true_range_uses_widest_of_three_measures():
    # Gap gore: raspon prema prethodnom zatvaranju je veci od raspona svijece.
    assert true_range(prev_close=100.0, high=112.0, low=110.0) == 12.0
    assert true_range(prev_close=111.0, high=112.0, low=110.0) == 2.0


def test_atr_is_positive_and_scales_with_range():
    narrow = make([100.0] * 30)
    wide = make([100.0] * 30, highs=[110.0] * 30, lows=[90.0] * 30)
    assert atr(narrow, 14) > 0
    assert atr(wide, 14) > atr(narrow, 14)


def test_atr_returns_zero_without_enough_candles():
    assert atr(make([1.0, 2.0, 3.0]), 14) == 0.0


def test_donchian_excludes_the_current_candle():
    # Zadnja svijeca ima ekstremni high; kanal ga ne smije ukljuciti, inace bi
    # uvjet "close > upper" bio nemoguc po konstrukciji.
    closes = [10.0] * 20 + [50.0]
    candles = make(closes)
    upper, lower = donchian(candles, 20)
    assert upper == pytest.approx(11.0)  # 10 + 1, bez zadnje svijece
    assert candles[-1].close > upper


def test_donchian_needs_enough_history():
    assert donchian(make([1.0, 2.0]), 20) == (0.0, 0.0)
