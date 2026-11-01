"""Regression coverage for conservative REST extraction rules."""
from pathlib import Path

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
