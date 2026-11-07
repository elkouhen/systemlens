from pathlib import Path

import pytest

from systemlens.infrastructure.config import ConfigError, init_config, load_config


def test_init_config_uses_generic_strategy_and_engine_keys(tmp_path: Path) -> None:
    text = init_config(tmp_path).read_text()

    assert "strategy: default" in text
    assert "topic_strategy" not in text
    assert "engine_threads: 0" in text
    assert "engine_timeout_seconds: 600" in text
    assert "engine_edge_confidence: possible" in text
    analysis = text[text.index("analysis:"):]
    assert "codeql" not in analysis, "the default profile names no external engine"


def test_load_config_accepts_legacy_topic_strategy_key(tmp_path: Path) -> None:
    state = tmp_path / ".systemlens"
    state.mkdir()
    (state / "config.yml").write_text("analysis:\n  topic_strategy: strategy1\n")

    assert load_config(tmp_path).strategy == "strategy1"


def test_load_config_reads_generic_engine_settings(tmp_path: Path) -> None:
    state = tmp_path / ".systemlens"
    state.mkdir()
    (state / "config.yml").write_text(
        "analysis:\n  engine_timeout_seconds: 1200\n  engine_threads: 0\n"
        "  engine_ram_mb: 4096\n  engine_max_hops: 5\n  engine_edge_confidence: exact\n"
    )

    config = load_config(tmp_path)

    assert config.engine_timeout_seconds == 1200
    assert config.engine_threads == 0
    assert config.engine_ram_mb == 4096
    assert config.engine_max_hops == 5
    assert config.engine_edge_confidence == "exact"


def test_load_config_reads_engine_owned_options(tmp_path: Path) -> None:
    state = tmp_path / ".systemlens"
    state.mkdir()
    (state / "config.yml").write_text(
        "analysis:\n  engines:\n    codeql:\n      verbosity: progress+++\n"
        "      database: .codeql/db\n"
    )

    config = load_config(tmp_path)
    settings = config.engine_settings("codeql", repo_root=tmp_path)

    assert config.engine_options == {
        "codeql": {"verbosity": "progress+++", "database": ".codeql/db"},
    }
    assert settings.options["verbosity"] == "progress+++"
    assert settings.options["database"] == tmp_path / ".codeql/db"


@pytest.mark.parametrize(
    ("analysis", "message"),
    [
        ("codeql: true", "Clé\\(s\\) inconnue\\(s\\) dans analysis : codeql"),
        ("codeql_timeout_seconds: 10", "codeql_timeout_seconds"),
        ("engines:\n    javaparser: {}", "moteur inconnu"),
        ("engines:\n    codeql:\n      threads: 2", "Option inconnue pour le moteur codeql"),
        ("engines:\n    codeql:\n      verbosity: loud", "valeurs autorisées"),
        ("engine_threads: -1", "engine_threads"),
    ],
)
def test_load_config_rejects_removed_keys_and_invalid_engine_options(
    tmp_path: Path, analysis: str, message: str,
) -> None:
    state = tmp_path / ".systemlens"
    state.mkdir()
    (state / "config.yml").write_text(f"analysis:\n  {analysis}\n")

    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path)


def test_load_config_rejects_ambiguous_strategy_keys(tmp_path: Path) -> None:
    state = tmp_path / ".systemlens"
    state.mkdir()
    (state / "config.yml").write_text(
        "analysis:\n  strategy: strategy1\n  topic_strategy: default\n"
    )

    with pytest.raises(ConfigError, match="ne peuvent pas être utilisés ensemble"):
        load_config(tmp_path)


def test_init_defaults_to_the_source_engine(tmp_path: Path) -> None:
    config = load_config(init_config(tmp_path).parent.parent)

    assert config.call_graph_engine == "source"
    assert config.strategy == "default"
    assert config.engine_options == {}


def test_init_codeql_engine_writes_its_defaults(tmp_path: Path) -> None:
    path = init_config(tmp_path, "codeql")
    config = load_config(tmp_path)

    assert "call_graph_engine: codeql" in path.read_text()
    assert config.call_graph_engine == "codeql"
    assert config.engine_options == {"codeql": {"database": None, "verbosity": None}}
    assert config.strategy == "default", "the engine does not choose the strategy"


@pytest.mark.parametrize(("engine", "strategy"), [("fast", "default"), ("codeql", "fast")])
def test_init_rejects_an_unknown_value_without_writing(
    tmp_path: Path, engine: str, strategy: str,
) -> None:
    with pytest.raises(ConfigError, match="invalide"):
        init_config(tmp_path, engine, strategy)

    assert not (tmp_path / ".systemlens" / "config.yml").exists()
