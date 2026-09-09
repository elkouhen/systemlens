import systemlens
from typer.testing import CliRunner

from systemlens.cli import app


def test_version() -> None:
    assert systemlens.__version__ == "0.1.0"


def test_export_help_uses_module_and_project_vocabulary() -> None:
    result = CliRunner().invoke(app, ["export", "--help"])

    assert result.exit_code == 0
    assert "modules" in result.stdout
    assert "projects" in result.stdout
    assert "clusters" not in result.stdout
    assert "namespaces" not in result.stdout

    root = CliRunner().invoke(app, ["--help"])
    assert root.exit_code == 0
    assert "projects" in root.stdout
    assert "modules" not in root.stdout
