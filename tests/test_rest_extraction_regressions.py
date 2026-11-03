"""Regression coverage for conservative REST extraction rules."""
from pathlib import Path

from systemlens.domain.graph import (
    build_graph,
    group_endpoints_by_module,
    paths_match,
    resolve_rest_target_service,
    select_matching_routes,
    service_hint_from_host,
)
from systemlens.domain.models import MessageEndpoint
from systemlens.scanner import clear_analysis_caches
from systemlens.scanner.rest_mvc import infer_framework_endpoints


def _write(root: Path, rel: str, text: str) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return rel


def _calls(root: Path, files=None):
    clear_analysis_caches()
    return [e for e in infer_framework_endpoints(root, files) if e.role == "call"]


def _serves(root: Path, files=None):
    clear_analysis_caches()
    return [e for e in infer_framework_endpoints(root, files) if e.role == "serve"]


# ---------------------------------------------------------------- A: shadowing
def test_parameter_shadowing_local_in_other_method_stays_dynamic(tmp_path):
    rel = _write(tmp_path, "src/main/java/Client.java", """
import org.springframework.web.client.RestTemplate;
class Client {
  RestTemplate restTemplate;
  void other() { String base = "http://orders"; }
  void run(String base) { restTemplate.getForObject(base + "/users", String.class); }
}
""")
    (e,) = _calls(tmp_path, [rel])
    assert e.topic_dynamic is True, (e.topic, e.snippet)
    assert "systemlens-api-domain" not in e.snippet, e.snippet


def test_parameter_shadowing_field_stays_dynamic(tmp_path):
    rel = _write(tmp_path, "src/main/java/Client.java", """
import org.springframework.web.client.RestTemplate;
class Client {
  RestTemplate restTemplate;
  private String base = "http://orders";
  void run(String base) { restTemplate.getForObject(base + "/users", String.class); }
}
""")
    (e,) = _calls(tmp_path, [rel])
    assert e.topic_dynamic is True, (e.topic, e.snippet)
    assert "systemlens-api-domain" not in e.snippet, e.snippet


def test_parameter_shadowing_value_field_stays_dynamic(tmp_path):
    _write(tmp_path, "src/main/resources/application.properties", "orders.url=http://orders\n")
    rel = _write(tmp_path, "src/main/java/Client.java", """
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.client.RestTemplate;
class Client {
  RestTemplate restTemplate;
  @Value("${orders.url}") private String base;
  void run(String base) { restTemplate.getForObject(base + "/users", String.class); }
}
""")
    (e,) = _calls(tmp_path, [rel])
    assert e.topic_dynamic is True, (e.topic, e.snippet)
    assert "systemlens-api-domain" not in e.snippet, e.snippet


def test_reassigned_local_stays_dynamic(tmp_path):
    rel = _write(tmp_path, "src/main/java/Client.java", """
import org.springframework.web.client.RestTemplate;
class Client {
  RestTemplate restTemplate;
  void run() { String base = "http://orders"; base = compute(); restTemplate.getForObject(base + "/users", String.class); }
}
""")
    (e,) = _calls(tmp_path, [rel])
    assert e.topic_dynamic is True, (e.topic, e.snippet)


# ---------------------------------------------------------------- B: partial placeholders
def test_partial_placeholder_in_mapping_is_not_a_literal_route(tmp_path):
    rel = _write(tmp_path, "src/main/java/C.java", """
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("${api.prefix}/orders")
class C {
  @GetMapping("/{id}") String get() { return ""; }
}
""")
    (e,) = _serves(tmp_path, [rel])
    assert "${" not in e.topic, e.topic
    assert e.topic_dynamic is True or e.topic == "GET /orders/{id}", (e.topic, e.topic_dynamic)


def test_partial_placeholder_in_mapping_resolves_when_property_exists(tmp_path):
    _write(tmp_path, "src/main/resources/application.properties", "api.prefix=/v1\n")
    rel = _write(tmp_path, "src/main/java/C.java", """
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("${api.prefix}/orders")
class C {
  @GetMapping("/{id}") String get() { return ""; }
}
""")
    (e,) = _serves(tmp_path, [rel])
    assert e.topic == "GET /v1/orders/{id}" or e.topic_dynamic is True, (e.topic, e.topic_dynamic)


