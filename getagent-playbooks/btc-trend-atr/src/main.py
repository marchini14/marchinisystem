"""BTC Trend Follower (ATR Risk) — historical and live entry point.

Historical (`runtime.is_historical()`): replay through the Nautilus-backed
managed backtest engine using `src/strategy.py`.

Live (`runtime.is_live()`): recompute the same EMA crossover + ATR stop/target
from recent closed candles, then let `runtime.emit_signal_or_follow(...)`
decide whether to actually manage the position (follow-trade subscriptions
only; signal-only subscriptions only ever emit).
"""
import math
from datetime import datetime, timezone
from typing import Any, Optional

from getagent import backtest, data, runtime

from .indicators import atr_series, ema_series, latest_valid

_INTERVAL = "1h"
_INTERVAL_MS = 60 * 60 * 1000
# Refuse live decisions when the newest closed bar lags now by more than this
# many intervals — a stalled feed must not silently drive a trade decision.
_MAX_STALE_INTERVALS = 2


def _sanitize(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _sanitize_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    return {key: _sanitize(val) for key, val in metrics.items()}


def _config() -> dict[str, Any]:
    return runtime.manifest.get("strategy_config", {}) or {}


def _symbol(cfg: dict[str, Any]) -> str:
    symbols = cfg.get("trading_symbols") or ["BTCUSDT"]
    return str(symbols[0])


# ---------------------------------------------------------------------------
# Historical
# ---------------------------------------------------------------------------


def _fetch_replay_records(symbol: str, chunks: int = 6) -> list[dict[str, Any]]:
    """`crypto.futures.kline` caps each request at 1000 bars; chunk backward
    in time to build a longer replay window than a single call can return.
    """
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    seen_times: set[int] = set()
    records: list[dict[str, Any]] = []
    end_time = now_ms
    for _ in range(chunks):
        # closed_only=True (the default, spelled out here) keeps the
        # currently-forming candle out of the replay frame; a half-formed tail
        # bar would make the same backtest return different numbers on every run.
        bars = data.crypto.futures.kline(
            symbol=symbol,
            interval=_INTERVAL,
            limit=1000,
            end_time=end_time,
            closed_only=True,
        )
        rows = list(data.to_records(bars))
        if not rows:
            break
        for row in rows:
            ts = row.get("time")
            if isinstance(ts, (int, float)) and not isinstance(ts, bool) and int(ts) not in seen_times:
                seen_times.add(int(ts))
                records.append(row)
        chunk_min_ts = min(int(row["time"]) for row in rows if isinstance(row.get("time"), (int, float)))
        # Step the window back before the oldest bar just fetched so the next
        # chunk doesn't re-request (and re-count) the same range.
        end_time = chunk_min_ts - _INTERVAL_MS
    records.sort(key=lambda row: row["time"])
    return records


def _run_historical() -> None:
    cfg = _config()
    symbol = _symbol(cfg)

    records = _fetch_replay_records(symbol)
    if not records:
        runtime.emit_signal(
            action="watch",
            symbol=symbol,
            confidence=0.0,
            metrics={"rows": 0},
            meta={"reason": "no historical bars returned"},
        )
        return

    replay_frame = backtest.prepare_frame(records, datetime_index="date")
    if replay_frame.empty:
        runtime.emit_signal(
            action="watch",
            symbol=symbol,
            confidence=0.0,
            metrics={"rows": 0},
            meta={"reason": "no historical bars returned"},
        )
        return

    instrument_key = f"{symbol}.BINANCE"
    result = backtest.run(
        ohlcv_data={instrument_key: replay_frame},
        spec=runtime.backtest_spec,
    )

    chart_path = backtest.generate_chart(result)
    summary = result.summary or {}
    try:
        net_pnl = float(summary.get("net_pnl", 0) or 0)
    except (TypeError, ValueError):
        net_pnl = 0.0

    last_bar_ts = int(replay_frame.index.max().timestamp() * 1000)
    action = "long" if net_pnl > 0 else "watch"
    metrics = _sanitize_metrics(
        {
            "total_return_pct": result.total_return_pct,
            "net_pnl": net_pnl,
            "starting_balance": summary.get("starting_balance"),
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown_pct": result.max_drawdown_pct,
            "win_rate": result.win_rate,
            "total_trades": result.total_trades,
            "profit_factor": result.profit_factor,
            "rows": len(replay_frame),
            "last_bar_ts": last_bar_ts,
        }
    )

    runtime.emit_signal(
        action=action,
        symbol=symbol,
        confidence=_sanitize(result.win_rate) or 0.0,
        metrics=metrics,
        meta={"chart_path": chart_path},
    )


# ---------------------------------------------------------------------------
# Live
# ---------------------------------------------------------------------------


def _last_bar_open_ms(rows: list[dict[str, Any]]) -> Optional[int]:
    stamps = [
        int(value)
        for row in rows
        for value in (row.get("time"),)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    return max(stamps) if stamps else None


def _is_stale(last_bar_open_ms: Optional[int]) -> bool:
    if last_bar_open_ms is None:
        return True
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last_close_ms = last_bar_open_ms + _INTERVAL_MS
    return now_ms - last_close_ms > _MAX_STALE_INTERVALS * _INTERVAL_MS


def _live_decision(symbol: str, cfg: dict[str, Any]) -> dict[str, Any]:
    fast_period = int(cfg.get("fast_period", 12) or 12)
    slow_period = int(cfg.get("slow_period", 26) or 26)
    atr_period = int(cfg.get("atr_period", 14) or 14)
    warmup = max(fast_period, slow_period, atr_period) + 2

    bars = data.crypto.futures.kline(
        symbol=symbol,
        interval=_INTERVAL,
        limit=max(warmup * 3, 100),
        closed_only=True,
    )
    rows = list(data.to_records(bars))
    frame = data.to_dataframe(bars)

    if frame.empty or len(frame) < warmup:
        return {"action": "hold", "reason": "insufficient warm-up bars", "atr": None}

    fast = ema_series(frame["close"], fast_period)
    slow = ema_series(frame["close"], slow_period)
    atr = atr_series(frame["high"], frame["low"], frame["close"], atr_period)
    diff = fast - slow

    if len(diff) < 2:
        return {"action": "hold", "reason": "insufficient diff history", "atr": None}

    prev_diff = float(diff.iloc[-2])
    last_diff = float(diff.iloc[-1])
    cross_up = prev_diff <= 0.0 < last_diff
    cross_down = prev_diff >= 0.0 > last_diff

    action = "long" if cross_up else "short" if cross_down else "hold"
    last_price = float(frame["close"].iloc[-1])
    atr_value = latest_valid(atr)
    last_bar_ts = _last_bar_open_ms(rows)

    return {
        "action": action,
        "last_price": last_price,
        "atr": atr_value,
        "last_bar_ts": last_bar_ts,
        "stale": _is_stale(last_bar_ts),
    }


def _manage_position(
    symbol: str,
    desired_action: str,
    leverage: int,
    margin_budget: str,
    atr_value: Optional[float],
    last_price: float,
    stop_multiplier: float,
    target_multiplier: float,
) -> dict[str, Any]:
    from getagent import trade

    from .risk import compute_stop_target

    # PRE-CHECK
    position_result = trade.contract.current_position(symbol=symbol)
    position = trade.helpers.find_contract_position(position_result, symbol=symbol)

    if position is not None:
        opposite = (
            (position.hold_side == "long" and desired_action == "short")
            or (position.hold_side == "short" and desired_action == "long")
        )
        if not opposite:
            return {"action": "held", "hold_side": position.hold_side}

        # EXECUTE
        result = trade.contract.close_position(symbol=symbol, hold_side=position.hold_side)
        if not trade.is_success(result):
            raise RuntimeError(f"close_position failed: {result}")
        # POST-CHECK
        after = trade.contract.current_position(symbol=symbol)
        still_open = trade.helpers.find_contract_position(after, symbol=symbol)
        return {
            "action": "closed",
            "closed_side": position.hold_side,
            "still_open": still_open is not None,
        }

    if desired_action not in ("long", "short") or atr_value is None:
        return {"action": "noop"}

    stop_target = compute_stop_target(
        entry_price=last_price,
        side=desired_action,
        atr_value=atr_value,
        stop_multiplier=stop_multiplier,
        target_multiplier=target_multiplier,
    )
    tpsl = trade.helpers.resolve_contract_tpsl(
        symbol=symbol,
        side=desired_action,
        leverage=leverage,
        tp_trigger_price=str(stop_target.target_price),
        sl_trigger_price=str(stop_target.stop_price),
        reference_price=str(last_price),
    )
    qty_plan = trade.helpers.compute_qty(
        symbol=symbol,
        market="contract",
        budget_amount=margin_budget,
        leverage=leverage,
    )

    # EXECUTE
    opener = trade.contract.open_long_market if desired_action == "long" else trade.contract.open_short_market
    result = opener(
        symbol=symbol,
        qty=qty_plan.qty,
        leverage=leverage,
        tp_trigger_price=tpsl.tp_trigger_price,
        sl_trigger_price=tpsl.sl_trigger_price,
    )
    if not trade.is_success(result):
        raise RuntimeError(f"{desired_action} open failed: {result}")

    # POST-CHECK
    after = trade.contract.current_position(symbol=symbol)
    opened = trade.helpers.find_contract_position(after, symbol=symbol)
    return {
        "action": f"opened_{desired_action}",
        "qty": str(qty_plan.qty),
        "stop_price": stop_target.stop_price,
        "target_price": stop_target.target_price,
        "confirmed_open": opened is not None,
    }


def _run_live() -> None:
    cfg = _config()
    symbol = _symbol(cfg)
    leverage = int(cfg.get("leverage", 3) or 3)
    margin_budget = str(cfg.get("margin_budget", "100") or "100")
    stop_multiplier = float(cfg.get("atr_stop_multiplier", 2.0) or 2.0)
    target_multiplier = float(cfg.get("atr_target_multiplier", 3.0) or 3.0)

    decision = _live_decision(symbol, cfg)
    action = decision["action"]
    stale = bool(decision.get("stale", False))
    if stale:
        # Newest closed bar is too old — refuse to trade on stale data,
        # regardless of what the crossover says.
        action = "hold"

    confidence = 0.0 if stale else 0.7 if action in ("long", "short") else 0.3

    runtime.emit_signal_or_follow(
        action=action,
        symbol=symbol,
        confidence=confidence,
        metrics=_sanitize_metrics(
            {
                "atr": decision.get("atr"),
                "last_price": decision.get("last_price"),
                "last_bar_ts": decision.get("last_bar_ts"),
                "data_stale": stale,
            }
        ),
        meta={"reason": decision.get("reason")} if decision.get("reason") else {},
        execute_trade=lambda: _manage_position(
            symbol=symbol,
            desired_action=action,
            leverage=leverage,
            margin_budget=margin_budget,
            atr_value=decision.get("atr"),
            last_price=float(decision.get("last_price") or 0.0),
            stop_multiplier=stop_multiplier,
            target_multiplier=target_multiplier,
        ),
    )


def run() -> None:
    if runtime.is_historical():
        _run_historical()
        return
    if runtime.is_live():
        _run_live()
        return
    raise ValueError(f"unsupported evaluation_mode={runtime.evaluation_mode!r}")


if __name__ == "__main__":
    run()
