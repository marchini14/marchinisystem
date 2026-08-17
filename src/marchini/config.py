"""Ucitavanje i validacija config.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ScreenerConfig:
    min_volume_24h: float = 5_000_000
    max_change_24h_pct: float = 25.0
    min_atr_pct: float = 1.0
    max_abs_funding: float = 0.0005
    top_n: int = 12
    blacklist: list[str] = field(default_factory=list)


@dataclass
class StrategyConfig:
    timeframe: str = "1H"
    donchian_lookback: int = 20
    ema_trend: int = 50
    atr_period: int = 14
    atr_stop_mult: float = 2.0
    atr_target_mult: float = 3.0
    allow_short: bool = True


@dataclass
class RiskConfig:
    leverage: int = 5
    risk_per_trade_pct: float = 1.0
    max_open_positions: int = 3
    daily_loss_limit_pct: float = 5.0
    max_fee_share_of_target_pct: float = 15.0
    min_liq_distance_vs_stop: float = 1.5


@dataclass
class RuntimeConfig:
    poll_seconds: int = 300
    state_file: str = "state/bot_state.json"
    log_file: str = "logs/bot.log"


@dataclass
class Config:
    mode: str = "paper"
    equity: float = 100.0
    screener: ScreenerConfig = field(default_factory=ScreenerConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)

    def validate(self) -> None:
        errors: list[str] = []
        if self.mode not in ("paper", "live"):
            errors.append(f"mode mora biti 'paper' ili 'live', ne '{self.mode}'")
        if self.equity <= 0:
            errors.append("equity mora biti > 0")
        if not 0 < self.risk.risk_per_trade_pct <= 100:
            errors.append("risk_per_trade_pct mora biti u (0, 100]")
        if self.risk.leverage < 1:
            errors.append("leverage mora biti >= 1")
        if self.risk.max_open_positions < 1:
            errors.append("max_open_positions mora biti >= 1")
        if self.strategy.atr_target_mult <= self.strategy.atr_stop_mult:
            errors.append(
                "atr_target_mult mora biti veci od atr_stop_mult, inace je "
                "R:R ispod 1 i strategija matematicki ne moze biti profitabilna"
            )
        if self.runtime.poll_seconds < 30:
            errors.append("poll_seconds < 30 rizikuje rate limit na Bitget API")
        if errors:
            raise ValueError("Greske u konfiguraciji:\n  - " + "\n  - ".join(errors))


def _section(raw: dict[str, Any], key: str, cls: type) -> Any:
    data = raw.get(key) or {}
    known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
    return cls(**known)


def load_config(path: str | Path = "config.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    cfg = Config(
        mode=str(raw.get("mode", "paper")),
        equity=float(raw.get("equity", 100.0)),
        screener=_section(raw, "screener", ScreenerConfig),
        strategy=_section(raw, "strategy", StrategyConfig),
        risk=_section(raw, "risk", RiskConfig),
        runtime=_section(raw, "runtime", RuntimeConfig),
    )
    cfg.validate()
    return cfg


def credentials() -> tuple[str, str, str]:
    return (
        os.getenv("BITGET_API_KEY", ""),
        os.getenv("BITGET_API_SECRET", ""),
        os.getenv("BITGET_API_PASSPHRASE", ""),
    )
