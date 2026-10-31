"""Typed data operations from Spring Data JPA, ``EntityManager`` and JDBC calls.

Receivers are resolved by their declared Java type: a field, a constructor or
method parameter, or a local variable of the enclosing scope. A repository
interface is accepted when it extends a Spring Data base interface, directly
or through another interface of the same module. The entity must resolve to
exactly one indexed JPA entity; otherwise the operation keeps an unresolved
resource with a reason rather than a guessed entity.
"""

import hashlib
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Sequence

from tree_sitter import Node

from systemlens.discovery.build.jdbc_operations import (
    JDBC_RECEIVER_TYPES,
    class_string_constants,
    classify_jdbc_invocation,
    literal_string,
    string_literal_value,
)
from systemlens.discovery.java import parser as java_parser
from systemlens.domain.data_operations import (
    ENTITY_MANAGER_ACTIONS,
    DataOperation,
    action_facets,
    classify_jpql,
    classify_repository_method,
    classify_sql,
    compute_data_operation_id,
    literal_key_identity,
    sort_data_operations,
)
from systemlens.domain.module_inventory import JpaEntity
from systemlens.discovery.java.value_dependencies import trace_value_dependencies

JPA_BASE_INTERFACES = frozenset({
    "Repository", "CrudRepository", "ListCrudRepository", "PagingAndSortingRepository",
    "ListPagingAndSortingRepository", "JpaRepository", "JpaRepositoryImplementation",
    "RevisionRepository",
})
MONGO_BASE_INTERFACES = frozenset({"MongoRepository", "ReactiveMongoRepository"})
_ENTITY_MANAGER_TYPES = frozenset({"EntityManager"})
_TRANSACTION_TEMPLATE_TYPES = frozenset({"TransactionTemplate"})
_CLASS_LITERAL = re.compile(r"\b((?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*)\s*\.\s*class\b")
_DYNAMIC_QUERY_PARAMETERS = (
    "Specification", "Example", "Predicate", "Querydsl", "QueryByExample",
)


@dataclass(frozen=True)
class RepositoryMethod:
    """A method declared on a repository interface, with its query facts."""

    name: str
    parameter_count: int
    query: str | None = None
    query_literal: bool = True
    native: bool = False
    modifying: bool = False
    lock_mode: str | None = None
    dynamic_parameter: str | None = None


@dataclass(frozen=True)
class ResolvedRepository:
    """A repository interface resolved to its technology and entity text."""

    qualified_name: str
    technology: str
    entity_text: str | None
    methods: tuple[RepositoryMethod, ...] = ()


@dataclass
class _InterfaceDeclaration:
    package: str
    name: str
    type_parameters: tuple[str, ...]
    bases: tuple[tuple[str, tuple[str, ...]], ...]
    methods: tuple[RepositoryMethod, ...]
    imports: set[str] = field(default_factory=set)


def _package_and_imports(root: Node, source: bytes) -> tuple[str, set[str]]:
    package = ""
    imports: set[str] = set()
    for child in root.named_children:
        if child.type == "package_declaration":
            package = java_parser.node_text(source, child).removeprefix("package").removesuffix(";").strip()
        elif child.type == "import_declaration":
            imports.add(
                java_parser.node_text(source, child).removeprefix("import").removesuffix(";")
                .replace("static ", "").strip()
            )
    return package, imports


def _type_simple(type_text: str) -> str:
    """Strip generics, arrays and qualifiers from a declared type."""
    base = type_text.split("<", 1)[0].strip().rstrip("[]").strip()
    return base.rsplit(".", 1)[-1]


def _type_arguments(generic: Node, source: bytes) -> tuple[str, tuple[str, ...]]:
    name_node = generic.child_by_field_name("name") if generic.type == "generic_type" else None
    if generic.type != "generic_type":
        return _type_simple(java_parser.node_text(source, generic)), ()
    name = java_parser.node_text(source, name_node) if name_node is not None else ""
    if not name:
        first = generic.named_children[0] if generic.named_children else None
        name = java_parser.node_text(source, first) if first is not None else ""
    arguments = java_parser.child_by_type(generic, "type_arguments")
    return _type_simple(name), tuple(
        java_parser.node_text(source, child).strip()
        for child in (arguments.named_children if arguments is not None else [])
    )


