# BTC Trend Follower (ATR Risk)

## 策略 / Strategy

A trend-following Playbook on BTC perpetual futures. The thesis: once a real
trend forms, price tends to travel along that direction for a while rather
than chopping back and forth, so the strategy waits for confirmation and rides
the middle of the move instead of trying to call the exact top or bottom.

## 开仓 / Entry

The Playbook opens a long position when short-term momentum clearly aligns
with and leads the longer-term trend upward — a fresh bullish regime. It opens
a short position on the mirror-image bearish alignment. Only the moment the
alignment first forms is treated as actionable, not every bar it persists.

## 平仓 / Exit

A position closes on whichever happens first:

- the trend alignment fades and reverses toward the opposite regime, or
- price moves against the position by a distance tied to current market
  volatility (a protective stop), or
- price moves in the position's favor by a wider volatility-tied distance (a
  take-profit).

Both the stop and the take-profit scale with how volatile the market currently
is, so the strategy's risk tolerance widens or tightens automatically as
conditions change, instead of using one fixed distance in every regime. On
live subscriptions the stop and take-profit are placed as real trigger orders
on the exchange at entry time, so they can execute even between scheduled
runs; the crossover-reversal exit is checked and applied on the next scheduled
run.

## Parameters

Subscribers may tune:

- **leverage** — amplifies both upside and drawdown equally; higher leverage
  does not make the strategy more selective, it only sizes risk larger.
- **margin_budget** — the per-strategy capital cap the platform sizes orders
  against and uses as the denominator for return percentage.
- **atr_stop_multiplier** — how far the protective stop sits from entry,
  scaled by current volatility. Widening it means fewer trades get stopped out
  by ordinary noise, at the cost of a larger loss when a trade is wrong.
- **atr_target_multiplier** — how far the take-profit sits from entry, scaled
  by current volatility. Widening it asks for a larger favorable move before
  locking in gains.

## Reading the backtest metrics

`total_return_pct` and `max_drawdown_pct` are reported on a strategy basis
(net P&L divided by `margin_budget`), not on the full backtest account
balance — a strategy can show a large percentage return on a modest margin
budget while representing a small slice of a much larger account. Check
`win_rate` together with `total_trades`: a high win rate over very few trades
is much less meaningful than the same win rate over dozens of trades.
`sharpe_ratio` is only meaningful once trades exist.

## 风险 / Risk

This strategy underperforms in choppy, range-bound markets where short-term
momentum flips back and forth without a real trend forming — each flip can
produce a small loss. Sudden volatility expansion around major news events can
widen the protective stop enough that a single losing trade costs more than
usual before it closes. Persistent funding-rate cost on a held position and
slippage during fast markets both erode live returns relative to backtest
assumptions. Past backtest performance is not a guarantee of live
profitability — only subscribe with capital, leverage, and a margin budget you
can tolerate losing in full.
