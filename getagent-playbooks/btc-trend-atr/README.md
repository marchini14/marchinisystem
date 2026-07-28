# BTC Turtle Breakout (ATR Risk)

## 策略 / Strategy

A Donchian-channel breakout Playbook on BTC perpetual futures — the classic
"Turtle Trading" style of trend-following. The thesis: a fresh push to a new
local high/low often marks the start of a sustained move rather than an
exhausted one, so the strategy treats the breakout itself as the signal
instead of reading a momentum indicator.

## 开仓 / Entry

The Playbook opens a long position when price breaks above its recent trading
range (a fresh N-bar high). It opens a short position on the mirror-image
breakdown below its recent range low. Only the bar where the range is actually
broken is treated as actionable, not every bar afterward.

## 平仓 / Exit

A position closes on whichever happens first:

- price reverses back through a shorter recent range on the opposite side
  (the classic Turtle "exit channel"), or
- price moves against the position by a distance tied to current market
  volatility (a protective stop), or
- price moves in the position's favor by a wider volatility-tied distance (a
  take-profit).

Both the stop and the take-profit scale with how volatile the market currently
is, so the strategy's risk tolerance widens or tightens automatically as
conditions change, instead of using one fixed distance in every regime. On
live subscriptions the stop and take-profit are placed as real trigger orders
on the exchange at entry time, so they can execute even between scheduled
runs; the exit-channel reversal is checked and applied on the next scheduled
run.

## Parameters

Subscribers may tune:

- **leverage** — amplifies both upside and drawdown equally; higher leverage
  does not make the strategy more selective, it only sizes risk larger.
- **margin_budget** — the per-strategy capital cap the platform sizes orders
  against and uses as the denominator for return percentage.
- **entry_channel_period** — how many recent bars define the breakout range
  used for entries. Widening it demands a bigger move before acting, giving
  fewer but more selective signals.
- **exit_channel_period** — how many recent bars define the (shorter) range
  used for exits. Widening it gives a winning trend more room to breathe
  before closing.
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

This strategy underperforms in choppy, range-bound markets where price
repeatedly pokes to a new local high/low without following through — each
false breakout can produce a small loss. Sudden volatility expansion around
major news events can widen the protective stop enough that a single losing
trade costs more than usual before it closes. Persistent funding-rate cost on
a held position and slippage during fast markets both erode live returns
relative to backtest assumptions. Past backtest performance is not a
guarantee of live profitability — only subscribe with capital, leverage, and
a margin budget you can tolerate losing in full.
