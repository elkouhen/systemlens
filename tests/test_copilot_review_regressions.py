"""Regressions of the Copilot review of source indexing.

Each test reproduces an item of ``backlog-copilot.md`` on a minimal fixture
and names the rule it protects.
"""

import os
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from systemlens.delivery.cli import app
from systemlens.domain.graph import url_service_hint
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.module_inventory import DiscoveredModule
from systemlens.indexing import integration_methods
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


# Lot 6: scanner precision.

KAFKA_IMPORTS = (
    "package demo;\n"
    "import org.springframework.kafka.core.KafkaTemplate;\n"
    "import org.springframework.beans.factory.annotation.Value;\n"
)


def _ports(repo: Path) -> set[tuple[str, str, str, bool]]:
    """Every persisted endpoint as ``(role, framework, topic, dynamic)``."""
    clear_analysis_caches()
    with Store(repo) as store:
        index_repo(repo, Config(), store, full=True)
        return {
            (endpoint.role, endpoint.framework or "", endpoint.topic, endpoint.topic_dynamic)
            for endpoint in store.all_endpoints()
        }


def _service(repo: Path, files: dict[str, str]) -> Path:
    _write(repo / "svc/pom.xml", POM.format(name="svc", deps=""))
    for name, text in files.items():
        _write(repo / "svc" / name, text)
    return repo


def test_profile_conflicting_properties_stay_dynamic(tmp_path: Path) -> None:
    """BC-31: profiles that disagree give no value, default included.

    ``extraction-rules.md`` keeps a key declared with different values in
    profiles, without a base value, dynamic. A placeholder default is not
    the value in any profile, and a multi-document ``application.yml``
    applies the same rule to its ``on-profile`` documents.
    """
    producer = KAFKA_IMPORTS + (
        "public class P {\n"
        "  private KafkaTemplate<String, String> kafkaTemplate;\n"
        '  @Value("${app.topic:orders}") private String topic;\n'
        '  void a() { kafkaTemplate.send(topic, "x"); }\n'
        '  void b() { kafkaTemplate.send("${app.topic:orders}", "x"); }\n'
        '  void c() { kafkaTemplate.send("${app.same}", "x"); }\n'
        '  void d() { kafkaTemplate.send("${app.based}", "x"); }\n'
        "}\n"
    )
    files = _service(tmp_path / "files", {
        "src/main/java/demo/P.java": producer,
        "src/main/resources/application.yml": "app:\n  based: base-topic\n",
        "src/main/resources/application-dev.yml": (
            "app:\n  topic: orders-dev\n  same: same-topic\n  based: dev-topic\n"
        ),
        "src/main/resources/application-prod.yml": (
            "app:\n  topic: orders-prod\n  same: same-topic\n  based: prod-topic\n"
        ),
    })
    documents = _service(tmp_path / "documents", {
        "src/main/java/demo/P.java": producer,
        "src/main/resources/application.yml": (
            "app:\n  based: base-topic\n"
            "---\nspring:\n  config:\n    activate:\n      on-profile: prod\n"
            "app:\n  topic: orders-prod\n  same: same-topic\n  based: prod-topic\n"
            "---\nspring:\n  profiles: dev\n"
            "app:\n  topic: orders-dev\n  same: same-topic\n  based: dev-topic\n"
        ),
    })

    for repo in (files, documents):
        produced = {
            (topic, dynamic) for role, _, topic, dynamic in _ports(repo) if role == "produce"
        }
        assert produced == {
            ("<dynamic>", True),
            ("app.topic", True),
            ("same-topic", False),
            ("base-topic", False),
        }, repo.name


