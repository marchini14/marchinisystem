"""Donchian channel and ATR helpers shared by the live path (src/main.py) and
the Nautilus backtest strategy (src/strategy.py), so both contexts compute
indicators the same way instead of drifting apart.
"""
from collections import deque
from typing import Optional, Tuple

import numpy as np
import pandas as pd


class StreamingDonchian:
    """Rolling N-bar high/low channel for Nautilus's event-driven `on_bar` loop.

    `channel()` reflects only bars seen so far (via `update`), so calling it
    before updating with the current bar avoids testing a breakout against a
    channel that already includes that same bar.
    """

    def __init__(self, period: int) -> None:
        self.period = period
        self._highs: deque = deque(maxlen=period)
        self._lows: deque = deque(maxlen=period)

    def channel(self) -> Tuple[Optional[float], Optional[float]]:
        if len(self._highs) < self.period:
            return (None, None)
        return (max(self._highs), min(self._lows))

    def update(self, high: float, low: float) -> None:
        self._highs.append(high)
        self._lows.append(low)


class StreamingAtr:
    """Wilder's ATR, updated one bar at a time."""

    def __init__(self, period: int) -> None:
        self.period = period
        self._prev_close: Optional[float] = None
        self._seed_trs: list[float] = []
        self.value: Optional[float] = None

    def update(self, high: float, low: float, close: float) -> Optional[float]:
        if self._prev_close is None:
            true_range = high - low
        else:
            true_range = max(
                high - low,
                abs(high - self._prev_close),
                abs(low - self._prev_close),
            )
        self._prev_close = close

        if self.value is not None:
            self.value = (self.value * (self.period - 1) + true_range) / self.period
            return self.value

        self._seed_trs.append(true_range)
        if len(self._seed_trs) >= self.period:
            self.value = sum(self._seed_trs) / len(self._seed_trs)
        return self.value


def donchian_channels(high: pd.Series, low: pd.Series, period: int) -> Tuple[pd.Series, pd.Series]:
    """Upper/lower channel at each bar, computed from the *prior* `period`
    bars only (shift(1) before rolling) so testing the current bar's
    high/low against it can't look ahead at itself.
    """
    upper = high.shift(1).rolling(period).max()
    lower = low.shift(1).rolling(period).min()
    return upper, lower


def atr_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    # Wilder smoothing == an EMA with alpha = 1/period.
    return true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def latest_valid(series: pd.Series) -> Optional[float]:
    clean = series.dropna()
    if clean.empty:
        return None
    value = float(clean.iloc[-1])
    return value if np.isfinite(value) else None
