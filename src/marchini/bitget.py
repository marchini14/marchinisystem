"""Bitget v2 API klijent (USDT-M futures, pravi i demo).

Javni endpointi rade bez kljuceva. Potpisani endpointi (racun, nalozi) traze
sva tri kredencijala: BITGET_API_KEY / _SECRET / _PASSPHRASE. Sam API key nije
dovoljan - potpis se racuna iz secreta, a passphrase ide kao zaseban header.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.bitget.com"

# Bitget razdvaja pravo i demo trgovanje po productType, na istom hostu i sa
# istim rutama. Demo ("SUSDT-FUTURES") ima virtualni novac i vlastite simbole
# sa S prefiksom (SBTCSUSDT), pa je jedini nacin da se potpisani nalozi testiraju
# bez pravog kapitala.
PRODUCT_TYPE_LIVE = "USDT-FUTURES"
PRODUCT_TYPE_DEMO = "SUSDT-FUTURES"
MARGIN_COIN_LIVE = "USDT"
MARGIN_COIN_DEMO = "SUSDT"


class BitgetError(RuntimeError):
    """Bitget je vratio code != 00000."""

    def __init__(self, code: str, msg: str, path: str) -> None:
        super().__init__(f"Bitget {path} -> [{code}] {msg}")
        self.code = code
        self.msg = msg


class BitgetClient:
    """Tanak wrapper nad Bitget REST v2.

    Retry sa exponential backoff na mreznim greskama i 429/5xx. Poslovne
    greske (npr. nedovoljan balans) se ne retryaju - dizu BitgetError odmah.
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        passphrase: str = "",
        timeout: int = 20,
        max_retries: int = 4,
        demo: bool = False,
    ) -> None:
        self._key = api_key
        self._secret = api_secret
        self._passphrase = passphrase
        self._timeout = timeout
        self._max_retries = max_retries
        self._session = requests.Session()
        self.demo = demo
        self.product_type = PRODUCT_TYPE_DEMO if demo else PRODUCT_TYPE_LIVE
        self.margin_coin = MARGIN_COIN_DEMO if demo else MARGIN_COIN_LIVE

    @property
    def has_credentials(self) -> bool:
        return bool(self._key and self._secret and self._passphrase)

    def missing_credentials(self) -> list[str]:
        """Koji kredencijali fale - da greska imenuje sta konkretno treba."""
        return [
            name
            for name, value in (
                ("BITGET_API_KEY", self._key),
                ("BITGET_API_SECRET", self._secret),
                ("BITGET_API_PASSPHRASE", self._passphrase),
            )
            if not value
        ]

    # ------------------------------------------------------------------ auth

    def _sign(self, timestamp: str, method: str, path: str, body: str) -> str:
        prehash = f"{timestamp}{method.upper()}{path}{body}"
        digest = hmac.new(
            self._secret.encode(), prehash.encode(), hashlib.sha256
        ).digest()
        return base64.b64encode(digest).decode()

    def _headers(self, method: str, path: str, body: str, signed: bool) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "locale": "en-US"}
        if not signed:
            return headers
        if not self.has_credentials:
            raise BitgetError(
                "no-credentials",
                f"fale kredencijali: {', '.join(self.missing_credentials())}",
                path,
            )
        ts = str(int(time.time() * 1000))
        headers.update(
            {
                "ACCESS-KEY": self._key,
                "ACCESS-SIGN": self._sign(ts, method, path, body),
                "ACCESS-TIMESTAMP": ts,
                "ACCESS-PASSPHRASE": self._passphrase,
            }
        )
        return headers

    # --------------------------------------------------------------- request

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        full_path = f"{path}{query}"
        payload = json.dumps(body) if body else ""

        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            if attempt:
                delay = 2**attempt
                log.warning("retry %s %s za %ss", method, path, delay)
                time.sleep(delay)
            try:
                resp = self._session.request(
                    method,
                    f"{BASE_URL}{full_path}",
                    headers=self._headers(method, full_path, payload, signed),
                    data=payload or None,
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                last_exc = exc
                continue

            # Rate limit i serverske greske su prolazne -> retry.
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = BitgetError(str(resp.status_code), resp.text[:200], path)
                continue

            try:
                data = resp.json()
            except ValueError as exc:
                last_exc = exc
                continue

            if data.get("code") != "00000":
                # Poslovna greska - retry nema smisla, korisnik mora vidjeti razlog.
                raise BitgetError(data.get("code", "?"), data.get("msg", "?"), path)
            return data.get("data")

        raise BitgetError("network", f"neuspjelo nakon {self._max_retries} pokusaja: {last_exc}", path)

    # ---------------------------------------------------------- javni market

    def tickers(self) -> list[dict[str, Any]]:
        return self._request(
            "GET", "/api/v2/mix/market/tickers", {"productType": self.product_type}
        ) or []

    def contracts(self) -> list[dict[str, Any]]:
        """Specifikacije kontrakta: minTradeUSDT, volumePlace, pricePlace, maxLever."""
        return self._request(
            "GET", "/api/v2/mix/market/contracts", {"productType": self.product_type}
        ) or []

    def candles(self, symbol: str, granularity: str = "1H", limit: int = 200) -> list[list[str]]:
        """Vraca [ts, open, high, low, close, baseVol, quoteVol], najstarije prvo."""
        return self._request(
            "GET",
            "/api/v2/mix/market/candles",
            {
                "symbol": symbol,
                "productType": self.product_type,
                "granularity": granularity,
                "limit": str(limit),
            },
        ) or []

    def funding_rate(self, symbol: str) -> float:
        data = self._request(
            "GET",
            "/api/v2/mix/market/current-fund-rate",
            {"symbol": symbol, "productType": self.product_type},
        ) or []
        return float(data[0]["fundingRate"]) if data else 0.0

    # -------------------------------------------------------- privatni racun

    def account(self) -> dict[str, Any]:
        data = self._request(
            "GET",
            "/api/v2/mix/account/accounts",
            {"productType": self.product_type},
            signed=True,
        ) or []
        return data[0] if data else {}

    def available_balance(self) -> float:
        return float(self.account().get("available", 0.0) or 0.0)

    def positions(self) -> list[dict[str, Any]]:
        return self._request(
            "GET",
            "/api/v2/mix/position/all-position",
            {"productType": self.product_type, "marginCoin": self.margin_coin},
            signed=True,
        ) or []

    def set_leverage(self, symbol: str, leverage: int) -> Any:
        return self._request(
            "POST",
            "/api/v2/mix/account/set-leverage",
            body={
                "symbol": symbol,
                "productType": self.product_type,
                "marginCoin": self.margin_coin,
                "leverage": str(leverage),
            },
            signed=True,
        )

    def place_market_order(
        self,
        symbol: str,
        side: str,
        size: str,
        stop_loss: str | None = None,
        take_profit: str | None = None,
        client_oid: str | None = None,
    ) -> Any:
        """Market ulaz sa preset SL/TP u istom nalogu.

        SL/TP idu uz nalog namjerno: ako bot padne odmah nakon ulaza, zastita
        je vec na burzi. Odvojeni SL nalog bi ostavio poziciju nezasticenom.
        """
        body: dict[str, Any] = {
            "symbol": symbol,
            "productType": self.product_type,
            "marginMode": "isolated",
            "marginCoin": self.margin_coin,
            "size": size,
            "side": side,  # "buy" | "sell"
            "orderType": "market",
        }
        if stop_loss:
            body["presetStopLossPrice"] = stop_loss
        if take_profit:
            body["presetStopSurplusPrice"] = take_profit
        if client_oid:
            body["clientOid"] = client_oid
        return self._request("POST", "/api/v2/mix/order/place-order", body=body, signed=True)

    def close_position(self, symbol: str, hold_side: str) -> Any:
        return self._request(
            "POST",
            "/api/v2/mix/order/close-positions",
            body={"symbol": symbol, "productType": self.product_type, "holdSide": hold_side},
            signed=True,
        )