def _annotation_value_text(annotation: Node, source: bytes, key: str | None) -> Node | None:
    return java_parser.annotation_argument(annotation, source, key) or (
        java_parser.annotation_argument(annotation, source) if key == "value" else None
    )


def _repository_method(method: Node, source: bytes) -> RepositoryMethod:
    name = java_parser.declaration_name(method, source) or ""
    parameters = java_parser.child_by_type(method, "formal_parameters")
    parameter_nodes = [
        child for child in (parameters.named_children if parameters is not None else [])
        if child.type in {"formal_parameter", "spread_parameter"}
    ]
    dynamic_parameter = next((
        kind for parameter in parameter_nodes
        for kind in _DYNAMIC_QUERY_PARAMETERS
        if kind in java_parser.node_text(source, parameter)
    ), None)
    query: str | None = None
    query_literal = True
    native = False
    modifying = False
    lock_mode: str | None = None
    for annotation in java_parser.annotations_of(method):
        annotation_name = java_parser.annotation_name(annotation, source)
        if annotation_name == "Query":
            value = _annotation_value_text(annotation, source, "value")
            if value is None:
                query, query_literal = None, False
            else:
                query = string_literal_value(value, source)
                query_literal = query is not None
            native_node = java_parser.annotation_argument(annotation, source, "nativeQuery")
            native = native_node is not None and java_parser.node_text(source, native_node).strip() == "true"
        elif annotation_name == "Modifying":
            modifying = True
        elif annotation_name == "Lock":
            value = _annotation_value_text(annotation, source, "value")
            lock_mode = java_parser.node_text(source, value).rsplit(".", 1)[-1] if value is not None else None
    return RepositoryMethod(
        name=name, parameter_count=len(parameter_nodes), query=query,
        query_literal=query_literal, native=native, modifying=modifying,
        lock_mode=lock_mode, dynamic_parameter=dynamic_parameter,
    )


def _interface_declarations(
    files: Sequence[tuple[str, bytes, Node]],
) -> dict[str, _InterfaceDeclaration]:
    declarations: dict[str, _InterfaceDeclaration] = {}
    for _path, source, root in files:
        package, imports = _package_and_imports(root, source)
        for declaration in java_parser.type_declarations(root):
            if declaration.type != "interface_declaration":
                continue
            name = java_parser.declaration_name(declaration, source)
            if not name:
                continue
            parameters = java_parser.child_by_type(declaration, "type_parameters")
            type_parameters = tuple(
                java_parser.node_text(source, child.named_children[0]).strip()
                for child in (parameters.named_children if parameters is not None else [])
                if child.type == "type_parameter" and child.named_children
            )
            extends = java_parser.child_by_type(declaration, "extends_interfaces")
            type_list = java_parser.child_by_type(extends, "type_list") if extends is not None else None
            bases = tuple(
                _type_arguments(child, source)
                for child in (type_list.named_children if type_list is not None else [])
            )
            body = declaration.child_by_field_name("body")
            methods = tuple(
                _repository_method(member, source)
                for member in (body.named_children if body is not None else [])
                if member.type == "method_declaration"
            )
            qualified = f"{package}.{name}" if package else name
            declarations[qualified] = _InterfaceDeclaration(
                package, name, type_parameters, bases, methods, imports,
            )
    return declarations