def test_property_value_containing_placeholder_is_not_literal(tmp_path):
    _write(tmp_path, "src/main/resources/application.properties", "orders.url=${orders.host}/api\n")
    rel = _write(tmp_path, "src/main/java/Client.java", """
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.client.RestTemplate;
class Client {
  RestTemplate restTemplate;
  @Value("${orders.url}") private String base;
  void run() { restTemplate.getForObject(base + "/users", String.class); }
}
""")
    (e,) = _calls(tmp_path, [rel])
    assert "${" not in e.topic, e.topic
    assert e.topic_dynamic is True, (e.topic, e.topic_dynamic)


# ---------------------------------------------------------------- H: @HttpExchange(url=...)
def test_http_exchange_url_attribute_is_the_route(tmp_path):
    rel = _write(tmp_path, "src/main/java/OrdersClient.java", """
import org.springframework.web.service.annotation.HttpExchange;
import org.springframework.web.service.annotation.GetExchange;
@HttpExchange(url = "/api/v1")
interface OrdersClient {
  @GetExchange(url = "/orders") void list();
}
""")
    (e,) = _calls(tmp_path, [rel])
    assert e.topic == "GET /api/v1/orders", (e.topic, e.topic_dynamic)


def test_get_exchange_without_type_level_http_exchange_is_indexed(tmp_path):
    rel = _write(tmp_path, "src/main/java/OrdersClient.java", """
import org.springframework.web.service.annotation.GetExchange;
interface OrdersClient {
  @GetExchange("/orders") void list();
}
""")
    calls = _calls(tmp_path, [rel])
    assert [e.topic for e in calls] == ["GET /orders"], calls


# ---------------------------------------------------------------- S: Spring Data REST default path case
def test_spring_data_rest_default_path_keeps_camel_case(tmp_path):
    _write(tmp_path, "pom.xml", """<project xmlns="http://maven.apache.org/POM/4.0.0">
<modelVersion>4.0.0</modelVersion><artifactId>svc</artifactId><version>1</version>
<dependencies><dependency><groupId>org.springframework.boot</groupId>
<artifactId>spring-boot-starter-data-rest</artifactId></dependency></dependencies></project>
""")
    rel = _write(tmp_path, "src/main/java/OrderItemRepository.java", """
import org.springframework.data.jpa.repository.JpaRepository;
interface OrderItemRepository extends JpaRepository<OrderItem, Long> {}
""")
    topics = {e.topic for e in _serves(tmp_path, [rel])}
    assert "GET /orderItems" in topics, topics


# ---------------------------------------------------------------- L: stale cache
def test_local_initializer_cache_survives_clear_analysis_caches(tmp_path):
    src = """
import org.springframework.web.client.RestTemplate;
class Client {
  RestTemplate restTemplate;
  private static final String BASE = "http://%s";
  class Inner { void run() { restTemplate.getForObject(BASE + "/users", String.class); } }
}
"""
    rel = _write(tmp_path, "src/main/java/Client.java", src % "orders")
    (first,) = _calls(tmp_path, [rel])
    assert "systemlens-api-domain:orders" in first.snippet, first.snippet
    _write(tmp_path, "src/main/java/Client.java", src % "billing")
    (second,) = _calls(tmp_path, [rel])
    assert "systemlens-api-domain:billing" in second.snippet, second.snippet


# ---------------------------------------------------------------- G: gateway Java DSL without method
def test_gateway_java_dsl_route_without_method_is_any(tmp_path):
    rel = _write(tmp_path, "src/main/java/GatewayConfig.java", """
import org.springframework.cloud.gateway.route.RouteLocator;
import org.springframework.cloud.gateway.route.builder.RouteLocatorBuilder;
class GatewayConfig {
  RouteLocator routes(RouteLocatorBuilder b) {
    return b.routes().route("orders", r -> r.path("/orders/**").uri("lb://orders")).build();
  }
}
""")
    clear_analysis_caches()
    topics = {(e.role, e.topic) for e in infer_framework_endpoints(tmp_path, [rel])}
    assert ("serve", "ANY /orders/**") in topics, topics


# ---------------------------------------------------------------- misc
def test_array_single_literal_mapping_is_resolved(tmp_path):
    rel = _write(tmp_path, "src/main/java/C.java", """
import org.springframework.web.bind.annotation.*;
@RestController
class C {
  @GetMapping({"/a"}) String get() { return ""; }
}
""")
    (e,) = _serves(tmp_path, [rel])
    assert e.topic == "GET /a", (e.topic, e.topic_dynamic)


