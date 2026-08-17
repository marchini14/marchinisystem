"""Strategija: Donchian breakout uz EMA trend filter.

Logika je namjerno jednostavna i mehanicka. Nema predikcije, nema "signala" -
samo pravilo: ako cijena probije kanal zadnjih N svijeca U SMJERU trenda, uzmi
poziciju, sa ATR stopom. Vecina takvih trejdova gubi malo, manjina zaradi vise.
"""

from __future__ import annotations

from dataclasses import dataclass

from .indicators import Candle, atr, donchian, ema


@dataclass
class Signal:
    side: str | None  # "long" | "short" | None
    entry: float
    atr_value: float
    reason: str


def evaluate(
    candles: list[Candle],
    *,
    donchian_lookback: int = 20,
    ema_trend: int = 50,
    atr_period: int = 14,
    allow_short: bool = True,
) -> Signal:
    """Procijeni zadnju svijecu. Vraca Signal(side=None) ako nema ulaza."""
    needed = max(donchian_lookback + 2, ema_trend + 1, atr_period + 2)
    if len(candles) < needed:
        return Signal(None, 0.0, 0.0, f"nedovoljno svijeca ({len(candles)} < {needed})")

    closes = [c.close for c in candles]
    last = candles[-1]
    atr_value = atr(candles, atr_period)
    if atr_value <= 0:
        return Signal(None, last.close, 0.0, "ATR nula")

    upper, lower = donchian(candles, donchian_lookback)
    if upper <= 0 or lower <= 0:
        return Signal(None, last.close, atr_value, "Donchian kanal nedostupan")

    trend = ema(closes, ema_trend)[-1]

    if last.close > upper and last.close > trend:
        return Signal(
            "long",
            last.close,
            atr_value,
            f"breakout iznad {upper:.6g} uz cijenu nad EMA{ema_trend} ({trend:.6g})",
        )

    if allow_short and last.close < lower and last.close < trend:
        return Signal(
            "short",
            last.close,
            atr_value,
            f"breakdown ispod {lower:.6g} uz cijenu pod EMA{ema_trend} ({trend:.6g})",
        )

    return Signal(
        None,
        last.close,
        atr_value,
        f"nema breakouta (close {last.close:.6g}, kanal {lower:.6g}-{upper:.6g})",
    )
