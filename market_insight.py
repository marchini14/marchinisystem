"""Tržišni kontekst iz besplatnih javnih izvora (bez API kljuceva).

Ovo NIJE prediktor cijene — nijedan sentiment indikator to pouzdano
ne radi. Koristi se kao:
  1. dnevni log konteksta (uvijek, informativno)
  2. opcionalni sigurnosni filter — pauzira otvaranje novih grid
     pozicija u ekstremnom strahu na trzistu (PAUSE_ON_EXTREME_FEAR)
  3. opcionalno racuna sirinu grida iz stvarne volatilnosti umjesto
     fiksnog postotka (DYNAMIC_RANGE)

Sve vanjske pozive obavezno okruzujemo tvrdim (thread-based) timeoutom:
DNS/mrezni problemi na nekim hostovima znaju "objesiti" spojenje daleko
dulje od timeout= parametra na samoj biblioteci (npr. spor/blokiran DNS
resolver) — a ovaj modul je sporedan i NIKAD ne smije zaustaviti glavnu
petlju bota koja stvarno trguje.
"""

import logging
import queue
import statistics
import threading
import urllib.request
import json
from decimal import Decimal

log = logging.getLogger("gridbot.insight")

FNG_URL = "https://api.alternative.me/fng/?limit=1"
COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"

HARD_TIMEOUT_S = 8


def _with_hard_timeout(fn, *args, timeout: float = HARD_TIMEOUT_S):
    """Pokrece fn u posebnoj (daemon) niti i garantirano odustaje nakon
    `timeout` sekundi, cak i ako fn interno visi (npr. DNS resolucija bez
    vlastitog timeouta). Daemon niti nikad ne blokiraju gasenje procesa —
    ostave se da istrunu u pozadini ako stvarno vjecno vise."""
    result_q: queue.Queue = queue.Queue(maxsize=1)

    def _runner():
        try:
            result_q.put(("ok", fn(*args)))
        except Exception as exc:
            result_q.put(("err", exc))

    threading.Thread(target=_runner, daemon=True).start()
    try:
        status, value = result_q.get(timeout=timeout)
    except queue.Empty:
        log.warning("%s nije zavrsio unutar %ss — preskacem.", getattr(fn, "__name__", fn), timeout)
        return None
    if status == "err":
        log.warning("%s nije uspio: %s", getattr(fn, "__name__", fn), value)
        return None
    return value


def _get_json(url: str, timeout: int = 6) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def _fetch_fear_greed_raw() -> dict | None:
    data = _get_json(FNG_URL)
    entry = data["data"][0]
    return {"value": int(entry["value"]), "classification": entry["value_classification"]}


def _fetch_btc_dominance_raw() -> float:
    data = _get_json(COINGECKO_GLOBAL_URL)
    return float(data["data"]["market_cap_percentage"]["btc"])


def _realized_volatility_raw(session, symbol: str, lookback: int) -> Decimal | None:
    resp = session.get_kline(category="spot", symbol=symbol, interval="15", limit=lookback)
    rows = resp["result"]["list"]
    if len(rows) < 10:
        return None
    closes = [float(r[4]) for r in reversed(rows)]
    returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1] > 0
    ]
    if not returns:
        return None
    stdev = statistics.pstdev(returns)
    # anualizirano na "dnevnu" skalu (96 svijeca od 15 min = 1 dan)
    daily_vol_pct = stdev * (len(returns) ** 0.5) * 100
    return Decimal(str(round(daily_vol_pct, 2)))


def fetch_fear_greed() -> dict | None:
    """Vraca {'value': int, 'classification': str} ili None ako izvor ne odgovori na vrijeme."""
    return _with_hard_timeout(_fetch_fear_greed_raw)


def fetch_btc_dominance() -> float | None:
    """Vraca postotak dominacije BTC-a na trzistu, ili None ako izvor ne odgovori na vrijeme."""
    return _with_hard_timeout(_fetch_btc_dominance_raw)


def realized_volatility_pct(session, symbol: str, lookback: int = 96) -> Decimal | None:
    """Realizirana volatilnost iz zadnjih 15-min svijeca (Bybit kline), kao %.

    Priblizna mjera koliko se cijena stvarno kreće — koristi se za
    dinamicko postavljanje sirine grida umjesto fiksnog postotka.
    """
    return _with_hard_timeout(_realized_volatility_raw, session, symbol, lookback)


def log_market_snapshot(session, symbol: str) -> dict:
    """Loga i vraca trenutni tržišni kontekst (za informaciju, ne za auto-odluke).

    Nikad ne baca iznimku i nikad ne blokira dulje od ~3x HARD_TIMEOUT_S —
    poziva se svaki poll ciklus glavne petlje bota.
    """
    fng = fetch_fear_greed()
    dominance = fetch_btc_dominance()
    vol = realized_volatility_pct(session, symbol)

    parts = []
    if fng:
        parts.append(f"Fear&Greed: {fng['value']}/100 ({fng['classification']})")
    if dominance is not None:
        parts.append(f"BTC dominacija: {dominance:.1f}%")
    if vol is not None:
        parts.append(f"volatilnost({symbol}, ~1d): {vol}%")

    log.info("Trzisni kontekst — %s", "; ".join(parts) if parts else "izvori nedostupni")
    return {"fear_greed": fng, "btc_dominance": dominance, "volatility_pct": vol}