def resolve_repository_interfaces(
    files: Sequence[tuple[str, bytes, Node]],
) -> dict[str, ResolvedRepository]:
    """Resolve Spring Data repository interfaces, including module-local bases.

    The entity text is the first type argument of the Spring Data base, with
    the type parameters of intermediate module interfaces substituted. The
    result is keyed by qualified interface name.
    """
    declarations = _interface_declarations(files)
    by_simple: dict[str, list[str]] = {}
    for qualified, declaration in declarations.items():
        by_simple.setdefault(declaration.name, []).append(qualified)

    def visible(declaration: _InterfaceDeclaration, simple: str) -> str | None:
        candidates = [
            qualified for qualified in by_simple.get(simple, [])
            if qualified in declaration.imports
            or declarations[qualified].package == declaration.package
        ]
        return candidates[0] if len(candidates) == 1 else None

    def resolve(
        qualified: str, substitution: dict[str, str], depth: int,
    ) -> tuple[str, str | None] | None:
        declaration = declarations[qualified]
        if depth > 5:
            return None
        for base_name, arguments in declaration.bases:
            substituted = tuple(substitution.get(argument, argument) for argument in arguments)
            if base_name in JPA_BASE_INTERFACES | MONGO_BASE_INTERFACES:
                technology = "mongodb" if base_name in MONGO_BASE_INTERFACES else "jpa"
                entity = substituted[0] if substituted else None
                if entity is not None and entity in declaration.type_parameters:
                    entity = None
                return technology, entity
            base_qualified = visible(declaration, base_name)
            if base_qualified is None:
                continue
            base_declaration = declarations[base_qualified]
            nested = dict(zip(base_declaration.type_parameters, substituted))
            resolved = resolve(base_qualified, nested, depth + 1)
            if resolved is not None:
                return resolved
        return None

    repositories: dict[str, ResolvedRepository] = {}
    for qualified, declaration in declarations.items():
        resolved = resolve(qualified, {}, 0)
        if resolved is None:
            continue
        technology, entity = resolved
        methods = declaration.methods
        repositories[qualified] = ResolvedRepository(qualified, technology, entity, methods)
    return repositories


def _resolve_entity(
    entity_text: str | None, package: str, imports: set[str], entities: Sequence[JpaEntity],
) -> tuple[str | None, str | None]:
    """Return the unique indexed entity for a type text, or a reason."""
    if not entity_text:
        return None, "entity type not established"
    simple = entity_text.rsplit(".", 1)[-1]
    names = [entity.qualified_name for entity in entities]
    if "." in entity_text and entity_text in names:
        return entity_text, None
    candidates = sorted({
        qualified for qualified in names
        if qualified.rsplit(".", 1)[-1] == simple
    })
    visible = [
        qualified for qualified in candidates
        if qualified in imports or qualified == f"{package}.{simple}"
        or f"{qualified.rsplit('.', 1)[0]}.*" in imports
    ]
    if len(visible) == 1:
        return visible[0], None
    if len(candidates) == 1 and not visible:
        return None, f"entity {simple} is not visible from this file"
    if not candidates:
        return None, f"entity {simple} is not an indexed JPA entity"
    return None, f"entity {simple} is ambiguous: {', '.join(candidates)}"


@dataclass(frozen=True)
class _Receiver:
    type_text: str
    simple: str
    origin: str  # field | parameter | local


def _declared_type(identifier: str, invocation: Node, source: bytes) -> _Receiver | None:
    """Declared type of an identifier: the nearest parameter, local or field."""
    scope = invocation.parent
    line = invocation.start_point.row
    while scope is not None:
        if scope.type in {"method_declaration", "constructor_declaration", "lambda_expression"}:
            parameters = scope.child_by_field_name("parameters") or java_parser.child_by_type(
                scope, "formal_parameters",
            )
            for parameter in (parameters.named_children if parameters is not None else []):
                if parameter.type not in {"formal_parameter", "spread_parameter"}:
                    continue
                name = parameter.child_by_field_name("name")
                type_node = parameter.child_by_field_name("type")
                if name is not None and type_node is not None and (
                    java_parser.node_text(source, name) == identifier
                ):
                    text = java_parser.node_text(source, type_node).strip()
                    return _Receiver(text, _type_simple(text), "parameter")
            body = scope.child_by_field_name("body")
            for declaration in (java_parser.walk(body) if body is not None else []):
                if declaration.type != "local_variable_declaration" or declaration.start_point.row > line:
                    continue
                type_node = declaration.child_by_field_name("type")
                if type_node is None:
                    continue
                for declarator in declaration.named_children:
                    if declarator.type != "variable_declarator":
                        continue
                    name = declarator.child_by_field_name("name")
                    if name is not None and java_parser.node_text(source, name) == identifier:
                        text = java_parser.node_text(source, type_node).strip()
                        return _Receiver(text, _type_simple(text), "local")
            if scope.type != "lambda_expression":
                break
        scope = scope.parent
    owner = java_parser.enclosing(invocation, "class_declaration", "record_declaration", "enum_declaration")
    while owner is not None:
        for member in java_parser.walk(owner):
            if member.type != "field_declaration" or java_parser.enclosing(
                member, "class_declaration", "record_declaration", "enum_declaration",
            ) != owner:
                continue
            type_node = member.child_by_field_name("type")
            if type_node is None:
                continue
            for declarator in member.children:
                if declarator.type != "variable_declarator":
                    continue
                name = declarator.child_by_field_name("name")
                if name is not None and java_parser.node_text(source, name) == identifier:
                    text = java_parser.node_text(source, type_node).strip()
                    return _Receiver(text, _type_simple(text), "field")
        owner = java_parser.enclosing(owner, "class_declaration", "record_declaration", "enum_declaration")
    return None


