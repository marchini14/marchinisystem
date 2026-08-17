"""Preflight komanda: mora zaustaviti live start prije naloga, ne poslije."""

import pytest

from marchini.bitget import BitgetClient
from marchini.cli import cmd_check
from marchini.config import Config


def test_paper_mode_reports_and_returns_without_touching_network(capsys):
    # Paper nikad ne salje naloge, pa check ne smije ni pokusati potpisani poziv.
    cmd_check(Config(mode="paper"), BitgetClient())
    out = capsys.readouterr().out
    assert "mode=paper" in out
    assert "NE salju" in out


def test_live_mode_without_credentials_exits_nonzero(capsys):
    with pytest.raises(SystemExit) as exc:
        cmd_check(Config(mode="live"), BitgetClient())
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "BITGET_API_SECRET" in out
    assert "BITGET_API_PASSPHRASE" in out


def test_live_mode_with_only_api_key_still_exits(capsys):
    # Tacno slucaj "imam samo API key" - mora pasti, ne poslati nalog.
    with pytest.raises(SystemExit):
        cmd_check(Config(mode="live"), BitgetClient("samo_key", "", ""))
    assert "BITGET_API_SECRET" in capsys.readouterr().out


def test_check_names_product_type_it_would_trade(capsys):
    cmd_check(Config(mode="paper"), BitgetClient(demo=True))
    assert "SUSDT-FUTURES" in capsys.readouterr().out


def test_signed_call_failure_exits_before_any_order(capsys, monkeypatch):
    """Ako racun ne moze da se procita, check staje - ne ide dalje ka trejdu."""
    client = BitgetClient("k", "s", "p")

    def boom() -> float:
        raise RuntimeError("40037 Apikey does not exist")

    monkeypatch.setattr(client, "available_balance", boom)
    with pytest.raises(SystemExit) as exc:
        cmd_check(Config(mode="live"), client)
    assert exc.value.code == 1
    assert "40037" in capsys.readouterr().out
