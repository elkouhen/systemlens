"""Regression coverage for REST peer identity and route ownership.

Each test reproduces one defect of BACKLOG.md on a minimal fixture.
"""
from pathlib import Path

from systemlens.domain.graph import (
    build_graph,
    group_endpoints_by_module,
    resolve_rest_target_service,
)
from systemlens.scanner import clear_analysis_caches, local_spring_application_names
from systemlens.scanner.rest_mvc import infer_framework_endpoints


def _write(root: Path, rel: str, text: str) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return rel


def _module(root: Path, name: str, files: dict[str, str]) -> None:
    _write(root, f"{name}/pom.xml", (
        "<project><modelVersion>4.0.0</modelVersion><groupId>g</groupId>"
        f"<artifactId>{name}</artifactId><version>1</version></project>"
    ))
    for rel, text in files.items():
        _write(root, f"{name}/{rel}", text)


def _endpoints(root: Path, files=None):
    clear_analysis_caches()
    return infer_framework_endpoints(root, files)


def _rest_edges(root: Path):
    endpoints = _endpoints(root)
    modules = {e.module for e in endpoints if e.module}
    aliases = {module: local_spring_application_names(root / module, None) for module in modules}
    return [
        edge for edge in build_graph(
            group_endpoints_by_module(endpoints), service_aliases=aliases,
        )
        if edge.kind == "rest"
    ]


def _edge_view(edges):
    return sorted(
        (e.from_service, e.to_service, e.to_endpoint.topic if e.to_endpoint else None)
        for e in edges
    )


# ------------------------------------------------- BL-02: Feign client name
_EMPLOYEE_CONTROLLER = """
package e;
import org.springframework.web.bind.annotation.*;
@RestController
public class EmployeeController {
  @GetMapping("/department/{departmentId}") public String byDepartment(@PathVariable Long departmentId) { return ""; }
  @GetMapping("/{id}") public String byId(@PathVariable Long id) { return ""; }
}
"""


def _feign_client(annotation: str, route: str = "/department/{departmentId}",
                  name: str = "EmployeeClient") -> str:
    return f"""
package d;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.*;
{annotation}
public interface {name} {{
  @GetMapping("{route}") String findByDepartment(@PathVariable("departmentId") Long departmentId);
}}
"""


def test_bl02_feign_name_is_the_peer_application_name(tmp_path):
    """BL-02: ``@FeignClient(name=...)`` names the peer's ``spring.application.name``."""
    _module(tmp_path, "emp", {
        "src/main/java/e/EmployeeController.java": _EMPLOYEE_CONTROLLER,
        "src/main/resources/application.yml": (
            "spring:\n  application:\n    name: employee-service\n"
        ),
    })
    _module(tmp_path, "department", {
        "src/main/java/d/EmployeeClient.java": _feign_client(
            '@FeignClient(name = "employee-service")'),
    })

    assert _edge_view(_rest_edges(tmp_path)) == [
        ("department", "emp", "GET /department/{departmentId}"),
    ]


def test_bl02_feign_value_and_resolved_placeholder_name_the_peer(tmp_path):
    _module(tmp_path, "employee-service", {
        "src/main/java/e/EmployeeController.java": _EMPLOYEE_CONTROLLER,
    })
    _module(tmp_path, "department", {
        "src/main/java/d/EmployeeClient.java": _feign_client(
            '@FeignClient("${clients.employee}")'),
        "src/main/resources/application.yml": "clients:\n  employee: employee-service\n",
    })

    assert _edge_view(_rest_edges(tmp_path)) == [
        ("department", "employee-service", "GET /department/{departmentId}"),
    ]


