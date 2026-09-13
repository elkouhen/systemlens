from pathlib import Path

import pytest

from systemlens.infrastructure.config import ConfigError, init_config, load_config


def test_init_config_uses_generic_strategy_key(tmp_path: Path) -> None:
    path = init_config(tmp_path)

    assert "strategy: default" in path.read_text()
    assert "topic_strategy" not in path.read_text()


def test_load_config_accepts_legacy_topic_strategy_key(tmp_path: Path) -> None:
    state = tmp_path / ".systemlens"
    state.mkdir()
    (state / "config.yml").write_text("analysis:\n  topic_strategy: strategy1\n")

    assert load_config(tmp_path).strategy == "strategy1"


def test_load_config_rejects_ambiguous_strategy_keys(tmp_path: Path) -> None:
    state = tmp_path / ".systemlens"
    state.mkdir()
    (state / "config.yml").write_text(
        "analysis:\n  strategy: strategy1\n  topic_strategy: default\n"
    )

    with pytest.raises(ConfigError, match="ne peuvent pas être utilisés ensemble"):
        load_config(tmp_path)
