"""Method-level projection of AST-extracted integration sites."""

import hashlib
from collections import defaultdict
from pathlib import Path

from systemlens.discovery.java import parser as java_parser
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.module_inventory import DiscoveredModule, module_identity

_INPUT_ROLES = {("rest", "serve"), ("kafka", "consume")}
_OUTPUT_ROLES = {("rest", "call"), ("kafka", "produce")}


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
                candidates = []
                for candidate in modules:
                    try:
                        prefix = candidate.path.resolve().relative_to(repo_root.resolve()).as_posix()
                    except ValueError:
                        continue
                    if path == prefix or path.startswith(f"{prefix}/"):
                        candidates.append((len(prefix), module_identity(candidate)))
                module = max(candidates, default=(0, None))[1]
            if module is None:
                continue
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
            methods.append(IntegrationMethod(
                id=_method_id(module, path, qualified_method, parameter_signature),
                module=module,
                qualified_method=qualified_method,
                path=path,
                start_line=start_line,
                end_line=end_line,
                input_endpoint_ids=inputs,
                output_endpoint_ids=outputs,
            ))
    return sorted(methods, key=lambda item: (item.module, item.path, item.start_line, item.id))