def test_a_value_field_resolves_only_in_its_declaring_class(tmp_path: Path) -> None:
    """BC-32: the repository-wide ``@Value`` table borrowed names.

    A plain ``topic`` field took another class's ``@Value`` topic, and a
    test's ``@Value`` beat a same-file constant. A field the class declares
    resolves from its file only; test sources are not in the table.
    """
    repo = _service(tmp_path, {
        "src/main/java/demo/A.java": KAFKA_IMPORTS + (
            'public class A { @Value("${kafka.orders}") String topic; }\n'
        ),
        "src/main/java/demo/B.java": KAFKA_IMPORTS + (
            "public class B {\n"
            "  private final String topic;\n"
            "  private KafkaTemplate<String, String> kafkaTemplate;\n"
            "  B(String t) { this.topic = t; }\n"
            '  void send() { kafkaTemplate.send(topic, "x"); }\n'
            "}\n"
        ),
        "src/main/java/demo/C.java": KAFKA_IMPORTS + (
            "public class C {\n"
            '  private static final String PAYTOPIC = "constant-topic";\n'
            "  private KafkaTemplate<String, String> kafkaTemplate;\n"
            '  void send() { kafkaTemplate.send(PAYTOPIC, "x"); }\n'
            "}\n"
        ),
        "src/main/java/demo/D.java": KAFKA_IMPORTS + (
            "public class D {\n"
            "  private KafkaTemplate<String, String> kafkaTemplate;\n"
            '  void send() { kafkaTemplate.send(topic, "x"); }\n'
            "}\n"
        ),
        "src/test/java/demo/T.java": KAFKA_IMPORTS + (
            'public class T { @Value("${kafka.pay}") String PAYTOPIC; }\n'
        ),
        "src/main/resources/application.yml": (
            "kafka:\n  orders: orders-topic\n  pay: payments-topic\n"
        ),
    })

    produced = {
        (topic, dynamic) for role, _, topic, dynamic in _ports(repo) if role == "produce"
    }

    # B declares ``topic``; D does not, so it may use A's declaration.
    assert produced == {
        ("<dynamic>", True), ("constant-topic", False), ("orders-topic", False),
    }


def test_feign_joins_url_and_path_and_defaults_nest(tmp_path: Path) -> None:
    """BC-33: Feign dropped ``path`` beside ``url``; nested defaults collapsed.

    The url's path joins ``path``, and an unresolved url placeholder adds
    nothing to a literal ``path``. ``${a:${b}}`` resolves innermost first,
    and an empty default is an empty route prefix, never an empty topic.
    """
    feign = (
        "package demo;\n"
        "import org.springframework.cloud.openfeign.FeignClient;\n"
        "import org.springframework.web.bind.annotation.GetMapping;\n"
        '@FeignClient(name = "{name}", url = "{url}", path = "/api/v1")\n'
        'public interface {type} {{ @GetMapping("/items") String items(); }}\n'
    )
    repo = _service(tmp_path, {
        "src/main/java/demo/F.java": feign.format(name="inv", url="${inv.url}", type="F"),
        "src/main/java/demo/F2.java": feign.format(
            name="inv2", url="${missing.url}", type="F2",
        ),
        "src/main/java/demo/F3.java": feign.format(
            name="inv3", url="http://inventory/${missing.base}", type="F3",
        ),
        "src/main/java/demo/Ctl.java": (
            "package demo;\n"
            "import org.springframework.web.bind.annotation.*;\n"
            "@RestController\n"
            '@RequestMapping("/orders")\n'
            "public class Ctl {\n"
            '  @GetMapping("${x.y:${b.base}}/n") public String n() { return ""; }\n'
            '  @GetMapping("${x.y:${b.missing:/v3}}/m") public String m() { return ""; }\n'
            '  @GetMapping("/p/${x.z:${b.base}}") public String p() { return ""; }\n'
            '  @GetMapping("${server.servlet.context-path:}/c") public String c() { return ""; }\n'
            '  @GetMapping("${x.y:${nothing}}/u") public String u() { return ""; }\n'
            "}\n"
        ),
        "src/main/java/demo/P.java": KAFKA_IMPORTS + (
            "public class P {\n"
            "  private KafkaTemplate<String, String> kafkaTemplate;\n"
            '  void a() { kafkaTemplate.send("${app.topic:}", "x"); }\n'
            "}\n"
        ),
        "src/main/resources/application.yml": (
            "inv:\n  url: http://inventory\nb:\n  base: /v2\n"
        ),
    })

    assert _ports(repo) == {
        ("call", "feign", "GET /api/v1/items", False),
        ("call", "feign", "GET <dynamic>", True),
        ("serve", "spring", "GET /orders/v2/n", False),
        ("serve", "spring", "GET /orders/v3/m", False),
        ("serve", "spring", "GET /orders/p/v2", False),
        ("serve", "spring", "GET /orders/c", False),
        ("serve", "spring", "GET <dynamic>", True),
        ("produce", "spring-kafka", "app.topic", True),
    }


