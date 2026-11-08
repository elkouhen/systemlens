"""Method-level projection of AST-extracted integration sites."""

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, replace
from collections.abc import Callable, Sequence
from pathlib import Path

import yaml
from tree_sitter import Node

from systemlens.discovery.java import parser as java_parser
from systemlens.domain.code_flows import IntegrationMethod, inherited_method_anchor
from systemlens.domain.models import MESSAGE_SYSTEMS, MessageEndpoint
from systemlens.domain.module_inventory import DiscoveredModule, module_identity

# A scheduler trigger is transient: it exists only while flows are built.
SCHEDULER_TRIGGER = ("scheduler", "trigger")
_INPUT_ROLES = {("rest", "serve"), ("kafka", "consume"), SCHEDULER_TRIGGER}
_OUTPUT_ROLES = {("rest", "call"), *((system, "produce") for system in MESSAGE_SYSTEMS)}

# ADR-75: a constructor is ``Owner.<init>``, a type's static initializer
# code is ``Owner.<clinit>``. Neither can collide with a Java method name.
CONSTRUCTOR = "<init>"
STATIC_INITIALIZER = "<clinit>"
INITIALIZER_NAMES = frozenset({CONSTRUCTOR, STATIC_INITIALIZER})
# Types that run initializer code. An interface's fields are constants and a
# record's canonical constructor is implicit; neither gets an implicit node.
_INITIALIZED_TYPES = {"class_declaration", "enum_declaration", "record_declaration"}
_CALL_NODES = {
    "method_invocation", "method_reference", "object_creation_expression",
    "explicit_constructor_invocation",
}


# A written constructor; a record's compact constructor is its canonical
# constructor, whose parameters are the record components (BL-39).
CONSTRUCTOR_DECLARATIONS = frozenset({"constructor_declaration", "compact_constructor_declaration"})


def declaration_parameters(declaration: Node) -> Node | None:
    """The formal parameters of a method or constructor declaration.

    A compact constructor writes none: it takes those of its record header.
    """
    if declaration.type == "compact_constructor_declaration":
        record = java_parser.enclosing(declaration, "record_declaration")
        return record.child_by_field_name("parameters") if record is not None else None
    return declaration.child_by_field_name("parameters")


def is_initializer(qualified_method: str) -> bool:
    """Whether a qualified name is a constructor or static initializer node."""
    return qualified_method.rsplit(".", 1)[-1] in INITIALIZER_NAMES


@dataclass(frozen=True)
class InitializerDeclaration:
    """One constructor or static initializer node of a named type (ADR-75).

    ``node`` is the constructor declaration, compact or not, or the type
    declaration for
    an implicit constructor and for ``<clinit>``. These two have no
    declaration of their own: their lines are the type's header, from its
    first annotation to the opening brace, where the typed engines locate
    them.
    """

    name: str
    node: Node
    type_node: Node
    start_line: int
    end_line: int


def type_members(type_node: Node) -> list[Node]:
    """The member declarations of a type body, enum members included."""
    body = type_node.child_by_field_name("body")
    members: list[Node] = []
    for child in body.named_children if body is not None else []:
        if child.type == "enum_body_declarations":
            members.extend(child.named_children)
        else:
            members.append(child)
    return members


def initializer_members(type_node: Node, source: bytes, *, static: bool) -> list[Node]:
    """Field initializers and initializer blocks of one kind, in order.

    Instance members run in every constructor that does not delegate to
    ``this(...)``; static members run once, in ``<clinit>``.
    """
    members: list[Node] = []
    for member in type_members(type_node):
        if member.type == "static_initializer":
            member_static = True
        elif member.type == "block":
            member_static = False
        elif member.type == "field_declaration":
            if not any(
                declarator.type == "variable_declarator"
                and declarator.child_by_field_name("value") is not None
                for declarator in member.named_children
            ):
                continue
            modifiers = java_parser.modifiers_node(member)
            member_static = modifiers is not None and "static" in java_parser.node_text(
                source, modifiers
            ).split()
        else:
            continue
        if member_static == static:
            members.append(member)
    return members


