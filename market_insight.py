"""Tržišni kontekst iz besplatnih javnih izvora (bez API kljuceva).

Ovo NIJE prediktor cijene — nijedan sentiment indikator to pouzdano
ne radi. Koristi se kao:
  1. dnevni log konteksta (uvijek, informativno)
  2. opcionalni sigurnosni filter — pauzira otvaranje novih grid
     pozicija u ekstremnom strahu na trzistu (PAUSE_ON_EXTREME_FEAR)
  3. opcionalno racuna sirinu grida iz stvarne volatilnosti umjesto
     fiksnog postotka (DYNAMIC_RANGE)
"""

import logging
import statistics
import urllib.request
import json
from decimal import Decimal

log = logging.getLogger("gridbot.insight")

FNG_URL = "https://api.alternative.me/fng/?limit=1"
COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"


def _get_json(url: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_fear_greed() -> dict | None:
    """Vraca {'value': int, 'classification': str} ili None ako izvor ne odgovori."""
    try:
        data = _get_json(FNG_URL)
        entry = data["data"][0]
        return {"value": int(entry["value"]), "classification": entry["value_classification"]}
    except Exception:
        log.warning("Fear&Greed API nedostupan — nastavljam bez njega.", exc_info=True)
        return None


def fetch_btc_dominance() -> float | None:
    """Vraca postotak dominacije BTC-a na trzistu, ili None ako izvor ne odgovori."""
    try:
        data = _get_json(COINGECKO_GLOBAL_URL)
        return float(data["data"]["market_cap_percentage"]["btc"])
    except Exception:
        log.warning("CoinGecko API nedostupan — nastavljam bez njega.", exc_info=True)
        return None


def realized_volatility_pct(session, symbol: str, lookback: int = 96) -> Decimal | None:
    """Realizirana volatilnost iz zadnjih 15-min svijeca (Bybit kline), kao %.

    Priblizna mjera koliko se cijena stvarno kreće — koristi se za
    dinamicko postavljanje sirine grida umjesto fiksnog postotka.
    """
    try:
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
    except Exception:
        log.warning("Racunanje volatilnosti nije uspjelo — koristim fiksni GRID_RANGE_PCT.", exc_info=True)
        return None


def log_market_snapshot(session, symbol: str) -> dict:
    """Loga i vraca trenutni tržišni kontekst (za informaciju, ne za auto-odluke)."""
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