def _receiver_identifier(receiver: Node | None, source: bytes) -> str | None:
    if receiver is None:
        return None
    if receiver.type == "identifier":
        return java_parser.node_text(source, receiver)
    if receiver.type == "field_access":
        text = java_parser.node_text(source, receiver)
        if text.startswith("this.") and re.fullmatch(r"this\.[A-Za-z_$][\w$]*", text):
            return text[5:]
    return None


def _argument_entity_text(argument: Node, source: bytes) -> str | None:
    """Type text of an ``EntityManager`` argument: ``new T(...)`` or a typed name."""
    if argument.type == "object_creation_expression":
        type_node = argument.child_by_field_name("type")
        return java_parser.node_text(source, type_node).strip() if type_node is not None else None
    if argument.type == "identifier":
        declared = _declared_type(java_parser.node_text(source, argument), argument, source)
        return declared.type_text if declared else None
    return None


def _class_literal(arguments: list[Node], source: bytes) -> str | None:
    matches = {
        match.group(1)
        for argument in arguments
        for match in _CLASS_LITERAL.finditer(java_parser.node_text(source, argument))
    }
    return next(iter(matches)) if len(matches) == 1 else None


@dataclass
class _Context:
    module: str
    path: str
    package: str
    imports: set[str]
    entities: Sequence[JpaEntity]
    source: bytes
    constants: dict[str, str]
    repositories_by_simple: dict[str, list[ResolvedRepository]]


def _visible_repository(context: _Context, receiver: _Receiver) -> ResolvedRepository | None:
    candidates = context.repositories_by_simple.get(receiver.simple, [])
    if "." in receiver.type_text.split("<", 1)[0]:
        qualified = receiver.type_text.split("<", 1)[0].strip()
        return next((item for item in candidates if item.qualified_name == qualified), None)
    visible = [
        item for item in candidates
        if item.qualified_name in context.imports
        or item.qualified_name == f"{context.package}.{receiver.simple}"
    ]
    return visible[0] if len(visible) == 1 else None


def _operation(
    context: _Context, invocation: Node, owner_method: str, technology: str, operation: str,
    action: str, *, resource: str | None, resource_identity: str | None,
    key_expression: str | None = None, key_property: str | None = None,
    reason: str | None = None, receiver: str | None = None, confidence: str | None = None,
    guard: str | None = None, guard_scope: str | None = None,
) -> DataOperation:
    reads, writes = action_facets(action)
    line = invocation.start_point.row + 1
    return DataOperation(
        id=compute_data_operation_id(
            context.module, context.path, owner_method, line, technology, operation,
        ),
        module=context.module, path=context.path, owner_method=owner_method, line=line,
        end_line=invocation.end_point.row + 1, technology=technology, operation=operation,
        action=action, reads=reads, writes=writes, resource=resource,
        resource_identity=resource_identity, key_expression=key_expression,
        key_property=key_property,
        key_identity=literal_key_identity(resource_identity, key_property, key_expression),
        confidence=confidence or (
            "medium" if action != "unknown" and resource_identity else "low"
        ),
        unresolved_reason=reason, receiver=receiver, guard=guard, guard_scope=guard_scope,
    )


