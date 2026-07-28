"""Pure ATR-based stop-loss / take-profit price math.

Kept separate from strategy/execution code because both the Nautilus backtest
strategy and the live path need the exact same formula. Neither this module
nor its callers may talk to the network or hold a trading identity.
"""
from typing import NamedTuple


class StopTarget(NamedTuple):
    stop_price: float
    target_price: float


def compute_stop_target(
    entry_price: float,
    side: str,
    atr_value: float,
    stop_multiplier: float,
    target_multiplier: float,
) -> StopTarget:
    """side is 'long' or 'short'. atr_value must already be a positive, warmed-up ATR reading."""
    if side not in ("long", "short"):
        raise ValueError(f"unsupported side={side!r}")
    # Config values may arrive as strings (Nautilus/manifest config merging
    # does not guarantee numeric coercion), so cast defensively rather than
    # trust the declared type.
    entry_price = float(entry_price)
    atr_value = float(atr_value)
    stop_multiplier = float(stop_multiplier)
    target_multiplier = float(target_multiplier)
    if atr_value <= 0:
        raise ValueError("atr_value must be positive")

    stop_distance = atr_value * stop_multiplier
    target_distance = atr_value * target_multiplier

    if side == "long":
        return StopTarget(
            stop_price=entry_price - stop_distance,
            target_price=entry_price + target_distance,
        )
    return StopTarget(
        stop_price=entry_price + stop_distance,
        target_price=entry_price - target_distance,
    )


def stop_breached(side: str, stop_price: float, bar_low: float, bar_high: float) -> bool:
    if side == "long":
        return bar_low <= stop_price
    return bar_high >= stop_price


def target_breached(side: str, target_price: float, bar_low: float, bar_high: float) -> bool:
    if side == "long":
        return bar_high >= target_price
    return bar_low <= target_price