def test_resttemplate_exchange_with_variable_method_is_kept(tmp_path):
    rel = _write(tmp_path, "src/main/java/Client.java", """
import org.springframework.web.client.RestTemplate;
import org.springframework.http.HttpMethod;
class Client {
  RestTemplate restTemplate;
  void run(HttpMethod m) { restTemplate.exchange("http://orders/users", m, null, String.class); }
}
""")
    calls = _calls(tmp_path, [rel])
    assert [e.topic for e in calls] == ["ANY /users"], calls


def test_text_block_literal_route(tmp_path):
    rel = _write(tmp_path, "src/main/java/C.java", '''
import org.springframework.web.bind.annotation.*;
@RestController
class C {
  @GetMapping("""
      /users""") String get() { return ""; }
}
''')
    (e,) = _serves(tmp_path, [rel])
    assert e.topic == "GET /users" or e.topic_dynamic is True, (e.topic, e.topic_dynamic)


def _topics(root: Path, files):
    clear_analysis_caches()
    return [(e.role, e.topic, e.topic_dynamic) for e in infer_framework_endpoints(root, files)]


def test_webclient_method_enum_is_indexed(tmp_path):
    rel = _write(tmp_path, "src/main/java/Client.java", """
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.http.HttpMethod;
class Client {
  WebClient webClient;
  void run() { webClient.method(HttpMethod.POST).uri("http://orders/users").retrieve(); }
}
""")
    assert _topics(tmp_path, [rel]) == [("call", "POST /users", False)], _topics(tmp_path, [rel])


# ---------------------------------------------------------------- lot 2 (2026-11-03 review): HTTP false facts
def _module(root: Path, name: str, files: dict[str, str]) -> None:
    _write(root, f"{name}/pom.xml", (
        "<project><modelVersion>4.0.0</modelVersion><groupId>g</groupId>"
        f"<artifactId>{name}</artifactId><version>1</version></project>"
    ))
    for rel, text in files.items():
        _write(root, f"{name}/{rel}", text)


def _rest_edges(root: Path):
    clear_analysis_caches()
    endpoints = infer_framework_endpoints(root)
    return [
        edge for edge in build_graph(group_endpoints_by_module(endpoints))
        if edge.kind == "rest"
    ]


def _endpoint(role: str, topic: str, line: int, snippet: str = "", **fields) -> MessageEndpoint:
    values = dict(
        id=f"{role}{line}", role=role, system="rest", topic=topic, topic_dynamic=False,
        source="code", framework="spring", path="x/C.java", start_line=line,
        end_line=line, snippet=snippet, module="m",
    )
    values.update(fields)
    return MessageEndpoint(**values)


_CATALOG_CONTROLLER = """
package c;
import org.springframework.web.bind.annotation.*;
@RestController
public class A {
  @GetMapping("/admin/stats") public String s() { return ""; }
  @GetMapping("/brands") public String b() { return ""; }
  @GetMapping("/categories/{id}/tree") public String t(@PathVariable String id) { return ""; }
}
"""

_INVENTORY_CONTROLLER = """
package i;
import org.springframework.web.bind.annotation.*;
@RestController @RequestMapping("/items")
public class InvController {
  @GetMapping("/{id}") public String get(@PathVariable String id) { return ""; }
  @GetMapping("/search") public String search() { return ""; }
  @GetMapping("/count") public String count() { return ""; }
}
"""


def test_h1_dynamic_call_with_known_host_is_not_projected_on_every_route(tmp_path):
    """H1: an ordinary call's host marker never makes it a configured client."""
    _module(tmp_path, "catalog", {"src/main/java/c/A.java": _CATALOG_CONTROLLER})
    _module(tmp_path, "order-service", {"src/main/java/o/T.java": """
package o;
import org.springframework.web.client.RestTemplate;
public class T {
  private final RestTemplate restTemplate = new RestTemplate();
  public Object b(String sku) { return restTemplate.getForObject("http://catalog/products/" + sku, String.class); }
}
"""})

    edges = _rest_edges(tmp_path)

    assert [(e.from_service, e.to_service) for e in edges] == [("order-service", "catalog")]
    (edge,) = edges
    assert edge.to_endpoint is None, edge.to_endpoint
    assert edge.from_endpoint.topic == "GET /products/{sku}"
    assert edge.from_endpoint.topic_dynamic is True


def test_h1_fully_dynamic_call_keeps_one_service_relation(tmp_path):
    _module(tmp_path, "inventory-service", {"src/main/java/i/InvController.java": _INVENTORY_CONTROLLER})
    _module(tmp_path, "order-service", {"src/main/java/o/Helper.java": """
package o;
import org.springframework.web.client.RestTemplate;
public class Helper {
  private final RestTemplate restTemplate = new RestTemplate();
  private static final String BASE = "http://inventory-service";
  private String get(String path) { return restTemplate.getForObject(BASE + path, String.class); }
}
"""})

    edges = _rest_edges(tmp_path)

    assert len(edges) == 1, [(e.from_endpoint.topic, e.to_endpoint) for e in edges]
    assert edges[0].to_endpoint is None