def _contains_call(nodes: Sequence[Node]) -> bool:
    return any(
        descendant.type in _CALL_NODES
        for node in nodes for descendant in java_parser.walk(node)
    )


def delegates_to_this(constructor: Node) -> bool:
    """Whether a constructor starts with ``this(...)``.

    Such a constructor runs no initializer code itself: the constructor it
    delegates to does (JLS 12.5).
    """
    body = constructor.child_by_field_name("body")
    first = next((
        child for child in body.named_children
        if child.type not in {"line_comment", "block_comment"}
    ), None) if body is not None else None
    keyword = first.child_by_field_name("constructor") if (
        first is not None and first.type == "explicit_constructor_invocation"
    ) else None
    return keyword is not None and keyword.type == "this"


def initializer_declarations(root: Node, source: bytes) -> list[InitializerDeclaration]:
    """Constructor and static initializer nodes of a compilation unit.

    Every written constructor is a node, a record's compact constructor
    included (BL-39). A class or enum without one gets
    an implicit ``<init>`` node when its instance initializer code makes a
    call; a type gets ``<clinit>`` when its static initializer code does.
    Code that calls nothing would be a node without an edge.
    """
    found: list[InitializerDeclaration] = []
    for node in java_parser.walk(root):
        if node.type in CONSTRUCTOR_DECLARATIONS:
            type_node = java_parser.enclosing(node, *_INITIALIZED_TYPES)
            if type_node is not None:
                found.append(InitializerDeclaration(
                    CONSTRUCTOR, node, type_node,
                    node.start_point.row + 1, node.end_point.row + 1,
                ))
            continue
        if node.type not in _INITIALIZED_TYPES:
            continue
        body = node.child_by_field_name("body")
        if body is None:
            continue
        header = (node.start_point.row + 1, body.start_point.row + 1)
        members = type_members(node)
        if (
            node.type != "record_declaration"
            and not any(member.type in CONSTRUCTOR_DECLARATIONS for member in members)
            and _contains_call(initializer_members(node, source, static=False))
        ):
            found.append(InitializerDeclaration(CONSTRUCTOR, node, node, *header))
        if _contains_call(initializer_members(node, source, static=True)):
            found.append(InitializerDeclaration(STATIC_INITIALIZER, node, node, *header))
    return found


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
    An engine's call result uses that form.  It is not, however, a database key:
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


def scheduled_trigger_label(annotation: Node, source: bytes) -> str:
    """Name a ``@Scheduled`` trigger step, such as ``@Scheduled(cron=0 * * * * *)``.

    The same-method pass and the transient trigger of the call-graph join
    use this one label, so a scheduled flow keeps one identity whichever
    pass builds it (review F5).
    """
    for key in ("cron", "fixedRate", "fixedDelay"):
        argument = java_parser.annotation_argument(annotation, source, key)
        if argument is not None:
            value = java_parser.node_text(source, argument).strip().strip('"')
            return f"@Scheduled({key}={value})"
    return "@Scheduled"


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
            schedule = scheduled_trigger_label(annotation, source)
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
    parameter_node = declaration_parameters(declaration)
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


