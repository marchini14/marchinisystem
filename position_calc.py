"""MEXC futures kalkulator pozicije i rizika.

Ti odlucujes smjer (long/short) i gdje ti je stop. Ovaj alat izracuna
SVE ostalo prije nego bilo sto kliknes u aplikaciji:

  - koliku poziciju smijes otvoriti da ne riskiras vise od zadanog %
  - gdje te tocno likvidira
  - koliko te kosta ako stop okine
  - upozorenja ako je postavka opasna

MEXC ne dozvoljava slanje futures naloga preko API-ja (403 na
/order/submit), pa ovaj alat NE trguje — ispisuje brojke koje sam
upises u MEXC aplikaciju.

Pokretanje:
    python position_calc.py --entry 64180 --stop 62900 --side long
    python position_calc.py --entry 64180 --stop 65500 --side short --risk 2
"""

import argparse
import sys
from decimal import Decimal

# BTC_USDT perpetual (dohvaceno s MEXC contract/detail)
CONTRACT_SIZE = Decimal("0.0001")   # BTC po ugovoru
MMR = Decimal("0.001")              # maintenance margin rate
TAKER_FEE = Decimal("0.0002")       # 0.02%


def liquidation_price(entry: Decimal, leverage: Decimal, side: str) -> Decimal:
    """Priblizna likvidacijska cijena za IZOLIRANU marzu.

    U cross marzi likvidacija ovisi o cijelom stanju racuna i moze biti
    znatno drukcija — zato ovaj alat trazi izoliranu marzu.
    """
    if side == "long":
        return entry * (Decimal(1) - Decimal(1) / leverage + MMR)
    return entry * (Decimal(1) + Decimal(1) / leverage - MMR)


def main() -> None:
    p = argparse.ArgumentParser(description="MEXC futures kalkulator rizika")
    p.add_argument("--balance", type=Decimal, default=Decimal("38.74"),
                   help="stanje racuna u USDT (zadano: 38.74)")
    p.add_argument("--entry", type=Decimal, required=True, help="ulazna cijena")
    p.add_argument("--stop", type=Decimal, required=True, help="stop-loss cijena")
    p.add_argument("--side", choices=["long", "short"], required=True)
    p.add_argument("--risk", type=Decimal, default=Decimal("2"),
                   help="koliki %% racuna riskiras na ovom tradeu (zadano 2%%)")
    p.add_argument("--leverage", type=Decimal, default=None,
                   help="poluga; ako se izostavi, alat predlaze najmanju sigurnu")
    a = p.parse_args()

    # --- provjere smjera i stopa ---------------------------------------
    if a.side == "long" and a.stop >= a.entry:
        sys.exit("GRESKA: za LONG stop mora biti ISPOD ulazne cijene.")
    if a.side == "short" and a.stop <= a.entry:
        sys.exit("GRESKA: za SHORT stop mora biti IZNAD ulazne cijene.")

    distance = abs(a.entry - a.stop)
    distance_pct = distance / a.entry * 100
    risk_usd = a.balance * a.risk / 100

    # --- velicina pozicije iz rizika, ne iz "koliko mogu" ---------------
    size_btc = risk_usd / distance
    vol = int(size_btc / CONTRACT_SIZE)          # broj ugovora (cijeli)
    if vol < 1:
        sys.exit(
            f"Pozicija bi bila manja od 1 ugovora ({size_btc:.6f} BTC).\n"
            f"Ili priblizi stop ulazu, ili povecaj --risk, ili ti je racun premali "
            f"za ovaj stop na ovom paru."
        )

    actual_btc = Decimal(vol) * CONTRACT_SIZE
    position_value = actual_btc * a.entry
    actual_risk = actual_btc * distance
    fees = position_value * TAKER_FEE * 2        # ulaz + izlaz

    # --- poluga: najmanja koja stane u racun, s rezervom ----------------
    if a.leverage:
        leverage = a.leverage
    else:
        # trazi polugu tako da margin ne pojede vise od 1/3 racuna
        min_lev = position_value / (a.balance / 3)
        leverage = Decimal(max(1, int(min_lev) + 1))

    margin = position_value / leverage
    liq = liquidation_price(a.entry, leverage, a.side)
    liq_distance_pct = abs(liq - a.entry) / a.entry * 100

    # ------------------------------------------------------------------
    print(f"\n{'='*58}")
    print(f"  {a.side.upper()}  BTC_USDT   @ {a.entry:,.1f}")
    print(f"{'='*58}")
    print(f"  Stanje racuna         {a.balance:>12,.2f} USDT")
    print(f"  Rizik na ovom tradeu  {a.risk:>12}%  = {risk_usd:,.2f} USDT")
    print()
    print(f"  UPISI U MEXC:")
    print(f"    Marza               {'IZOLIRANA (isolated)':>20}")
    print(f"    Poluga              {leverage:>18}x")
    print(f"    Kolicina            {vol:>15} ugovora  ({actual_btc:.4f} BTC)")
    print(f"    Stop-loss           {a.stop:>18,.1f}")
    print()
    print(f"  Vrijednost pozicije   {position_value:>12,.2f} USDT")
    print(f"  Potrebna marza        {margin:>12,.2f} USDT")
    print(f"  Stop je udaljen       {distance_pct:>11.2f}%")
    print(f"  LIKVIDACIJA na        {liq:>12,.1f}   ({liq_distance_pct:.2f}% od ulaza)")
    print()
    print(f"  Ako stop okine gubis  {actual_risk:>12,.2f} USDT")
    print(f"  Naknade (ulaz+izlaz)  {fees:>12,.2f} USDT")
    print(f"{'='*58}")

    # --- upozorenja -----------------------------------------------------
    warnings = []
    if margin > a.balance:
        warnings.append(
            f"NEMAS DOVOLJNO: treba {margin:,.2f} USDT marze, imas {a.balance:,.2f}.")
    if liq_distance_pct < distance_pct * Decimal("1.5"):
        warnings.append(
            f"LIKVIDACIJA JE PREBLIZU: na {liq_distance_pct:.2f}%, a stop na "
            f"{distance_pct:.2f}%. Likvidacija te moze pokupiti PRIJE stopa — "
            f"smanji polugu.")
    if leverage > 20:
        warnings.append(
            f"POLUGA {leverage}x je vrlo visoka — likvidacija na svega "
            f"{liq_distance_pct:.2f}% pomaka.")
    if fees > actual_risk / 4:
        warnings.append(
            f"Naknade ({fees:,.2f}) su velike u odnosu na rizik ({actual_risk:,.2f}) "
            f"— stop ti je preblizu da bi se trade isplatio.")

    if warnings:
        print("\n  UPOZORENJA:")
        for w in warnings:
            print(f"   !  {w}")
        print()


if __name__ == "__main__":
    main()