def test_bl02_unknown_feign_name_stays_unresolved(tmp_path):
    _module(tmp_path, "employee-service", {
        "src/main/java/e/EmployeeController.java": _EMPLOYEE_CONTROLLER,
    })
    _module(tmp_path, "department", {
        "src/main/java/d/EmployeeClient.java": _feign_client(
            '@FeignClient(name = "payroll-service")'),
    })

    [call] = [e for e in _endpoints(tmp_path) if e.role == "call"]
    resolution = resolve_rest_target_service(call, ["department", "employee-service"])

    assert resolution.status == "unresolved", resolution
    assert resolution.hint == "payroll-service", resolution
    assert _rest_edges(tmp_path) == []


def test_bl02_unresolved_or_dotted_feign_name_gives_no_target(tmp_path):
    first = _write(tmp_path, "src/main/java/d/EmployeeClient.java", _feign_client(
        '@FeignClient(name = "${clients.unknown}")'))
    second = _write(tmp_path, "src/main/java/d/Other.java", _feign_client(
        '@FeignClient(name = "api.example.com")', name="Other"))

    calls = [e for e in _endpoints(tmp_path, [first, second]) if e.role == "call"]

    assert len(calls) == 2
    assert all("systemlens-api-domain" not in e.snippet for e in calls), calls


def test_bl02_feign_url_wins_over_the_name(tmp_path):
    """An explicit ``url`` is the target evidence; the name is then ignored."""
    _module(tmp_path, "employee-service", {
        "src/main/java/e/EmployeeController.java": _EMPLOYEE_CONTROLLER,
    })
    _module(tmp_path, "legacy", {
        "src/main/java/e/EmployeeController.java": _EMPLOYEE_CONTROLLER,
    })
    _module(tmp_path, "department", {
        "src/main/java/d/EmployeeClient.java": _feign_client(
            '@FeignClient(name = "employee-service", url = "http://legacy:8080")'),
        "src/main/java/d/External.java": _feign_client(
            '@FeignClient(name = "employee-service", url = "https://api.example.com")',
            name="External"),
    })

    assert _edge_view(_rest_edges(tmp_path)) == [
        ("department", "legacy", "GET /department/{departmentId}"),
    ]


def test_bl02_dynamic_feign_route_is_not_projected_onto_host_routes(tmp_path):
    _module(tmp_path, "employee-service", {
        "src/main/java/e/EmployeeController.java": _EMPLOYEE_CONTROLLER,
    })
    _module(tmp_path, "department", {
        "src/main/java/d/EmployeeClient.java": _feign_client(
            '@FeignClient(name = "employee-service")', route="${routes.unknown}"),
    })

    assert _edge_view(_rest_edges(tmp_path)) == [("department", "employee-service", None)]


# ------------------------------------- BL-04: inherited request mappings
_EMPLOYEE_API = """
package api;
import org.springframework.web.bind.annotation.*;
@RequestMapping("/employees")
public interface EmployeeApi {
  @GetMapping("/{id}") String findById(@PathVariable("id") Long id);
  @PostMapping String create(@RequestBody String body);
}
"""


def _rest(endpoints, role):
    return sorted(
        (e.path.rsplit("/", 1)[-1], e.topic, e.start_line, e.framework)
        for e in endpoints if e.system == "rest" and e.role == role
    )


def test_bl04_controller_implementing_an_api_interface_serves_its_routes(tmp_path):
    """BL-04: an interface's mappings belong to the controller implementing it."""
    _module(tmp_path, "api", {"src/main/java/api/EmployeeApi.java": _EMPLOYEE_API})
    _module(tmp_path, "employee-service", {"src/main/java/e/EmployeeController.java": """
package e;
import api.EmployeeApi;
import org.springframework.web.bind.annotation.*;
@RestController
public class EmployeeController implements EmployeeApi {
  @Override
  public String findById(Long id) {
    return "";
  }
  public String create(String body) { return ""; }
}
"""})

    endpoints = _endpoints(tmp_path)

    assert _rest(endpoints, "serve") == [
        ("EmployeeController.java", "GET /employees/{id}", 7, "spring"),
        ("EmployeeController.java", "POST /employees", 11, "spring"),
    ]
    assert {e.module for e in endpoints} == {"employee-service"}
    [serve] = [e for e in endpoints if e.topic == "GET /employees/{id}"]
    assert "systemlens-inherited-mapping:api/src/main/java/api/EmployeeApi.java" in serve.snippet


