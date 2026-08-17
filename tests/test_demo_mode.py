"""Demo mod: productType/marginCoin routing i obavezna sva tri kredencijala."""

import pytest
import yaml

from marchini.bitget import (
    MARGIN_COIN_DEMO,
    MARGIN_COIN_LIVE,
    PRODUCT_TYPE_DEMO,
    PRODUCT_TYPE_LIVE,
    BitgetClient,
)
from marchini.broker import LiveBroker
from marchini.config import Config, load_config

FULL = ("key", "secret", "pass")


def test_live_client_targets_real_product_type():
    c = BitgetClient(*FULL)
    assert c.demo is False
    assert c.product_type == PRODUCT_TYPE_LIVE == "USDT-FUTURES"
    assert c.margin_coin == MARGIN_COIN_LIVE == "USDT"


def test_demo_client_targets_demo_product_type():
    c = BitgetClient(*FULL, demo=True)
    assert c.demo is True
    assert c.product_type == PRODUCT_TYPE_DEMO == "SUSDT-FUTURES"
    assert c.margin_coin == MARGIN_COIN_DEMO == "SUSDT"


def test_demo_and_live_product_types_never_collide():
    # Zamjena ovih dvaju bi znacila prave naloge u demo namjeri.
    assert PRODUCT_TYPE_DEMO != PRODUCT_TYPE_LIVE
    assert MARGIN_COIN_DEMO != MARGIN_COIN_LIVE


def test_api_key_alone_is_not_enough_to_sign():
    # Ovo je cijela poenta: sam key ne autentifikuje nista.
    c = BitgetClient("samo_key", "", "")
    assert not c.has_credentials
    assert c.missing_credentials() == ["BITGET_API_SECRET", "BITGET_API_PASSPHRASE"]


@pytest.mark.parametrize(
    "creds,missing",
    [
        (("k", "s", ""), ["BITGET_API_PASSPHRASE"]),
        (("k", "", "p"), ["BITGET_API_SECRET"]),
        (("", "s", "p"), ["BITGET_API_KEY"]),
        (("", "", ""), ["BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE"]),
    ],
)
def test_missing_credentials_names_exactly_what_is_absent(creds, missing):
    assert BitgetClient(*creds).missing_credentials() == missing


def test_full_credentials_report_nothing_missing():
    c = BitgetClient(*FULL)
    assert c.has_credentials
    assert c.missing_credentials() == []


def test_live_broker_refuses_partial_credentials_and_says_which():
    with pytest.raises(RuntimeError, match="BITGET_API_SECRET"):
        LiveBroker(BitgetClient("only_key", "", ""), leverage=5)


def test_live_broker_labels_itself_by_client_mode():
    assert LiveBroker(BitgetClient(*FULL, demo=True), 5).mode == "demo"
    assert LiveBroker(BitgetClient(*FULL), 5).mode == "live"


def test_mode_flags_on_config():
    assert Config(mode="paper").sends_orders is False
    assert Config(mode="paper").is_demo is False
    assert Config(mode="demo").sends_orders is True
    assert Config(mode="demo").is_demo is True
    assert Config(mode="live").sends_orders is True
    assert Config(mode="live").is_demo is False


def test_demo_is_an_accepted_mode(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"mode": "demo"}))
    assert load_config(path).mode == "demo"


def test_shipped_demo_config_is_valid_and_demo():
    cfg = load_config("config.demo.yaml")
    assert cfg.mode == "demo"
    assert cfg.is_demo and cfg.sends_orders
    # Filteri moraju biti spusteni ispod izmjerenog ATR-a demo parova (0.34-0.51%),
    # inace screener vrati praznu listu i bot nikad ne trguje.
    assert cfg.screener.min_atr_pct < 0.34
    assert cfg.screener.min_volume_24h <= 1_700_000  # SXRPSUSDT volumen


def test_shipped_live_config_is_paper_by_default():
    # Repo ne smije dolaziti sa configom koji salje prave naloge.
    assert load_config("config.yaml").mode == "paper"
