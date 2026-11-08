"""Regressions of the Copilot review of source indexing.

Each test reproduces an item of ``backlog-copilot.md`` on a minimal fixture
and names the rule it protects.
"""

from pathlib import Path

from systemlens.indexing.service import index_repo
from systemlens.infrastructure.config import Config
from systemlens.scanner import clear_analysis_caches
from systemlens.storage.sqlite import Store

POM = (
    "<project><modelVersion>4.0.0</modelVersion><groupId>example</groupId>"
    "<artifactId>{name}</artifactId><version>1.0.0</version>{deps}</project>"
)
CONTROLLER = """package demo;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
@RestController
class Ctl {
  @GetMapping("/ping")
  String ping() { return "pong"; }
}
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _index(repo: Path) -> list[str]:
    clear_analysis_caches()
    with Store(repo) as store:
        index_repo(repo, Config(), store, full=True)
        return sorted(
            endpoint.topic for endpoint in store.all_endpoints() if endpoint.role == "serve"
        )


def test_an_impossible_yaml_date_does_not_abort_the_index(tmp_path: Path) -> None:
    """BC-01: PyYAML raises ``ValueError`` for ``2023-02-30``.

    Every YAML loader of the extractors treats such a file as unreadable
    (``extractors.md``: a bad file never aborts the index), and a
    ``.properties`` file no longer reaches the gateway YAML parser.
    """
    for name in ("application.yml", "application.properties"):
        repo = tmp_path / name
        _write(repo / "svc/pom.xml", POM.format(name="svc", deps=""))
        _write(repo / "svc/src/main/java/demo/Ctl.java", CONTROLLER)
        separator = ": " if name.endswith(".yml") else "="
        _write(
            repo / "svc/src/main/resources" / name,
            f"build-date{separator}2023-02-30\n",
        )
        _write(
            repo / "svc/src/main/resources/openapi.yml",
            "openapi: 3.0.0\ninfo: {version: 2023-02-30}\npaths: {}\n",
        )

        assert _index(repo) == ["GET /ping"], name
