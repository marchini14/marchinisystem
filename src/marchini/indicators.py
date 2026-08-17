"""Indikatori na cistom Pythonu - bez pandas/numpy, da instalacija ostane trivijalna."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_bitget(cls, row: list[str]) -> "Candle":
        return cls(
            ts=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )


def parse_candles(rows: list[list[str]]) -> list[Candle]:
    """Bitget vraca najstarije prvo; zadrzavamo taj poredak."""
    return [Candle.from_bitget(r) for r in rows if len(r) >= 6]


def ema(values: list[float], period: int) -> list[float]:
    """EMA seedovan SMA-om prvih `period` vrijednosti.

    Vraca listu iste duzine kao ulaz; prvih period-1 elemenata je None-free
    ali nepouzdano, pa citaj samo od indeksa period-1 nadalje.
    """
    if not values or period <= 0:
        return []
    out: list[float] = []
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / min(period, len(values))
    prev = seed
    for i, v in enumerate(values):
        if i < period - 1:
            out.append(seed)
        elif i == period - 1:
            prev = seed
            out.append(prev)
        else:
            prev = v * k + prev * (1 - k)
            out.append(prev)
    return out


def true_range(prev_close: float, high: float, low: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr(candles: list[Candle], period: int = 14) -> float:
    """Wilder ATR. Vraca 0.0 ako nema dovoljno svijeca."""
    if len(candles) < period + 1:
        return 0.0
    trs = [
        true_range(candles[i - 1].close, candles[i].high, candles[i].low)
        for i in range(1, len(candles))
    ]
    # Wilder smoothing: seed = prosjek prvih `period`, pa rekurzivno.
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def donchian(candles: list[Candle], lookback: int) -> tuple[float, float]:
    """(gornji, donji) kanal iz zadnjih `lookback` ZATVORENIH svijeca.

    Zadnja svijeca je namjerno izostavljena - ona je u toku i njen high/low
    bi ucinio breakout uvjet trivijalno istinitim (look-ahead bias).
    """
    if len(candles) < lookback + 1:
        return (0.0, 0.0)
    window = candles[-(lookback + 1) : -1]
    return (max(c.high for c in window), min(c.low for c in window))
