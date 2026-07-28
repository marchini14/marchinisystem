"""BTC Trend Follower (ATR Risk) — historical and live entry point.

Historical (`runtime.is_historical()`): replay through the Nautilus-backed
managed backtest engine using `src/strategy.py`.

Live (`runtime.is_live()`): recompute the same Donchian breakout + ATR
stop/target from recent closed candles, then let
`runtime.emit_signal_or_follow(...)` decide whether to actually manage the
position (follow-trade subscriptions only; signal-only subscriptions only
ever emit).
"""
import math
from datetime import datetime, timezone
from typing import Any, Optional

from getagent import backtest, data, runtime

from .indicators import atr_series, donchian_channels, latest_valid

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


def _fetch_recent_bars(symbol: str, chunks: int = 6) -> list[dict[str, Any]]:
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

    records = _fetch_recent_bars(symbol, chunks=6)
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


def _live_decision(symbol: str, cfg: dict[str, Any], hold_side: Optional[str]) -> dict[str, Any]:
    entry_period = int(cfg.get("entry_channel_period", 480) or 480)
    exit_period = int(cfg.get("exit_channel_period", 240) or 240)
    atr_period = int(cfg.get("atr_period", 14) or 14)
    warmup = max(entry_period, exit_period, atr_period) + 2

    # entry_period can be several hundred bars; a single kline call is capped
    # at 1000, so chunk enough history to cover warmup plus a comfortable buffer.
    chunks = max(2, (warmup * 2) // 1000 + 1)
    records = _fetch_recent_bars(symbol, chunks=chunks)
    if len(records) < warmup:
        return {"entry_signal": None, "exit_signal": None, "reason": "insufficient warm-up bars", "atr": None}

    frame = backtest.prepare_frame(records, datetime_index="date")
    if frame.empty or len(frame) < warmup:
        return {"entry_signal": None, "exit_signal": None, "reason": "insufficient warm-up bars", "atr": None}

    entry_upper, entry_lower = donchian_channels(frame["high"], frame["low"], entry_period)
    exit_upper, exit_lower = donchian_channels(frame["high"], frame["low"], exit_period)
    atr = atr_series(frame["high"], frame["low"], frame["close"], atr_period)

    last_high = float(frame["high"].iloc[-1])
    last_low = float(frame["low"].iloc[-1])
    last_price = float(frame["close"].iloc[-1])
    atr_value = latest_valid(atr)

    entry_up_level = latest_valid(entry_upper)
    entry_low_level = latest_valid(entry_lower)
    exit_up_level = latest_valid(exit_upper)
    exit_low_level = latest_valid(exit_lower)

    entry_signal = None
    if entry_up_level is not None and last_high > entry_up_level:
        entry_signal = "long"
    elif entry_low_level is not None and last_low < entry_low_level:
        entry_signal = "short"

    exit_signal = None
    if hold_side == "long" and exit_low_level is not None and last_low < exit_low_level:
        exit_signal = "long_exit"
    elif hold_side == "short" and exit_up_level is not None and last_high > exit_up_level:
        exit_signal = "short_exit"

    last_bar_ts = _last_bar_open_ms(records)
    return {
        "entry_signal": entry_signal,
        "exit_signal": exit_signal,
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

    # PRE-CHECK (re-read live state right before mutating; the signal above
    # may have been computed slightly earlier in this same run).
    position_result = trade.contract.current_position(symbol=symbol)
    position = trade.helpers.find_contract_position(position_result, symbol=symbol)

    if desired_action == "close":
        if position is None:
            return {"action": "noop", "reason": "already flat"}
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

    if position is not None:
        return {"action": "held", "hold_side": position.hold_side}

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
    from getagent import trade

    cfg = _config()
    symbol = _symbol(cfg)
    leverage = int(cfg.get("leverage", 3) or 3)
    margin_budget = str(cfg.get("margin_budget", "100") or "100")
    stop_multiplier = float(cfg.get("atr_stop_multiplier", 2.0) or 2.0)
    target_multiplier = float(cfg.get("atr_target_multiplier", 3.0) or 3.0)

    # Read-only position check (safe outside follow-trade branches) decides
    # whether this run is watching for an entry breakout or an exit-channel
    # breach on an existing position.
    position_result = trade.contract.current_position(symbol=symbol)
    position = trade.helpers.find_contract_position(position_result, symbol=symbol)
    hold_side = position.hold_side if position is not None else None

    decision = _live_decision(symbol, cfg, hold_side)
    stale = bool(decision.get("stale", False))

    if stale:
        # Newest closed bar is too old — refuse to trade on stale data,
        # regardless of what the channels say.
        action = "hold"
    elif hold_side is not None:
        action = "close" if decision.get("exit_signal") else "hold"
    else:
        action = decision.get("entry_signal") or "watch"

    confidence = 0.0 if stale else 0.7 if action in ("long", "short", "close") else 0.3

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
                "hold_side": hold_side,
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
