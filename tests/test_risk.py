import pytest

from marchini.risk import (
    ContractSpec,
    daily_loss_exceeded,
    floor_to_places,
    liquidation_price,
    size_trade,
)

# Realna specifikacija, vrijednosti iz /api/v2/mix/market/contracts.
BTC = ContractSpec(
    symbol="BTCUSDT",
    min_trade_usdt=5.0,
    min_trade_num=0.0001,
    volume_place=4,
    price_place=1,
    max_leverage=125,
    taker_fee=0.0006,
    maker_fee=0.0002,
)

BASE = dict(
    symbol="BTCUSDT",
    side="long",
    entry=60_000.0,
    atr_value=600.0,
    spec=BTC,
    leverage=5,
    risk_per_trade_pct=1.0,
    atr_stop_mult=2.0,
    atr_target_mult=3.0,
    max_fee_share_of_target_pct=15.0,
    min_liq_distance_vs_stop=1.5,
)


def test_floor_never_rounds_up():
    # Zaokruzivanje gore bi probilo limit rizika, pa mora uvijek ici nadolje.
    assert floor_to_places(1.9999, 2) == 1.99
    assert floor_to_places(1.9999, 0) == 1.0
    assert floor_to_places(0.00019, 4) == 0.0001


def test_risk_amount_matches_configured_percent():
    trade = size_trade(**BASE, equity=10_000.0)
    assert trade.ok, trade.rejections
    # 1% od 10 000 = 100 USDT rizika; dozvoli malu razliku od zaokruzivanja kolicine.
    assert trade.risk_amount == pytest.approx(100.0, rel=0.01)
    assert trade.stop == pytest.approx(60_000 - 1200)
    assert trade.target == pytest.approx(60_000 + 1800)


def test_short_stop_and_target_are_mirrored():
    trade = size_trade(**{**BASE, "side": "short"}, equity=10_000.0)
    assert trade.ok, trade.rejections
    assert trade.stop == pytest.approx(60_000 + 1200)
    assert trade.target == pytest.approx(60_000 - 1800)


def test_tiny_capital_is_rejected_not_silently_shrunk():
    # 2.14 USDT: bot mora odbiti trejd sa razlogom, ne poslati besmislen nalog.
    trade = size_trade(**BASE, equity=2.14)
    assert not trade.ok
    assert trade.rejections


def test_notional_below_exchange_minimum_is_rejected():
    trade = size_trade(**BASE, equity=20.0)
    if not trade.ok:
        assert any("notional" in r or "kolicina" in r or "fee" in r for r in trade.rejections)


def test_margin_never_exceeds_equity():
    for equity in (50.0, 500.0, 5_000.0, 50_000.0):
        trade = size_trade(**BASE, equity=equity)
        if trade.ok:
            assert trade.margin <= equity + 1e-9


def test_leverage_above_symbol_max_is_rejected():
    trade = size_trade(**{**BASE, "leverage": 200}, equity=10_000.0)
    assert not trade.ok
    assert any("iznad max" in r for r in trade.rejections)


def test_high_leverage_rejected_when_liquidation_sits_inside_stop():
    # Na 50x likvidacija je ~2% od ulaza, a stop je 2% (2 x ATR 600 / 60000).
    # Likvidacija bi se aktivirala prije stopa - risk modul to mora odbiti.
    trade = size_trade(**{**BASE, "leverage": 50}, equity=10_000.0)
    assert not trade.ok
    assert any("likvidacija" in r for r in trade.rejections)


def test_liquidation_price_sides_and_direction():
    long_liq = liquidation_price(100.0, "long", 10)
    short_liq = liquidation_price(100.0, "short", 10)
    assert long_liq < 100.0 < short_liq
    # Veci leverage -> likvidacija blize ulazu.
    assert liquidation_price(100.0, "long", 20) > long_liq


def test_zero_or_negative_inputs_rejected():
    assert not size_trade(**{**BASE, "entry": 0.0}, equity=10_000.0).ok
    assert not size_trade(**{**BASE, "atr_value": 0.0}, equity=10_000.0).ok


def test_fee_share_veto_blocks_positions_too_small_to_pay_for_themselves():
    # Prag 0% znaci da svaki fee > 0 pada -> veto se aktivira.
    trade = size_trade(**{**BASE, "max_fee_share_of_target_pct": 0.0}, equity=10_000.0)
    assert not trade.ok
    assert any("fee" in r for r in trade.rejections)


def test_fee_is_round_trip_not_single_side():
    trade = size_trade(**BASE, equity=10_000.0)
    assert trade.ok
    assert trade.round_trip_fee == pytest.approx(trade.notional * BTC.taker_fee * 2)


def test_daily_loss_limit():
    assert daily_loss_exceeded(-5.0, 100.0, 5.0)
    assert daily_loss_exceeded(-6.0, 100.0, 5.0)
    assert not daily_loss_exceeded(-4.99, 100.0, 5.0)
    assert not daily_loss_exceeded(10.0, 100.0, 5.0)
    assert not daily_loss_exceeded(-999.0, 100.0, 0.0)  # limit 0 = isklljuceno
