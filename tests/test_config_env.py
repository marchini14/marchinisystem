"""MARCHINI_CONFIG: izbor configa runtime varijablom (za Docker/Northflank)."""

from marchini.cli import main


def _capture_config(monkeypatch) -> dict:
    """Presretni load_config da vidimo koju putanju CLI stvarno otvori."""
    seen: dict = {}

    def fake_load(path):
        seen["path"] = path
        raise SystemExit(0)  # zaustavi prije mrezne aktivnosti

    monkeypatch.setattr("marchini.cli.load_config", fake_load)
    return seen


def test_defaults_to_config_yaml(monkeypatch):
    monkeypatch.delenv("MARCHINI_CONFIG", raising=False)
    seen = _capture_config(monkeypatch)
    try:
        main(["screen"])
    except SystemExit:
        pass
    assert seen["path"] == "config.yaml"


def test_env_var_selects_config(monkeypatch):
    monkeypatch.setenv("MARCHINI_CONFIG", "config.small.yaml")
    seen = _capture_config(monkeypatch)
    try:
        main(["screen"])
    except SystemExit:
        pass
    assert seen["path"] == "config.small.yaml"


def test_explicit_flag_overrides_env_var(monkeypatch):
    # Eksplicitni -c mora pobijediti okolinu, inace lokalni debug postaje zamka.
    monkeypatch.setenv("MARCHINI_CONFIG", "config.small.yaml")
    seen = _capture_config(monkeypatch)
    try:
        main(["screen", "-c", "config.demo.yaml"])
    except SystemExit:
        pass
    assert seen["path"] == "config.demo.yaml"
