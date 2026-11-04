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


def test_init_default_profile_selects_the_source_engine(tmp_path: Path) -> None:
    config = load_config(init_config(tmp_path).parent.parent)

    assert config.call_graph_engine == "source"
    assert config.codeql_enabled is False


def test_init_codeql_profile_selects_the_codeql_engine(tmp_path: Path) -> None:
    path = init_config(tmp_path, "codeql")
    config = load_config(tmp_path)

    assert "codeql: true" in path.read_text()
    assert config.call_graph_engine == "codeql"
    assert config.codeql_enabled is True
    assert config.strategy == "default", "a profile only chooses the engine"


def test_init_rejects_an_unknown_profile_without_writing(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Profil d'initialisation invalide"):
        init_config(tmp_path, "fast")

    assert not (tmp_path / ".systemlens" / "config.yml").exists()


def test_init_command_takes_a_profile_and_warns_without_codeql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from systemlens.delivery import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)
    result = CliRunner().invoke(cli.app, ["init", "--profile", "codeql"])

    assert result.exit_code == 0, result.output
    assert "(profil codeql)" in result.output
    assert "CLI codeql introuvable" in result.output
    assert load_config(tmp_path).call_graph_engine == "codeql"


def test_init_command_rejects_an_unknown_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from systemlens.delivery import cli

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.app, ["init", "--profile", "fast"])

    assert result.exit_code == 2
    assert "Profil d'initialisation invalide" in result.output
    assert not (tmp_path / ".systemlens" / "config.yml").exists()
