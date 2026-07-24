"""Bybit spot grid trading bot.

Strategija: postavi limit BUY naloge na razine ispod trenutne cijene.
Kad se BUY ispuni, postavi SELL jednu razinu iznad. Kad se SELL ispuni,
vrati BUY na staru razinu. Zarada = razlika izmedu razina, svaki put
kad cijena "prosece" razinu gore-dolje.

Sigurnosna ogranicenja:
- samo SPOT (bez poluge, bez likvidacije)
- ukupni iznos naloga nikad ne prelazi GRID_CAPITAL_USDT
- DRY_RUN=true samo simulira, ne salje prave naloge

Pokretanje:  python bot.py
Zaustavljanje: Ctrl+C (otvoreni nalozi ostaju na burzi; ponovno
pokretanje nastavlja tocno gdje je stalo preko state.json).
"""

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from dotenv import load_dotenv
from pybit.unified_trading import HTTP

import market_insight

# STATE_DIR: gdje se pisu state.json i bot.log. Zadano je mapa skripte;
# na Northflanku (ili bilo kojem kontejneru) postavite na trajni volume,
# inace se stanje gubi pri svakom redeployu dok su nalozi jos otvoreni.
STATE_DIR = Path(os.getenv("STATE_DIR", Path(__file__).parent))
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(STATE_DIR / "bot.log", encoding="utf-8")],
)
log = logging.getLogger("gridbot")


@dataclass
class Config:
    api_key: str
    api_secret: str
    env: str
    dry_run: bool
    symbol: str
    capital: Decimal
    range_pct: Decimal
    levels: int
    poll_seconds: int
    market_insight: bool
    insight_interval_min: int
    dynamic_range: bool
    pause_on_extreme_fear: bool
    fear_greed_pause_below: int

    @staticmethod
    def load() -> "Config":
        load_dotenv()
        cfg = Config(
            api_key=os.getenv("BYBIT_API_KEY", ""),
            api_secret=os.getenv("BYBIT_API_SECRET", ""),
            env=os.getenv("BYBIT_ENV", "demo").lower(),
            dry_run=os.getenv("DRY_RUN", "true").lower() != "false",
            symbol=os.getenv("SYMBOL", "BTCUSDT").upper(),
            capital=Decimal(os.getenv("GRID_CAPITAL_USDT", "100")),
            range_pct=Decimal(os.getenv("GRID_RANGE_PCT", "5")),
            levels=int(os.getenv("GRID_LEVELS", "10")),
            poll_seconds=int(os.getenv("POLL_SECONDS", "15")),
            market_insight=os.getenv("MARKET_INSIGHT", "true").lower() != "false",
            insight_interval_min=int(os.getenv("INSIGHT_INTERVAL_MIN", "60")),
            dynamic_range=os.getenv("DYNAMIC_RANGE", "false").lower() == "true",
            pause_on_extreme_fear=os.getenv("PAUSE_ON_EXTREME_FEAR", "false").lower() == "true",
            fear_greed_pause_below=int(os.getenv("FEAR_GREED_PAUSE_BELOW", "15")),
        )
        if not cfg.api_key or not cfg.api_secret or "UPISITE" in cfg.api_key:
            raise SystemExit("Upisite BYBIT_API_KEY i BYBIT_API_SECRET u .env datoteku.")
        if cfg.env not in ("live", "demo", "testnet"):
            raise SystemExit("BYBIT_ENV mora biti live, demo ili testnet.")
        if cfg.levels < 2 or cfg.levels > 50:
            raise SystemExit("GRID_LEVELS mora biti izmedu 2 i 50.")
        return cfg


