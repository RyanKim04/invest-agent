from pathlib import Path

import pytest

from invest_agent.config import ConfigError, load_pipeline, load_universe

CONFIG = Path(__file__).resolve().parents[1] / "config"


def test_repo_universe_loads():
    tickers = [c.ticker for c in load_universe(CONFIG / "universe.yaml")]
    assert tickers == ["GOOG", "META", "RDDT"]


def test_repo_pipeline_loads():
    assert load_pipeline(CONFIG / "pipeline.yaml").chunker_version


def test_short_cik_is_rejected(tmp_path: Path):
    path = tmp_path / "universe.yaml"
    path.write_text("companies:\n  - {ticker: X, cik: 123, name: X}\n")
    with pytest.raises(ConfigError, match="10 digits"):
        load_universe(path)


def test_unknown_pipeline_key_is_rejected(tmp_path: Path):
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        "chunker_version: a\nembedding_model_key: b\nsummary_prompt_version: c\n"
        "fact_parser_version: d\ntypo_key: e\n"
    )
    with pytest.raises(ConfigError):
        load_pipeline(path)