def _repository_operation(
    context: _Context, invocation: Node, owner_method: str, receiver_name: str,
    repository: ResolvedRepository, operation: str, arguments: list[Node],
) -> DataOperation | None:
    entity, entity_reason = _resolve_entity(
        repository.entity_text, context.package, context.imports, context.entities,
    )
    identity = f"jpa:{context.module}:{entity}" if entity else None
    declared = [
        method for method in repository.methods
        if method.name == operation and method.parameter_count == len(arguments)
    ] or [method for method in repository.methods if method.name == operation]
    method = declared[0] if len(declared) == 1 else None
    key_expression: str | None = None
    key_property: str | None = None
    reasons: list[str] = []
    action: str
    guard: str | None = None
    guard_scope: str | None = None
    if method is not None and method.lock_mode in {"PESSIMISTIC_READ", "PESSIMISTIC_WRITE"}:
        guard, guard_scope = "pessimistic_lock", "operation"
    if method is not None and (method.query is not None or not method.query_literal):
        if not method.query_literal:
            action, reasons = "unknown", ["@Query string is not a literal"]
        elif method.native:
            classification = classify_sql(method.query or "")
            if method.modifying or classification.action == "read":
                action = classification.action
            else:
                action = "unknown"
                reasons.append(f"{classification.action} native query without @Modifying")
            reasons.extend(["native query: resource kept on the JDBC table"])
            if classification.target:
                identity = f"jdbc:{context.module}:{classification.target}"
                entity = classification.target
            else:
                identity, entity = None, None
                reasons.append(classification.reason or "table not established")
        else:
            classification = classify_jpql(method.query or "", modifying=method.modifying)
            action = classification.action
            if classification.reason:
                reasons.append(classification.reason)
            if classification.target and entity and (
                classification.target.rsplit(".", 1)[-1] != entity.rsplit(".", 1)[-1]
            ):
                queried, queried_reason = _resolve_entity(
                    classification.target, context.package, context.imports, context.entities,
                )
                entity = queried
                identity = f"jpa:{context.module}:{queried}" if queried else None
                if queried_reason:
                    reasons.append(queried_reason)
    elif method is not None and method.dynamic_parameter is not None:
        action = "unknown"
        reasons.append(f"query built from a {method.dynamic_parameter} argument")
    else:
        classification_name = classify_repository_method(operation)
        action = classification_name.action
        if classification_name.reason:
            reasons.append(classification_name.reason)
        if classification_name.keyed and len(arguments) == 1:
            key_expression = java_parser.node_text(context.source, arguments[0]).strip()
            key_property = classification_name.key_property
    if entity_reason and identity is None:
        reasons.append(entity_reason)
    return _operation(
        context, invocation, owner_method, "jpa", operation, action,
        resource=entity, resource_identity=identity, key_expression=key_expression,
        key_property=key_property, reason="; ".join(reasons) or None,
        receiver=receiver_name, guard=guard, guard_scope=guard_scope,
    )


