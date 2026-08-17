"""Izvrsavanje naloga: paper (simulacija) i live (pravi nalozi na Bitget).

Oba brokera dijele isti interfejs, pa bot.py ne zna u kojem je modu. Paper je
default jer se strategija testira bez novca; live se ukljucuje eksplicitno.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field

from .bitget import BitgetClient, BitgetError
from .risk import ContractSpec, SizedTrade

log = logging.getLogger(__name__)


@dataclass
class Position:
    symbol: str
    side: str  # "long" | "short"
    qty: float
    entry: float
    stop: float
    target: float
    opened_ts: int = field(default_factory=lambda: int(time.time() * 1000))
    notional: float = 0.0
    fee_paid: float = 0.0

    def unrealized(self, price: float) -> float:
        delta = price - self.entry if self.side == "long" else self.entry - price
        return delta * self.qty

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Position":
        known = {k: raw[k] for k in raw if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class Fill:
    symbol: str
    side: str
    qty: float
    price: float
    reason: str
    pnl: float = 0.0
    fee: float = 0.0


class PaperBroker:
    """Simulacija. Fill je po zadnjoj cijeni, uz taker fee - bez slippagea.

    Slippage nije modeliran, pa su paper rezultati blago optimisticni. Screener
    trazi likvidne parove upravo da ta razlika ostane mala.
    """

    mode = "paper"

    def __init__(self, starting_equity: float) -> None:
        self.equity = starting_equity
        self.positions: dict[str, Position] = {}
        self.realized_pnl = 0.0

    def open(self, trade: SizedTrade, spec: ContractSpec) -> Position:
        fee = trade.notional * spec.taker_fee
        self.equity -= fee
        pos = Position(
            symbol=trade.symbol,
            side=trade.side,
            qty=trade.qty,
            entry=trade.entry,
            stop=trade.stop,
            target=trade.target,
            notional=trade.notional,
            fee_paid=fee,
        )
        self.positions[trade.symbol] = pos
        log.info(
            "[PAPER] OPEN %s %s qty=%g @ %g stop=%g target=%g fee=%.4f",
            pos.side, pos.symbol, pos.qty, pos.entry, pos.stop, pos.target, fee,
        )
        return pos

    def close(self, symbol: str, price: float, reason: str, spec: ContractSpec) -> Fill | None:
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return None
        fee = pos.qty * price * spec.taker_fee
        pnl = pos.unrealized(price) - fee
        self.equity += pnl
        self.realized_pnl += pnl
        log.info(
            "[PAPER] CLOSE %s %s @ %g (%s) pnl=%.4f equity=%.2f",
            pos.side, symbol, price, reason, pnl, self.equity,
        )
        return Fill(symbol, pos.side, pos.qty, price, reason, pnl, fee)

    def check_exits(self, symbol: str, high: float, low: float, spec: ContractSpec) -> Fill | None:
        """Da li je svijeca aktivirala stop ili target.

        Ako su u istoj svijeci dotaknuta oba nivoa, pretpostavljamo da je stop
        prvi - iz svijece se ne vidi poredak, pa biramo pesimisticnu varijantu
        da paper rezultati ne budu ljepsi od stvarnosti.
        """
        pos = self.positions.get(symbol)
        if pos is None:
            return None
        if pos.side == "long":
            if low <= pos.stop:
                return self.close(symbol, pos.stop, "stop-loss", spec)
            if high >= pos.target:
                return self.close(symbol, pos.target, "take-profit", spec)
        else:
            if high >= pos.stop:
                return self.close(symbol, pos.stop, "stop-loss", spec)
            if low <= pos.target:
                return self.close(symbol, pos.target, "take-profit", spec)
        return None


class LiveBroker:
    """Pravi nalozi. SL/TP se salju uz ulazni nalog, pa ih burza drzi.

    Zato ovdje nema check_exits: izlaze vodi Bitget, a bot ih samo detektuje
    tako sto pozicija nestane sa liste otvorenih pozicija.
    """

    mode = "live"

    def __init__(self, client: BitgetClient, leverage: int) -> None:
        if not client.has_credentials:
            raise RuntimeError(
                "live mode traži BITGET_API_KEY / _SECRET / _PASSPHRASE u okolini"
            )
        self.client = client
        self.leverage = leverage
        self._leverage_set: set[str] = set()

    @property
    def equity(self) -> float:
        return self.client.available_balance()

    def positions_from_exchange(self) -> dict[str, Position]:
        out: dict[str, Position] = {}
        for raw in self.client.positions():
            qty = float(raw.get("total", 0) or 0)
            if qty <= 0:
                continue
            symbol = raw["symbol"]
            out[symbol] = Position(
                symbol=symbol,
                side="long" if raw.get("holdSide") == "long" else "short",
                qty=qty,
                entry=float(raw.get("openPriceAvg", 0) or 0),
                stop=float(raw.get("presetStopLossPrice", 0) or 0),
                target=float(raw.get("presetStopSurplusPrice", 0) or 0),
            )
        return out

    def open(self, trade: SizedTrade, spec: ContractSpec) -> Position | None:
        if trade.symbol not in self._leverage_set:
            try:
                self.client.set_leverage(trade.symbol, self.leverage)
                self._leverage_set.add(trade.symbol)
            except BitgetError as exc:
                log.error("ne mogu postaviti leverage za %s: %s", trade.symbol, exc)
                return None

        qty_str = f"{trade.qty:.{spec.volume_place}f}" if spec.volume_place else f"{int(trade.qty)}"
        try:
            self.client.place_market_order(
                symbol=trade.symbol,
                side="buy" if trade.side == "long" else "sell",
                size=qty_str,
                stop_loss=f"{trade.stop:.{spec.price_place}f}",
                take_profit=f"{trade.target:.{spec.price_place}f}",
                client_oid=f"marchini-{int(time.time() * 1000)}",
            )
        except BitgetError as exc:
            log.error("nalog odbijen za %s: %s", trade.symbol, exc)
            return None

        log.info(
            "[LIVE] OPEN %s %s qty=%s @ ~%g stop=%g target=%g",
            trade.side, trade.symbol, qty_str, trade.entry, trade.stop, trade.target,
        )
        return Position(
            symbol=trade.symbol,
            side=trade.side,
            qty=trade.qty,
            entry=trade.entry,
            stop=trade.stop,
            target=trade.target,
            notional=trade.notional,
        )

    def close(self, symbol: str, price: float, reason: str, spec: ContractSpec) -> Fill | None:
        positions = self.positions_from_exchange()
        pos = positions.get(symbol)
        if pos is None:
            return None
        try:
            self.client.close_position(symbol, pos.side)
        except BitgetError as exc:
            log.error("ne mogu zatvoriti %s: %s", symbol, exc)
            return None
        log.info("[LIVE] CLOSE %s %s (%s)", pos.side, symbol, reason)
        return Fill(symbol, pos.side, pos.qty, price, reason)