def test_h2_gateway_java_routes_each_use_their_own_uri_and_line(tmp_path):
    """H2: the n-th ``.route(...)`` must not read the whole builder chain."""
    _module(tmp_path, "gateway", {"src/main/java/g/Routes.java": """
package g;
import org.springframework.cloud.gateway.route.RouteLocator;
import org.springframework.cloud.gateway.route.builder.RouteLocatorBuilder;
public class Routes {
  public RouteLocator routes(RouteLocatorBuilder builder) {
    return builder.routes()
        .route("users", r -> r.path("/users/**").uri("http://user-service:8080"))
        .route("orders", r -> r.path("/orders/**").uri("lb://order-service"))
        .build();
  }
}
"""})
    _module(tmp_path, "user-service", {"src/main/java/u/C.java": """
package u;
import org.springframework.web.bind.annotation.*;
@RestController @RequestMapping("/users")
public class C { @GetMapping("/{id}") public String g(@PathVariable String id) { return ""; } }
"""})
    _module(tmp_path, "order-service", {"src/main/java/o/C.java": """
package o;
import org.springframework.web.bind.annotation.*;
@RestController @RequestMapping("/orders")
public class C { @GetMapping("/{id}") public String g(@PathVariable String id) { return ""; } }
"""})

    edges = _rest_edges(tmp_path)

    by_route = {e.from_endpoint.topic: (e.to_service, e.from_endpoint.start_line) for e in edges}
    assert by_route == {
        "ANY /users/**": ("user-service", 8),
        "ANY /orders/**": ("order-service", 9),
    }, by_route


def test_h2_gateway_lb_uri_wins_over_a_later_http_url():
    call = _endpoint(
        "call", "ANY /orders/**", 1,
        'route(r -> r.path("/orders/**").uri("lb://order-service")'
        '.filters(f -> f.addRequestHeader("X-Doc", "http://user-service/doc")))',
        framework="spring-cloud-gateway", module="gateway",
    )

    resolution = resolve_rest_target_service(call, ["gateway", "order-service", "user-service"])

    assert resolution.service == "order-service", resolution


def test_h4_literal_route_wins_over_sibling_template(tmp_path):
    """H4 / ADR-77: Spring MVC prefers ``/items/count`` over ``/items/{id}``."""
    _module(tmp_path, "inventory-service", {"src/main/java/i/InvController.java": _INVENTORY_CONTROLLER})
    _module(tmp_path, "report-service", {"src/main/java/r/Rep.java": """
package r;
import org.springframework.web.client.RestTemplate;
public class Rep {
  private final RestTemplate restTemplate = new RestTemplate();
  public Object x() { return restTemplate.getForObject("http://inventory-service/items/count", String.class); }
  public Object y() { return restTemplate.getForObject("http://inventory-service/items/me", String.class); }
  public Object z(String path) { return restTemplate.getForObject("http://inventory-service/items/" + path, String.class); }
}
"""})

    edges = _rest_edges(tmp_path)

    targets = sorted((e.from_endpoint.topic, e.to_endpoint.topic) for e in edges if e.to_endpoint)
    assert targets == [
        ("GET /items/count", "GET /items/count"),
        ("GET /items/me", "GET /items/{id}"),
        ("GET /items/{path}", "GET /items/{id}"),
    ], targets


def test_h4_templated_call_without_aligned_route_keeps_every_candidate():
    serves = [_endpoint("serve", "GET /items/search", 1), _endpoint("serve", "GET /items/count", 2)]

    selected = select_matching_routes("GET /items/{path}", serves)

    assert [s.topic for s in selected] == ["GET /items/search", "GET /items/count"]


def test_h4_any_call_is_ranked_per_served_method():
    serves = [
        _endpoint("serve", "GET /items/{id}", 1),
        _endpoint("serve", "GET /items/count", 2),
        _endpoint("serve", "DELETE /items/{id}", 3),
    ]

    selected = select_matching_routes("ANY /items/count", serves)

    assert [s.topic for s in selected] == ["GET /items/count", "DELETE /items/{id}"]