def test_bl04_base_class_route_takes_the_subclass_prefix(tmp_path):
    rel_base = _write(tmp_path, "src/main/java/c/CrudController.java", """
package c;
import org.springframework.web.bind.annotation.*;
public abstract class CrudController {
  @GetMapping("/{id}") public String get(@PathVariable Long id) { return ""; }
  @GetMapping public String list(@RequestParam("page") int page) { return ""; }
}
""")
    rel_sub = _write(tmp_path, "src/main/java/c/OrderController.java", """
package c;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/orders")
public class OrderController extends CrudController {
  @GetMapping("/{id}") public String get(@PathVariable Long id) { return "own"; }
  @GetMapping("/count") public String count() { return ""; }
}
""")

    serves = _rest(_endpoints(tmp_path, [rel_base, rel_sub]), "serve")

    assert serves == [
        ("OrderController.java", "GET /orders/count", 8, "spring"),
        ("OrderController.java", "GET /orders/{id}", 7, "spring"),
        ("OrderController.java", "GET /orders?page", 4, "spring"),
    ], serves


def test_bl04_controller_without_own_prefix_inherits_the_interface_prefix(tmp_path):
    rel_api = _write(tmp_path, "src/main/java/api/EmployeeApi.java", _EMPLOYEE_API)
    rel_ctrl = _write(tmp_path, "src/main/java/e/EmployeeController.java", """
package e;
import api.EmployeeApi;
import org.springframework.web.bind.annotation.*;
@RestController
public class EmployeeController implements EmployeeApi {
  public String findById(Long id) { return ""; }
  public String create(String body) { return ""; }
  @GetMapping("/count") public String count() { return ""; }
}
""")

    topics = sorted(e.topic for e in _endpoints(tmp_path, [rel_api, rel_ctrl]))

    assert topics == ["GET /employees/count", "GET /employees/{id}", "POST /employees"]


def test_bl04_feign_client_extending_an_api_interface_calls_its_routes(tmp_path):
    _module(tmp_path, "api", {"src/main/java/api/EmployeeApi.java": _EMPLOYEE_API})
    _module(tmp_path, "employee-service", {"src/main/java/e/EmployeeController.java": """
package e;
import api.EmployeeApi;
import org.springframework.web.bind.annotation.*;
@RestController
public class EmployeeController implements EmployeeApi {
  public String findById(Long id) { return ""; }
  public String create(String body) { return ""; }
}
"""})
    _module(tmp_path, "department", {"src/main/java/d/EmployeeClient.java": """
package d;
import api.EmployeeApi;
import org.springframework.cloud.openfeign.FeignClient;
@FeignClient(name = "employee-service")
public interface EmployeeClient extends EmployeeApi {
}
"""})

    endpoints = _endpoints(tmp_path)

    assert _rest(endpoints, "call") == [
        ("EmployeeClient.java", "GET /employees/{id}", 5, "feign"),
        ("EmployeeClient.java", "POST /employees", 5, "feign"),
    ]
    assert _edge_view(_rest_edges(tmp_path)) == [
        ("department", "employee-service", "GET /employees/{id}"),
        ("department", "employee-service", "POST /employees"),
    ]


def test_bl04_interface_without_inheritor_keeps_its_mappings(tmp_path):
    rel = _write(tmp_path, "src/main/java/api/EmployeeApi.java", _EMPLOYEE_API)

    assert _rest(_endpoints(tmp_path, [rel]), "serve") == [
        ("EmployeeApi.java", "GET /employees/{id}", 6, "spring"),
        ("EmployeeApi.java", "POST /employees", 7, "spring"),
    ]


