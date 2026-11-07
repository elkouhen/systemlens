"""The CLI side of the engine seam (ADR-85).

Regressions of findings C2 to C5 and J6 of the 2026-11-06 review
(``BACKLOG.md``, section 3.4).
"""

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from systemlens.application.doctor import run_doctor
from systemlens.delivery import cli
from systemlens.engines.joern import declaration as joern_declaration
from systemlens.indexing.service import index_repo
from systemlens.infrastructure.config import Config, ConfigError, load_config
from systemlens.storage.sqlite import Store

POM = (
    "<project><modelVersion>4.0.0</modelVersion><groupId>example</groupId>"
    "<artifactId>orders</artifactId><version>1.0</version></project>"
)


def _config(root: Path, text: str) -> None:
    path = root / ".systemlens" / "config.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _indexed(root: Path) -> Path:
    source = root / "src/main/java/example/Orders.java"
    source.parent.mkdir(parents=True)
    (root / "pom.xml").write_text(POM, encoding="utf-8")
    source.write_text(
        "package example;\nclass Orders {\n  void a() { b(); }\n  void b() { }\n}\n",
        encoding="utf-8",
    )
    with Store(root) as store:
        index_repo(root, Config(), store, full=True)
    return root


def test_call_edge_with_a_database_reports_a_broken_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review C3: a broken ``config.yml`` became "`source` n'en accepte pas"."""
    repo = _indexed(tmp_path / "repo")
    _config(repo, "analysis:\n  engine_threads: many\n")
    monkeypatch.chdir(repo)

    result = CliRunner().invoke(cli.app, [
        "analyze", "call-edge", "example.Orders.a", "example.Orders.b",
        "--engine-database", str(tmp_path / "db"), "--call-graph-engine", "codeql",
    ])

    assert result.exit_code == 2, result.output
    assert "engine_threads" in result.output
    assert "n'en accepte pas" not in result.output


def test_an_engines_section_must_be_a_mapping(tmp_path: Path) -> None:
    """Review C4: ``analysis.engines: []`` loaded as an empty section."""
    _config(tmp_path, "analysis:\n  engines: []\n")

    with pytest.raises(ConfigError, match="analysis.engines doit être un objet YAML"):
        load_config(tmp_path)


def test_engine_option_errors_end_with_one_period(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review C4: an unknown option ended its message with ``..``."""
    _config(tmp_path, "analysis:\n  call_graph_engine: source\n")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli.app, ["index", "--engine-option", "foo=1"])

    assert result.exit_code == 2, result.output
    assert "Option inconnue pour le moteur source" in result.output
    assert ".." not in result.output


def test_compare_engines_offers_only_engines_it_can_compare() -> None:
    """Review C4: the help listed ``none``, which the command refuses."""
    result = CliRunner().invoke(
        cli.app, ["analyze", "compare-engines", "--help"], env={"COLUMNS": "200"},
    )

    assert result.exit_code == 0
    help_text = " ".join(result.output.split())
    assert "parmi source, codeql, joern." in help_text
    assert "none" not in help_text


