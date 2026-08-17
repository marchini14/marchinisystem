"""Mali kapital: potvrda da niski ATR kupuje veci notional i da 2.14 prolazi.

Ovi testovi cuvaju nalaz iz analize: sa ~2 USDT prolaze samo mirni majori, i to
tek od 2% rizika po trejdu. Ako neko kasnije stegne filtere, ovo pukne.
"""

import pytest

from marchini.config import load_config
from marchini.risk import ContractSpec, size_trade

EQ = 2.14

# BTCUSDT, stvarne vrijednosti iz /mix/market/contracts.
BTC = ContractSpec(
    symbol="BTCUSDT", min_trade_usdt=5.0, min_trade_num=0.0001, volume_place=4,
    price_place=1, max_leverage=125, taker_fee=0.0006, maker_fee=0.0002,
)

PRICE = 63_545.0
ATR_LOW = PRICE * 0.0029   # 0.29% - izmjereno na BTCUSDT
ATR_HIGH = PRICE * 0.08    # 8%    - tipicno za volatilni altcoin


def size(atr_value, risk_pct, leverage=3, equity=EQ, spec=BTC, price=PRICE):
    return size_trade(
        symbol=spec.symbol, side="long", entry=price, atr_value=atr_value,
        spec=spec, equity=equity, leverage=leverage, risk_per_trade_pct=risk_pct,
        atr_stop_mult=2.0, atr_target_mult=3.0,
        max_fee_share_of_target_pct=15.0, min_liq_distance_vs_stop=1.5,
    )


def test_one_percent_risk_rounds_quantity_to_zero():
    """Zasto default config.yaml ne radi sa ovim kapitalom.

    Stvarna prepreka na ovoj velicini nije minimalni notional od 5 USDT nego
    granularnost kolicine: 1% od 2.14 je 0.0214 USDT rizika, sto na stopu od
    ~369 daje 0.000058 BTC - ispod koraka 0.0001, pa se zaokruzi na nulu.
    """
    trade = size(ATR_LOW, risk_pct=1.0)
    assert not trade.ok
    assert any("zaokruzena na 0" in r for r in trade.rejections)


def test_two_percent_risk_clears_the_minimum():
    trade = size(ATR_LOW, risk_pct=2.0)
    assert trade.ok, trade.rejections
    assert trade.notional >= BTC.min_trade_usdt
    assert trade.margin <= EQ


def test_high_volatility_pair_is_unreachable_at_this_capital():
    # Veci ATR -> dalji stop -> manja kolicina -> notional pada ispod minimuma.
    assert not size(ATR_HIGH, risk_pct=2.0).ok


def test_lower_atr_buys_larger_notional_at_equal_risk():
    """Jezgro nalaza: mirniji par daje veci notional za isti rizik.

    Mjereno na 20x, gdje margina nije ogranicavajuca. Na 3x je kapacitet margine
    (2.14 x 3 / 63545 = 0.0001 BTC) manji od onoga sto rizik dozvoljava, pa
    kolicina bude ista za oba ATR-a - vidi test ispod.
    """
    calm = size(ATR_LOW, risk_pct=5.0, leverage=20)          # 0.29%
    choppy = size(PRICE * 0.005, risk_pct=5.0, leverage=20)  # 0.50%
    assert calm.ok, calm.rejections
    assert choppy.ok, choppy.rejections
    assert calm.notional > choppy.notional


def test_margin_capacity_caps_quantity_at_low_leverage():
    """Na 3x sa 2.14 USDT margina dozvoljava tacno 0.0001 BTC, ne vise.

    Zato dva razlicita ATR-a daju identican notional: ogranicenje nije rizik
    nego koliko pozicije kapital moze drzati pri tom leveridzu.
    """
    calm = size(ATR_LOW, risk_pct=5.0, leverage=3)
    choppy = size(PRICE * 0.005, risk_pct=5.0, leverage=3)
    assert calm.ok and choppy.ok
    assert calm.qty == choppy.qty == 0.0001
    assert calm.notional == pytest.approx(choppy.notional)


def test_quantity_granularity_is_the_binding_limit_not_notional():
    """Iznad skromnog ATR-a kolicina padne na nulu prije nego notional zasmeta."""
    trade = size(PRICE * 0.01, risk_pct=5.0)  # 1% ATR
    assert not trade.ok
    assert any("zaokruzena na 0" in r for r in trade.rejections)


def test_risk_amount_stays_tiny_in_absolute_terms():
    # 2% od 2.14 su ~4 centa. Mehanika radi; rast je zato spor.
    trade = size(ATR_LOW, risk_pct=2.0)
    assert trade.ok
    assert trade.risk_amount < 0.06


def test_fees_are_a_large_share_of_the_risk_at_this_size():
    # Fee ~0.008 na rizik ~0.037 je ~20% - troskovi dominiraju na malom racunu.
    trade = size(ATR_LOW, risk_pct=2.0)
    assert trade.round_trip_fee / trade.risk_amount > 0.10


@pytest.mark.parametrize("leverage", [3, 5, 10, 20])
def test_leverage_does_not_unlock_the_exchange_minimum(leverage):
    """Veci leverage smanjuje marginu, ali notional dolazi iz rizika i stopa.

    Zato dizanje leveridza ne rjesava premali kapital - to je cesta zabluda.
    """
    assert not size(ATR_LOW, risk_pct=1.0, leverage=leverage).ok


def test_shipped_small_config_is_paper_and_admits_low_atr_pairs():
    cfg = load_config("config.small.yaml")
    assert cfg.mode == "paper"
    assert cfg.screener.min_atr_pct <= 0.29   # inace BTCUSDT bude filtriran
    assert cfg.risk.risk_per_trade_pct >= 2.0  # inace notional ne dosegne 5 USDT
    assert cfg.risk.max_open_positions == 1    # margina zauzima skoro sav kapital
