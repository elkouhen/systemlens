"""Method-level projection of AST-extracted integration sites."""

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path

import yaml

from systemlens.discovery.java import parser as java_parser
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.module_inventory import DiscoveredModule, module_identity

# A scheduler trigger is transient: it exists only while flows are built.
SCHEDULER_TRIGGER = ("scheduler", "trigger")
_INPUT_ROLES = {("rest", "serve"), ("kafka", "consume"), SCHEDULER_TRIGGER}
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


def _path_module(repo_root: Path, path: str, modules: Sequence[DiscoveredModule]) -> str | None:
    """Return the innermost discovered module containing a repository path."""
    return path_module_resolver(repo_root, modules)(path)


def path_module_resolver(
    repo_root: Path, modules: Sequence[DiscoveredModule],
) -> Callable[[str], str | None]:
    """Map a repository-relative path to its innermost discovered module.

    Module roots are resolved once; each lookup walks the path's ancestors,
    so it costs the path depth rather than the module count. A path outside
    every discovered module has no module identity.
    """
    root = repo_root.resolve()
    by_prefix: dict[str, str] = {}
    for discovered_module in modules:
        try:
            prefix = discovered_module.path.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        # A repository-root module has ``.`` as its relative prefix, while
        # its files still use paths such as ``src/main/java/Foo.java``.
        by_prefix.setdefault("" if prefix == "." else prefix, module_identity(discovered_module))

    def resolve(path: str) -> str | None:
        parts = path.replace("\\", "/").removeprefix("./").split("/")
        for end in range(len(parts), -1, -1):
            module = by_prefix.get("/".join(parts[:end]))
            if module is not None:
                return module
        return None

    return resolve


SCHEDULER_TRIGGER_PREFIX = "scheduler:"


def scheduled_trigger_endpoints(
    repo_root: Path,
    java_paths: list[str],
    endpoints: list[MessageEndpoint],
    modules: list[DiscoveredModule],
) -> list[MessageEndpoint]:
    """Return transient triggers for Spring ``@Scheduled`` methods.

    They let interprocedural flow construction start at a scheduler, like an
    HTTP route or a Kafka listener. They are never persisted as endpoints, so
    the architecture topology is unchanged; flow steps built from them carry
    no endpoint ID.
    """
    module_by_path = {
        endpoint.path: endpoint.module for endpoint in endpoints
        if endpoint.module and endpoint.source == "code"
    }
    triggers: list[MessageEndpoint] = []
    for path in sorted(set(java_paths)):
        if not path.endswith(".java"):
            continue
        parsed = java_parser.parse_java(str(repo_root.resolve()), path)
        if parsed is None or b"Scheduled" not in parsed[0]:
            continue
        source, root = parsed
        for declaration in java_parser.walk(root):
            if declaration.type != "method_declaration" or java_parser.is_anonymous_member(declaration):
                continue
            annotation = next((
                item for item in java_parser.annotations_of(declaration)
                if java_parser.annotation_name(item, source) == "Scheduled"
            ), None)
            if annotation is None:
                continue
            module = module_by_path.get(path) or _path_module(repo_root, path, modules)
            if module is None:
                continue
            schedule = "@Scheduled"
            for key in ("cron", "fixedRate", "fixedDelay"):
                argument = java_parser.annotation_argument(annotation, source, key)
                if argument is not None:
                    value = java_parser.node_text(source, argument).strip().strip('"')
                    schedule = f"@Scheduled({key}={value})"
                    break
            line = declaration.start_point.row + 1
            triggers.append(MessageEndpoint(
                id=f"{SCHEDULER_TRIGGER_PREFIX}{module}:{path}:{line}",
                role=SCHEDULER_TRIGGER[1], system=SCHEDULER_TRIGGER[0],
                topic=schedule, topic_dynamic=False, source="code",
                framework="spring-scheduling", path=path, start_line=line, end_line=line,
                snippet="", module=module,
                qualified_name=_qualified_method_owner(declaration, root, source),
            ))
    return triggers


