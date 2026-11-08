"""Regressions of the Copilot review of source indexing.

Each test reproduces an item of ``backlog-copilot.md`` on a minimal fixture
and names the rule it protects.
"""

import os
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from systemlens.delivery.cli import app
from systemlens.domain.graph import url_service_hint
from systemlens.domain.redaction import redact_url_secrets
from systemlens.indexing.file_inventory import list_repo_files
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


# Obviously fake values: the test asserts that they never persist.
FAKE_USERINFO = "user:not-a-secret"
FAKE_TOKEN = "fake-token-value"
FAKE_KEY = "fake-key-value"
APPLICATION = """package demo;
import org.springframework.boot.autoconfigure.SpringBootApplication;
@SpringBootApplication
public class App { public static void main(String[] args) {} }
"""


def test_a_gateway_uri_credential_is_redacted_everywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BC-02: a gateway ``uri`` kept its userinfo and token (invariant 5).

    The YAML and Java DSL snippets lose the userinfo and the secret query
    values, and the userinfo no longer becomes the service hint: the
    gateway still calls ``inventory``.
    """
    repo = tmp_path / "repo"
    _write(repo / "pom.xml", POM.format(
        name="root",
        deps="<packaging>pom</packaging><modules><module>gw</module>"
        "<module>inventory</module></modules>",
    ))
    _write(repo / "gw/pom.xml", POM.format(name="gw", deps=""))
    _write(repo / "inventory/pom.xml", POM.format(name="inventory", deps=""))
    for module in ("gw", "inventory"):
        _write(repo / module / "src/main/java/demo/App.java", APPLICATION)
        _write(
            repo / module / "src/main/resources/application.yml",
            f"spring:\n  application:\n    name: {module}\n",
        )
    with (repo / "gw/src/main/resources/application.yml").open("a") as handle:
        handle.write(
            "  cloud:\n    gateway:\n      routes:\n        - id: inv\n"
            f"          uri: https://{FAKE_USERINFO}@inventory/?token={FAKE_TOKEN}\n"
            "          predicates:\n            - Path=/items/**\n"
        )
    _write(repo / "gw/src/main/java/demo/Routes.java", f"""package demo;
import org.springframework.cloud.gateway.route.RouteLocator;
import org.springframework.cloud.gateway.route.builder.RouteLocatorBuilder;
class Routes {{
  RouteLocator routes(RouteLocatorBuilder b) {{
    return b.routes().route("java", r -> r.path("/stock/**")
        .uri("http://{FAKE_USERINFO}@inventory?api_key={FAKE_KEY}")).build();
  }}
}}
""")
    _write(repo / "inventory/src/main/java/demo/Items.java", """package demo;
import org.springframework.web.bind.annotation.*;
@RestController
class Items {
  @GetMapping("/items/{id}") String item(@PathVariable String id) { return id; }
  @GetMapping("/stock/{id}") String stock(@PathVariable String id) { return id; }
}
""")

    _index(repo)
    with Store(repo) as store:
        gateway = [
            endpoint for endpoint in store.all_endpoints()
            if endpoint.framework == "spring-cloud-gateway"
        ]
        services = {
            (relation.source_name, relation.target_name)
            for relation in store.all_architecture_relations()
            if relation.relation == "calls_service"
        }
    monkeypatch.chdir(repo)
    html = repo / "architecture.html"
    exported = CliRunner().invoke(app, ["export", "microservices", "--html", str(html)])

    assert len(gateway) == 4
    assert all("<redacted>" in endpoint.snippet for endpoint in gateway)
    assert services == {("gw", "inventory")}
    assert exported.exit_code == 0
    with sqlite3.connect(repo / ".systemlens/findings.db") as connection:
        dump = "\n".join(connection.iterdump())
    for secret in ("not-a-secret", FAKE_TOKEN, FAKE_KEY):
        assert secret not in dump
        assert secret not in html.read_text(encoding="utf-8")


def test_url_redaction_keeps_placeholders_and_hints_skip_userinfo() -> None:
    """BC-02: the shared helper and the host hint, shape by shape."""
    assert url_service_hint("https://admin:pw@inventory/x") == "inventory"
    assert redact_url_secrets("lb://u:p@orders/a?password=x&page=2") == (
        "lb://orders/a?password=<redacted>&page=2"
    )
    assert redact_url_secrets("http://h/a?token=${app.token}&key={key}") == (
        "http://h/a?token=${app.token}&key={key}"
    )
    assert redact_url_secrets('"http://" + user + "@" + host') == (
        '"http://" + user + "@" + host'
    )


def test_a_latin1_comment_in_a_parameter_list_keeps_its_integration_methods(
    tmp_path: Path,
) -> None:
    """BC-03: parameter text was decoded as strict UTF-8.

    One Latin-1 byte in a comment of a method or constructor parameter list
    aborted the index; it now decodes with replacement, like node text.
    """
    _write(tmp_path / "svc/pom.xml", POM.format(name="svc", deps=""))
    java = tmp_path / "svc/src/main/java/demo/Latin.java"
    java.parent.mkdir(parents=True)
    java.write_bytes(
        b"package demo;\n"
        b"import org.springframework.web.bind.annotation.GetMapping;\n"
        b"import org.springframework.web.bind.annotation.RestController;\n"
        b"@RestController\n"
        b"public class Latin {\n"
        b"  Latin(String a /* caf\xe9 */) {}\n"
        b'  @GetMapping("/latin")\n'
        b"  public String get(String a /* caf\xe9 */, String b) { return a; }\n"
        b"}\n"
    )

    assert _index(tmp_path) == ["GET /latin"]
    with Store(tmp_path) as store:
        methods = {method.qualified_method for method in store.all_integration_methods()}

    assert {"demo.Latin.get", "demo.Latin.<init>"} <= methods


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="needs POSIX permissions and a non-root user",
)
def test_an_unreadable_file_is_reported_and_the_index_completes(tmp_path: Path) -> None:
    """BC-04: ``sha256_file`` was unguarded in the inventory.

    A ``chmod 000`` file aborted the index with ``PermissionError``. It is
    now left out and reported, like a symbolic link that resolves outside
    the repository root.
    """
    repo = tmp_path / "repo"
    _write(repo / "svc/pom.xml", POM.format(name="svc", deps=""))
    _write(repo / "svc/src/main/java/demo/Ctl.java", CONTROLLER)
    locked = repo / "svc/notes.txt"
    _write(locked, "x")
    outside = tmp_path / "outside.txt"
    _write(outside, "x")
    (repo / "svc/link.txt").symlink_to(outside)
    (repo / "svc/inside.txt").symlink_to(repo / "svc/pom.xml")
    locked.chmod(0)
    messages: list[str] = []
    try:
        unreadable: dict[str, str] = {}
        files = list_repo_files(repo, Config(), unreadable=unreadable)
        clear_analysis_caches()
        with Store(repo) as store:
            index_repo(repo, Config(), store, full=True, progress=messages.append)
            serves = [
                endpoint.topic for endpoint in store.all_endpoints()
                if endpoint.role == "serve"
            ]
    finally:
        locked.chmod(0o644)

    assert set(unreadable) == {"svc/notes.txt", "svc/link.txt"}
    assert "svc/inside.txt" in files and "svc/notes.txt" not in files
    assert serves == ["GET /ping"]
    assert any("svc/notes.txt" in message for message in messages)
    assert any("svc/link.txt" in message for message in messages)
