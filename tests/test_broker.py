from marchini.broker import PaperBroker, Position
from marchini.risk import ContractSpec, SizedTrade

SPEC = ContractSpec(
    symbol="TESTUSDT",
    min_trade_usdt=5.0,
    min_trade_num=0.001,
    volume_place=3,
    price_place=2,
    max_leverage=50,
    taker_fee=0.0006,
    maker_fee=0.0002,
)


def trade(side="long", entry=100.0, stop=98.0, target=103.0, qty=1.0):
    return SizedTrade(
        ok=True, symbol="TESTUSDT", side=side, qty=qty,
        entry=entry, stop=stop, target=target, notional=qty * entry,
    )


def test_open_charges_entry_fee():
    broker = PaperBroker(1000.0)
    broker.open(trade(), SPEC)
    assert broker.equity == 1000.0 - 100.0 * 0.0006
    assert "TESTUSDT" in broker.positions


def test_take_profit_is_profitable_after_fees():
    broker = PaperBroker(1000.0)
    broker.open(trade(), SPEC)
    fill = broker.check_exits("TESTUSDT", high=104.0, low=99.0, spec=SPEC)
    assert fill is not None
    assert fill.reason == "take-profit"
    assert 0 < fill.pnl < 3.0  # bruto 3.0 minus fee
    assert broker.equity > 1000.0


def test_stop_loss_realizes_loss():
    broker = PaperBroker(1000.0)
    broker.open(trade(), SPEC)
    fill = broker.check_exits("TESTUSDT", high=101.0, low=97.0, spec=SPEC)
    assert fill is not None
    assert fill.reason == "stop-loss"
    assert fill.pnl < 0
    assert broker.equity < 1000.0


def test_stop_wins_when_candle_touches_both_levels():
    # Pesimisticno po dizajnu: iz svijece se ne vidi sta je prvo doslo.
    broker = PaperBroker(1000.0)
    broker.open(trade(), SPEC)
    fill = broker.check_exits("TESTUSDT", high=105.0, low=97.0, spec=SPEC)
    assert fill.reason == "stop-loss"


def test_short_exits_are_mirrored():
    broker = PaperBroker(1000.0)
    broker.open(trade(side="short", stop=102.0, target=97.0), SPEC)
    fill = broker.check_exits("TESTUSDT", high=100.5, low=96.0, spec=SPEC)
    assert fill.reason == "take-profit"
    assert fill.pnl > 0


def test_no_exit_while_price_stays_inside_range():
    broker = PaperBroker(1000.0)
    broker.open(trade(), SPEC)
    assert broker.check_exits("TESTUSDT", high=101.0, low=99.0, spec=SPEC) is None
    assert "TESTUSDT" in broker.positions


def test_check_exits_on_unknown_symbol_is_safe():
    assert PaperBroker(100.0).check_exits("NEMA", 1.0, 1.0, SPEC) is None


def test_unrealized_pnl_direction():
    long_pos = Position("X", "long", 2.0, 100.0, 98.0, 103.0)
    assert long_pos.unrealized(105.0) == 10.0
    assert long_pos.unrealized(95.0) == -10.0
    short_pos = Position("X", "short", 2.0, 100.0, 102.0, 97.0)
    assert short_pos.unrealized(95.0) == 10.0


def test_position_survives_serialization_roundtrip():
    pos = Position("X", "short", 1.5, 10.0, 11.0, 8.0, notional=15.0)
    assert Position.from_dict(pos.to_dict()) == pos


def test_realized_pnl_accumulates_across_trades():
    broker = PaperBroker(1000.0)
    broker.open(trade(), SPEC)
    broker.check_exits("TESTUSDT", high=104.0, low=99.0, spec=SPEC)
    broker.open(trade(), SPEC)
    broker.check_exits("TESTUSDT", high=101.0, low=97.0, spec=SPEC)
    assert broker.realized_pnl != 0
    assert not broker.positions
