"""EMA and ATR helpers shared by the live path (src/main.py) and the Nautilus
backtest strategy (src/strategy.py), so both contexts compute indicators the
same way instead of drifting apart.
"""
from typing import Optional

import numpy as np
import pandas as pd


class StreamingEma:
    """Bar-by-bar EMA update for Nautilus's event-driven `on_bar` loop."""

    def __init__(self, period: int) -> None:
        self.period = period
        self.value: Optional[float] = None

    def update(self, price: float) -> float:
        if self.value is None:
            self.value = price
        else:
            alpha = 2.0 / (self.period + 1)
            self.value = alpha * price + (1.0 - alpha) * self.value
        return self.value


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


def ema_series(closes: pd.Series, period: int) -> pd.Series:
    return closes.ewm(span=period, adjust=False).mean()


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