class GridBot:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.session = HTTP(
            testnet=(cfg.env == "testnet"),
            demo=(cfg.env == "demo"),
            api_key=cfg.api_key,
            api_secret=cfg.api_secret,
        )
        self._load_instrument_rules()
        self.state = self._load_state()
        self._sim_counter = 0
        self.paused = False
        # -inf garantira da se prvi tržišni snapshot izvrši odmah — referentna
        # tocka time.monotonic() nije zajamcena kao 0 (moze biti sistemski uptime).
        self._last_insight_ts = float("-inf")

    # ------------------------------------------------------------------ setup

    def _load_instrument_rules(self) -> None:
        info = self.session.get_instruments_info(category="spot", symbol=self.cfg.symbol)
        items = info["result"]["list"]
        if not items:
            raise SystemExit(f"Nepoznat simbol: {self.cfg.symbol}")
        item = items[0]
        self.tick_size = Decimal(item["priceFilter"]["tickSize"])
        self.qty_step = Decimal(item["lotSizeFilter"]["basePrecision"])
        self.min_order_amt = Decimal(item["lotSizeFilter"].get("minOrderAmt", "1"))
        self.min_order_qty = Decimal(item["lotSizeFilter"].get("minOrderQty", "0"))

    def _load_state(self) -> dict:
        if STATE_FILE.exists():
            state = json.loads(STATE_FILE.read_text())
            log.info("Nastavljam postojeci grid iz state.json (%d aktivnih naloga).",
                     len(state["orders"]))
            return state
        return {"levels": [], "orders": {}, "profit_usdt": "0", "round_trips": 0}

    def _save_state(self) -> None:
        STATE_FILE.write_text(json.dumps(self.state, indent=2))

    # ------------------------------------------------------------------ utils

    def _round_price(self, price: Decimal) -> Decimal:
        return price.quantize(self.tick_size, rounding=ROUND_DOWN)

    def _round_qty(self, qty: Decimal) -> Decimal:
        return qty.quantize(self.qty_step, rounding=ROUND_DOWN)

    def last_price(self) -> Decimal:
        data = self.session.get_tickers(category="spot", symbol=self.cfg.symbol)
        return Decimal(data["result"]["list"][0]["lastPrice"])

    # ------------------------------------------------------------------ orders

    def _place_limit(self, side: str, price: Decimal, qty: Decimal, level: int) -> None:
        if side == "Buy" and self.paused:
            log.warning(
                "Razina %d (BUY) preskocena — trzisni uvjeti su ekstremni (Fear&Greed < %d). "
                "Nove kupnje su pauzirane dok se sentiment ne popravi.",
                level, self.cfg.fear_greed_pause_below,
            )
            return
        price = self._round_price(price)
        qty = self._round_qty(qty)
        if qty < self.min_order_qty or price * qty < self.min_order_amt:
            log.warning(
                "Razina %d preskocena: nalog %s %s @ %s je ispod minimuma burze. "
                "Povecajte GRID_CAPITAL_USDT ili smanjite GRID_LEVELS.",
                level, side, qty, price,
            )
            return
        if self.cfg.dry_run:
            self._sim_counter += 1
            order_id = f"SIM-{self._sim_counter}"
            log.info("[DRY RUN] %s %s %s @ %s (razina %d)", side, qty, self.cfg.symbol, price, level)
        else:
            resp = self.session.place_order(
                category="spot",
                symbol=self.cfg.symbol,
                side=side,
                orderType="Limit",
                qty=str(qty),
                price=str(price),
                timeInForce="GTC",
            )
            order_id = resp["result"]["orderId"]
            log.info("Postavljen %s %s %s @ %s (razina %d, id %s)",
                     side, qty, self.cfg.symbol, price, level, order_id)
        self.state["orders"][order_id] = {
            "side": side, "level": level, "price": str(price), "qty": str(qty),
        }
        self._save_state()

    def _open_order_ids(self) -> set:
        if self.cfg.dry_run:
            return set(self.state["orders"])
        resp = self.session.get_open_orders(category="spot", symbol=self.cfg.symbol, limit=50)
        return {o["orderId"] for o in resp["result"]["list"]}

    def _order_filled(self, order_id: str) -> bool:
        resp = self.session.get_order_history(category="spot", orderId=order_id)
        rows = resp["result"]["list"]
        return bool(rows) and rows[0]["orderStatus"] == "Filled"

    # ------------------------------------------------------------------ insight

    def _check_market_insight(self) -> None:
        """Loguje trzisni kontekst i (opcionalno) pauzira nove kupnje u ekstremnom strahu."""
        if not self.cfg.market_insight:
            return
        now = time.monotonic()
        if now - self._last_insight_ts < self.cfg.insight_interval_min * 60:
            return
        self._last_insight_ts = now
        snapshot = market_insight.log_market_snapshot(self.session, self.cfg.symbol)

        if not self.cfg.pause_on_extreme_fear:
            return
        fng = snapshot.get("fear_greed")
        if fng is None:
            return
        should_pause = fng["value"] < self.cfg.fear_greed_pause_below
        if should_pause and not self.paused:
            log.warning("PAUZA: Fear&Greed %d/100 (%s) je ispod praga %d — nove kupnje se obustavljaju.",
                        fng["value"], fng["classification"], self.cfg.fear_greed_pause_below)
        elif not should_pause and self.paused:
            log.info("Sentiment se oporavio (Fear&Greed %d/100) — nove kupnje su ponovno dopustene.",
                      fng["value"])
        self.paused = should_pause

    # ------------------------------------------------------------------ grid

    def init_grid(self) -> None:
        if self.state["levels"]:
            return
        price = self.last_price()
        range_pct = self.cfg.range_pct
        if self.cfg.dynamic_range:
            vol = market_insight.realized_volatility_pct(self.session, self.cfg.symbol)
            if vol is not None:
                range_pct = max(Decimal("2"), min(vol, Decimal("15")))
                log.info("DYNAMIC_RANGE: izmjerena volatilnost %s%%, koristim raspon %s%% (umjesto fiksnog %s%%).",
                          vol, range_pct, self.cfg.range_pct)
        span = price * range_pct / Decimal(100)
        lower, upper = price - span, price + span
        step = (upper - lower) / Decimal(self.cfg.levels)
        levels = [self._round_price(lower + step * i) for i in range(self.cfg.levels + 1)]
        self.state["levels"] = [str(p) for p in levels]
        log.info("Grid za %s: %s — %s, %d razina, trenutna cijena %s",
                 self.cfg.symbol, lower, upper, self.cfg.levels, price)

        buy_levels = [i for i, p in enumerate(levels) if p < price and i < len(levels) - 1]
        if not buy_levels:
            raise SystemExit("Nema razina ispod trenutne cijene — prosirite GRID_RANGE_PCT.")
        budget_per_level = self.cfg.capital / Decimal(len(buy_levels))
        for i in buy_levels:
            self._place_limit("Buy", levels[i], budget_per_level / levels[i], i)

    def _handle_fill(self, order_id: str, order: dict) -> None:
        levels = [Decimal(p) for p in self.state["levels"]]
        level = order["level"]
        qty = Decimal(order["qty"])
        price = Decimal(order["price"])
        del self.state["orders"][order_id]

        if order["side"] == "Buy":
            log.info("BUY ispunjen @ %s (razina %d) — postavljam SELL razinu vise.", price, level)
            # spot fee se naplacuje u base valuti, pa prodajemo 99.9% kupljenog
            sell_qty = qty * Decimal("0.999")
            self._place_limit("Sell", levels[level + 1], sell_qty, level + 1)
        else:
            buy_level = level - 1
            profit = (price - levels[buy_level]) * qty
            self.state["profit_usdt"] = str(Decimal(self.state["profit_usdt"]) + profit)
            self.state["round_trips"] += 1
            log.info("SELL ispunjen @ %s — krug #%d zatvoren, zarada ~%.4f USDT (ukupno ~%.4f). "
                     "Vracam BUY na razinu %d.",
                     price, self.state["round_trips"], profit,
                     Decimal(self.state["profit_usdt"]), buy_level)
            self._place_limit("Buy", levels[buy_level], qty, buy_level)
        self._save_state()

    def _check_orders(self) -> None:
        if self.cfg.dry_run:
            price = self.last_price()
            for order_id, order in list(self.state["orders"].items()):
                p = Decimal(order["price"])
                hit = price <= p if order["side"] == "Buy" else price >= p
                if hit:
                    self._handle_fill(order_id, order)
            return
        open_ids = self._open_order_ids()
        for order_id, order in list(self.state["orders"].items()):
            if order_id not in open_ids and self._order_filled(order_id):
                self._handle_fill(order_id, order)

    # ------------------------------------------------------------------ main

    def run(self) -> None:
        mode = "DRY RUN (simulacija)" if self.cfg.dry_run else f"{self.cfg.env.upper()} trgovanje"
        log.info("Grid bot pokrenut — %s, par %s, kapital %s USDT.",
                 mode, self.cfg.symbol, self.cfg.capital)
        self.init_grid()
        while True:
            try:
                self._check_market_insight()
                self._check_orders()
            except Exception:
                log.exception("Greska u petlji — pokusavam ponovno za %d s.", self.cfg.poll_seconds)
            time.sleep(self.cfg.poll_seconds)


def main() -> None:
    cfg = Config.load()
    if cfg.env == "live" and not cfg.dry_run:
        print(f"\nPOZOR: LIVE trgovanje pravim novcem, kapital {cfg.capital} USDT, par {cfg.symbol}.")
        if sys.stdin.isatty():
            if input("Upisite 'DA' za nastavak: ").strip().upper() != "DA":
                raise SystemExit("Prekinuto.")
        elif os.getenv("LIVE_CONFIRM", "").strip().upper() != "DA":
            raise SystemExit(
                "Nema terminala za potvrdu (npr. Docker/Northflank). "
                "Postavite environment varijablu LIVE_CONFIRM=DA da potvrdite live trgovanje."
            )
    try:
        GridBot(cfg).run()
    except KeyboardInterrupt:
        print("\nBot zaustavljen. Otvoreni limit nalozi ostaju na burzi; "
              "ponovno pokretanje nastavlja gdje je stalo (state.json).")


if __name__ == "__main__":
    main()
