"""Glavna petlja bota: screening -> signal -> risk -> izvrsavanje."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from . import screener, strategy
from .bitget import BitgetClient
from .broker import Fill, LiveBroker, PaperBroker, Position
from .config import Config
from .indicators import atr, parse_candles
from .risk import ContractSpec, daily_loss_exceeded, size_trade

log = logging.getLogger(__name__)


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class Bot:
    def __init__(self, cfg: Config, client: BitgetClient) -> None:
        self.cfg = cfg
        self.client = client
        self.specs: dict[str, ContractSpec] = {}
        self.day = _today_utc()
        self.realized_today = 0.0
        self.history: list[dict] = []
        self.halted_reason: str | None = None

        if cfg.mode == "live":
            self.broker: PaperBroker | LiveBroker = LiveBroker(client, cfg.risk.leverage)
        else:
            self.broker = PaperBroker(cfg.equity)

        self.state_path = Path(cfg.runtime.state_file)
        self._load_state()

    # ------------------------------------------------------------- specs

    def load_specs(self) -> None:
        self.specs = {
            raw["symbol"]: ContractSpec.from_api(raw)
            for raw in self.client.contracts()
            if raw.get("symbolStatus") == "normal"
        }
        log.info("ucitano %d specifikacija kontrakta", len(self.specs))

    # ------------------------------------------------------------- state

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text())
        except (OSError, ValueError) as exc:
            log.warning("ne mogu procitati state (%s), pocinjem od nule", exc)
            return
        self.day = data.get("day", self.day)
        self.realized_today = float(data.get("realized_today", 0.0))
        self.history = data.get("history", [])
        if isinstance(self.broker, PaperBroker):
            self.broker.equity = float(data.get("equity", self.broker.equity))
            self.broker.realized_pnl = float(data.get("realized_pnl", 0.0))
            self.broker.positions = {
                sym: Position.from_dict(p) for sym, p in (data.get("positions") or {}).items()
            }
        log.info("state ucitan: equity=%.2f, %d otvorenih", self.broker.equity, len(self.open_positions))

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "day": self.day,
            "mode": self.cfg.mode,
            "equity": self.broker.equity,
            "realized_today": self.realized_today,
            "realized_pnl": getattr(self.broker, "realized_pnl", 0.0),
            "positions": {s: p.to_dict() for s, p in self.open_positions.items()},
            "history": self.history[-500:],
            "updated": datetime.now(timezone.utc).isoformat(),
        }
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.state_path)  # atomicno: state ne ostaje polupisan ako bot padne

    @property
    def open_positions(self) -> dict[str, Position]:
        if isinstance(self.broker, LiveBroker):
            return self.broker.positions_from_exchange()
        return self.broker.positions

    # ------------------------------------------------------- jedan prolaz

    def tick(self) -> None:
        if self.day != _today_utc():
            log.info("novi dan - resetujem dnevni PnL (bio %.2f)", self.realized_today)
            self.day = _today_utc()
            self.realized_today = 0.0
            self.halted_reason = None

        if not self.specs:
            self.load_specs()

        equity = self.broker.equity
        if daily_loss_exceeded(self.realized_today, equity, self.cfg.risk.daily_loss_limit_pct):
            self.halted_reason = (
                f"dnevni limit gubitka dosegnut ({self.realized_today:.2f} USDT) - "
                f"nema novih ulaza do sutra"
            )
            log.warning(self.halted_reason)
            self._manage_open_positions()
            self._save_state()
            return

        candidates = screener.screen(
            self.client,
            min_volume_24h=self.cfg.screener.min_volume_24h,
            max_change_24h_pct=self.cfg.screener.max_change_24h_pct,
            min_atr_pct=self.cfg.screener.min_atr_pct,
            max_abs_funding=self.cfg.screener.max_abs_funding,
            top_n=self.cfg.screener.top_n,
            blacklist=self.cfg.screener.blacklist,
            timeframe=self.cfg.strategy.timeframe,
            atr_period=self.cfg.strategy.atr_period,
        )
        log.info("screener: %d kandidata", len(candidates))

        self._manage_open_positions()
        self._look_for_entries(candidates)
        self._save_state()

    # ------------------------------------------------- upravljanje pozicijama

    def _manage_open_positions(self) -> None:
        if isinstance(self.broker, LiveBroker):
            # SL/TP drzi burza; nista se ne provjerava lokalno.
            return
        for symbol in list(self.broker.positions):
            spec = self.specs.get(symbol)
            if spec is None:
                continue
            try:
                candles = parse_candles(
                    self.client.candles(symbol, self.cfg.strategy.timeframe, 3)
                )
            except Exception as exc:
                log.warning("ne mogu dobiti svijece za %s: %s", symbol, exc)
                continue
            if not candles:
                continue
            last = candles[-1]
            fill = self.broker.check_exits(symbol, last.high, last.low, spec)
            if fill:
                self._record_fill(fill)

    def _record_fill(self, fill: Fill) -> None:
        self.realized_today += fill.pnl
        self.history.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "symbol": fill.symbol,
                "side": fill.side,
                "qty": fill.qty,
                "price": fill.price,
                "reason": fill.reason,
                "pnl": round(fill.pnl, 6),
            }
        )

    # ---------------------------------------------------------- novi ulazi

    def _look_for_entries(self, candidates: list[screener.Candidate]) -> None:
        open_now = self.open_positions
        slots = self.cfg.risk.max_open_positions - len(open_now)
        if slots <= 0:
            log.info("sve pozicije zauzete (%d) - ne trazim nove ulaze", len(open_now))
            return

        for cand in candidates:
            if slots <= 0:
                break
            if cand.symbol in open_now:
                continue
            spec = self.specs.get(cand.symbol)
            if spec is None:
                continue

            sig = strategy.evaluate(
                cand.candles,
                donchian_lookback=self.cfg.strategy.donchian_lookback,
                ema_trend=self.cfg.strategy.ema_trend,
                atr_period=self.cfg.strategy.atr_period,
                allow_short=self.cfg.strategy.allow_short,
            )
            if sig.side is None:
                log.debug("%s: %s", cand.symbol, sig.reason)
                continue

            trade = size_trade(
                symbol=cand.symbol,
                side=sig.side,
                entry=sig.entry,
                atr_value=sig.atr_value,
                spec=spec,
                equity=self.broker.equity,
                leverage=self.cfg.risk.leverage,
                risk_per_trade_pct=self.cfg.risk.risk_per_trade_pct,
                atr_stop_mult=self.cfg.strategy.atr_stop_mult,
                atr_target_mult=self.cfg.strategy.atr_target_mult,
                max_fee_share_of_target_pct=self.cfg.risk.max_fee_share_of_target_pct,
                min_liq_distance_vs_stop=self.cfg.risk.min_liq_distance_vs_stop,
            )
            if not trade.ok:
                log.info("%s SIGNAL %s odbijen: %s", cand.symbol, sig.side, "; ".join(trade.rejections))
                continue

            log.info("%s SIGNAL %s - %s", cand.symbol, sig.side, sig.reason)
            if self.broker.open(trade, spec):
                slots -= 1

    # ------------------------------------------------------------ petlja

    def run_forever(self) -> None:
        log.info(
            "bot startuje | mode=%s equity=%.2f leverage=%dx risk/trade=%.2f%%",
            self.cfg.mode, self.broker.equity, self.cfg.risk.leverage,
            self.cfg.risk.risk_per_trade_pct,
        )
        while True:
            started = time.time()
            try:
                self.tick()
            except KeyboardInterrupt:
                raise
            except Exception:
                # Petlja mora prezivjeti gresku jednog prolaza; alternativa je
                # da bot padne i ostavi otvorene pozicije bez nadzora.
                log.exception("greska u tick-u, nastavljam u sljedecem ciklusu")
            elapsed = time.time() - started
            time.sleep(max(self.cfg.runtime.poll_seconds - elapsed, 5))


def setup_logging(log_file: str, verbose: bool = False) -> None:
    path = Path(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
        handlers=[logging.FileHandler(path), logging.StreamHandler()],
    )
