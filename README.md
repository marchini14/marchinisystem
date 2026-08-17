# marchinisystem

Automatizovani trading bot za **Bitget USDT-M perpetual futures**.

Bot sam skenira sve parove, nalazi likvidne i volatilne, trazi mehanicki
breakout signal, izracuna velicinu pozicije iz rizika i otvori trejd sa
stop-lossom i take-profitom. Radi u petlji bez nadzora.

**Default mod je `paper` — simulacija bez pravog novca.** Live trgovanje se
ukljucuje eksplicitno u `config.yaml`.

---

## Prvo procitaj ovo

Ovaj bot nema nikakav nacin da predvidi cijenu i ne postoji podesavanje koje
mu to omogucava. On radi jednu stvar: primjenjuje isto pravilo hiljadu puta
bez emocija, sa kontrolisanim rizikom po trejdu. Vecina trejdova gubi malo,
manjina zaradi vise. To je cijeli mehanizam.

Realnost brojeva na Bitgetu (mjereno preko njihovog javnog API-ja):

- Najveci dnevni rast medju **756 USDT-M parova** je tipicno **+30% do +60%**.
  Rast od 400x (40000%) ne postoji u ovim podacima. Ako neko takav "signal"
  prodaje na Telegramu ili YouTubeu — to je prevara, bez izuzetka.
- **Minimalni notional je 5 USDT** po nalogu (`minTradeUSDT`).
- Taker fee je **0.06%**, maker **0.02%**. Round-trip na malu poziciju pojede
  procentualno vise nego sto ta pozicija realno moze zaraditi.

Zato `python -m marchini capital` postoji: kaze ti tacno koliko kapitala
treba da trejd uopste prodje risk provjere. Na trenutnim parovima to je
**~16–45 USDT po trejdu pri 5x**. Sa 2 USDT bot nece otvoriti nista — i to je
namjerno, jer bi svaki takav nalog bio gubitak na fee-jevima.

Ovo nije finansijski savjet. Trgovanje leveridzom moze potpuno unistiti kapital.

---

## Instalacija

```bash
git clone <repo> && cd marchinisystem
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

Za postavljanje korak po korak — lokalno, na VPS-u preko systemd, ili u
Dockeru — vidi **[deploy/SETUP.md](deploy/SETUP.md)**.

## Upotreba

Za `screen` i `capital` **ne trebaju API kljucevi** — koriste javne podatke.

```bash
export PYTHONPATH=src

# Koji parovi su danas vrijedni trgovanja + ima li signala
python -m marchini screen

# Koliko kapitala treba za trejd na tim parovima
python -m marchini capital

# Jedan prolaz bota (paper), pa izlaz
python -m marchini once

# Bot u petlji
python -m marchini run
```

### Live mod

1. `cp .env.example .env` i popuni kljuceve sa Bitgeta (Futures trading
   dozvola; **bez** withdraw dozvole — botu ne treba i to je jedina zastita
   ako kljuc procuri).
2. `set -a; . ./.env; set +a`
3. U `config.yaml` postavi `mode: live` i `equity` na kapital koji si
   spreman izgubiti.

```bash
python -m marchini run
```

Prije live moda pusti paper nekoliko sedmica. Ako paper gubi, live gubi brze.

---

## Kako bot odlucuje

```
1. SCREENER   svih ~750 parova -> filtriraj i rangiraj
              likvidnost (volumen 24h) x volatilnost (ATR%) - kazna za funding
              izbacuje parove koji su vec +25% (kupovina na vrhu je najgori ulaz)

2. STRATEGIJA Donchian breakout uz EMA trend filter
              LONG:  close probije high zadnjih 20 svijeca I close > EMA50
              SHORT: close probije low  zadnjih 20 svijeca I close < EMA50

3. RISK       sizing IZ RIZIKA, ne iz zeljene velicine
              qty = (kapital x risk%) / (ATR x stop_mult)
              pa veto ako: notional < 5 USDT, margina > kapital,
                           fee > 15% ocekivanog profita,
                           likvidacija blize od 1.5x stopa

4. IZVRSAVANJE market ulaz + SL/TP poslani ZAJEDNO sa nalogom
              (ako bot padne, zastita je vec na burzi)
```

Screener rangira po likvidnosti i volatilnosti — **ne** po 24h rastu. Par koji
je danas +50% je statisticki najgori ulaz, ne najbolji.

## Risk kontrole

| Kontrola | Default | Sta sprjecava |
|---|---|---|
| `risk_per_trade_pct` | 1% | Jedan trejd ne moze napraviti veliku stetu |
| `max_open_positions` | 3 | Sve pozicije ne padaju istovremeno |
| `daily_loss_limit_pct` | 5% | Bot staje za taj dan nakon serije gubitaka |
| `min_liq_distance_vs_stop` | 1.5x | Likvidacija ne moze stici prije stopa |
| `max_fee_share_of_target_pct` | 15% | Nema pozicija premalih da pokriju fee |
| `atr_target_mult > atr_stop_mult` | 3.0 / 2.0 | R:R ispod 1 se odbija u configu |

Sizing uvijek zaokruzuje kolicinu **nadolje** — zaokruzivanje gore bi probilo
limit rizika.

## Struktura

```
src/marchini/
  bitget.py       REST v2 klijent, HMAC potpis, retry na 429/5xx
  indicators.py   EMA, Wilder ATR, Donchian (bez pandas/numpy)
  screener.py     filtriranje i rangiranje ~750 parova
  strategy.py     breakout signal
  risk.py         sizing i veto pravila
  broker.py       PaperBroker (simulacija) i LiveBroker (pravi nalozi)
  bot.py          glavna petlja, state, dnevni limit
  cli.py          screen / capital / once / run
```

## Testovi

```bash
pip install pytest && python -m pytest -q
```

46 testova pokrivaju risk matematiku, indikatore, signale, paper fill logiku
i validaciju configa. Risk modul je testiran najgusce — to je dio koji cuva
kapital.

## Poznata ogranicenja

- **Paper mod ne modelira slippage**, pa su rezultati blago optimisticni.
  Screener trazi likvidne parove da ta razlika ostane mala.
- **Nema backtesta** na istorijskim podacima. `screen` i paper mod pokazuju
  ponasanje unaprijed, ne unazad.
- Likvidacijska cijena je **procjena** (fiksni MMR 0.5%); stvarni MMR raste
  sa velicinom pozicije. Procjena je konzervativna u nasu korist.
- Live mod prepusta izlaze burzi preko preset SL/TP — bot ih ne pomjera
  (nema trailing stopa).
