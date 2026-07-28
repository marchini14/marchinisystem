from decimal import Decimal
from typing import Optional

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from .indicators import StreamingAtr, StreamingEma
from .risk import compute_stop_target, stop_breached, target_breached


class AtrTrendStrategyConfig(StrategyConfig):
    instrument_id: Optional[InstrumentId] = None
    bar_type: Optional[BarType] = None
    instrument_ids: tuple[InstrumentId, ...] = ()
    bar_types: tuple[BarType, ...] = ()
    trade_size: str = "0.01"
    fast_period: int = 12
    slow_period: int = 26
    atr_period: int = 14
    # Kept as str: manifest.strategy_config declares these quoted (to match
    # user_config_schema's pattern-validated string tunables), and that value
    # wins the config merge over backtest.yaml's default here. risk.py casts
    # to float defensively regardless.
    atr_stop_multiplier: str = "2.0"
    atr_target_multiplier: str = "3.0"


class AtrTrendStrategy(Strategy):
    """EMA crossover entry (same signal as Bitget's own btc-ema-cross-demo),
    but exits are gated by an ATR-derived stop/target in addition to the
    inverse crossover, instead of only exiting on the next cross.
    """

    def __init__(self, config: AtrTrendStrategyConfig) -> None:
        super().__init__(config)
        self.cfg = config
        self._fast_ema = StreamingEma(config.fast_period)
        self._slow_ema = StreamingEma(config.slow_period)
        self._atr = StreamingAtr(config.atr_period)
        self._prev_diff: Optional[float] = None
        self._bar_count = 0
        self._position_side: Optional[str] = None
        self._stop_price: Optional[float] = None
        self._target_price: Optional[float] = None
        self._instrument: Optional[Instrument] = None

    def on_start(self) -> None:
        bar_type = self.cfg.bar_type or (self.cfg.bar_types[0] if self.cfg.bar_types else None)
        instrument_id = self.cfg.instrument_id or (
            self.cfg.instrument_ids[0] if self.cfg.instrument_ids else None
        )
        if bar_type is None or instrument_id is None:
            raise RuntimeError("bar_type and instrument_id must be set")
        self._instrument = self.cache.instrument(instrument_id)
        self.subscribe_bars(bar_type)

    def on_bar(self, bar: Bar) -> None:
        high = float(bar.high)
        low = float(bar.low)
        close = float(bar.close)
        self._bar_count += 1

        atr_value = self._atr.update(high, low, close)
        fast = self._fast_ema.update(close)
        slow = self._slow_ema.update(close)

        instrument = self._instrument
        if instrument is None:
            return

        # Risk exit takes priority over signal exit: a stop/target breach
        # intrabar closes the position before we even look at the crossover.
        if self._position_side is not None:
            if self._stop_price is not None and stop_breached(
                self._position_side, self._stop_price, low, high
            ):
                self._close_open(instrument.id)
                self._reset_position()
                return
            if self._target_price is not None and target_breached(
                self._position_side, self._target_price, low, high
            ):
                self._close_open(instrument.id)
                self._reset_position()
                return

        warmup = max(self.cfg.slow_period, self.cfg.fast_period, self.cfg.atr_period) + 1
        if self._bar_count < warmup or atr_value is None:
            self._prev_diff = fast - slow
            return

        diff = fast - slow
        if self._prev_diff is None:
            self._prev_diff = diff
            return

        cross_up = self._prev_diff <= 0.0 < diff
        cross_down = self._prev_diff >= 0.0 > diff
        self._prev_diff = diff

        qty = Quantity(Decimal(self.cfg.trade_size), instrument.size_precision)

        if self._position_side is None:
            if cross_up:
                self._open(instrument, OrderSide.BUY, qty, close, "long", atr_value)
            elif cross_down:
                self._open(instrument, OrderSide.SELL, qty, close, "short", atr_value)
            return

        if self._position_side == "long" and cross_down:
            self._close_open(instrument.id)
            self._reset_position()
        elif self._position_side == "short" and cross_up:
            self._close_open(instrument.id)
            self._reset_position()

    def _open(
        self,
        instrument: Instrument,
        side: OrderSide,
        quantity: Quantity,
        entry_price: float,
        position_side: str,
        atr_value: float,
    ) -> None:
        order = self.order_factory.market(
            instrument_id=instrument.id,
            order_side=side,
            quantity=quantity,
            time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)
        stop_target = compute_stop_target(
            entry_price=entry_price,
            side=position_side,
            atr_value=atr_value,
            stop_multiplier=self.cfg.atr_stop_multiplier,
            target_multiplier=self.cfg.atr_target_multiplier,
        )
        self._position_side = position_side
        self._stop_price = stop_target.stop_price
        self._target_price = stop_target.target_price

    def _close_open(self, instrument_id: InstrumentId) -> None:
        for position in self.cache.positions_open(instrument_id=instrument_id):
            close_side = OrderSide.SELL if position.side.name == "LONG" else OrderSide.BUY
            order = self.order_factory.market(
                instrument_id=instrument_id,
                order_side=close_side,
                quantity=position.quantity,
                time_in_force=TimeInForce.GTC,
            )
            self.submit_order(order)

    def _reset_position(self) -> None:
        self._position_side = None
        self._stop_price = None
        self._target_price = None

    def on_stop(self) -> None:
        if self._instrument is not None:
            self.cancel_all_orders(self._instrument.id)
            self.close_all_positions(self._instrument.id)
