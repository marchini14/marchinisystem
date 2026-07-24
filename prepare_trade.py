"""Priprema MEXC futures naloga — CROSS marza (Multi-Asset mode).

Racuna oba smjera odjednom i ispisuje spremne postavke. NE salje nista;
slanje ide zasebno, tek nakon izricite potvrde.

Cross marza: cijelo stanje racuna stiti poziciju, pa se likvidacija
racuna iz ukupnog kapitala, a ne iz marze pozicije.

    python prepare_trade.py --symbol BTC_USDT --stop-pct 2 --risk 2
"""

import argparse
import json
import urllib.request
from decimal import Decimal, getcontext

getcontext().prec = 28
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36"}


def api(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        return json.loads(r.read())


def cross_liquidation(entry: Decimal, size_btc: Decimal, balance: Decimal,
                      mmr: Decimal, side: str) -> Decimal:
    """Likvidacijska cijena u cross marzi.

    Likvidacija nastupa kad kapital padne na maintenance margin:
        balance +/- (liq - entry) * size = liq * size * mmr
    """
    if side == "long":
        denom = size_btc * (Decimal(1) - mmr)
        return (entry * size_btc - balance) / denom if denom else Decimal(0)
    denom = size_btc * (Decimal(1) + mmr)
    return (balance + entry * size_btc) / denom if denom else Decimal(0)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTC_USDT")
    p.add_argument("--balance", type=Decimal, default=None, help="USDT; zadano = dohvati s racuna")
    p.add_argument("--stop-pct", type=Decimal, default=Decimal("2"))
    p.add_argument("--risk", type=Decimal, default=Decimal("2"), help="%% racuna po tradeu")
    p.add_argument("--leverage", type=int, default=5)
    a = p.parse_args()

    d = api(f"https://contract.mexc.com/api/v1/contract/detail?symbol={a.symbol}")["data"]
    t = api(f"https://contract.mexc.com/api/v1/contract/ticker?symbol={a.symbol}")["data"]

    entry = Decimal(str(t["lastPrice"]))
    cs = Decimal(str(d["contractSize"]))
    mmr = Decimal(str(d["maintenanceMarginRate"]))
    taker = Decimal(str(d["takerFeeRate"]))
    balance = a.balance if a.balance is not None else Decimal("38.744")

    risk_usd = balance * a.risk / 100
    stop_dist = entry * a.stop_pct / 100
    vol = int((risk_usd / stop_dist) / cs)

    print(f"\n{'='*62}")
    print(f"  {a.symbol}   cijena {entry:,.2f}   |   racun {balance:,.2f} USDT")
    print(f"  CROSS marza (Multi-Asset mode) | poluga {a.leverage}x | rizik {a.risk}%")
    print(f"{'='*62}")

    if vol < 1:
        print(f"\n  Rizik {risk_usd:.2f} USDT / stop {a.stop_pct}% daje manje od 1 ugovora.")
        print(f"  1 ugovor = {cs} = {cs*entry:.2f} USDT vrijednosti.")
        vol = 1
        print(f"  -> koristim minimalnih {vol} ugovora (rizik ispada veci od zadanog).\n")

    size = Decimal(vol) * cs
    notional = size * entry
    margin = notional / a.leverage
    fees = notional * taker * 2

    for side in ("long", "short"):
        if side == "long":
            stop = entry - stop_dist
            tp1, tp2 = entry + stop_dist * 2, entry + stop_dist * 3
        else:
            stop = entry + stop_dist
            tp1, tp2 = entry - stop_dist * 2, entry - stop_dist * 3
        loss = size * stop_dist
        liq = cross_liquidation(entry, size, balance, mmr, side)
        liq_pct = abs(liq - entry) / entry * 100

        print(f"\n  --- {side.upper()} {'-'*(52-len(side))}")
        print(f"    Ulaz (market)      {entry:>14,.2f}")
        print(f"    Kolicina           {vol:>11} ugovora  ({size} {a.symbol.split('_')[0]})")
        print(f"    Vrijednost         {notional:>14,.2f} USDT")
        print(f"    Marza ({a.leverage}x)        {margin:>14,.2f} USDT")
        print(f"    STOP-LOSS          {stop:>14,.2f}   (gubitak {loss:,.2f} USDT)")
        print(f"    Take-profit 2R     {tp1:>14,.2f}   (dobit  {loss*2:,.2f} USDT)")
        print(f"    Take-profit 3R     {tp2:>14,.2f}   (dobit  {loss*3:,.2f} USDT)")
        if liq <= 0:
            print(f"    Likvidacija        {'nema realne':>14}   (pozicija manja od kapitala)")
        else:
            print(f"    Likvidacija        {liq:>14,.2f}   ({liq_pct:.1f}% od ulaza)")
        print(f"    Naknade            {fees:>14,.2f} USDT")

    print(f"\n{'='*62}")
    print(f"  Rizik po tradeu: {size*stop_dist:,.2f} USDT "
          f"({size*stop_dist/balance*100:.1f}% racuna)")
    print(f"{'='*62}\n")


if __name__ == "__main__":
    main()
