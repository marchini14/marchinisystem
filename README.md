# marchinisystem — Bybit grid trading bot

Jednostavan **spot grid bot** za Bybit: postavlja limit kupovne naloge ispod
trenutne cijene, a kad se koji ispuni, postavlja prodajni nalog jednu razinu
iznad. Svaki zatvoreni krug (kupi nisko → prodaj visoko) donosi malu zaradu.

> ⚠️ **Upozorenje o riziku:** nijedan trading bot nije "perfektan" i ne
> garantira zaradu. Ako cijena padne ispod grid raspona, bot drži kupljene
> kovanice u minusu dok se cijena ne vrati. Koristite samo novac čiji
> gubitak možete podnijeti. Bot radi isključivo na spotu — bez poluge,
> pa likvidacija nije moguća.

## Sigurnost API ključa

- Ključ napravite na **glavnom računu** za svoj subaccount, samo s **Trade**
  dozvolom. **Nikada** ne uključujte Withdrawal.
- Ključ i secret idu **samo** u lokalnu `.env` datoteku — ona je u
  `.gitignore` i ne smije nikada na GitHub.
- Ako ste secret ikome poslali (porukom, screenshotom…), obrišite ključ na
  Bybitu i napravite novi.
- Po mogućnosti ograničite ključ na IP adresu računala na kojem bot radi.

## Instalacija (Windows / Mac / Linux)

Potreban je [Python 3.10+](https://www.python.org/downloads/) — kod
instalacije na Windowsu označite "Add Python to PATH".

```bash
git clone https://github.com/marchini14/marchinisystem.git
cd marchinisystem
pip install -r requirements.txt
```

Zatim kopirajte `.env.example` u `.env` i upišite svoje podatke:

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

## Pokretanje — redoslijed

**1. Provjera ključa i računa:**

```bash
python check_account.py
```

Ispisuje dozvole ključa i stanje novčanika. Ako piše upozorenje o
Withdrawal dozvoli — obrišite ključ i napravite novi.

**2. Simulacija (DRY_RUN=true u .env):**

```bash
python bot.py
```

Bot prati pravu cijenu, ali naloge samo simulira i ispisuje. Pustite ga
barem dan-dva da vidite kako se ponaša.

**3. Demo novac (BYBIT_ENV=demo, DRY_RUN=false):** pravi nalozi na
Bybit **Demo Trading** računu s virtualnim novcem.

**4. Pravi novac (BYBIT_ENV=live, DRY_RUN=false):** bot prije starta traži
da upišete `DA`. Počnite s malim iznosom (npr. 50–100 USDT).

## Postavke (.env)

| Postavka | Zadano | Značenje |
|---|---|---|
| `SYMBOL` | `BTCUSDT` | par koji se trguje (spot) |
| `GRID_CAPITAL_USDT` | `100` | maksimalni ukupni kapital bota |
| `GRID_RANGE_PCT` | `5` | raspon grida: ±5 % oko trenutne cijene |
| `GRID_LEVELS` | `10` | broj razina u gridu |
| `POLL_SECONDS` | `15` | koliko često bot provjerava naloge |

Uže postavke (`GRID_RANGE_PCT=2`) → češći, manji profiti; šire (`10`) →
rjeđi, veći. Pazite da `GRID_CAPITAL_USDT / GRID_LEVELS` bude iznad
minimalnog naloga burze (za BTCUSDT ~1 USDT, ali realno neka bude ≥ 10 USDT
po razini).

## Rad bota

- Bot mora **stalno raditi** — na kućnom računalu koje ne spava ili na
  malom VPS-u (Hetzner/Contabo, ~5 €/mj). S mobitela ne može raditi.
- Zaustavljanje: `Ctrl+C`. Otvoreni nalozi ostaju na burzi; ponovno
  pokretanje nastavlja preko `state.json`.
- Za potpuno gašenje: zaustavite bota, obrišite `state.json` i ručno
  otkažite naloge na Bybitu (Orders → Cancel All).
- Dnevnik rada: `bot.log`, zarada i broj krugova: `state.json`.

## Napomena

Bybit blokira pristup iz nekih regija (npr. SAD) — ako dobijete CloudFront
grešku "blocked from your country", bot morate pokretati s mreže/servera u
podržanoj regiji (Hrvatska/EU je podržana).