_IMPLEMENTING_CONTROLLER = """
package e;
import api.EmployeeApi;
import org.springframework.web.bind.annotation.*;
@RestController
public class EmployeeController implements EmployeeApi {
  public String findById(Long id) { return ""; }
  public String create(String body) { return ""; }
}
"""


def _indexed_serves(repo: Path, store) -> list[tuple[str, str]]:
    from systemlens.indexing.service import index_repo
    from systemlens.infrastructure.config import Config

    index_repo(repo, Config(), store)
    return sorted(
        (e.path.rsplit("/", 1)[-1], e.topic)
        for e in store.all_endpoints(system="rest", role="serve")
    )


def test_bl04_incremental_change_of_the_interface_refreshes_its_implementor(tmp_path):
    from systemlens.storage.sqlite import Store

    _module(tmp_path, "api", {"src/main/java/api/EmployeeApi.java": _EMPLOYEE_API})
    _module(tmp_path, "employee-service", {
        "src/main/java/e/EmployeeController.java": _IMPLEMENTING_CONTROLLER,
    })
    api = tmp_path / "api/src/main/java/api/EmployeeApi.java"
    with Store(tmp_path) as store:
        assert _indexed_serves(tmp_path, store) == [
            ("EmployeeController.java", "GET /employees/{id}"),
            ("EmployeeController.java", "POST /employees"),
        ]
        api.write_text(api.read_text().replace('"/employees"', '"/staff"'))
        assert _indexed_serves(tmp_path, store) == [
            ("EmployeeController.java", "GET /staff/{id}"),
            ("EmployeeController.java", "POST /staff"),
        ]


def test_bl04_incremental_link_changes_refresh_the_declaring_type(tmp_path):
    from systemlens.storage.sqlite import Store

    _module(tmp_path, "api", {"src/main/java/api/EmployeeApi.java": _EMPLOYEE_API})
    controller = tmp_path / "employee-service/src/main/java/e/EmployeeController.java"
    with Store(tmp_path) as store:
        assert _indexed_serves(tmp_path, store) == [
            ("EmployeeApi.java", "GET /employees/{id}"),
            ("EmployeeApi.java", "POST /employees"),
        ]
        _module(tmp_path, "employee-service", {
            "src/main/java/e/EmployeeController.java": _IMPLEMENTING_CONTROLLER,
        })
        assert _indexed_serves(tmp_path, store) == [
            ("EmployeeController.java", "GET /employees/{id}"),
            ("EmployeeController.java", "POST /employees"),
        ]
        controller.unlink()
        assert _indexed_serves(tmp_path, store) == [
            ("EmployeeApi.java", "GET /employees/{id}"),
            ("EmployeeApi.java", "POST /employees"),
        ]


# ----------------------------------------- BL-06: gateway path filters
def _gateway_yaml(routes: str, default_filters: str = "") -> str:
    return (
        "spring:\n  cloud:\n    gateway:\n"
        + default_filters
        + "      routes:\n"
        + routes
    )


def _gateway_calls(tmp_path, text: str):
    rel = _write(tmp_path, "src/main/resources/application.yml", text)
    return sorted(
        (e.topic, e.topic_dynamic, e.start_line)
        for e in _endpoints(tmp_path, [rel]) if e.role == "call"
    )


def test_bl06_rewrite_path_applies_to_the_route_pattern(tmp_path):
    """BL-06: the sample's ``RewritePath`` drops the public prefix."""
    calls = _gateway_calls(tmp_path, _gateway_yaml(
        "      - id: employee\n"
        "        uri: lb://employee-service\n"
        "        predicates:\n"
        "        - Path=/employee/**\n"
        "        filters:\n"
        "        - RewritePath=/employee/(?<path>.*), /$\\{path}\n"
        "      - id: orders\n"
        "        uri: lb://order-service\n"
        "        predicates:\n"
        "        - Path=/api/orders/{id}\n"
        "        filters:\n"
        "        - RewritePath=/api/(?<rest>.*), /v2/$\\{rest}\n"
    ))

    assert calls == [("ANY /**", False, 6), ("ANY /v2/orders/{id}", False, 12)]