def test_stream_bridge_bindings_and_kafka_streams_receivers(tmp_path: Path) -> None:
    """BC-34: a binding name was a topic; Streams calls matched by name.

    ``streamBridge.send`` names a binding, mapped through its declared
    destination or else dynamic. A Kafka Streams source or sink is
    recognised by the declared type its call chain starts from, so
    ``mapper.to(dto)`` is no producer and ``streamsBuilder.table`` is a
    consumer.
    """
    repo = _service(tmp_path, {
        "src/main/java/demo/S.java": (
            "package demo;\n"
            "import org.springframework.cloud.stream.function.StreamBridge;\n"
            "public class S {\n"
            "  private StreamBridge streamBridge;\n"
            '  void a() { streamBridge.send("orders-out-0", "x"); }\n'
            '  void b() { streamBridge.send("unbound-out-0", "x"); }\n'
            "}\n"
        ),
        "src/main/java/demo/K.java": (
            "package demo;\n"
            "import org.apache.kafka.streams.StreamsBuilder;\n"
            "import org.apache.kafka.streams.kstream.KStream;\n"
            "public class K {\n"
            "  Mapper mapper;\n"
            "  void topo(StreamsBuilder streamsBuilder) {\n"
            '    KStream<String, String> s = streamsBuilder.stream("in-topic");\n'
            '    s.through("through-topic");\n'
            '    streamsBuilder.stream("chained-in").mapValues(v -> v).to("chained-out");\n'
            '    streamsBuilder.table("tbl-topic");\n'
            '    streamsBuilder.globalTable("global-topic");\n'
            "  }\n"
            "  Object conv(Object dto) { return mapper.to(dto); }\n"
            "}\n"
            "class Mapper { Object to(Object o) { return o; } }\n"
        ),
        "src/main/resources/application.yml": (
            "spring:\n  cloud:\n    stream:\n      bindings:\n"
            "        orders-out-0:\n          destination: orders-topic\n"
        ),
    })

    assert _ports(repo) == {
        ("produce", "spring-cloud-stream", "orders-topic", False),
        ("produce", "spring-cloud-stream", "unbound-out-0", True),
        ("consume", "kafka-streams", "in-topic", False),
        ("consume", "kafka-streams", "chained-in", False),
        ("consume", "kafka-streams", "tbl-topic", False),
        ("consume", "kafka-streams", "global-topic", False),
        ("produce", "kafka-streams", "through-topic", False),
        ("produce", "kafka-streams", "chained-out", False),
    }


