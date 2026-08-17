"""Screener: od ~750 parova izdvoji one koji su uopste vrijedni trgovanja.

Rangiranje je po *stvarnim* metrikama - likvidnost, volatilnost, funding -
a ne po 24h rastu. Par koji je danas +50% je najgori mogući ulaz, ne najbolji.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .bitget import BitgetClient
from .indicators import Candle, atr, parse_candles

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    symbol: str
    price: float
    change_24h_pct: float
    volume_24h: float
    atr_value: float
    atr_pct: float
    funding: float
    candles: list[Candle]
    score: float = 0.0


def _f(raw: dict, *keys: str, default: float = 0.0) -> float:
    """Uzmi prvi prisutan kljuc i pretvori u float (Bitget mijenja imena polja)."""
    for key in keys:
        if raw.get(key) not in (None, ""):
            try:
                return float(raw[key])
            except (TypeError, ValueError):
                continue
    return default


def screen(
    client: BitgetClient,
    *,
    min_volume_24h: float,
    max_change_24h_pct: float,
    min_atr_pct: float,
    max_abs_funding: float,
    top_n: int,
    blacklist: list[str],
    timeframe: str,
    atr_period: int,
    candle_limit: int = 200,
) -> list[Candidate]:
    """Vrati do `top_n` kandidata, najbolji prvi.

    Filtriranje ide u dvije faze: prvo jeftini filteri nad tickerima (jedan
    API poziv za sve parove), pa tek onda svijece i funding za prezivjele -
    da ne trosimo 750 poziva na parove koje smo ionako odbacili.
    """
    tickers = client.tickers()
    banned = {s.upper() for s in blacklist}

    prelim: list[dict] = []
    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol or symbol.upper() in banned:
            continue
        volume = _f(t, "usdtVolume", "quoteVolume")
        if volume < min_volume_24h:
            continue
        change = _f(t, "change24h") * 100.0
        if abs(change) > max_change_24h_pct:
            continue
        price = _f(t, "lastPr", "last", "close")
        if price <= 0:
            continue
        prelim.append({"symbol": symbol, "price": price, "change": change, "volume": volume})

    # Najlikvidniji prvi, i uzmi razuman visak da imamo rezerve nakon ATR/funding filtera.
    prelim.sort(key=lambda d: d["volume"], reverse=True)
    prelim = prelim[: max(top_n * 4, 20)]
    log.info("screener: %d parova proslo osnovne filtere", len(prelim))

    candidates: list[Candidate] = []
    for row in prelim:
        symbol = row["symbol"]
        try:
            candles = parse_candles(client.candles(symbol, timeframe, candle_limit))
            if len(candles) < atr_period + 2:
                continue
            atr_value = atr(candles, atr_period)
            if atr_value <= 0:
                continue
            atr_pct = atr_value / row["price"] * 100.0
            if atr_pct < min_atr_pct:
                continue
            funding = client.funding_rate(symbol)
            if abs(funding) > max_abs_funding:
                continue
        except Exception as exc:  # pojedinacni par ne smije srusiti cijeli screening
            log.warning("screener preskace %s: %s", symbol, exc)
            continue

        candidates.append(
            Candidate(
                symbol=symbol,
                price=row["price"],
                change_24h_pct=row["change"],
                volume_24h=row["volume"],
                atr_value=atr_value,
                atr_pct=atr_pct,
                funding=funding,
                candles=candles,
            )
        )

    _apply_scores(candidates)
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:top_n]


def _apply_scores(candidates: list[Candidate]) -> None:
    """Score = likvidnost x volatilnost, uz kaznu za funding.

    Oba faktora se normalizuju na [0,1] unutar trenutnog skupa, pa je score
    relativan - rangira kandidate medju sobom, nije apsolutna mjera.
    """
    if not candidates:
        return
    max_volume = max(c.volume_24h for c in candidates) or 1.0
    max_atr_pct = max(c.atr_pct for c in candidates) or 1.0
    max_funding = max((abs(c.funding) for c in candidates), default=0.0) or 1.0

    for c in candidates:
        liquidity = c.volume_24h / max_volume
        volatility = c.atr_pct / max_atr_pct
        funding_penalty = abs(c.funding) / max_funding
        c.score = 0.5 * liquidity + 0.5 * volatility - 0.2 * funding_penalty