def test_h4_template_constraint_is_honoured():
    assert paths_match("GET /orders/42", r"GET /orders/{id:\d+}")
    assert not paths_match("GET /orders/abc", r"GET /orders/{id:\d+}")
    assert paths_match("GET /orders/{orderId}", r"GET /orders/{id:\d+}")
    # A constraint Python cannot compile leaves the template unconstrained.
    assert paths_match("GET /orders/abc", r"GET /orders/{id:\p{Alpha}+}")


def test_h4_literal_call_does_not_reach_constrained_template(tmp_path):
    _module(tmp_path, "order-service", {"src/main/java/o/C.java": r"""
package o;
import org.springframework.web.bind.annotation.*;
@RestController @RequestMapping("/orders")
public class C {
  @GetMapping("/{id:\\d+}") public String byId(@PathVariable String id) { return ""; }
  @GetMapping("/{name:[a-z]+}") public String byName(@PathVariable String name) { return ""; }
}
"""})
    _module(tmp_path, "billing", {"src/main/java/b/B.java": """
package b;
import org.springframework.web.client.RestTemplate;
public class B {
  private final RestTemplate restTemplate = new RestTemplate();
  public Object a() { return restTemplate.getForObject("http://order-service/orders/latest", String.class); }
}
"""})

    edges = _rest_edges(tmp_path)

    assert [e.to_endpoint.topic for e in edges if e.to_endpoint] == ["GET /orders/{name:[a-z]+}"]


def test_h5_public_multi_label_host_does_not_name_an_internal_service(tmp_path):
    """H5: ``auth.okta.com`` is external; its first label is not module ``auth``."""
    _module(tmp_path, "auth", {"src/main/java/a/TokenController.java": """
package a;
import org.springframework.web.bind.annotation.*;
@RestController
public class TokenController { @PostMapping("/oauth2/token") public String t() { return ""; } }
"""})
    _module(tmp_path, "order-service", {"src/main/java/o/A.java": """
package o;
import org.springframework.web.client.RestTemplate;
public class A {
  private final RestTemplate restTemplate = new RestTemplate();
  private static final String IDP = "https://auth.okta.com";
  public Object o() { return restTemplate.postForObject("https://auth.okta.com/oauth2/token", null, String.class); }
  public Object p() { return restTemplate.postForObject(IDP + "/oauth2/token", null, String.class); }
}
"""})

    clear_analysis_caches()
    calls = [e for e in infer_framework_endpoints(tmp_path) if e.role == "call"]
    assert len(calls) == 2
    assert all("systemlens-api-domain" not in e.snippet for e in calls), [e.snippet for e in calls]
    assert _rest_edges(tmp_path) == []


def test_h5_ip_host_yields_no_service_hint(tmp_path):
    rel = _write(tmp_path, "src/main/java/Client.java", """
import org.springframework.web.client.RestTemplate;
class Client {
  RestTemplate restTemplate;
  private static final String BASE = "http://10.0.0.5:8080";
  void run() { restTemplate.getForObject(BASE + "/users", String.class); }
  void literal() { restTemplate.getForObject("http://10.0.0.5/users", String.class); }
}
""")
    calls = _calls(tmp_path, [rel])

    assert len(calls) == 2
    assert all("systemlens-api-domain" not in e.snippet for e in calls), [e.snippet for e in calls]
    for call in calls:
        resolution = resolve_rest_target_service(call, ["10", "users"])
        assert resolution.status == "unresolved" and resolution.hint is None, resolution


def test_h5_internal_host_shapes_and_exact_alias():
    assert service_hint_from_host("inventory-service") == "inventory-service"
    assert service_hint_from_host("inventory.shop.svc.cluster.local") == "inventory"
    assert service_hint_from_host("inventory.shop.svc") == "inventory"
    assert service_hint_from_host("auth.okta.com") == "auth.okta.com"
    assert service_hint_from_host("10.0.0.5") is None
    assert service_hint_from_host("inventory-") is None

    def call(url: str) -> MessageEndpoint:
        return _endpoint(
            "call", "GET /x", 1, f'restTemplate.getForObject("{url}/x", String.class)',
            framework="resttemplate", module="order-service",
        )

    services = ["order-service", "inventory", "auth"]
    cluster = resolve_rest_target_service(call("http://inventory.shop.svc.cluster.local:8080"), services)
    assert cluster.service == "inventory", cluster
    public = resolve_rest_target_service(call("https://auth.okta.com"), services)
    assert public.status == "unresolved", public
    aliased = resolve_rest_target_service(
        call("https://auth.okta.com"), services, service_aliases={"auth": ("auth.okta.com",)},
    )
    assert aliased.service == "auth", aliased
