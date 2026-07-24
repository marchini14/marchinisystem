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

## Tržišni kontekst (Fear&Greed, volatilnost)

Bot povlači podatke iz besplatnih javnih izvora (alternative.me, CoinGecko)
i Bybitovih vlastitih podataka o cijeni — **ne** radi se o prediktoru
cijene, nego o sigurnosnom filteru i logu:

| Postavka | Zadano | Značenje |
|---|---|---|
| `MARKET_INSIGHT` | `true` | loguje Fear&Greed index, BTC dominaciju i volatilnost |
| `INSIGHT_INTERVAL_MIN` | `60` | koliko često (u minutama) |
| `DYNAMIC_RANGE` | `false` | ako `true`, širina grida se računa iz stvarne volatilnosti (2-15%) umjesto fiksnog `GRID_RANGE_PCT` |
| `PAUSE_ON_EXTREME_FEAR` | `false` | ako `true`, bot obustavlja **nove kupnje** kad je Fear&Greed ispod praga (postojeći nalozi i prodaje rade dalje normalno) |
| `FEAR_GREED_PAUSE_BELOW` | `15` | prag za pauzu (0 = ekstremni strah, 100 = ekstremna pohlepa) |

Ovo su opt-in sigurnosne kočnice, ne "signal za kupnju/prodaju" — nijedan
sentiment indikator pouzdano ne predviđa cijenu, pa ih bot koristi samo da
smanji rizik ulaska u loš trenutak, nikad da poveća agresivnost.

## Rad bota

- Bot mora **stalno raditi** — na kućnom računalu koje ne spava, na malom
  VPS-u (Hetzner/Contabo, ~5 €/mj), ili na Northflanku (upute ispod). S
  mobitela izravno ne može raditi (aplikacije se gase u pozadini) — ali
  preko Northflanka se pokreće i upravlja iz preglednika na mobitelu.
- Zaustavljanje: `Ctrl+C` (ili "Stop" u Northflanku). Otvoreni nalozi
  ostaju na burzi; ponovno pokretanje nastavlja preko `state.json`.
- Za potpuno gašenje: zaustavite bota, obrišite `state.json` i ručno
  otkažite naloge na Bybitu (Orders → Cancel All).
- Dnevnik rada: `bot.log`, zarada i broj krugova: `state.json`.

## Pokretanje na Northflanku (24/7, upravljanje s mobitela)

Northflank vozi Docker kontejner umjesto vas, pa bot radi neprekidno bez
da vaš uređaj mora biti uključen. Sve niže ide kroz njihov web dashboard
(radi i na mobitelu u pregledniku).

1. **Napravite račun** na [northflank.com](https://northflank.com) i
   povežite svoj GitHub (dat ćete pristup repozitoriju
   `marchini14/marchinisystem`).
2. **Create new → Service** → odaberite "Deploy from Git repository" →
   `marchinisystem`, granu `main` (ili `claude/bol-cvpe3t` dok PR nije
   spojen). Northflank će prepoznati `Dockerfile` u repou i sam ga
   izgraditi.
3. **Tip servisa: "Deployment" (ne "Job")** — mora ostati trajno
   pokrenut, ne jednokratno izvršavanje.
4. **Dodajte trajni volume:** Service → Volumes → Add volume → mount
   path `/app/data`, veličina 1 GB je dovoljna. Ovo čuva `state.json`
   preko restarta/redeploya — **bez ovoga bot gubi zapis o otvorenim
   nalozima i može duplicirati pozicije.**
5. **Postavite environment varijable** (Service → Environment) — isto
   što i u `.env.example`, ali upisano kao Northflank secrets (ne u
   kod!): `BYBIT_API_KEY`, `BYBIT_API_SECRET`, `BYBIT_ENV`, `DRY_RUN`,
   `SYMBOL`, `GRID_CAPITAL_USDT`, `GRID_RANGE_PCT`, `GRID_LEVELS`,
   `POLL_SECONDS`.
6. **Deploy.** Logove (isto što i `bot.log`) gledate uživo u Northflank
   dashboardu pod "Logs" — s mobitela, bilo gdje.
7. Isti savjet kao i lokalno: krenite s `DRY_RUN=true`, zatim
   `BYBIT_ENV=demo`, tek onda `live` s malim iznosom.

Napomena: `live`-mod inače traži upis `DA` u terminal prije starta (radi
sigurnosne potvrde). U Northflanku nema interaktivnog terminala, pa bot
tamo umjesto toga traži environment varijablu `LIVE_CONFIRM=DA` — bez nje
odbija krenuti u live modu. Postavite je tek kad ste sigurni u sve ostale
postavke, i prvo isprobajte na demo računu.

## Napomena

Bybit blokira pristup iz nekih regija (npr. SAD) — ako dobijete CloudFront
grešku "blocked from your country", bot morate pokretati s mreže/servera u
podržanoj regiji (Hrvatska/EU je podržana).