def test_doctor_checks_the_configured_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review C5: ``analyze call-edge`` sends the user to ``doctor`` for an
    unavailable engine, but ``doctor`` did not check engines."""
    _config(tmp_path, "analysis:\n  call_graph_engine: codeql\n")
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    checks = {check.name: check for check in run_doctor(tmp_path)}

    engine = checks["moteur de graphe d'appel"]
    assert engine.status == "warning"
    assert "codeql" in engine.detail and "source" in engine.detail


def test_doctor_reports_an_available_engine(tmp_path: Path) -> None:
    _config(tmp_path, "analysis:\n  call_graph_engine: source\n")

    checks = {check.name: check for check in run_doctor(tmp_path)}

    assert checks["moteur de graphe d'appel"].status == "ok"


def test_a_missing_greadlink_names_the_link_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review J6: coreutils installed but not linked still lacks ``greadlink``."""
    monkeypatch.setattr(joern_declaration.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        joern_declaration.shutil, "which",
        lambda name: None if name == "greadlink" else f"/usr/bin/{name}",
    )

    reason = joern_declaration.unavailable_reason()

    assert reason is not None
    assert "brew install coreutils" in reason and "brew link coreutils" in reason


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--engine-option", "nosep"], "`--engine-option` attend NOM=VALEUR, reçu 'nosep'."),
        (
            ["--call-graph-engine", "codeql", "--engine-option", "threads=2"],
            "--engine-option codeql.threads invalide : Option inconnue pour le moteur codeql",
        ),
        (
            ["--call-graph-engine", "codeql", "--engine-option", "verbosity=loud"],
            "--engine-option codeql.verbosity invalide : valeurs autorisées : errors,",
        ),
        (
            ["--call-graph-engine", "joern", "--engine-option", "database=db"],
            "--engine-option joern.database invalide : Option inconnue pour le moteur joern",
        ),
        (
            ["--call-graph-engine", "joern", "--engine-database", "db"],
            "`--engine-database` requiert un moteur qui accepte une base ; `joern` n'en accepte pas.",
        ),
        (
            ["--call-graph-engine", "source", "--engine-database", "db"],
            "`source` n'en accepte pas.",
        ),
    ],
)
def test_index_refuses_an_engine_option_the_selected_engine_does_not_declare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str], message: str,
) -> None:
    """Review C2: the CLI side of ``--engine-option`` and ``--engine-database``."""
    _config(tmp_path, "analysis:\n  call_graph_engine: source\n")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli.app, ["index", *arguments])

    assert result.exit_code == 2, result.output
    assert message in " ".join(result.output.split())
    assert not (tmp_path / ".systemlens" / "findings.db").exists()


def test_index_accepts_a_declared_engine_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(tmp_path, "analysis:\n  call_graph_engine: source\n")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli.app, [
        "index", "--call-graph-engine", "codeql", "--engine-option", "verbosity=errors",
    ])

    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("strategy", ["default", "strategy1"])
@pytest.mark.parametrize("engine", ["source", "codeql", "joern"])
def test_init_writes_the_chosen_engine_and_strategy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: str, strategy: str,
) -> None:
    """PDR-13: every engine and strategy combination gets a ready config."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    result = CliRunner().invoke(
        cli.app, ["init", "--engine", engine, "--strategy", strategy]
    )

    assert result.exit_code == 0, result.output
    assert f"(moteur {engine}, stratégie {strategy})" in result.output
    text = (tmp_path / ".systemlens" / "config.yml").read_text(encoding="utf-8")
    assert f"strategy: {strategy}\n" in text
    assert f"call_graph_engine: {engine}\n" in text
    config = load_config(tmp_path)
    assert (config.call_graph_engine, config.strategy) == (engine, strategy)
    expected = {
        "source": ({}, None),
        "codeql": ({"codeql": {"database": None, "verbosity": None}}, "CLI codeql introuvable"),
        "joern": ({}, "Joern introuvable"),
    }[engine]
    assert config.engine_options == expected[0]
    if expected[1] is None:
        assert "introuvable" not in result.output
    else:
        assert expected[1] in result.output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--engine", "fast"], "Moteur invalide"),
        (["--engine", "none"], "Moteur invalide"),
        (["--strategy", "fast"], "Stratégie invalide"),
    ],
)
def test_init_rejects_an_unknown_value_with_exit_code_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], message: str,
) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli.app, ["init", *args])

    assert result.exit_code == 2
    assert message in result.output
    assert not (tmp_path / ".systemlens" / "config.yml").exists()


def test_init_no_longer_accepts_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PDR-13 removes ``--profile`` with no alias."""
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli.app, ["init", "--profile", "codeql"])

    assert result.exit_code == 2
    assert "--profile" in result.output
    assert not (tmp_path / ".systemlens" / "config.yml").exists()


def test_init_never_overwrites_an_existing_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    _config(tmp_path, "analysis: {}\n")

    result = CliRunner().invoke(cli.app, ["init", "--engine", "codeql"])

    assert result.exit_code == 1
    assert (tmp_path / ".systemlens" / "config.yml").read_text() == "analysis: {}\n"
