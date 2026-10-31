import subprocess
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest

from systemlens.application.engine_comparison import (
    compare_engines,
    render_engine_comparison_text,
)
from systemlens.callgraph.engine import EngineRequest
from systemlens.callgraph.model import (
    MethodCall,
    MethodReachability,
    PayloadTypeEvidence,
    SourceMethod,
)
from systemlens.callgraph.registry import load_engine
from systemlens.callgraph.source import SourceEngine
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.engines import codeql
from systemlens.indexing.service import index_repo
from systemlens.infrastructure.config import Config, ConfigError, init_config, load_config
from systemlens.storage.sqlite import Store


def test_the_core_never_imports_the_codeql_adapter() -> None:
    script = (
        "import sys\n"
        "import systemlens.indexing.service, systemlens.indexing.code_flows\n"
        "import systemlens.application.flow_calculation, systemlens.application.engine_comparison\n"
        "import systemlens.delivery.cli, systemlens.render\n"
        "assert 'systemlens.engines.codeql' not in sys.modules, 'core imported CodeQL'\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_registry_loads_engines_by_name() -> None:
    assert load_engine("none") is None
    assert isinstance(load_engine("source"), SourceEngine)
    engine = load_engine("codeql", database=Path("/db"), threads=2, ram_mb=512)
    assert isinstance(engine, codeql.CodeQLEngine)
    assert (engine.database, engine.threads, engine.ram_mb) == (Path("/db"), 2, 512)
    assert engine.available()
    with pytest.raises(ValueError, match="inconnu"):
        load_engine("javaparser")


def test_configuration_accepts_the_source_engine(tmp_path: Path) -> None:
    init_config(tmp_path)
    config_file = tmp_path / ".systemlens" / "config.yml"
    text = config_file.read_text(encoding="utf-8")
    config_file.write_text(text.replace("call_graph_engine: codeql", "call_graph_engine: source"), encoding="utf-8")
    assert load_config(tmp_path).call_graph_engine == "source"

    config_file.write_text(
        text.replace("codeql: true", "codeql: false").replace("  call_graph_engine: codeql\n", ""),
        encoding="utf-8",
    )
    # ``codeql: false`` historically kept the source symbol pass.
    assert load_config(tmp_path).call_graph_engine == "source"

    config_file.write_text(text.replace("call_graph_engine: codeql", "call_graph_engine: javaparser"), encoding="utf-8")
    with pytest.raises(ConfigError, match="call_graph_engine"):
        load_config(tmp_path)


def _repository(root: Path) -> Path:
    source = root / "src/main/java/example"
    source.mkdir(parents=True)
    (root / "pom.xml").write_text(
        "<project><modelVersion>4.0.0</modelVersion><groupId>example</groupId>"
        "<artifactId>orders</artifactId><version>1.0</version></project>",
        encoding="utf-8",
    )
    (source / "OrderController.java").write_text(
        """package example;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/orders")
class OrderController {
  private Generator generator;
  @PostMapping("/generate") public void create() { generator.generate(); }
}
""",
        encoding="utf-8",
    )
    (source / "Generator.java").write_text(
        """package example;
import org.springframework.kafka.core.KafkaTemplate;
class Generator {
  private KafkaTemplate<Long, String> template;
  void generate() { template.send("orders", "new"); }
}
""",
        encoding="utf-8",
    )
    return root


def test_an_unavailable_engine_falls_back_to_the_source_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(tmp_path / "repo")
    monkeypatch.setattr(codeql, "codeql_executable", lambda: None)
    messages: list[str] = []

    with Store(repo) as store:
        index_repo(repo, Config(), store, full=True, progress=messages.append)
        flows = store.all_code_flows()
        edges = store.all_call_edges()

    assert any("CodeQL indisponible ; moteur source (tree-sitter) utilisé" in item for item in messages)
    assert [step.kind for step in flows[0].steps] == ["http_entry", "method_call", "message_publish"]
    assert edges, "the source engine persists its call graph like any engine"


class _RecordedEngine:
    """An engine answering with fixed calls, like a typed analyzer would."""

    name = "recorded"
    label = "Recorded"
    supports_module_refresh = False
    resolves_types = True

    def __init__(self, calls: list[MethodCall]) -> None:
        self._calls = calls
        self.requests: list[EngineRequest] = []

    def available(self) -> bool:
        return True

    @contextmanager
    def open(self, request: EngineRequest) -> Iterator["_RecordedEngine"]:
        self.requests.append(request)
        yield self

    def calls(self) -> list[MethodCall]:
        return self._calls

    def methods(self, source_prefix: str = "") -> list[SourceMethod]:
        return []

    def reachability(self, methods: Sequence[IntegrationMethod]) -> list[MethodReachability]:
        return []

    def payload_types(self) -> list[PayloadTypeEvidence]:
        return []


def test_an_injected_engine_drives_flow_construction(tmp_path: Path) -> None:
    repo = _repository(tmp_path / "repo")
    engine = _RecordedEngine([MethodCall(
        "example.OrderController.create", "src/main/java/example/OrderController.java", 7,
        "example.Generator.generate", "src/main/java/example/Generator.java", 5, 7,
    )])

    with Store(repo) as store:
        index_repo(repo, Config(), store, full=True, call_graph_engine=engine)
        flows = store.all_code_flows()

    [request] = engine.requests
    assert request.repo_root == repo
    assert "src/main/java/example/Generator.java" in request.allowed_paths
    assert [step.name for step in flows[0].steps][1:] == ["example.Generator.generate", "orders"]
    assert flows[0].confidence == "medium"
    assert "The call graph resolved a static call path" in flows[0].reason


def test_engine_comparison_reports_shared_and_missing_flows(tmp_path: Path) -> None:
    repo = _repository(tmp_path / "repo")
    with Store(repo) as store:
        index_repo(repo, Config(call_graph_engine="source"), store, full=True)

    with Store(repo, readonly=True) as store:
        result = compare_engines(store, [SourceEngine(), _RecordedEngine([MethodCall(
            "example.OrderController.create", "src/main/java/example/OrderController.java", 7,
            "example.Generator.generate", "src/main/java/example/Generator.java", 5, 7,
        )])])
        with pytest.raises(ValueError, match="distincts"):
            compare_engines(store, [SourceEngine(), SourceEngine()])

    assert result["engines"] == ["source", "recorded"]
    flows = result["flows"]
    assert isinstance(flows, dict)
    assert flows["both"] == 1
    assert flows["only_source_count"] == 0 and flows["only_recorded_count"] == 0
    runs = result["runs"]
    assert isinstance(runs, dict)
    assert runs["source"]["flows"] == 1 and runs["recorded"]["calls"] == 1
    text = render_engine_comparison_text(result)
    assert "Flux : 1 communs, 0 seulement source, 0 seulement recorded." in text
    with Store(repo, readonly=True) as store:
        assert len(store.all_code_flows()) == 1, "comparison never persists flows"
