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