def _class_listener_handler_spans(
    root, source: bytes, endpoints: list[MessageEndpoint], owned: dict[str, tuple[int, int]],
) -> dict[str, set[tuple[int, int]]]:
    """Map a class-level ``@KafkaListener`` endpoint to its handler methods.

    The listener annotation sits on the class, outside every method; Spring
    dispatches each record to one of the class's ``@KafkaHandler`` methods,
    so each of them receives the consume endpoint.
    """
    pending = [
        endpoint for endpoint in endpoints
        if endpoint.id not in owned and (endpoint.system, endpoint.role) == ("kafka", "consume")
    ]
    if not pending:
        return {}
    classes = [node for node in java_parser.walk(root) if node.type == "class_declaration"]
    handlers: dict[str, set[tuple[int, int]]] = {}
    for endpoint in pending:
        containing = [
            node for node in classes
            if node.start_point.row + 1 <= endpoint.start_line <= node.end_point.row + 1
        ]
        if not containing:
            continue
        owner = min(containing, key=lambda node: node.end_byte - node.start_byte)
        spans = {
            (member.start_byte, member.end_byte)
            for member in java_parser.walk(owner)
            if member.type == "method_declaration"
            and java_parser.enclosing(member, "class_declaration") == owner
            and any(
                java_parser.annotation_name(annotation, source) == "KafkaHandler"
                for annotation in java_parser.annotations_of(member)
            )
        }
        if spans:
            handlers[endpoint.id] = spans
    return handlers


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

    def allocate_id(
        module: str, path: str, qualified_method: str, parameter_signature: str,
        start_line: int, end_line: int,
    ) -> str:
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
        return method_id
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
        handler_spans = _class_listener_handler_spans(root, source, path_endpoints, owner_span)
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
                or span in handler_spans.get(endpoint.id, ())
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
                # An engine call chain may traverse a plain application method;
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
            method_id = allocate_id(
                module, path, qualified_method, parameter_signature, start_line, end_line,
            )
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
        # Constructors and initializers are call-graph nodes, never ports:
        # an endpoint in their code stays without a method (ADR-75).
        for initializer in initializer_declarations(root, source):
            module = (
                next((endpoint.module for endpoint in path_endpoints if endpoint.module), None)
                or _path_module(repo_root, path, modules)
            )
            anchor = (
                initializer.node if initializer.node.type in CONSTRUCTOR_DECLARATIONS
                else initializer.type_node.child_by_field_name("body")
            )
            owner = _qualified_method_owner(anchor, root, source) if anchor is not None else None
            if module is None or owner is None:
                continue
            qualified_method = f"{owner}.{initializer.name}"
            parameter_node = (
                declaration_parameters(initializer.node)
                if initializer.node.type in CONSTRUCTOR_DECLARATIONS else None
            )
            parameter_signature = (
                source[parameter_node.start_byte:parameter_node.end_byte].decode("utf-8")
                if parameter_node is not None else "()"
            )
            methods.append(IntegrationMethod(
                id=allocate_id(
                    module, path, qualified_method, parameter_signature,
                    initializer.start_line, initializer.end_line,
                ),
                module=module,
                qualified_method=qualified_method,
                path=path,
                start_line=initializer.start_line,
                end_line=initializer.end_line,
                input_endpoint_ids=(),
                output_endpoint_ids=(),
                parameter_types=(
                    declared_parameter_types(initializer.node, source)
                    if initializer.node.type in CONSTRUCTOR_DECLARATIONS else ()
                ),
            ))

    endpoint_ids_with_method = {
        endpoint_id
        for method in methods
        for endpoint_id in (*method.input_endpoint_ids, *method.output_endpoint_ids)
    }
    # ADR-88: a route inherited and not overridden sits on the inheritor's
    # declaration; the inherited method owns it. Only a unique method
    # declared at the marked line is an owner.
    methods_by_start: dict[tuple[str, int], list[int]] = defaultdict(list)
    for index, method in enumerate(methods):
        if not is_initializer(method.qualified_method):
            methods_by_start[(method.path, method.start_line)].append(index)
    for endpoint in endpoints:
        if (
            endpoint.id in endpoint_ids_with_method
            or endpoint.source != "code"
            or (endpoint.system, endpoint.role) != ("rest", "serve")
        ):
            continue
        declared_at = inherited_method_anchor(endpoint.snippet)
        owners = methods_by_start.get(declared_at, []) if declared_at is not None else []
        if len(owners) != 1:
            continue
        inherited = methods[owners[0]]
        methods[owners[0]] = replace(
            inherited,
            input_endpoint_ids=tuple(sorted((*inherited.input_endpoint_ids, endpoint.id))),
        )
        endpoint_ids_with_method.add(endpoint.id)
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