def test_bl06_rewrite_depending_on_a_pattern_segment_stays_dynamic(tmp_path):
    calls = _gateway_calls(tmp_path, _gateway_yaml(
        "      - uri: lb://item-service\n"
        "        predicates:\n"
        "        - Path=/items/{id}\n"
        "        filters:\n"
        "        - RewritePath=/items/(?<id>\\d+), /x/$\\{id}\n"
    ))

    assert calls == [("ANY <dynamic>", True, 5)]


def test_bl06_prefix_set_and_long_form_filters(tmp_path):
    calls = _gateway_calls(tmp_path, _gateway_yaml(
        "      - uri: lb://a\n"
        "        predicates:\n"
        "        - Path=/orders/**\n"
        "        filters:\n"
        "        - PrefixPath=/api\n"
        "      - uri: lb://b\n"
        "        predicates:\n"
        "        - Path=/red/{segment}\n"
        "        filters:\n"
        "        - SetPath=/{segment}\n"
        "      - uri: lb://c\n"
        "        predicates:\n"
        "        - Path=/c/v1/**\n"
        "        filters:\n"
        "        - name: StripPrefix\n"
        "          args:\n"
        "            parts: 2\n"
        "      - uri: lb://d\n"
        "        predicates:\n"
        "        - Path=/d/**\n"
        "        filters:\n"
        "        - name: RewritePath\n"
        "          args:\n"
        "            regexp: /d/(?<rest>.*)\n"
        "            replacement: /internal/${rest}\n"
    ))

    assert calls == [
        ("ANY /**", False, 15),
        ("ANY /api/orders/**", False, 5),
        ("ANY /internal/**", False, 22),
        ("ANY /{segment}", False, 10),
    ]


def test_bl06_default_filters_apply_before_route_filters(tmp_path):
    calls = _gateway_calls(tmp_path, _gateway_yaml(
        "      - uri: lb://a\n"
        "        predicates:\n"
        "        - Path=/gw/orders/**\n"
        "        filters:\n"
        "        - PrefixPath=/api\n",
        default_filters="      default-filters:\n      - StripPrefix=1\n",
    ))

    assert calls == [("ANY /api/orders/**", False, 7)]


def test_bl06_http_uri_route_is_kept_and_quoted_uri_has_its_line(tmp_path):
    calls = _gateway_calls(tmp_path, _gateway_yaml(
        "      - uri: \"http://legacy:8080\"\n"
        "        predicates:\n"
        "        - Path=/legacy/**\n"
        "      - uri: 'lb://orders'\n"
        "        predicates:\n"
        "        - Path=/orders/**\n"
    ))

    assert calls == [("ANY /legacy/**", False, 5), ("ANY /orders/**", False, 8)]


def test_bl06_java_dsl_filters_rewrite_the_call(tmp_path):
    rel = _write(tmp_path, "src/main/java/g/Routes.java", """
package g;
import org.springframework.cloud.gateway.route.RouteLocator;
import org.springframework.cloud.gateway.route.builder.RouteLocatorBuilder;
class Routes {
  RouteLocator routes(RouteLocatorBuilder builder) {
    return builder.routes()
      .route("a", r -> r.path("/a/v1/**").filters(f -> f.stripPrefix(2)).uri("lb://a"))
      .route("b", r -> r.path("/b/**")
          .filters(f -> f.rewritePath("/b/(?<rest>.*)", "/${rest}").prefixPath("/api"))
          .uri("lb://b"))
      .route("c", r -> r.path("/c/{id}").filters(f -> f.setPath("/items/{id}")).uri("lb://c"))
      .route("d", r -> r.path("/d/**").filters(f -> f.stripPrefix(count)).uri("lb://d"))
      .build();
  }
}
""")

    calls = sorted(
        (e.topic, e.topic_dynamic)
        for e in _endpoints(tmp_path, [rel]) if e.role == "call"
    )

    assert calls == [
        ("ANY /**", False), ("ANY /api/**", False),
        ("ANY /items/{id}", False), ("ANY <dynamic>", True),
    ]