def _entity_manager_operation(
    context: _Context, invocation: Node, owner_method: str, receiver_name: str,
    operation: str, arguments: list[Node],
) -> DataOperation | None:
    source = context.source
    if operation in {"persist", "merge", "remove"}:
        entity_text = _argument_entity_text(arguments[0], source) if arguments else None
        entity, reason = _resolve_entity(entity_text, context.package, context.imports, context.entities)
        if entity_text is None:
            reason = "entity type of the argument not established"
        return _operation(
            context, invocation, owner_method, "jpa", operation, ENTITY_MANAGER_ACTIONS[operation],
            resource=entity, resource_identity=f"jpa:{context.module}:{entity}" if entity else None,
            reason=reason, receiver=receiver_name,
        )
    if operation in {"find", "getReference", "refresh"}:
        entity_text = _class_literal(arguments[:1], source) if arguments else None
        if entity_text is None and arguments:
            entity_text = _argument_entity_text(arguments[0], source)
        entity, reason = _resolve_entity(entity_text, context.package, context.imports, context.entities)
        key = (
            java_parser.node_text(source, arguments[1]).strip()
            if len(arguments) >= 2 and operation in {"find", "getReference"} else None
        )
        return _operation(
            context, invocation, owner_method, "jpa", operation, "read",
            resource=entity, resource_identity=f"jpa:{context.module}:{entity}" if entity else None,
            key_expression=key, reason=reason, receiver=receiver_name,
        )
    if operation in {"createQuery", "createNativeQuery", "createNamedQuery"}:
        if operation == "createNamedQuery":
            return _operation(
                context, invocation, owner_method, "jpa", operation, "unknown",
                resource=None, resource_identity=None,
                reason="named query not resolved", receiver=receiver_name,
            )
        text, reason = (
            literal_string(arguments[0], source, context.constants) if arguments else (None, "query argument missing")
        )
        if text is None:
            return _operation(
                context, invocation, owner_method, "jpa", operation, "unknown",
                resource=None, resource_identity=None, reason=reason, receiver=receiver_name,
            )
        if operation == "createNativeQuery":
            classification = classify_sql(text)
            identity = f"jdbc:{context.module}:{classification.target}" if classification.target else None
            return _operation(
                context, invocation, owner_method, "jpa", operation, classification.action,
                resource=classification.target, resource_identity=identity,
                reason=classification.reason, receiver=receiver_name,
            )
        # JPQL through EntityManager executes without @Modifying: the keyword
        # decides, and ``executeUpdate`` is the caller's responsibility.
        classification = classify_jpql(text, modifying=True)
        entity, entity_reason = _resolve_entity(
            classification.target, context.package, context.imports, context.entities,
        )
        reasons = [item for item in (classification.reason, entity_reason) if item]
        return _operation(
            context, invocation, owner_method, "jpa", operation, classification.action,
            resource=entity, resource_identity=f"jpa:{context.module}:{entity}" if entity else None,
            reason="; ".join(reasons) or None, receiver=receiver_name,
        )
    return None


def _jdbc_operation(
    context: _Context, invocation: Node, owner_method: str, receiver_name: str,
    operation: str, arguments: list[Node],
) -> DataOperation | None:
    classified = classify_jdbc_invocation(operation, arguments, context.source, context.constants)
    if classified is None:
        return None
    identity = f"jdbc:{context.module}:{classified.table}" if classified.table else None
    guard = (
        "conditional_update"
        if classified.action == "update" and any("version" in column.lower() for column in classified.where_columns)
        else None
    )
    return _operation(
        context, invocation, owner_method, "jdbc", operation, classified.action,
        resource=classified.table, resource_identity=identity,
        key_expression=classified.key_expression,
        key_property=(
            None if classified.key_column in {None, "id"} else classified.key_column
        ),
        reason=classified.reason, receiver=receiver_name,
        guard=guard, guard_scope="operation" if guard else None,
    )


_ISOLATIONS = {
    "READ_UNCOMMITTED": "read_uncommitted", "READ_COMMITTED": "read_committed",
    "REPEATABLE_READ": "repeatable_read", "SERIALIZABLE": "serializable",
}


@dataclass(frozen=True)
class TransactionScope:
    transaction_id: str | None = None
    propagation: str | None = None
    isolation: str | None = None
    read_only: bool | None = None


def _transactional_annotation(node: Node, source: bytes) -> Node | None:
    return next((
        annotation for annotation in java_parser.annotations_of(node)
        if java_parser.annotation_name(annotation, source) == "Transactional"
    ), None)


def _annotation_scope(annotation: Node, source: bytes, transaction_id: str) -> TransactionScope:
    """Read ``@Transactional`` attributes; absent isolation stays unknown."""
    propagation_node = java_parser.annotation_argument(annotation, source, "propagation")
    isolation_node = java_parser.annotation_argument(annotation, source, "isolation")
    read_only_node = java_parser.annotation_argument(annotation, source, "readOnly")
    propagation = (
        java_parser.node_text(source, propagation_node).rsplit(".", 1)[-1]
        if propagation_node is not None else "REQUIRED"
    )
    isolation_name = (
        java_parser.node_text(source, isolation_node).rsplit(".", 1)[-1]
        if isolation_node is not None else "DEFAULT"
    )
    read_only = (
        java_parser.node_text(source, read_only_node).strip() == "true"
        if read_only_node is not None else False
    )
    return TransactionScope(
        transaction_id, propagation, _ISOLATIONS.get(isolation_name), read_only,
    )