def test_rest_message_type_comes_only_from_mapped_methods(tmp_path: Path) -> None:
    """BC-35: every serve endpoint was typed from the method around it.

    A gateway route took ``RouteLocatorBuilder``, a WebFlux route its
    handler and a Data REST search ``String``. Only a Spring MVC or Feign
    mapping is typed from its method's parameters.
    """
    repo = _service(tmp_path, {
        "src/main/java/demo/G.java": (
            "package demo;\n"
            "import org.springframework.cloud.gateway.route.RouteLocator;\n"
            "import org.springframework.cloud.gateway.route.builder.RouteLocatorBuilder;\n"
            "import org.springframework.context.annotation.Bean;\n"
            "public class G {\n"
            "  @Bean\n"
            "  public RouteLocator routes(RouteLocatorBuilder builder) {\n"
            '    return builder.routes().route("o", r -> r.path("/orders/**").uri("lb://orders")).build();\n'
            "  }\n"
            "}\n"
        ),
        "src/main/java/demo/W.java": (
            "package demo;\n"
            "import org.springframework.context.annotation.Bean;\n"
            "import org.springframework.web.reactive.function.server.*;\n"
            "import static org.springframework.web.reactive.function.server.RouterFunctions.route;\n"
            "import static org.springframework.web.reactive.function.server.RequestPredicates.*;\n"
            "public class W {\n"
            "  @Bean\n"
            "  public RouterFunction<ServerResponse> r(OrderHandler handler) {\n"
            '    return route(GET("/w/orders"), handler::list);\n'
            "  }\n"
            "}\n"
            "class OrderHandler {}\n"
        ),
        "src/main/java/demo/Order.java": (
            "package demo;\nimport jakarta.persistence.Entity;\n@Entity public class Order { }\n"
        ),
        "src/main/java/demo/OrderRepo.java": (
            "package demo;\n"
            "import org.springframework.data.repository.CrudRepository;\n"
            "import org.springframework.data.rest.core.annotation.RepositoryRestResource;\n"
            '@RepositoryRestResource(path = "orders")\n'
            "public interface OrderRepo extends CrudRepository<Order, Long> {\n"
            "  java.util.List<Order> findByName(String name);\n"
            "}\n"
        ),
        "src/main/java/demo/Ctl.java": (
            "package demo;\n"
            "import org.springframework.web.bind.annotation.*;\n"
            "@RestController\n"
            "public class Ctl {\n"
            '  @PostMapping("/orders")\n'
            "  public String post(@RequestBody OrderDto body) { return \"\"; }\n"
            "}\n"
            "class OrderDto {}\n"
        ),
    })

    clear_analysis_caches()
    with Store(repo) as store:
        index_repo(repo, Config(), store, full=True)
        typed = {
            (endpoint.framework, endpoint.topic, endpoint.message_type)
            for endpoint in store.all_endpoints()
            if endpoint.message_type is not None
        }

    assert typed == {("spring", "POST /orders", "OrderDto")}


