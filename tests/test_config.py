from pathlib import Path

import pytest

from systemlens.infrastructure.config import ConfigError, init_config, load_config


def test_init_config_uses_generic_strategy_key(tmp_path: Path) -> None:
    path = init_config(tmp_path)

    assert "strategy: default" in path.read_text()
    assert "topic_strategy" not in path.read_text()
    assert "codeql_threads: 0" in path.read_text()
    assert "codeql_verbosity: null" in path.read_text()


def test_load_config_accepts_legacy_topic_strategy_key(tmp_path: Path) -> None:
    state = tmp_path / ".systemlens"
    state.mkdir()
    (state / "config.yml").write_text("analysis:\n  topic_strategy: strategy1\n")

    assert load_config(tmp_path).strategy == "strategy1"


def test_load_config_reads_codeql_timeout_seconds(tmp_path: Path) -> None:
    state = tmp_path / ".systemlens"
    state.mkdir()
    (state / "config.yml").write_text("analysis:\n  codeql_timeout_seconds: 1200\n")

    assert load_config(tmp_path).codeql_timeout_seconds == 1200


def test_load_config_reads_codeql_resource_limits(tmp_path: Path) -> None:
    state = tmp_path / ".systemlens"
    state.mkdir()
    (state / "config.yml").write_text(
        "analysis:\n  codeql_threads: 0\n  codeql_ram_mb: 4096\n"
    )

    config = load_config(tmp_path)

    assert config.codeql_threads == 0
    assert config.codeql_ram_mb == 4096


def test_load_config_reads_codeql_verbosity(tmp_path: Path) -> None:
    state = tmp_path / ".systemlens"
    state.mkdir()
    (state / "config.yml").write_text("analysis:\n  codeql_verbosity: progress+++\n")

    assert load_config(tmp_path).codeql_verbosity == "progress+++"


def test_load_config_rejects_ambiguous_strategy_keys(tmp_path: Path) -> None:
    state = tmp_path / ".systemlens"
    state.mkdir()
    (state / "config.yml").write_text(
        "analysis:\n  strategy: strategy1\n  topic_strategy: default\n"
    )

    with pytest.raises(ConfigError, match="ne peuvent pas être utilisés ensemble"):
        load_config(tmp_path)
