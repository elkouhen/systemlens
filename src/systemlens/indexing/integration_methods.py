"""Method-level projection of AST-extracted integration sites."""

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import yaml

from systemlens.discovery.java import parser as java_parser
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.module_inventory import DiscoveredModule, module_identity

_INPUT_ROLES = {("rest", "serve"), ("kafka", "consume")}
_OUTPUT_ROLES = {("rest", "call"), ("kafka", "produce")}


def _openapi_operation_id(repo_root: Path, endpoint: MessageEndpoint) -> str | None:
    """Return the exact OpenAPI operation ID carried by a contract endpoint.

    Generated controller interfaces commonly put the route declaration in the
    contract and leave their checked-in implementation with only ``@Override``.
    The contract remains the endpoint evidence; this helper supplies the
    explicit operation identifier required to associate it with that method.
    """
    if endpoint.framework != "openapi" or endpoint.system != "rest":
        return None
    try:
        method, route = endpoint.topic.split(" ", 1)
        contract_path = repo_root / endpoint.path
        text = contract_path.read_text(encoding="utf-8", errors="replace")
        document = json.loads(text) if contract_path.suffix == ".json" else yaml.safe_load(text)
        operation = document.get("paths", {}).get(route, {}).get(method.lower(), {})
    except (OSError, ValueError, yaml.YAMLError, AttributeError):
        return None
    operation_id = operation.get("operationId") if isinstance(operation, dict) else None
    return operation_id if isinstance(operation_id, str) and operation_id else None


def _declared_on_rest_controller(declaration, source: bytes) -> bool:
    """Whether a Java method belongs to a locally declared REST controller."""
    current = declaration.parent
    while current is not None:
        if current.type == "class_declaration":
            return any(
                java_parser.annotation_name(annotation, source) == "RestController"
                for annotation in java_parser.annotations_of(current)
            )
        current = current.parent
    return False


def _method_id(
    module: str, path: str, qualified_method: str, parameter_signature: str
) -> str:
    """Return a stable method identity, including Java overload parameters.

    ``qualified_method`` intentionally remains class-and-method only because
    CodeQL's call result uses that form.  It is not, however, a database key:
    Java permits overloaded methods.  The AST parameter node distinguishes
    those declarations without making the identity depend on a source line.
    """
    coordinate = f"{module}|{path}|{qualified_method}|{parameter_signature}"
    return hashlib.sha256(coordinate.encode()).hexdigest()[:16]


def _qualified_method_owner(declaration, root, source: bytes) -> str | None:
    """Return the lexical Java owner instead of borrowing one from an endpoint.

    A file can contain several declarations, including nested helper classes.
    Methods without a port cannot safely inherit the first endpoint owner's
    name from that file: identical helper method names would then collide in
    the persisted method projection.
    """
    package = next(
        (java_parser.node_text(source, node)
         .removeprefix("package").removesuffix(";").strip()
         for node in java_parser.walk(root)
         if node.type == "package_declaration"),
        "",
    )
    owners: list[str] = []
    current = declaration.parent
    type_nodes = {"class_declaration", "interface_declaration", "record_declaration", "enum_declaration"}
    while current is not None:
        if current.type in type_nodes:
            if name := java_parser.declaration_name(current, source):
                owners.append(name)
        current = current.parent
    if not owners:
        return None
    parts = [part for part in (package, *reversed(owners)) if part]
    return ".".join(parts)


