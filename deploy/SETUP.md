# Postavljanje bota — korak po korak

Tri nacina. **Pocni sa Varijantom A** (lokalno, paper) da vidis da radi, pa
tek onda razmisljaj o 24/7.

---

## Varijanta A — lokalno, paper mod (pocni ovdje)

Ne treba ti API kljuc. Ne trosi novac.

```bash
git clone https://github.com/marchini14/marchinisystem.git
cd marchinisystem
git checkout claude/bitget-futures-usdt-analysis-0nbiy2

python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

export PYTHONPATH=src
```

Sad tri komande, u ovom redu:

```bash
# 1. Koji parovi su danas vrijedni trgovanja i ima li signala
python -m marchini screen

# 2. Koliko kapitala treba da trejd prodje risk provjere
python -m marchini capital

# 3. Jedan prolaz bota, pa izlaz - da vidis sta bi uradio
python -m marchini once
```

Ako to sve prodje, pusti ga u petlju:

```bash
python -m marchini run
```

Ctrl+C ga zaustavlja. State se cuva u `state/bot_state.json`, pa nastavlja
gdje je stao.

**Pusti paper mod barem 2-3 sedmice prije pravog novca.** Nakon toga pogledaj
`state/bot_state.json` → `history` i `realized_pnl`. Ako paper gubi, live gubi
brze.

---

## Varijanta A2 — demo mod (pravi nalozi, virtualni novac)

Ovo je pravi sandbox. Testira cijeli put — potpis, nalog, SL/TP na burzi,
pracenje pozicije — bez ikakvog rizika.

1. Bitget → prebaci se na **Demo trading**
2. Napravi API kljuc **unutar demo moda** (demo i pravi racun imaju odvojene
   kljuceve; nisu zamjenjivi)
3. Dozvole: `Futures Trading` + `Read`. **Nikad `Withdraw`.**

```bash
cp .env.example .env
nano .env                  # popuni SVA TRI polja
set -a; . ./.env; set +a

python -m marchini run -c config.demo.yaml
```

Ako fali bilo koji od tri kredencijala, bot ti kaze tacno koji:

```
mode je 'demo' ali fale kredencijali: BITGET_API_SECRET, BITGET_API_PASSPHRASE
```

`screen` i `capital` rade i u demo modu **bez** kljuceva — citaju samo javne rute.

---

## Varijanta B — VPS, 24/7 (za pravo trgovanje)

Bot mora raditi neprekidno. Laptop koji se uspava propusti signale i, gore,
propusti upravljanje otvorenom pozicijom.

### 1. Server

Bilo koji Linux VPS, 1 GB RAM je dovoljno. Bot je lagan.

### 2. Kod i venv

```bash
sudo useradd -r -m -d /opt/marchinisystem marchini
sudo -u marchini git clone https://github.com/marchini14/marchinisystem.git /opt/marchinisystem
cd /opt/marchinisystem
sudo -u marchini git checkout claude/bitget-futures-usdt-analysis-0nbiy2
sudo -u marchini python3 -m venv .venv
sudo -u marchini .venv/bin/pip install -r requirements.txt
sudo -u marchini mkdir -p state logs
```

### 3. API kljuc na Bitgetu

Bitget → API Management → Create API Key:

- **Dozvole: samo `Futures Trading` (i `Read`).**
- **NIKAD ne uključuj `Withdraw`.** Botu ne treba, i to je jedina stvar koja
  te stiti ako kljuc procuri.
- Ako mozes vezati kljuc na IP adresu servera — vezi ga.

Zapamti passphrase koji si postavio; ne moze se ponovo vidjeti.

### 4. Kljucevi u fajl, ne u kod

```bash
sudo mkdir -p /etc/marchini
sudo tee /etc/marchini/env > /dev/null <<'EOF'
BITGET_API_KEY=tvoj_key
BITGET_API_SECRET=tvoj_secret
BITGET_API_PASSPHRASE=tvoj_passphrase
EOF
sudo chmod 600 /etc/marchini/env
sudo chown root:root /etc/marchini/env
```

### 5. Config

```bash
sudo -u marchini nano /opt/marchinisystem/config.yaml
```

Postavi:

```yaml
mode: live
equity: 50.0        # SAMO novac koji si spreman izgubiti
risk:
  leverage: 5       # ne dizi ovo; visi leverage = bliza likvidacija
  risk_per_trade_pct: 1.0
```

### 6. Pokreni kao servis

```bash
sudo cp deploy/marchini.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now marchini
```

Provjera:

```bash
systemctl status marchini
journalctl -u marchini -f      # logovi u realnom vremenu
```

---

## Varijanta C — Docker

```bash
docker build -t marchini .

docker run -d --name marchini --restart unless-stopped \
  --env-file .env \
  -v "$PWD/state:/app/state" \
  -v "$PWD/logs:/app/logs" \
  -v "$PWD/config.yaml:/app/config.yaml:ro" \
  marchini

docker logs -f marchini
```

Volumeni za `state/` i `logs/` nisu opcioni — bez njih bot pri svakom
restartu zaboravi otvorene pozicije i istoriju.

---

## Sta pratiti kad radi

```bash
# Sta bot trenutno drzi i koliko je zaradio/izgubio
cat state/bot_state.json | python3 -m json.tool

# Zasto signal nije postao nalog (risk veto se uvijek loguje)
grep "odbijen" logs/bot.log

# Otvaranja i zatvaranja
grep -E "OPEN|CLOSE" logs/bot.log
```

## Ako nista ne otvara

To je najcesce **ispravno ponasanje**, ne greska. Redom provjeri:

1. `python -m marchini screen` — ima li ijedan par `SIGNAL` koji nije `-`?
   Breakout se ne desava svaki sat. Vecinu vremena nema ulaza.
2. `grep "odbijen" logs/bot.log` — ako pise `notional ... ispod minimuma` ili
   `fee je X% ocekivanog profita`, kapital ti je premali. Pokreni
   `python -m marchini capital` da vidis koliko treba.
3. Ako pise `likvidacija ... preblizu stopa` — leverage je previsok za
   volatilnost tog para. **Smanji leverage**, ne diraj `min_liq_distance_vs_stop`.

## Sta NE dirati

- `min_liq_distance_vs_stop` — spusti ovo i dozvolio si da te likvidacija
  pogodi prije stop-lossa. To je nacin da izgubis cijelu marginu na jednom
  trejdu.
- `max_fee_share_of_target_pct` gore — dozvoljavas pozicije premale da
  pokriju vlastite troskove.
- `risk_per_trade_pct` iznad ~2% — serija od 5 gubitaka pri 5% je -25%
  kapitala.
- `leverage` iznad 10 — na volatilnim parovima likvidacija dolazi prije stopa,
  i risk modul ce ionako sve odbiti.

## Zaustavljanje

```bash
sudo systemctl stop marchini      # systemd
docker stop marchini              # docker
```

**Zaustavljanje bota ne zatvara otvorene pozicije.** One ostaju na Bitgetu sa
svojim SL/TP nalozima. Ako ih zelis zatvoriti, uradi to rucno u Bitget aplikaciji.
