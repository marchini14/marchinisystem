"""Risk management i sizing pozicije.

Ovo je najvazniji modul u sistemu. Strategija odlucuje *da li* ulazimo;
ovaj modul odlucuje *koliko* i ima pravo veta. Svaki veto vraca citljiv
razlog da se u logu vidi zasto trejd nije poslan.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Priblizna maintenance margin stopa za USDT-M perpetuale na najnizem tieru.
# Konzervativna procjena: stvarni MMR raste sa velicinom pozicije, pa ovo
# potcjenjuje udaljenost likvidacije u nasu korist.
DEFAULT_MMR = 0.005


@dataclass(frozen=True)
class ContractSpec:
    """Pravila burze za jedan simbol - dolaze iz /mix/market/contracts."""

    symbol: str
    min_trade_usdt: float
    min_trade_num: float
    volume_place: int
    price_place: int
    max_leverage: int
    taker_fee: float
    maker_fee: float

    @classmethod
    def from_api(cls, raw: dict) -> "ContractSpec":
        return cls(
            symbol=raw["symbol"],
            min_trade_usdt=float(raw.get("minTradeUSDT", 5) or 5),
            min_trade_num=float(raw.get("minTradeNum", 0) or 0),
            volume_place=int(raw.get("volumePlace", 0) or 0),
            price_place=int(raw.get("pricePlace", 4) or 4),
            max_leverage=int(float(raw.get("maxLever", 20) or 20)),
            taker_fee=float(raw.get("takerFeeRate", 0.0006) or 0.0006),
            maker_fee=float(raw.get("makerFeeRate", 0.0002) or 0.0002),
        )


@dataclass
class SizedTrade:
    """Rezultat sizinga. `ok` je False ako je risk modul stavio veto."""

    ok: bool
    symbol: str
    side: str = ""
    qty: float = 0.0
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    notional: float = 0.0
    margin: float = 0.0
    risk_amount: float = 0.0
    round_trip_fee: float = 0.0
    liq_price: float = 0.0
    rejections: list[str] = field(default_factory=list)

    def reject(self, reason: str) -> "SizedTrade":
        self.ok = False
        self.rejections.append(reason)
        return self


def floor_to_places(value: float, places: int) -> float:
    """Zaokruzi NADOLJE na `places` decimala.

    Nadolje, ne na najblize: zaokruzivanje gore bi povecalo poziciju iznad
    dozvoljenog rizika i moglo probiti raspolozivu marginu.
    """
    if places <= 0:
        return float(math.floor(value))
    factor = 10**places
    return math.floor(value * factor) / factor


def round_price(value: float, places: int) -> float:
    return round(value, places)


def liquidation_price(entry: float, side: str, leverage: int, mmr: float = DEFAULT_MMR) -> float:
    """Priblizna likvidacijska cijena za izolovanu marginu.

    Pozicija se likvidira kad izgubljeni dio priblizno pojede marginu umanjenu
    za maintenance margin: relativni potez ~ (1/leverage - mmr).
    """
    move = max(1.0 / leverage - mmr, 1e-9)
    return entry * (1 - move) if side == "long" else entry * (1 + move)


def size_trade(
    *,
    symbol: str,
    side: str,
    entry: float,
    atr_value: float,
    spec: ContractSpec,
    equity: float,
    leverage: int,
    risk_per_trade_pct: float,
    atr_stop_mult: float,
    atr_target_mult: float,
    max_fee_share_of_target_pct: float,
    min_liq_distance_vs_stop: float,
) -> SizedTrade:
    """Izracunaj velicinu pozicije iz rizika, pa provjeri sve limite.

    Sizing ide od rizika, ne od zeljene velicine: fiksiramo koliko USDT smijemo
    izgubiti do stopa, i iz udaljenosti stopa izvedemo kolicinu.
    """
    result = SizedTrade(ok=True, symbol=symbol, side=side, entry=entry)

    if entry <= 0 or atr_value <= 0:
        return result.reject("nevalidna cijena ili ATR")
    if leverage < 1:
        return result.reject("leverage < 1")
    if leverage > spec.max_leverage:
        return result.reject(
            f"leverage {leverage}x iznad max {spec.max_leverage}x za {symbol}"
        )

    stop_distance = atr_value * atr_stop_mult
    target_distance = atr_value * atr_target_mult
    if side == "long":
        result.stop = round_price(entry - stop_distance, spec.price_place)
        result.target = round_price(entry + target_distance, spec.price_place)
    else:
        result.stop = round_price(entry + stop_distance, spec.price_place)
        result.target = round_price(entry - target_distance, spec.price_place)

    if result.stop <= 0:
        return result.reject("stop cijena ispod nule - ATR prevelik za ovu cijenu")

    # --- sizing iz rizika ---
    risk_amount = equity * (risk_per_trade_pct / 100.0)
    raw_qty = risk_amount / stop_distance
    qty = floor_to_places(raw_qty, spec.volume_place)

    # Margina ne smije preci raspolozivi kapital: ako pređe, smanji kolicinu.
    max_qty_by_margin = floor_to_places((equity * leverage) / entry, spec.volume_place)
    qty = min(qty, max_qty_by_margin)

    if qty <= 0:
        return result.reject(
            f"kolicina zaokruzena na 0 (min korak {10 ** -spec.volume_place:g}) - "
            f"kapital {equity:.2f} USDT premali za ovaj par"
        )
    if spec.min_trade_num and qty < spec.min_trade_num:
        return result.reject(
            f"kolicina {qty:g} ispod minimuma burze {spec.min_trade_num:g}"
        )

    result.qty = qty
    result.risk_amount = qty * stop_distance
    result.notional = qty * entry
    result.margin = result.notional / leverage
    result.round_trip_fee = result.notional * spec.taker_fee * 2
    result.liq_price = liquidation_price(entry, side, leverage)

    # --- limiti burze ---
    if result.notional < spec.min_trade_usdt:
        return result.reject(
            f"notional {result.notional:.2f} USDT ispod minimuma burze "
            f"{spec.min_trade_usdt:.2f} USDT"
        )
    if result.margin > equity:
        return result.reject(
            f"potrebna margina {result.margin:.2f} > kapital {equity:.2f} USDT"
        )

    # --- fee sanity: profit mora znacajno nadmasiti troskove ---
    expected_profit = qty * target_distance
    if expected_profit <= 0:
        return result.reject("ocekivani profit nije pozitivan")
    fee_share = result.round_trip_fee / expected_profit * 100.0
    if fee_share > max_fee_share_of_target_pct:
        return result.reject(
            f"fee je {fee_share:.1f}% ocekivanog profita "
            f"(limit {max_fee_share_of_target_pct:.1f}%) - pozicija previse mala "
            f"da bi imala smisla"
        )

    # --- likvidacija mora biti dalje od stopa ---
    liq_distance = abs(entry - result.liq_price)
    if liq_distance < stop_distance * min_liq_distance_vs_stop:
        return result.reject(
            f"likvidacija na {liq_distance:.6g} od ulaza je preblizu stopa "
            f"({stop_distance:.6g}) pri {leverage}x - smanji leverage"
        )

    return result


def daily_loss_exceeded(realized_pnl_today: float, equity: float, limit_pct: float) -> bool:
    """True ako je danasnji gubitak dosegao dnevni limit."""
    if limit_pct <= 0:
        return False
    return realized_pnl_today <= -abs(equity * limit_pct / 100.0)