def test_dropped_client_and_listener_shapes_are_recognised(tmp_path: Path) -> None:
    """BC-36: each shape gave no endpoint, or a dynamic one, and no trace.

    RestTemplate receivers are matched by declared type, with
    ``postForLocation``; WebClient takes its builder's base URL; a message
    send names its inline ``TOPIC`` header or is dynamic; container
    annotations are unwrapped; a mapping value may be a same-class
    constant; a method-level ``@HttpExchange`` names its verb; and every
    ``_genkey_<n>`` of a gateway ``Path`` predicate is a pattern.
    """
    repo = _service(tmp_path, {
        "src/main/java/demo/R.java": (
            "package demo;\n"
            "import org.springframework.web.client.RestTemplate;\n"
            "import org.springframework.web.client.RestOperations;\n"
            "import org.springframework.web.reactive.function.client.WebClient;\n"
            "public class R {\n"
            "  RestTemplate template; RestTemplate restTemplate; RestOperations rest;\n"
            '  WebClient wc = WebClient.builder().baseUrl("http://inventory/v1").build();\n'
            '  void a() { template.getForObject("http://inventory/a", String.class); }\n'
            '  void b() { restTemplate.postForLocation("http://inventory/b", null); }\n'
            '  void c() { rest.getForObject("http://inventory/c", String.class); }\n'
            '  void e() { wc.get().uri("/e").retrieve(); }\n'
            "}\n"
        ),
        "src/main/java/demo/KK.java": (
            "package demo;\n"
            "import org.springframework.kafka.core.KafkaTemplate;\n"
            "import org.springframework.kafka.annotation.*;\n"
            "import org.springframework.amqp.rabbit.annotation.*;\n"
            "import org.springframework.messaging.support.MessageBuilder;\n"
            "import org.springframework.kafka.support.KafkaHeaders;\n"
            "import org.springframework.messaging.Message;\n"
            "public class KK {\n"
            "  KafkaTemplate<String, String> kafkaTemplate;\n"
            "  void a() { kafkaTemplate.send(MessageBuilder.withPayload(\"x\")"
            '.setHeader(KafkaHeaders.TOPIC, "inline-t").build()); }\n'
            "  void b(Message<String> m) { kafkaTemplate.send(m); }\n"
            '  @KafkaListeners({@KafkaListener(topics = "multi-a"), @KafkaListener(topics = "multi-b")})\n'
            "  void l(String s) {}\n"
            '  @RabbitListeners({@RabbitListener(queues = "q-a")})\n'
            "  void r(String s) {}\n"
            "}\n"
        ),
        "src/main/java/demo/M.java": (
            "package demo;\n"
            "import org.springframework.web.bind.annotation.*;\n"
            "@RestController\n"
            "public class M {\n"
            '  static final String BASE = "/base";\n'
            '  @GetMapping({"/x", "/y"}) public String xy() { return ""; }\n'
            '  @GetMapping(BASE + "/c") public String c() { return ""; }\n'
            '  @GetMapping(BASE) public String b() { return ""; }\n'
            "}\n"
        ),
        "src/main/java/demo/H.java": (
            "package demo;\n"
            "import org.springframework.web.service.annotation.*;\n"
            '@HttpExchange("/h")\n'
            'public interface H { @HttpExchange(method = "POST", url = "/p") String p(); }\n'
        ),
        "src/main/resources/application.yml": (
            "spring:\n  cloud:\n    gateway:\n      routes:\n"
            "        - id: two\n          uri: lb://inv\n          predicates:\n"
            "            - name: Path\n              args:\n"
            "                _genkey_0: /one/**\n                _genkey_1: /two/**\n"
        ),
    })

    assert _ports(repo) == {
        ("call", "resttemplate", "GET /a", False),
        ("call", "resttemplate", "POST /b", False),
        ("call", "resttemplate", "GET /c", False),
        ("call", "webclient", "GET /v1/e", False),
        ("produce", "spring-kafka", "inline-t", False),
        ("produce", "spring-kafka", "<dynamic>", True),
        ("consume", "spring-kafka", "multi-a", False),
        ("consume", "spring-kafka", "multi-b", False),
        ("consume", "spring-amqp", "q-a", False),
        ("serve", "spring", "GET <dynamic>", True),
        ("serve", "spring", "GET /base/c", False),
        ("serve", "spring", "GET /base", False),
        ("call", "http-interface", "POST /h/p", False),
        ("serve", "spring-cloud-gateway", "ANY /one/**", False),
        ("serve", "spring-cloud-gateway", "ANY /two/**", False),
        ("call", "spring-cloud-gateway", "ANY /one/**", False),
        ("call", "spring-cloud-gateway", "ANY /two/**", False),
    }
    with Store(repo) as store:
        hosts = {
            endpoint.topic for endpoint in store.all_endpoints()
            if "systemlens-api-domain:inventory" in endpoint.snippet
        }
    assert "GET /v1/e" in hosts


def test_a_schedules_container_starts_a_flow_per_schedule(tmp_path: Path) -> None:
    """BC-36: ``@Schedules`` started no flow; repeated ``@Scheduled`` did."""
    repo = _service(tmp_path, {
        "src/main/java/demo/Job.java": (
            "package demo;\n"
            "import org.springframework.kafka.core.KafkaTemplate;\n"
            "import org.springframework.scheduling.annotation.*;\n"
            "public class Job {\n"
            "  KafkaTemplate<String, String> kafkaTemplate;\n"
            '  @Scheduled(cron = "0 * * * * *")\n'
            "  @Scheduled(fixedRate = 5000)\n"
            '  void twice() { kafkaTemplate.send("t1", "x"); }\n'
            '  @Schedules({@Scheduled(fixedDelay = 1000), @Scheduled(cron = "1 * * * * *")})\n'
            '  void container() { kafkaTemplate.send("t2", "x"); }\n'
            "}\n"
        ),
    })
    clear_analysis_caches()
    with Store(repo) as store:
        index_repo(repo, Config(), store, full=True)
        triggers = sorted(
            (flow.method.rsplit(".", 1)[-1], flow.steps[0].name)
            for flow in store.all_code_flows()
        )

    assert triggers == [
        ("container", "@Scheduled(cron=1 * * * * *)"),
        ("container", "@Scheduled(fixedDelay=1000)"),
        ("twice", "@Scheduled(cron=0 * * * * *)"),
        ("twice", "@Scheduled(fixedRate=5000)"),
    ]


