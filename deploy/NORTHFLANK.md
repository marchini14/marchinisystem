# Northflank — 24/7 deploy

Northflank rjesava pravi problem: bot mora raditi neprekidno. Laptop koji se
uspava, ili privremeni kontejner koji se ugasi, propusti signal i — gore —
prestane upravljati **otvorenom** pozicijom.

**Kljucna stvar: API kljuceve unosis TI, direktno u Northflank secret group.
Ne prolaze kroz chat, ne idu u git, ne vidi ih niko osim Northflanka.**

---

## 1. Obavezno prije svega: cist kljuc

Ako je kljuc ikad bio u chatu, poruci, screenshotu ili commitu — obrisi ga na
Bitgetu i napravi novi. Deploy sa procurjelim kljucem samo premjesta problem
na server koji radi 24/7.

Novi kljuc: **`Futures Trading` + `Read`. Bez `Withdraw`.**

---

## 2. Poveži repo

Northflank → **Create new** → **Service** → **Combined service**
(build + deploy u jednom).

- **Repository**: `marchini14/marchinisystem`
- **Branch**: `claude/bitget-futures-usdt-analysis-0nbiy2`
- **Build type**: **Dockerfile**
- **Dockerfile path**: `/Dockerfile`
- **Build context**: `/`

## 3. Bez porta

Ovaj bot nema HTTP server — samo se povezuje na Bitget i spava izmedju ciklusa.

- **Ports**: nijedan. Ne dodavaj port i ne ukljucuj public ingress.
- Ako Northflank trazi tip, izaberi **worker / background** varijantu, ne web
  servis. Web servis bi cekao health check na portu koji ne postoji i restartovao
  bi se u krug.

## 4. Tajne — ovdje unosis kljuceve

Northflank → **Secrets** → **Create secret group** → dodaj kao *runtime
variables*:

| Ime | Vrijednost |
|---|---|
| `BITGET_API_KEY` | tvoj novi key |
| `BITGET_API_SECRET` | tvoj novi secret |
| `BITGET_API_PASSPHRASE` | tvoj novi passphrase |

Linkuj secret group na servis. Bot ih cita iz okoline — isti kod, ista logika
kao lokalno.

## 5. Koji config

Dodaj jos jednu runtime varijablu na **servisu** (ne u secret grupi):

| Ime | Vrijednost | Znaci |
|---|---|---|
| `MARCHINI_CONFIG` | `config.yaml` | paper — **pocni ovdje** |
| `MARCHINI_CONFIG` | `config.small.yaml` | za ~2-10 USDT kapitala |
| `MARCHINI_CONFIG` | `config.demo.yaml` | Bitget demo, virtualni novac |

Svi configi su u imageu, pa se mod mijenja bez novog builda — samo promijeni
varijablu i restartuj servis.

**Da bi bot stvarno trgovao, u tom configu mora biti `mode: live`.** Ako je
`mode: paper`, radice i logovati signale ali nista nece poslati. To je namjerno.

## 6. Persistent volume — nije opciono

Northflank → servis → **Volumes** → dodaj:

| Mount path | Velicina |
|---|---|
| `/app/state` | 1 GB |
| `/app/logs` | 1 GB |

Bez ovoga bot pri **svakom** restartu ili redeployu zaboravi otvorene pozicije,
istoriju i dnevni PnL — pa i dnevni limit gubitka pocinje od nule. Pozicije bi
ostale na Bitgetu sa svojim SL/TP, ali bot ih vise ne bi vodio.

## 7. Resursi

Bot je lagan — bez pandas/numpy, samo `requests` + `PyYAML`.

- **CPU**: 0.1 vCPU
- **RAM**: 256 MB

## 8. Provjeri prije nego pustis da trguje

Prvo pusti sa `MARCHINI_CONFIG=config.yaml` (paper) i pogledaj logove. Trebalo
bi da vidis:

```
bot startuje | mode=paper equity=... leverage=... risk/trade=...
ucitano 759 specifikacija kontrakta
screener: N kandidata
```

Ako to radi, veza sa Bitgetom i cijeli pipeline su ispravni.

Za live provjeru pokreni preflight **kao jednokratni Job**, ne kao servis —
`check` ne salje nalog:

```
python -m marchini check
```

Kaze ti da li potpis prolazi, koliko je na racunu, i kako bi izgledao prvi trejd
sa stopom, targetom i likvidacijskom cijenom. Ako `check` padne, `run` bi pao na
istom mjestu — samo eventualno poslije poslanog naloga.

## 9. Sta pratiti

Northflank logovi, ili u shellu servisa:

```bash
grep "odbijen" logs/*.log            # zasto signal nije postao nalog
grep -E "OPEN|CLOSE" logs/*.log      # otvaranja i zatvaranja
cat state/*.json | python3 -m json.tool
```

---

## Ako se servis restartuje u krug

Skoro uvijek jedno od dvoje:

1. **Konfigurisan je kao web servis sa portom.** Bot ne slusa na portu, health
   check pada, Northflank restartuje. Prebaci na worker/background tip.
2. **`mode: live/demo` bez kljuceva.** Bot izlazi sa kodom 1 i jasnom porukom u
   logu — provjeri da je secret group linkovan na servis.

## Zaustavljanje

Pause ili scale na 0 replika.

**Zaustavljanje servisa ne zatvara otvorene pozicije.** One ostaju na Bitgetu sa
svojim SL/TP nalozima. Zatvaras ih rucno u Bitget aplikaciji.