def materialize_integration_methods(
    repo_root: Path, endpoints: list[MessageEndpoint], java_paths: list[str], modules: list[DiscoveredModule]
) -> list[IntegrationMethod]:
    """Persist methods containing HTTP/message inputs or outputs from the AST.

    Manifest endpoints deliberately do not participate: they have no Java
    method evidence and must not be promoted into an executable flow.
    """
    by_path: dict[str, list[MessageEndpoint]] = defaultdict(list)
    for endpoint in endpoints:
        if (
            endpoint.source == "code"
            and endpoint.path.endswith(".java")
            and endpoint.module
            and (endpoint.system, endpoint.role) in _INPUT_ROLES | _OUTPUT_ROLES
        ):
            by_path[endpoint.path].append(endpoint)

    methods: list[IntegrationMethod] = []
    overridden_rest_controller_method_ids: set[str] = set()
    for path in sorted(set(java_paths) | set(by_path)):
        if not path.endswith(".java"):
            continue
        path_endpoints = by_path[path]
        parsed = java_parser.parse_java(str(repo_root.resolve()), path)
        if parsed is None:
            continue
        source, root = parsed
        for declaration in java_parser.walk(root):
            if declaration.type != "method_declaration":
                continue
            name = java_parser.declaration_name(declaration, source)
            if name is None:
                continue
            start_line = declaration.start_point.row + 1
            end_line = declaration.end_point.row + 1
            contained = [
                endpoint for endpoint in path_endpoints
                if start_line <= endpoint.start_line <= end_line
            ]
            inputs = tuple(sorted(
                endpoint.id for endpoint in contained
                if (endpoint.system, endpoint.role) in _INPUT_ROLES
            ))
            outputs = tuple(sorted(
                endpoint.id for endpoint in contained
                if (endpoint.system, endpoint.role) in _OUTPUT_ROLES
            ))
            module = next((endpoint.module for endpoint in contained if endpoint.module), None)
            if module is None:
                # A CodeQL call chain may traverse a plain application method;
                # attribute it from a sibling integration method in its file.
                module = next((endpoint.module for endpoint in path_endpoints if endpoint.module), None)
            if module is None:
                module_candidates = []
                for discovered_module in modules:
                    try:
                        prefix = discovered_module.path.resolve().relative_to(repo_root.resolve()).as_posix()
                    except ValueError:
                        continue
                    if path == prefix or path.startswith(f"{prefix}/"):
                        module_candidates.append((len(prefix), module_identity(discovered_module)))
                module = max(module_candidates, default=(0, None))[1]
            if module is None:
                continue
            owner = _qualified_method_owner(declaration, root, source)
            if owner is None:
                owner = next(
                    (endpoint.qualified_name for endpoint in contained if endpoint.qualified_name),
                    next((endpoint.qualified_name for endpoint in path_endpoints if endpoint.qualified_name), None),
                )
            qualified_method = f"{owner}.{name}" if owner else name
            parameter_node = declaration.child_by_field_name("parameters")
            parameter_signature = (
                source[parameter_node.start_byte:parameter_node.end_byte]
                .decode("utf-8")
                if parameter_node is not None
                else "()"
            )
            method_id = _method_id(module, path, qualified_method, parameter_signature)
            methods.append(IntegrationMethod(
                id=method_id,
                module=module,
                qualified_method=qualified_method,
                path=path,
                start_line=start_line,
                end_line=end_line,
                input_endpoint_ids=inputs,
                output_endpoint_ids=outputs,
            ))
            if (
                _declared_on_rest_controller(declaration, source)
                and any(
                    java_parser.annotation_name(annotation, source) == "Override"
                    for annotation in java_parser.annotations_of(declaration)
                )
            ):
                overridden_rest_controller_method_ids.add(method_id)

    endpoint_ids_with_method = {
        endpoint_id
        for method in methods
        for endpoint_id in (*method.input_endpoint_ids, *method.output_endpoint_ids)
    }
    for endpoint in endpoints:
        if (
            endpoint.id in endpoint_ids_with_method
            or (endpoint.system, endpoint.role) != ("rest", "serve")
            or endpoint.framework != "openapi"
        ):
            continue
        operation_id = _openapi_operation_id(repo_root, endpoint)
        if operation_id is None:
            continue
        method_candidates = [
            method for method in methods
            if (
                method.id in overridden_rest_controller_method_ids
                and method.module == endpoint.module
                and method.qualified_method.rsplit(".", 1)[-1] == operation_id
            )
        ]
        # An operation ID is a contract-level name. Attribute it only when it
        # has exactly one local Java implementation; overloads and helpers
        # remain unresolved rather than being guessed.
        if len(method_candidates) != 1:
            continue
        candidate = method_candidates[0]
        methods[methods.index(candidate)] = IntegrationMethod(
            **{
                **candidate.__dict__,
                "input_endpoint_ids": tuple(sorted((*candidate.input_endpoint_ids, endpoint.id))),
            }
        )
    return sorted(methods, key=lambda item: (item.module, item.path, item.start_line, item.id))
