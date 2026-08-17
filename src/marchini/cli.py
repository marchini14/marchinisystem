"""CLI: python -m marchini <komanda>

  screen    prikazi trenutne najbolje parove (bez trgovanja)
  capital   izracunaj koliko kapitala treba za trejd na tim parovima
  check     preflight pred live/demo: kredencijali, racun, kapital (bez naloga)
  once      odradi jedan prolaz bota i izadji
  run       pokreni bota u petlji
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import screener, strategy
from .bitget import BitgetClient
from .bot import Bot, setup_logging
from .config import Config, credentials, load_config
from .risk import ContractSpec, size_trade

log = logging.getLogger(__name__)


def _client(cfg: Config, needs_orders: bool) -> BitgetClient:
    """Napravi klijent; trazi kredencijale samo ako komanda moze poslati nalog.

    `screen` i `capital` citaju samo javne rute, pa rade i bez kljuceva - i u
    demo modu, gdje demo market data takodje nije zasticena.
    """
    key, secret, passphrase = credentials()
    client = BitgetClient(key, secret, passphrase, demo=cfg.is_demo)
    if needs_orders and cfg.sends_orders and not client.has_credentials:
        # Sva tri su obavezna: sam API key ne moze potpisati nalog.
        sys.exit(
            f"mode je '{cfg.mode}' ali fale kredencijali: "
            f"{', '.join(client.missing_credentials())}\n"
            "Kopiraj .env.example u .env, popuni sva tri polja, pa:\n"
            "  set -a; . ./.env; set +a"
        )
    return client


def _screen(cfg: Config, client: BitgetClient) -> list[screener.Candidate]:
    return screener.screen(
        client,
        min_volume_24h=cfg.screener.min_volume_24h,
        max_change_24h_pct=cfg.screener.max_change_24h_pct,
        min_atr_pct=cfg.screener.min_atr_pct,
        max_abs_funding=cfg.screener.max_abs_funding,
        top_n=cfg.screener.top_n,
        blacklist=cfg.screener.blacklist,
        timeframe=cfg.strategy.timeframe,
        atr_period=cfg.strategy.atr_period,
    )


def cmd_screen(cfg: Config, client: BitgetClient) -> None:
    candidates = _screen(cfg, client)
    if not candidates:
        print("Nijedan par ne prolazi filtere. Ublazi screener u config.yaml.")
        return

    print(f"\n{'PAR':<16}{'CIJENA':>13}{'24H%':>9}{'VOL 24H':>15}{'ATR%':>8}{'FUND%':>9}  SIGNAL")
    print("-" * 96)
    for c in candidates:
        sig = strategy.evaluate(
            c.candles,
            donchian_lookback=cfg.strategy.donchian_lookback,
            ema_trend=cfg.strategy.ema_trend,
            atr_period=cfg.strategy.atr_period,
            allow_short=cfg.strategy.allow_short,
        )
        label = sig.side.upper() if sig.side else "-"
        print(
            f"{c.symbol:<16}{c.price:>13.6g}{c.change_24h_pct:>8.2f}%"
            f"{c.volume_24h:>15,.0f}{c.atr_pct:>7.2f}%{c.funding * 100:>8.4f}%  {label}"
        )
    print(f"\n{len(candidates)} kandidata. Ovo NIJE preporuka za ulaganje.\n")


def cmd_capital(cfg: Config, client: BitgetClient) -> None:
    """Koliko kapitala treba da trejd prodje sve risk provjere.

    Trazi minimalni equity binarnom pretragom: risk modul je monoton po
    kapitalu (veci kapital -> veca pozicija), pa je granica jedinstvena.
    """
    specs = {raw["symbol"]: ContractSpec.from_api(raw) for raw in client.contracts()}
    candidates = _screen(cfg, client)
    if not candidates:
        print("Nijedan par ne prolazi filtere.")
        return

    print(f"\nMinimalni kapital za jedan trejd pri {cfg.risk.leverage}x i "
          f"{cfg.risk.risk_per_trade_pct}% rizika po trejdu:\n")
    print(f"{'PAR':<16}{'MIN KAPITAL':>14}{'NOTIONAL':>12}{'MARGINA':>10}{'FEE':>9}")
    print("-" * 63)

    for c in candidates[:8]:
        spec = specs.get(c.symbol)
        if spec is None:
            continue

        def attempt(equity: float):
            return size_trade(
                symbol=c.symbol,
                side="long",
                entry=c.price,
                atr_value=c.atr_value,
                spec=spec,
                equity=equity,
                leverage=cfg.risk.leverage,
                risk_per_trade_pct=cfg.risk.risk_per_trade_pct,
                atr_stop_mult=cfg.strategy.atr_stop_mult,
                atr_target_mult=cfg.strategy.atr_target_mult,
                max_fee_share_of_target_pct=cfg.risk.max_fee_share_of_target_pct,
                min_liq_distance_vs_stop=cfg.risk.min_liq_distance_vs_stop,
            )

        high = 10.0
        while high <= 500_000 and not attempt(high).ok:
            high *= 2
        probe = attempt(high)
        if not probe.ok:
            print(f"{c.symbol:<16}{'nemoguce':>14}  {'; '.join(probe.rejections)[:40]}")
            continue

        low = 0.0
        for _ in range(40):  # dovoljno iteracija da granica bude tocna na cent
            mid = (low + high) / 2
            if attempt(mid).ok:
                high = mid
            else:
                low = mid
        best = attempt(high)
        print(
            f"{c.symbol:<16}{high:>13.2f}$"
            f"{best.notional:>12.2f}{best.margin:>10.2f}{best.round_trip_fee:>9.3f}"
        )
    print()


def cmd_check(cfg: Config, client: BitgetClient) -> None:
    """Preflight za live/demo: provjeri sve sto treba PRIJE prvog naloga.

    Ne salje nijedan nalog. Namjena je da prvi live start ne bude i prvi put
    kad se otkrije da passphrase fali ili da je leverage iznad limita.
    """
    ok = True
    print(f"\nmode={cfg.mode}  productType={client.product_type}  "
          f"marginCoin={client.margin_coin}")
    print("-" * 60)

    missing = client.missing_credentials()
    if missing:
        print(f"[X] kredencijali        fale: {', '.join(missing)}")
        if cfg.sends_orders:
            print("\n    Bez sva tri nema potpisa i nijedan nalog ne moze proci.")
            print("    Popuni .env pa: set -a; . ./.env; set +a")
            sys.exit(1)
        ok = False
    else:
        print("[OK] kredencijali       sva tri prisutna")

    if not cfg.sends_orders:
        print(f"\n[!] mode je '{cfg.mode}' - nalozi se NE salju na burzu.")
        print("    Za prave naloge stavi mode: live u config.yaml")
        return

    # Potpisani poziv: ako ovo prodje, potpis i dozvole kljuca su ispravni.
    try:
        balance = client.available_balance()
        print(f"[OK] potpisani poziv    racun cita, raspolozivo {balance:.2f} "
              f"{client.margin_coin}")
    except Exception as exc:
        print(f"[X] potpisani poziv     {exc}")
        print("\n    Najcesci uzroci: pogresan passphrase, kljuc bez Futures "
              "Trading dozvole,\n    ili demo kljuc upotrijebljen za live (i obrnuto).")
        sys.exit(1)

    try:
        positions = client.positions()
        print(f"[OK] pozicije           {len(positions)} otvorenih na burzi")
    except Exception as exc:
        print(f"[X] pozicije            {exc}")
        ok = False

    specs = {r["symbol"]: ContractSpec.from_api(r) for r in client.contracts()}
    print(f"[OK] kontrakti          {len(specs)} simbola")

    # Da li kapital na racunu uopste moze proci risk provjere.
    candidates = _screen(cfg, client)
    print(f"[OK] screener           {len(candidates)} kandidata")
    if not candidates:
        print("\n[!] Nijedan par ne prolazi filtere - bot nece imati sta trgovati.")
        return

    tradeable = []
    for c in candidates:
        spec = specs.get(c.symbol)
        if spec is None:
            continue
        trade = size_trade(
            symbol=c.symbol, side="long", entry=c.price, atr_value=c.atr_value,
            spec=spec, equity=balance, leverage=cfg.risk.leverage,
            risk_per_trade_pct=cfg.risk.risk_per_trade_pct,
            atr_stop_mult=cfg.strategy.atr_stop_mult,
            atr_target_mult=cfg.strategy.atr_target_mult,
            max_fee_share_of_target_pct=cfg.risk.max_fee_share_of_target_pct,
            min_liq_distance_vs_stop=cfg.risk.min_liq_distance_vs_stop,
        )
        if trade.ok:
            tradeable.append((c.symbol, trade))

    print(f"[{'OK' if tradeable else 'X'}] kapital            "
          f"{len(tradeable)}/{len(candidates)} parova prolazi risk provjere "
          f"sa {balance:.2f} {client.margin_coin}")

    if not tradeable:
        print("\n    Kapital je premali za sve kandidate pri ovom leverageu.")
        print("    Pokreni: python -m marchini capital   (koliko treba po paru)")
        sys.exit(1)

    print(f"\n    Prvi moguci trejd bi izgledao ovako:")
    symbol, t = tradeable[0]
    print(f"      {symbol} long qty={t.qty:g} @ {t.entry:g}")
    print(f"      stop={t.stop:g}  target={t.target:g}  likvidacija={t.liq_price:g}")
    print(f"      margina={t.margin:.2f}  rizik={t.risk_amount:.2f}  "
          f"fee={t.round_trip_fee:.3f}")

    print(f"\n{'[OK] spremno za run' if ok else '[!] provjeri upozorenja gore'}\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="marchini", description=__doc__)
    parser.add_argument("command", choices=["screen", "capital", "check", "once", "run"])
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    setup_logging(cfg.runtime.log_file, args.verbose)
    read_only = args.command in ("screen", "capital")
    # check je namjerno "needs_orders": treba da padne ako kredencijali fale,
    # jer mu je cijela svrha da to otkrije prije prvog run-a.
    client = _client(cfg, needs_orders=not read_only)

    if cfg.sends_orders and not read_only:
        log.warning(
            "mode=%s - bot ce slati PRAVE naloge na Bitget (%s)",
            cfg.mode, client.product_type,
        )

    if args.command == "screen":
        cmd_screen(cfg, client)
    elif args.command == "capital":
        cmd_capital(cfg, client)
    elif args.command == "check":
        cmd_check(cfg, client)
    else:
        bot = Bot(cfg, client)
        if args.command == "once":
            bot.tick()
        else:
            try:
                bot.run_forever()
            except KeyboardInterrupt:
                log.info("prekinuto sa tastature - state je sacuvan")


if __name__ == "__main__":
    main()