def test_an_openapi_contract_is_parsed_once_for_all_its_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BC-37: each OpenAPI endpoint re-parsed its contract.

    Three operations of one contract now read it once, and each still
    reaches its unique overriding controller method by ``operationId``.
    """
    java_path = "orders/src/main/java/com/example/OrderController.java"
    contract_path = "orders/src/main/resources/static/openapi.yaml"
    _write(tmp_path / java_path, (
        "package com.example;\n"
        "@RestController\n"
        "class OrderController {\n"
        "  @Override String placeOrder(Object r) { return null; }\n"
        "  @Override String getOrder(String id) { return null; }\n"
        "  @Override String listOrders() { return null; }\n"
        "}\n"
    ))
    _write(tmp_path / contract_path, (
        "openapi: 3.0.3\npaths:\n  /orders:\n"
        "    post: {operationId: placeOrder}\n    get: {operationId: listOrders}\n"
        "  /orders/{id}:\n    parameters: []\n    get: {operationId: getOrder}\n"
    ))
    module = DiscoveredModule(
        name="orders", path=tmp_path / "orders", build_system="maven", version=None,
        kind="application", starts_application=True, configuration_example="",
    )
    endpoint = MessageEndpoint(
        id="", role="serve", system="rest", topic="", topic_dynamic=False, source="code",
        framework="openapi", path=contract_path, start_line=1, end_line=1, snippet="",
        module="orders",
    )
    endpoints = [
        replace(endpoint, id=topic, topic=topic)
        for topic in ("POST /orders", "GET /orders", "GET /orders/{id}")
    ]
    reads: list[str] = []
    read = integration_methods._openapi_operation_ids

    def counted(path: Path) -> dict[tuple[str, str], str]:
        reads.append(str(path))
        return read(path)

    monkeypatch.setattr(integration_methods, "_openapi_operation_ids", counted)

    methods = integration_methods.materialize_integration_methods(
        tmp_path, endpoints, [java_path], [module],
    )

    inputs = {
        method.qualified_method.rsplit(".", 1)[-1]: method.input_endpoint_ids
        for method in methods
    }
    assert inputs == {
        "placeOrder": ("POST /orders",),
        "listOrders": ("GET /orders",),
        "getOrder": ("GET /orders/{id}",),
    }
    assert len(reads) == 1


def test_a_single_quoted_openapi_path_keeps_its_line(tmp_path: Path) -> None:
    """BC-38: ``'/a':`` fell back to line 1 as the endpoint evidence."""
    from systemlens.scanner.rest_mvc import _infer_openapi_endpoints

    contract = "svc/src/main/resources/openapi.yaml"
    _write(tmp_path / contract, (
        "openapi: 3.0.3\n"
        "paths:\n"
        "  '/a':\n"
        "    'get':\n"
        "      operationId: getA\n"
        '  "/b":\n'
        "    post:\n"
        "      operationId: postB\n"
    ))

    lines = {
        endpoint.topic: endpoint.start_line
        for endpoint in _infer_openapi_endpoints(tmp_path, contract, force_contract=True)
    }

    assert lines == {"GET /a": 4, "POST /b": 7}


def test_an_empty_allowed_path_list_projects_no_source(tmp_path: Path) -> None:
    """BC-38: an empty ``allowed_paths`` meant "no restriction".

    ``None`` projects every Java file; an empty list, as for an index
    without Java files, projects none.
    """
    from systemlens.indexing.source_projection import project_java_sources

    _write(tmp_path / "repo/svc/src/main/java/demo/A.java", "class A {}")

    assert project_java_sources(tmp_path / "repo", tmp_path / "all") == 1
    assert project_java_sources(tmp_path / "repo", tmp_path / "none", allowed_paths=[]) == 0