def test_bl06_rewritten_gateway_call_no_longer_matches_by_coincidence(tmp_path):
    """The public ``/organization/**`` no longer reaches a route it does not forward to."""
    _module(tmp_path, "organization-service", {"src/main/java/o/C.java": """
package o;
import org.springframework.web.bind.annotation.*;
@RestController
public class C {
  @GetMapping("/organization/stats") public String s() { return ""; }
}
"""})
    _module(tmp_path, "gateway", {"src/main/resources/application.yml": _gateway_yaml(
        "      - uri: lb://organization-service\n"
        "        predicates:\n"
        "        - Path=/organization/**\n"
        "        filters:\n"
        "        - RewritePath=/organization/(?<path>.*), /api/$\\{path}\n"
    )})

    assert _rest_edges(tmp_path) == []


# ----------------------------------------- BL-08: RestClient baseUrl scope
def _restclient_calls(tmp_path, body: str):
    rel = _write(tmp_path, "src/main/java/c/Clients.java", f"""
package c;
import org.springframework.web.client.RestClient;
class Clients {{
{body}
}}
""")
    return sorted(
        (e.topic, e.topic_dynamic, next(
            (line.split(":", 1)[1] for line in e.snippet.splitlines()
             if "systemlens-api-domain:" in line), None))
        for e in _endpoints(tmp_path, [rel]) if e.role == "call"
    )


def test_bl08_each_restclient_keeps_its_own_base_url(tmp_path):
    """BL-08: a file-wide unique base host was applied to every client."""
    calls = _restclient_calls(tmp_path, """
  private final RestClient inventory = RestClient.builder()
      .baseUrl("http://inventory-service/api").build();
  private final RestClient other;
  Clients(RestClient.Builder builder) { this.other = builder.build(); }
  void load() {
    inventory.get().uri("/items").retrieve();
    other.get().uri("/orders").retrieve();
  }
""")

    assert calls == [
        ("GET /api/items", False, "inventory-service"),
        ("GET /orders", False, None),
    ]


def test_bl08_two_clients_in_one_file_get_their_own_host_and_path(tmp_path):
    calls = _restclient_calls(tmp_path, """
  private final RestClient orders;
  private final RestClient billing = RestClient.create("http://billing-service/v2");
  Clients() {
    this.orders = RestClient.builder().baseUrl("http://order-service/api").build();
  }
  void load(String id) {
    orders.post().uri("/orders").retrieve();
    billing.get().uri("/invoices/{id}", id).retrieve();
    RestClient local = RestClient.builder().baseUrl("http://audit-service").build();
    local.get().uri("/events").retrieve();
    RestClient.builder().baseUrl("http://inline-service/x").build().get().uri("/y").retrieve();
    orders.get().uri("http://other-service/abs").retrieve();
  }
""")

    assert calls == [
        ("GET /abs", False, "other-service"),
        ("GET /events", False, "audit-service"),
        ("GET /v2/invoices/{id}", False, "billing-service"),
        ("GET /x/y", False, "inline-service"),
        ("POST /api/orders", False, "order-service"),
    ]


def test_bl08_client_name_with_several_bases_has_none(tmp_path):
    calls = _restclient_calls(tmp_path, """
  private RestClient client;
  void a() { client = RestClient.create("http://a-service"); }
  void b() { client = RestClient.create("http://b-service"); }
  void load() { client.get().uri("/x").retrieve(); }
""")

    assert calls == [("GET /x", False, None)]

