import systemlens
from typer.testing import CliRunner

from systemlens.cli import app


def test_version() -> None:
    assert systemlens.__version__ == "0.1.0"


def test_export_help_uses_cluster_vocabulary() -> None:
    result = CliRunner().invoke(app, ["export", "--help"])

    assert result.exit_code == 0
    assert "clusters" in result.stdout
    assert "namespaces" not in result.stdout
