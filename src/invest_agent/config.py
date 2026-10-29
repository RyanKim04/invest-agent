"""Load and validate the YAML files in `config/`."""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from invest_agent.settings import config_dir


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Company:
    ticker: str
    cik: str
    name: str
    aliases: tuple[str, ...]
    sectors: tuple[str, ...]
    lists: tuple[str, ...]


@dataclass(frozen=True)
class Pipeline:
    chunker_version: str
    embedding_model_key: str
    summary_prompt_version: str
    fact_parser_version: str


def load_universe(path: Path | None = None) -> list[Company]:
    data = _read(path or config_dir() / "universe.yaml")
    companies = [
        Company(
            ticker=c["ticker"],
            cik=str(c["cik"]),
            name=c["name"],
            aliases=tuple(c.get("aliases", [])),
            sectors=tuple(c.get("sectors", [])),
            lists=tuple(c.get("lists", [])),
        )
        for c in data["companies"]
    ]
    for c in companies:
        if not re.fullmatch(r"[0-9]{10}", c.cik):
            raise ConfigError(f"{c.ticker}: cik must be 10 digits, got {c.cik!r}")
    tickers = [c.ticker for c in companies]
    if len(tickers) != len(set(tickers)):
        raise ConfigError("duplicate tickers in universe.yaml")
    return companies


def load_pipeline(path: Path | None = None) -> Pipeline:
    data = _read(path or config_dir() / "pipeline.yaml")
    try:
        return Pipeline(**data)
    except TypeError as e:
        raise ConfigError(f"pipeline.yaml: {e}") from e


def _read(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)
