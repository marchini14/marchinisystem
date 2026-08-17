import pytest
import yaml

from marchini.config import Config, load_config


def write(tmp_path, data):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_defaults_are_valid():
    Config().validate()


def test_load_reads_nested_sections(tmp_path):
    path = write(tmp_path, {"mode": "paper", "equity": 250, "risk": {"leverage": 3}})
    cfg = load_config(path)
    assert cfg.equity == 250
    assert cfg.risk.leverage == 3
    assert cfg.strategy.timeframe == "1H"  # default ostaje


def test_unknown_keys_are_ignored(tmp_path):
    path = write(tmp_path, {"risk": {"leverage": 3, "izmisljeni_kljuc": 1}})
    assert load_config(path).risk.leverage == 3


def test_bad_mode_rejected(tmp_path):
    with pytest.raises(ValueError, match="mode"):
        load_config(write(tmp_path, {"mode": "turbo"}))


def test_target_must_exceed_stop(tmp_path):
    # R:R ispod 1 ne moze biti profitabilan na duge staze - config to mora odbiti.
    with pytest.raises(ValueError, match="atr_target_mult"):
        load_config(write(tmp_path, {"strategy": {"atr_stop_mult": 3.0, "atr_target_mult": 2.0}}))


def test_negative_risk_rejected(tmp_path):
    with pytest.raises(ValueError, match="risk_per_trade_pct"):
        load_config(write(tmp_path, {"risk": {"risk_per_trade_pct": -1}}))


def test_too_fast_polling_rejected(tmp_path):
    with pytest.raises(ValueError, match="poll_seconds"):
        load_config(write(tmp_path, {"runtime": {"poll_seconds": 5}}))
