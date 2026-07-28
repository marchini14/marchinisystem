"""Crypto/DeFi/trading quant formulas.

Extracted from an uploaded reference file (satoshi_crypto.py) and cleaned up:
the knowledge-graph/"consciousness"/fake-learning wrapper around these
formulas was dropped (it didn't compute anything real), keeping only the
actual math. Not wired into main.py/strategy.py yet — available for future
strategy iterations (e.g. Kelly-sized positions, RSI/Bollinger confirmation
filters, DeFi-side risk checks).
"""
import math
from typing import Dict, List, Tuple

# --- Technical indicators ---------------------------------------------------


def rsi(prices: List[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, period + 1):
        change = prices[-i] - prices[-i - 1]
        (gains if change > 0 else losses).append(abs(change))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def bollinger_bands(prices: List[float], period: int = 20, std_dev: float = 2.0) -> Tuple[float, float, float]:
    """Returns (upper, mid, lower)."""
    if len(prices) < period:
        return (0.0, 0.0, 0.0)
    window = prices[-period:]
    mean = sum(window) / period
    variance = sum((p - mean) ** 2 for p in window) / period
    std = math.sqrt(variance)
    return (mean + std_dev * std, mean, mean - std_dev * std)


# --- Portfolio / risk metrics ------------------------------------------------


def sharpe_ratio(returns: List[float], risk_free: float = 0.0) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(variance)
    return (mean - risk_free) / std if std > 0 else 0.0


def max_drawdown(prices: List[float]) -> float:
    max_dd, peak = 0.0, prices[0]
    for p in prices:
        peak = max(peak, p)
        max_dd = max(max_dd, (peak - p) / peak)
    return max_dd


def volatility(returns: List[float], periods_per_year: int = 365) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(periods_per_year)


def kelly_criterion(win_prob: float, win_loss_ratio: float) -> float:
    """Fraction of bankroll to risk. win_loss_ratio = avg win size / avg loss size."""
    b, p = win_loss_ratio, win_prob
    q = 1 - p
    return (b * p - q) / b if b > 0 else 0.0


def position_size(portfolio_value: float, risk_per_trade: float, stop_loss_pct: float) -> float:
    return portfolio_value * risk_per_trade / stop_loss_pct if stop_loss_pct > 0 else 0.0


# --- Derivatives -------------------------------------------------------------


def funding_rate_premium(index_price: float, mark_price: float) -> float:
    return (mark_price - index_price) / index_price if index_price != 0 else 0.0


def delta_neutral_hedge(spot_position: float, perp_position: float) -> float:
    return spot_position - perp_position


# --- AMM / DEX ----------------------------------------------------------------


def amm_price(x_reserve: float, y_reserve: float) -> float:
    return y_reserve / x_reserve if x_reserve > 0 else 0.0


def amm_swap_out(dx: float, x_reserve: float, y_reserve: float, fee: float = 0.003) -> float:
    dx_eff = dx * (1 - fee)
    return y_reserve * dx_eff / (x_reserve + dx_eff)


def impermanent_loss(p0: float, p1: float) -> float:
    ratio = p1 / p0
    return (2 * math.sqrt(ratio) / (1 + ratio)) - 1


def slippage(dx: float, x_reserve: float) -> float:
    return dx / (x_reserve + dx)


def price_impact(dx: float, x_reserve: float, y_reserve: float, fee: float = 0.003) -> float:
    dy = amm_swap_out(dx, x_reserve, y_reserve, fee)
    spot = y_reserve / x_reserve
    exec_price = dy / dx
    return (exec_price - spot) / spot if spot != 0 else 0.0


# --- Lending / liquidation ----------------------------------------------------


def lending_health_factor(collateral: float, collateral_price: float, ltv: float, debt: float) -> float:
    return (collateral * collateral_price * ltv) / debt if debt > 0 else float("inf")


def liquidation_price(collateral: float, debt: float, threshold: float = 0.825) -> float:
    return debt / (collateral * threshold) if collateral > 0 else float("inf")


def flash_loan_profit(arbitrage_spread: float, loan_amount: float, fee: float = 0.0009) -> float:
    return arbitrage_spread * loan_amount - loan_amount * fee


# --- Staking / yield ------------------------------------------------------------


def staking_apy(reward_per_block: float, blocks_per_year: float, total_staked: float, price_reward: float, price_staked: float) -> float:
    annual_rewards = reward_per_block * blocks_per_year * price_reward
    staked_value = total_staked * price_staked
    return annual_rewards / staked_value if staked_value > 0 else 0.0


def compound_apy(apr: float, compounds_per_year: int = 365) -> float:
    return (1 + apr / compounds_per_year) ** compounds_per_year - 1


# --- Tokenomics -----------------------------------------------------------------


def market_cap(price: float, circulating_supply: float) -> float:
    return price * circulating_supply


def fdv(price: float, max_supply: float) -> float:
    return price * max_supply


def tvl_ratio(market_cap_usd: float, tvl: float) -> float:
    return market_cap_usd / tvl if tvl > 0 else 0.0


def nvt_ratio(market_cap_usd: float, tx_volume_24h: float) -> float:
    return market_cap_usd / tx_volume_24h if tx_volume_24h > 0 else 0.0


def mvrv_ratio(market_cap_usd: float, realized_cap: float) -> float:
    return market_cap_usd / realized_cap if realized_cap > 0 else 0.0