def transaction_scope(
    module: str, path: str, invocation: Node, owner: Node, declaration: Node, source: bytes,
) -> TransactionScope:
    """Scope evidence of an operation: ``@Transactional`` or a template lambda.

    A method without the annotation inherits the class annotation. A method
    called from a transactional method inherits nothing: propagation is a
    runtime decision. ``TransactionTemplate.execute`` lambdas form their own
    scope, keyed by the lambda's line.
    """
    class_name = java_parser.declaration_name(declaration, source) or ""
    method_name = java_parser.declaration_name(owner, source) or ""
    qualified = f"{class_name}.{method_name}"
    current: Node | None = invocation.parent
    while current is not None and current != owner:
        if current.type == "lambda_expression":
            argument_list = current.parent
            call = argument_list.parent if argument_list is not None else None
            if (
                argument_list is not None and argument_list.type == "argument_list"
                and call is not None and call.type == "method_invocation"
            ):
                receiver_node, operation, _arguments = java_parser.invocation_parts(call, source)
                receiver_name = _receiver_identifier(receiver_node, source)
                receiver = (
                    _declared_type(receiver_name, call, source) if receiver_name else None
                )
                if operation in {"execute", "executeWithoutResult"} and receiver is not None and (
                    receiver.simple in _TRANSACTION_TEMPLATE_TYPES
                ):
                    return TransactionScope(stable_scope_id(
                        module, path, f"{qualified}@{current.start_point.row + 1}",
                    ))
        current = current.parent
    annotation = _transactional_annotation(owner, source) or _transactional_annotation(
        declaration, source,
    )
    if annotation is None:
        return TransactionScope()
    return _annotation_scope(annotation, source, stable_scope_id(module, path, qualified))


def _with_scope(operation: DataOperation, scope: TransactionScope) -> DataOperation:
    if scope.transaction_id is None:
        return operation
    return replace(
        operation, transaction_id=scope.transaction_id, propagation=scope.propagation,
        isolation=scope.isolation, read_only=scope.read_only,
    )


def _with_optimistic_version(
    operation: DataOperation, entities: Sequence[JpaEntity],
) -> DataOperation:
    """Every write on an entity with a ``@Version`` field carries the guard."""
    if not operation.writes or operation.guard is not None or operation.resource is None:
        return operation
    versioned = any(
        entity.qualified_name == operation.resource and entity.version_field
        for entity in entities
    )
    if not versioned:
        return operation
    return replace(operation, guard="optimistic_version", guard_scope="resource")


def _entity_manager_type(receiver: _Receiver, imports: set[str]) -> bool:
    if receiver.simple not in _ENTITY_MANAGER_TYPES:
        return False
    qualified = receiver.type_text.split("<", 1)[0].strip()
    return (
        qualified in {"jakarta.persistence.EntityManager", "javax.persistence.EntityManager"}
        or "jakarta.persistence.EntityManager" in imports
        or "javax.persistence.EntityManager" in imports
        or "jakarta.persistence.*" in imports
        or "javax.persistence.*" in imports
    )