def declared_parameter_types(declaration, source: bytes) -> tuple[str, ...] | None:
    """Parameter types of a method declaration as written, or ``None``.

    This is the ``parameter_types`` signature persisted for each method; the
    symbol pass uses it to tell apart declarations that share a line.
    """
    parameter_node = declaration.child_by_field_name("parameters")
    parameters = [
        child for child in parameter_node.named_children
        if child.type in {"formal_parameter", "spread_parameter"}
    ] if parameter_node is not None else []
    parameter_type_nodes = [item.child_by_field_name("type") for item in parameters]
    if not all(parameter_type_nodes):
        return None
    declared_types = []
    for item, type_node in zip(parameters, parameter_type_nodes, strict=True):
        assert type_node is not None
        dimensions = item.child_by_field_name("dimensions")
        declared_types.append(
            java_parser.node_text(source, type_node).strip()
            + ("..." if item.type == "spread_parameter" else "")
            + (
                "[]" * java_parser.node_text(source, dimensions).count("[")
                if dimensions is not None else ""
            )
        )
    return tuple(declared_types)


def _innermost_declaring_spans(
    declarations: list, endpoints: list[MessageEndpoint]
) -> dict[str, tuple[int, int]]:
    """Map each endpoint to the byte span of its innermost declaring method.

    A method of a local named class is a method of its own: a port in its
    body belongs to it, not to the enclosing method that declares the class
    (review F7). The narrowest line range that contains the port wins.
    """
    owners: dict[str, tuple[int, int]] = {}
    for endpoint in endpoints:
        best: tuple[int, int, int] | None = None
        for declaration in declarations:
            start_line = declaration.start_point.row + 1
            end_line = declaration.end_point.row + 1
            if not start_line <= endpoint.start_line <= end_line:
                continue
            width = declaration.end_byte - declaration.start_byte
            if best is None or width < best[0]:
                best = (width, declaration.start_byte, declaration.end_byte)
        if best is not None:
            owners[endpoint.id] = (best[1], best[2])
    return owners


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
    method_ids: set[str] = set()
    overridden_rest_controller_method_ids: set[str] = set()
    for path in sorted(set(java_paths) | set(by_path)):
        if not path.endswith(".java"):
            continue
        path_endpoints = by_path[path]
        parsed = java_parser.parse_java(str(repo_root.resolve()), path)
        if parsed is None:
            continue
        source, root = parsed
        # An anonymous class method has no source type of its own: its body
        # is part of the named method that creates the instance.
        declarations = [
            declaration for declaration in java_parser.walk(root)
            if declaration.type == "method_declaration"
            and not java_parser.is_anonymous_member(declaration)
        ]
        owner_span = _innermost_declaring_spans(declarations, path_endpoints)
        for declaration in declarations:
            name = java_parser.declaration_name(declaration, source)
            if name is None:
                continue
            start_line = declaration.start_point.row + 1
            end_line = declaration.end_point.row + 1
            span = (declaration.start_byte, declaration.end_byte)
            contained = [
                endpoint for endpoint in path_endpoints
                if owner_span.get(endpoint.id) == span
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
                module = _path_module(repo_root, path, modules)
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
            parameter_types = declared_parameter_types(declaration, source)
            method_id = _method_id(module, path, qualified_method, parameter_signature)
            if method_id in method_ids:
                # Invalid or generated Java can contain two declarations with
                # the same lexical signature. Keep the historical stable ID
                # for the first declaration and disambiguate later ones with
                # their source coordinates instead of failing the transaction.
                method_id = _method_id(
                    module,
                    path,
                    qualified_method,
                    f"{parameter_signature}|lines={start_line}:{end_line}",
                )
                collision = 2
                while method_id in method_ids:
                    method_id = _method_id(
                        module,
                        path,
                        qualified_method,
                        f"{parameter_signature}|lines={start_line}:{end_line}|collision={collision}",
                    )
                    collision += 1
            method_ids.add(method_id)
            methods.append(IntegrationMethod(
                id=method_id,
                module=module,
                qualified_method=qualified_method,
                path=path,
                start_line=start_line,
                end_line=end_line,
                input_endpoint_ids=inputs,
                output_endpoint_ids=outputs,
                parameter_types=parameter_types,
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