def discover_data_operations(
    module: str,
    files: Sequence[tuple[str, bytes, Node]],
    entities: Sequence[JpaEntity],
) -> tuple[DataOperation, ...]:
    """Extract JPA, ``EntityManager`` and JDBC operations from parsed files.

    ``files`` are ``(repository-relative path, source, root)`` triples of the
    module's production sources. Mongo operations come from the Mongo
    extractor and are projected separately.
    """
    repositories = resolve_repository_interfaces(files)
    by_simple: dict[str, list[ResolvedRepository]] = {}
    for resolved in repositories.values():
        if resolved.technology == "jpa":
            by_simple.setdefault(resolved.qualified_name.rsplit(".", 1)[-1], []).append(resolved)
    operations: list[DataOperation] = []
    for path, source, root in files:
        if root.has_error:
            continue
        package, imports = _package_and_imports(root, source)
        for declaration in java_parser.type_declarations(root):
            if declaration.type not in {"class_declaration", "record_declaration", "enum_declaration"}:
                continue
            context = _Context(
                module, path, package, imports, entities, source,
                class_string_constants(declaration, source), by_simple,
            )
            owners: dict[int, tuple[Node, list[DataOperation]]] = {}
            for invocation in java_parser.walk(declaration):
                if invocation.type != "method_invocation":
                    continue
                owner = java_parser.enclosing_method(invocation)
                if owner is None or java_parser.enclosing(
                    owner, "class_declaration", "record_declaration", "enum_declaration",
                ) != declaration:
                    continue
                owner_name = java_parser.declaration_name(owner, source)
                receiver_node, operation, arguments = java_parser.invocation_parts(invocation, source)
                receiver_name = _receiver_identifier(receiver_node, source)
                if owner_name is None or receiver_name is None:
                    continue
                receiver = _declared_type(receiver_name, invocation, source)
                if receiver is None:
                    continue
                result: DataOperation | None = None
                repository = _visible_repository(context, receiver)
                if repository is not None:
                    result = _repository_operation(
                        context, invocation, owner_name, receiver_name, repository,
                        operation, arguments,
                    )
                elif _entity_manager_type(receiver, imports):
                    result = _entity_manager_operation(
                        context, invocation, owner_name, receiver_name, operation, arguments,
                    )
                elif receiver.simple in JDBC_RECEIVER_TYPES:
                    result = _jdbc_operation(
                        context, invocation, owner_name, receiver_name, operation, arguments,
                    )
                if result is not None:
                    result = _with_scope(result, transaction_scope(
                        module, path, invocation, owner, declaration, source,
                    ))
                    owners.setdefault(owner.id, (owner, []))[1].append(
                        _with_optimistic_version(result, entities)
                    )
            for owner, owned in owners.values():
                operations.extend(_with_value_dependencies(owner, owned, source))
    return tuple(sort_data_operations(operations))


def _with_value_dependencies(
    owner: Node, operations: list[DataOperation], source: bytes,
) -> list[DataOperation]:
    """Record same-method read-to-write dependencies and inherited keys."""
    reads = {(item.line, item.operation): item.id for item in operations if item.reads}
    writes = {(item.line, item.operation): item.id for item in operations if item.writes}
    dependencies = trace_value_dependencies(source, owner, reads, writes)
    if not dependencies:
        return operations
    by_id = {item.id: item for item in operations}
    result: list[DataOperation] = []
    for item in operations:
        linked = dependencies.get(item.id)
        if not linked:
            result.append(item)
            continue
        read = by_id[linked[0]]
        updated = replace(item, value_dependencies=linked)
        if (
            item.key_expression is None and read.key_expression is not None
            and item.technology != "jdbc" and item.resource_identity == read.resource_identity
        ):
            # The written object is the read record: its key is inherited.
            updated = replace(
                updated, key_expression=read.key_expression, key_property=read.key_property,
                key_identity=read.key_identity,
            )
        result.append(updated)
    return result


def stable_scope_id(module: str, path: str, qualified_method: str) -> str:
    """Identity of a transaction scope: module, path and method."""
    return hashlib.sha256(f"{module}|{path}|{qualified_method}".encode("utf-8")).hexdigest()[:16]


def module_java_files(
    repo_root: Path, java_files: Sequence[Path],
) -> list[tuple[str, bytes, Node]]:
    """Parse production Java files once through the shared parse cache."""
    parsed: list[tuple[str, bytes, Node]] = []
    for java_file in java_files:
        relative = java_file.relative_to(repo_root).as_posix()
        result = java_parser.parse_java(str(repo_root), relative)
        if result is None:
            continue
        source, root = result
        parsed.append((relative, source, root))
    return parsed
