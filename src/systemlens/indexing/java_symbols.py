"""Transient, conservative Java symbols for missing call-graph edges.

This is not a Java compiler: unresolved types, generic substitutions and
ambiguous overloads remain unresolved. Names alone never establish dispatch.
"""

from collections import defaultdict
from dataclasses import dataclass, field as dataclass_field, replace
from pathlib import Path
import re
from typing import Sequence

from tree_sitter import Node

from systemlens.discovery.java import parser, patterns
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.domain.module_inventory import ModuleDependency
from systemlens.indexing.file_inventory import is_test_source
from systemlens.indexing.integration_methods import (
    CONSTRUCTOR,
    INITIALIZER_NAMES,
    STATIC_INITIALIZER,
    declared_parameter_types,
    delegates_to_this,
    initializer_declarations,
    initializer_members,
)
from systemlens.callgraph.model import MethodCall


_TYPES = {"class_declaration", "interface_declaration", "record_declaration", "enum_declaration"}
# Nodes whose children are member or top-level types; a type declared
# anywhere else is local to its block.
_MEMBER_CONTAINERS = {"program", "class_body", "interface_body", "enum_body_declarations", "annotation_type_body"}
_GENERIC_DECLARATIONS = {
    "method_declaration", "constructor_declaration", "class_declaration", "interface_declaration",
    "record_declaration",
}
_PRIMITIVES = {"byte", "short", "int", "long", "float", "double", "char", "boolean", "void"}
_JAVA_LANG = {
    "String", "Object", "Boolean", "Byte", "Short", "Integer", "Long", "Float", "Double", "Character",
    "Class", "Number", "CharSequence", "StringBuilder", "StringBuffer", "Enum", "Void", "Iterable",
    "Comparable", "Runnable", "Thread", "Throwable", "Exception", "RuntimeException", "Error",
    "IllegalArgumentException", "IllegalStateException", "Record", "Math", "System",
}
_LITERAL_TYPES = {
    "string_literal": "java.lang.String",
    "text_block": "java.lang.String",
    "character_literal": "char",
    "true": "boolean",
    "false": "boolean",
}
_BOXED = {
    "int": "java.lang.Integer", "long": "java.lang.Long", "double": "java.lang.Double",
    "float": "java.lang.Float", "boolean": "java.lang.Boolean", "char": "java.lang.Character",
    "byte": "java.lang.Byte", "short": "java.lang.Short",
}
_INTEGER_LITERALS = {
    "decimal_integer_literal", "hex_integer_literal", "octal_integer_literal", "binary_integer_literal",
}
_FLOATING_LITERALS = {"decimal_floating_point_literal", "hex_floating_point_literal"}
# Widening primitive conversions (JLS 5.1.2).
_WIDENING = {
    "byte": {"short", "int", "long", "float", "double"},
    "short": {"int", "long", "float", "double"},
    "char": {"int", "long", "float", "double"},
    "int": {"long", "float", "double"},
    "long": {"float", "double"},
    "float": {"double"},
}
_UNBOXED = {boxed: primitive for primitive, boxed in _BOXED.items()}
_NUMERIC_BOXES = {"java.lang." + name for name in ("Byte", "Short", "Integer", "Long", "Float", "Double")}
# Supertypes of ``String`` and the boxes, fixed by the Java SE API.
_FIXED_SUPERTYPES: dict[str, frozenset[str]] = {
    name: frozenset({
        "java.lang.Object", "java.io.Serializable", "java.lang.Comparable",
        "java.lang.constant.Constable",
        *(("java.lang.Number",) if name in _NUMERIC_BOXES else ()),
        *(("java.lang.CharSequence", "java.lang.constant.ConstantDesc") if name == "java.lang.String" else ()),
        *(("java.lang.constant.ConstantDesc",) if name in _NUMERIC_BOXES - {
            "java.lang.Byte", "java.lang.Short"} else ()),
    })
    for name in ("java.lang.String", *_BOXED.values())
}


def _literal_type(kind: str, text: str) -> str | None:
    """The type of a numeric literal, from its suffix (JLS 3.10)."""
    text = text.replace("_", "")
    if kind in _INTEGER_LITERALS:
        return "long" if text[-1:] in {"l", "L"} else "int"
    if kind == "hex_floating_point_literal":
        # ``f`` is a hexadecimal digit: the suffix follows the exponent.
        return "float" if text[-1:] in {"f", "F"} and "p" in text.lower() else "double"
    if kind in _FLOATING_LITERALS:
        return "float" if text[-1:] in {"f", "F"} else "double"
    return None


# Members every type has: an unqualified call to one of them never reaches an
# outer type, and its return type is fixed by ``java.lang.Object``.
_OBJECT_METHODS = {
    "equals", "hashCode", "toString", "getClass", "notify", "notifyAll", "wait", "clone", "finalize",
}
_OBJECT_RETURNS = {("toString", 0): "java.lang.String", ("hashCode", 0): "int", ("equals", 1): "boolean"}
_ENUM_METHODS = {"name", "ordinal", "values", "valueOf", "compareTo", "getDeclaringClass", "describeConstable"}
# Method names of the few external supertypes whose members are fixed by the
# Java SE API or by a Spring interface that declares only them (beyond the
# ``Object`` methods). Any other external supertype may declare any name.
_KNOWN_EXTERNAL_MEMBERS: dict[str, frozenset[str]] = {
    "org.springframework.security.core.userdetails.UserDetails": frozenset({
        "getAuthorities", "getPassword", "getUsername", "isAccountNonExpired",
        "isAccountNonLocked", "isCredentialsNonExpired", "isEnabled",
    }),
    "org.springframework.security.core.GrantedAuthority": frozenset({"getAuthority"}),
    "org.springframework.boot.CommandLineRunner": frozenset({"run"}),
    "org.springframework.boot.ApplicationRunner": frozenset({"run"}),
    "org.springframework.beans.factory.InitializingBean": frozenset({"afterPropertiesSet"}),
    "org.springframework.beans.factory.DisposableBean": frozenset({"destroy"}),
    "java.lang.Runnable": frozenset({"run"}),
    "java.util.concurrent.Callable": frozenset({"call"}),
    "java.io.Serializable": frozenset(),
    "java.lang.Cloneable": frozenset(),
    "java.lang.Comparable": frozenset({"compareTo"}),
    "java.lang.AutoCloseable": frozenset({"close"}),
    "java.io.Closeable": frozenset({"close"}),
}
# Supertypes of external JDK types, fixed by the Java SE API.
_KNOWN_EXTERNAL_SUPERTYPES: dict[str, tuple[str, ...]] = {
    "java.lang.Thread": ("java.lang.Runnable",),
    "java.util.concurrent.ExecutorService": ("java.util.concurrent.Executor",),
    "java.util.concurrent.ScheduledExecutorService": (
        "java.util.concurrent.ExecutorService", "java.util.concurrent.Executor",
    ),
}
_LOMBOK_GETTERS = {"Data", "Getter", "Value"}
# Functional JDK interfaces: a lambda or method reference can implement them
# anywhere, so dispatch on them is never exact.
_FUNCTIONAL_JDK = {"java.lang.Runnable", "java.util.concurrent.Callable"}
# JDK generic types whose type arguments type an untyped lambda parameter
# (BL-14): the element of a collection, stream or optional, or the key and
# value of a map. Each maps to its kind and its number of type arguments.
_GENERIC_CONTAINERS: dict[str, tuple[str, int]] = {
    **{name: ("iterable", 1) for name in (
        "java.lang.Iterable", "java.util.Collection", "java.util.List", "java.util.ArrayList",
        "java.util.LinkedList", "java.util.Set", "java.util.HashSet", "java.util.LinkedHashSet",
        "java.util.SortedSet", "java.util.NavigableSet", "java.util.TreeSet", "java.util.Queue",
        "java.util.Deque", "java.util.ArrayDeque", "java.util.Vector", "java.util.PriorityQueue",
        "java.util.concurrent.CopyOnWriteArrayList", "java.util.concurrent.CopyOnWriteArraySet",
        "java.util.concurrent.ConcurrentLinkedQueue", "java.util.concurrent.BlockingQueue",
        "java.util.concurrent.LinkedBlockingQueue",
    )},
    "java.util.stream.Stream": ("stream", 1),
    "java.util.Optional": ("optional", 1),
    **{name: ("map", 2) for name in (
        "java.util.Map", "java.util.HashMap", "java.util.LinkedHashMap", "java.util.SortedMap",
        "java.util.NavigableMap", "java.util.TreeMap", "java.util.concurrent.ConcurrentMap",
        "java.util.concurrent.ConcurrentHashMap",
    )},
    "java.util.Iterator": ("iterator", 1),
    "java.util.ListIterator": ("iterator", 1),
    "java.util.Map.Entry": ("entry", 2),
}
_ELEMENT_CALLBACKS = (
    "filter", "map", "flatMap", "forEach", "forEachOrdered", "peek", "anyMatch", "allMatch",
    "noneMatch", "takeWhile", "dropWhile", "mapToInt", "mapToLong", "mapToDouble",
)
# The functional parameter a JDK method declares, by receiver kind, method
# name, argument count, lambda position and lambda parameter count: the type
# argument of the receiver each lambda parameter takes, in order.
_LAMBDA_PARAMETERS: dict[tuple[str, str, int, int, int], tuple[int, ...]] = {
    ("iterable", "forEach", 1, 0, 1): (0,),
    ("iterable", "removeIf", 1, 0, 1): (0,),
    ("iterable", "replaceAll", 1, 0, 1): (0,),
    ("iterable", "sort", 1, 0, 2): (0, 0),
    **{("stream", name, 1, 0, 1): (0,) for name in _ELEMENT_CALLBACKS},
    ("stream", "sorted", 1, 0, 2): (0, 0),
    ("stream", "min", 1, 0, 2): (0, 0),
    ("stream", "max", 1, 0, 2): (0, 0),
    ("stream", "reduce", 1, 0, 2): (0, 0),
    ("stream", "reduce", 2, 1, 2): (0, 0),
    **{("optional", name, 1, 0, 1): (0,) for name in ("filter", "map", "flatMap", "ifPresent")},
    ("optional", "ifPresentOrElse", 2, 0, 1): (0,),
    ("map", "forEach", 1, 0, 2): (0, 1),
    ("map", "replaceAll", 1, 0, 2): (0, 1),
    ("map", "computeIfAbsent", 2, 1, 1): (0,),
    ("map", "computeIfPresent", 2, 1, 2): (0, 1),
    ("map", "compute", 2, 1, 2): (0, 1),
    ("map", "merge", 3, 2, 2): (1, 1),
}
# Calls that keep a container's type arguments under another kind:
# (receiver kind, method name, argument count) -> (result kind, arguments).
_GENERIC_CALLS: dict[tuple[str, str, int], tuple[str, tuple[int, ...]]] = {
    ("iterable", "stream", 0): ("stream", (0,)),
    ("iterable", "parallelStream", 0): ("stream", (0,)),
    **{("stream", name, 1): ("stream", (0,)) for name in (
        "filter", "peek", "limit", "skip", "sorted", "takeWhile", "dropWhile",
    )},
    **{("stream", name, 0): ("stream", (0,)) for name in (
        "distinct", "sorted", "parallel", "sequential", "unordered",
    )},
    ("stream", "findFirst", 0): ("optional", (0,)),
    ("stream", "findAny", 0): ("optional", (0,)),
    ("stream", "min", 1): ("optional", (0,)),
    ("stream", "max", 1): ("optional", (0,)),
    ("optional", "filter", 1): ("optional", (0,)),
    ("map", "keySet", 0): ("iterable", (0,)),
    ("map", "values", 0): ("iterable", (1,)),
    ("iterable", "iterator", 0): ("iterator", (0,)),
    ("stream", "iterator", 0): ("iterator", (0,)),
}
# Calls that return one type argument of their JDK container (BL-27):
# (receiver kind, method name, argument count) -> type argument index.
_ELEMENT_ACCESSORS: dict[tuple[str, str, int], int] = {
    ("iterable", "get", 1): 0,
    ("iterable", "getFirst", 0): 0,
    ("iterable", "getLast", 0): 0,
    ("optional", "get", 0): 0,
    ("optional", "orElse", 1): 0,
    ("optional", "orElseGet", 1): 0,
    ("optional", "orElseThrow", 0): 0,
    ("optional", "orElseThrow", 1): 0,
    ("map", "get", 1): 1,
    ("iterator", "next", 0): 0,
    ("entry", "getKey", 0): 0,
    ("entry", "getValue", 0): 1,
}
_ACCESSOR_CALLS = {(name, count) for _kind, name, count in _ELEMENT_ACCESSORS}


def _written_type(source: bytes, parameter: Node) -> str:
    """The erased simple name of a parameter type, as written."""
    parameter_type = parameter.child_by_field_name("type")
    if parameter_type is None or parameter.type != "formal_parameter":
        return ""
    text = re.sub(r"<.*>", "", parser.node_text(source, parameter_type)).strip()
    dimensions = parameter.child_by_field_name("dimensions")
    if dimensions is not None:
        text += "[]" * parser.node_text(source, dimensions).count("[")
    return text.rsplit(".", 1)[-1]


def _refers_to_anonymous_instance(source: bytes, receiver: Node | None) -> bool:
    """Whether a receiver inside an anonymous class body denotes that instance."""
    if receiver is None or receiver.type in {"this", "super"}:
        return True
    return receiver.type == "field_access" and parser.node_text(source, receiver).startswith("this.")


def _is_anonymous_body(node: Node) -> bool:
    return node.type == "class_body" and node.parent is not None and node.parent.type == "object_creation_expression"


_CALL_SITES = {"method_invocation", "method_reference"}
# Scopes that can introduce a pattern variable into one of their children.
_PATTERN_SCOPES = {
    "if_statement", "while_statement", "ternary_expression", "binary_expression", "switch_rule",
    "switch_block_statement_group", "block", "constructor_body",
}
_CONSTRUCTOR_SITES = {"object_creation_expression", "explicit_constructor_invocation"}
_INITIALIZER_MEMBERS = {"field_declaration", "block", "static_initializer"}


def _call_context(node: Node) -> Node | None:
    """The code a call site belongs to (ADR-62, ADR-75).

    A named method or constructor, or an initializer member (a field
    initializer, an initializer block) of a named type. The members of an
    anonymous class and lambda bodies belong to the code creating them.
    """
    current = node.parent
    while current is not None:
        if current.type in {"method_declaration", "constructor_declaration"}:
            if not parser.is_anonymous_member(current):
                return current
        elif (
            current.type in _INITIALIZER_MEMBERS and current.parent is not None
            and current.parent.type in {"class_body", "enum_body_declarations"}
            and not _is_anonymous_body(current.parent)
        ):
            return current
        current = current.parent
    return None


def _in_anonymous_body(node: Node, context: Node | None) -> bool:
    """Whether ``node`` lies in an anonymous class body below ``context``."""
    current = node.parent
    while current is not None and current != context:
        if _is_anonymous_body(current):
            return True
        current = current.parent
    return False


@dataclass
class _Unit:
    source: bytes
    package: str
    imports: dict[str, str]
    wildcards: list[str]
    # ``import static p.Util.helper;`` maps ``helper`` to ``p.Util``.
    static_imports: dict[str, list[str]] = dataclass_field(default_factory=dict)
    # ``import static p.Util.*;`` contributes ``p.Util``.
    static_wildcards: list[str] = dataclass_field(default_factory=list)


@dataclass
class _Method:
    method: IntegrationMethod
    module: str
    owner: str
    node: Node
    parameters: tuple[str | None, ...]
    concrete: bool
    # Erased simple type names as written, to match an override whose
    # parameter type comes from an external wildcard import.
    written: tuple[str, ...] = ()
    # A static or private method binds to its own declaration: no subtype
    # overrides it and it implements no ancestor's contract.
    binds_statically: bool = False
    # The last parameter is ``T...``: any arity from the fixed count up.
    varargs: bool = False


@dataclass(frozen=True)
class _Declarator:
    """How a scope declares a variable: its written type, if any.

    ``value`` is the initializer of a ``var``; ``lambda_slot`` is
    ``(lambda, position, count)`` for an untyped lambda parameter;
    ``element_of`` is the iterable of a ``var`` loop variable.
    """

    type_node: Node | None = None
    value: Node | None = None
    lambda_slot: tuple[Node, int, int] | None = None
    element_of: Node | None = None


class JavaSymbols:
    """Build once per join; index types by FQN and methods by name/arity."""

    def __init__(
        self, root: Path, methods: list[IntegrationMethod], source_paths: Sequence[str] = (),
        *, module_dependencies: Sequence[ModuleDependency] = (),
    ):
        self.units: dict[str, _Unit] = {}
        self.types: dict[str, list[tuple[str, Node]]] = defaultdict(list)
        self.path_modules: dict[str, str] = {}
        self.bases: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
        # The source superclass of a class, the type ``super`` names.
        self.superclasses: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
        self.methods: dict[str, _Method] = {}
        self.by_owner: dict[tuple[str, str, int, str], list[_Method]] = defaultdict(list)
        self.varargs_by_owner: dict[tuple[str, str, str], list[_Method]] = defaultdict(list)
        self.dispatch: dict[tuple[str, str, str, tuple[str | None, ...]], list[_Method]] = defaultdict(list)
        self._ancestors: dict[tuple[str, str], set[tuple[str, str]]] = {}
        self.inherited_dispatch: dict[tuple[str, str], set[str]] = defaultdict(set)
        # Supertypes outside the sources, ``None`` when the name is unresolved.
        self.external_bases: dict[tuple[str, str], set[str | None]] = defaultdict(set)
        # External interfaces and the source types that implement them directly.
        self.external_implementors: dict[str, set[tuple[str, str]]] = defaultdict(set)
        self.interface_witnesses: dict[MethodCall, MethodCall] = {}
        # ``var`` initializers being typed, to break reference cycles.
        self._typing: set[tuple[str, int]] = set()
        # Local classes: qualified name -> (path, declaration, enclosing block).
        self.local_types: dict[str, list[tuple[str, Node, Node]]] = defaultdict(list)
        self._levels: dict[tuple[str, str], list[list[tuple[str, str]]]] = {}
        # Types an anonymous class body extends or implements somewhere.
        self.anonymous_supertypes: set[str] = set()
        # Build dependencies between module identities (ADR-71), and the
        # modules each caller module sees, computed on demand.
        self.module_dependencies: dict[str, set[str]] = defaultdict(set)
        for dependency in module_dependencies:
            self.module_dependencies[dependency.source].add(dependency.target)
        self._visible_modules: dict[str, frozenset[str]] = {}
        self._external_targets: dict[
            tuple[str, str, int, tuple[str | None, ...] | None, frozenset[str]],
            list[IntegrationMethod],
        ] = {}
        by_path: dict[str, list[IntegrationMethod]] = defaultdict(list)
        for method in methods:
            by_path[method.path].append(method)
            self.path_modules.setdefault(method.path, method.module)
        # Include empty intermediate classes too: they have no method facts.
        paths = set(by_path)
        paths.update(path for path in source_paths if path.endswith(".java"))
        declarations: dict[str, dict[tuple[str, str, int], list[Node]]] = {}
        for path in sorted(paths):
            parsed = parser.parse_java(str(root.resolve()), path)
            if parsed is None:
                continue
            source, tree = parsed
            package = ""
            imports: dict[str, str] = {}
            wildcards: list[str] = []
            static_imports: dict[str, list[str]] = defaultdict(list)
            static_wildcards: list[str] = []
            for child in tree.named_children:
                text = parser.node_text(source, child)
                if child.type == "package_declaration":
                    package = text.removeprefix("package").removesuffix(";").strip()
                elif child.type == "import_declaration":
                    imported = text.removeprefix("import").removesuffix(";").strip()
                    if imported.startswith("static "):
                        imported = imported.removeprefix("static").strip()
                        if imported.endswith(".*"):
                            static_wildcards.append(imported[:-2])
                        elif "." in imported:
                            owner_name, member = imported.rsplit(".", 1)
                            static_imports[member].append(owner_name)
                    elif imported.endswith(".*"):
                        wildcards.append(imported[:-2])
                    else:
                        imports[imported.rsplit(".", 1)[-1]] = imported
            self.units[path] = _Unit(
                source, package, imports, wildcards, dict(static_imports), static_wildcards,
            )
            self.path_modules.setdefault(path, self.path_modules.get(path, path.split("/", 1)[0] if "/" in path else ""))
            nodes = list(parser.walk(tree))
            for node in nodes:
                if node.type in _TYPES:
                    self.types[self.owner(path, node)].append((path, node))
                    if node.parent is not None and (
                        node.parent.type not in _MEMBER_CONTAINERS or _is_anonymous_body(node.parent)
                    ):
                        self.local_types[self.owner(path, node)].append((path, node, node.parent))
                elif node.type == "object_creation_expression" and parser.child_by_type(node, "class_body"):
                    type_node = node.child_by_field_name("type")
                    if type_node is not None:
                        anonymous = self.resolve(
                            path, parser.node_text(source, type_node), self.owner(path, node),
                            allow_ambiguous=True, at=type_node,
                        )
                        if anonymous is not None:
                            self.anonymous_supertypes.add(anonymous)
                elif node.type == "annotation_type_declaration":
                    # An annotation type names parameters (``NewSpan``) but
                    # declares no method or supertype of its own.
                    outer = self.owner(path, node)
                    name = parser.declaration_name(node, source) or ""
                    self.types[f"{outer}.{name}" if outer else name].append((path, node))
            declarations[path] = defaultdict(list)
            for node in nodes:
                # An anonymous class method is no indexed method: its body
                # belongs to the method creating the instance.
                if node.type == "method_declaration" and not parser.is_anonymous_member(node):
                    key = (self.owner(path, node), parser.declaration_name(node, source) or "", node.start_point.row + 1)
                    declarations[path][key].append(node)
            # Constructors and initializers (ADR-75); an implicit constructor
            # and ``<clinit>`` are keyed on their type declaration.
            for initializer in initializer_declarations(tree, source):
                declarations[path][(
                    self.owner(path, initializer.node), initializer.name, initializer.start_line,
                )].append(initializer.node)
        for owner, locations in self.types.items():
            for path, node in locations:
                for child in node.named_children:
                    if child.type not in {"superclass", "super_interfaces", "extends_interfaces"}:
                        continue
                    for type_node in child.named_children:
                        candidates = type_node.named_children if type_node.type == "type_list" else [type_node]
                        for candidate in candidates:
                            base = self.resolve(
                                path,
                                parser.node_text(self.units[path].source, candidate),
                                owner,
                                allow_ambiguous=True,
                                at=candidate,
                            )
                            if base is not None and base in self.types and base != owner:
                                base_locations = self._type_candidates(base, path)
                                for base_path, _base_node in base_locations:
                                    base_module = self.path_modules.get(base_path, "")
                                    self.bases[(owner, self.path_modules.get(path, ""))].add(
                                        (base, base_module)
                                    )
                                    if child.type == "superclass":
                                        self.superclasses[(owner, self.path_modules.get(path, ""))].add(
                                            (base, base_module)
                                        )
                            elif base != owner:
                                type_key = (owner, self.path_modules.get(path, ""))
                                self.external_bases[type_key].add(base)
                                if base is not None and child.type != "superclass":
                                    self.external_implementors[base].add(type_key)
                                for known in _KNOWN_EXTERNAL_SUPERTYPES.get(base or "", ()):
                                    self.external_implementors[known].add(type_key)
        for path, path_methods in by_path.items():
            if path not in self.units:
                continue
            source = self.units[path].source
            for method in path_methods:
                name = method.qualified_method.rsplit(".", 1)[-1]
                owner = method.qualified_method.rsplit(".", 1)[0]
                matching = declarations[path].get((owner, name, method.start_line), [])
                if len(matching) > 1:
                    # Several declarations on one line: the persisted end
                    # line and written signature identify the node (S6).
                    matching = [
                        node for node in matching
                        if node.end_point.row + 1 == method.end_line
                        and method.parameter_types is not None
                        and declared_parameter_types(node, source) == method.parameter_types
                    ]
                if len(matching) != 1:
                    continue
                node = matching[0]
                # An implicit constructor and ``<clinit>`` stand on their type.
                owner_node = node if node.type in _TYPES else parser.enclosing(node, *_TYPES)
                if owner_node is None:
                    continue
                owner = self.owner(path, owner_node)
                if len(self._type_candidates(owner, path)) != 1:
                    continue
                initializer_node = name in INITIALIZER_NAMES
                parameters = None if node.type in _TYPES else node.child_by_field_name("parameters")
                parameter_types = []
                written: list[str] = []
                for parameter in parameters.named_children if parameters is not None else []:
                    if parameter.type not in {"formal_parameter", "spread_parameter"}:
                        continue
                    parameter_type = parameter.child_by_field_name("type")
                    # Varargs and unresolved generic parameters are intentionally
                    # not treated as an ordinary overload signature.
                    resolved_type = (
                        self.resolve(path, parser.node_text(source, parameter_type), owner, at=parameter_type)
                        if parameter_type is not None and parameter.type == "formal_parameter" else None
                    )
                    dimensions = parameter.child_by_field_name("dimensions")
                    if resolved_type is not None and dimensions is not None:
                        resolved_type += "[]" * parser.node_text(source, dimensions).count("[")
                    parameter_types.append(resolved_type)
                    written.append(_written_type(source, parameter))
                modifiers = parser.modifiers_node(node)
                info = _Method(
                    method, method.module, owner, node, tuple(parameter_types),
                    node.child_by_field_name("body") is not None, tuple(written),
                    # A constructor is not inherited and is never overridden.
                    initializer_node or bool(modifiers is not None and {"static", "private"} & set(
                        parser.node_text(source, modifiers).split()
                    )),
                    varargs=bool(parameters is not None and parameters.named_children and (
                        parameters.named_children[-1].type == "spread_parameter"
                    )),
                )
                self.methods[method.id] = info
                self.by_owner[(owner, name, len(parameter_types), method.module)].append(info)
                if info.varargs:
                    self.varargs_by_owner[(owner, name, method.module)].append(info)
                if info.concrete and all(value is not None for value in info.parameters):
                    for base_owner, base_module in self._dispatch_owners(info):
                        self.dispatch[(base_owner, base_module, name, info.parameters)].append(info)

        # Secondary indexes of ``by_owner``, so a call site reads only the
        # declarations of its receiver's types instead of scanning them all
        # (S13): by owner, name and arity across modules, in ``by_owner``
        # order, and the declared arities of a name per owner.
        self._by_owner_name: dict[tuple[str, str, int], list[_Method]] = defaultdict(list)
        self._by_owner_order: dict[tuple[str, str, int, str], int] = {}
        self._arities: dict[tuple[str, str], set[int]] = defaultdict(set)
        for order, ((owner_name, method_name, arity, module_name), values) in enumerate(self.by_owner.items()):
            self._by_owner_name[(owner_name, method_name, arity)].extend(values)
            self._by_owner_order[(owner_name, method_name, arity, module_name)] = order
            self._arities[(owner_name, method_name)].add(arity)

        self._complete_override_signatures()
        self._index_inherited_implementations()
        self._drop_unreachable_defaults()
        # ``dispatch`` is complete: index its signatures by name and arity so
        # a generic contract reads only the candidates it can match.
        self._dispatch_signatures: dict[
            tuple[str, str, str, int], list[tuple[str | None, ...]]
        ] = defaultdict(list)
        for dispatch_owner, dispatch_module, dispatch_name, signature in self.dispatch:
            self._dispatch_signatures[
                (dispatch_owner, dispatch_module, dispatch_name, len(signature))
            ].append(signature)
        # Concrete bodies whose signature stays unresolved: dispatch cannot
        # index them, yet they may override a contract (S9, ADR-81).
        self._unresolved_bodies: dict[tuple[str, int], list[_Method]] = defaultdict(list)
        for info in self.methods.values():
            if info.concrete and not info.binds_statically and None in info.parameters:
                name = info.method.qualified_method.rsplit(".", 1)[-1]
                self._unresolved_bodies[(name, len(info.parameters))].append(info)
        # ``@FeignClient`` interfaces: Spring implements them with a runtime
        # proxy that no source body stands for (Q8, ADR-81 rule 3).
        self.proxied_interfaces: set[tuple[str, str]] = {
            (owner, self.path_modules.get(path, ""))
            for owner, locations in self.types.items()
            for path, node in locations
            if node.type == "interface_declaration" and self._feign_client(path, node)
        }
        self._subtypes: dict[tuple[str, str], list[tuple[str, str, str, Node]]] = {}

    def _feign_client(self, path: str, declaration: Node) -> bool:
        unit = self.units[path]
        for annotation in parser.annotations_of(declaration):
            written = parser.node_text(unit.source, annotation.child_by_field_name("name") or annotation)
            if written.rsplit(".", 1)[-1] != "FeignClient":
                continue
            qualified = written if "." in written else unit.imports.get(written, "")
            if "feign" in qualified or any("feign" in package for package in unit.wildcards):
                return True
        return False

    def _concrete_subtypes(self, owner: str, module: str) -> list[tuple[str, str, str, Node]]:
        """Source classes, records and enums that can be the runtime type of
        a receiver typed ``owner``: ``(type, module, path, declaration)``."""
        key = (owner, module)
        found = self._subtypes.get(key)
        if found is None:
            found = []
            for candidate, locations in self.types.items():
                for path, node in locations:
                    if node.type not in {"class_declaration", "record_declaration", "enum_declaration"}:
                        continue
                    modifiers = parser.modifiers_node(node)
                    if node.type == "class_declaration" and modifiers is not None and (
                        "abstract" in parser.node_text(self.units[path].source, modifiers).split()
                    ):
                        continue
                    candidate_module = self.path_modules.get(path, "")
                    if key in self.ancestors(candidate, candidate_module):
                        found.append((candidate, candidate_module, path, node))
            self._subtypes[key] = found
        return found

    def _generated_member(self, path: str, declaration: Node, name: str, arity: int) -> bool:
        """Whether the compiler or Lombok writes ``name`` on this type.

        A record accessor and Lombok's getters, setters, ``equals``,
        ``hashCode`` and ``toString`` are implementors without an indexed
        body (ADR-81 rule 1).
        """
        source = self.units[path].source
        fields: list[tuple[str, Node | None]] = []
        if declaration.type == "record_declaration":
            parameters = declaration.child_by_field_name("parameters")
            for parameter in parameters.named_children if parameters is not None else []:
                ident = parameter.child_by_field_name("name")
                if ident is not None and arity == 0 and parser.node_text(source, ident) == name:
                    return True
            return False
        if declaration.type != "class_declaration":
            return False
        if (name, arity) in {("equals", 1), ("hashCode", 0)}:
            return self._lombok_annotated(path, declaration, {"Data", "Value", "EqualsAndHashCode"})
        if (name, arity) == ("toString", 0):
            return self._lombok_annotated(path, declaration, {"Data", "Value", "ToString"})
        prefix = next((prefix for prefix in ("get", "is", "set") if name.startswith(prefix)), None)
        if prefix is None or arity != (1 if prefix == "set" else 0):
            return False
        accessors = {"Getter"} if prefix != "set" else {"Setter"}
        class_level = self._lombok_annotated(
            path, declaration, accessors | ({"Data"} if prefix == "set" else {"Data", "Value"}),
        )
        body = declaration.child_by_field_name("body")
        for member in body.named_children if body is not None else []:
            if member.type != "field_declaration":
                continue
            modifiers = parser.modifiers_node(member)
            if modifiers is not None and "static" in parser.node_text(source, modifiers).split():
                continue
            if not class_level and not self._lombok_annotated(path, member, accessors):
                continue
            for child in member.named_children:
                ident = child.child_by_field_name("name")
                field_name = parser.node_text(source, ident) if ident is not None else ""
                fields.append((field_name, member.child_by_field_name("type")))
        suffix = name[len(prefix):]
        return any(
            field_name and field_name[:1].upper() + field_name[1:] == suffix
            and (prefix != "is" or (type_node is not None and parser.node_text(source, type_node) == "boolean"))
            for field_name, type_node in fields
        )

    def _incomplete_implementors(
        self, receiver_type: str, caller: _Method, name: str, arity: int, targets: list[IntegrationMethod],
    ) -> bool:
        """Whether some runtime type of the receiver has a body no target is.

        Every concrete source subtype of the receiver, in the modules the
        caller sees, must inherit or declare one of ``targets`` and generate
        no member of its own for the call. Otherwise its body is generated
        (record, Lombok), inherited from an external class or ``Object``, or
        unresolved, and no target is exact (S10, ADR-81 rule 1). A
        ``@FeignClient`` proxy is such a body too (Q8, rule 3).
        """
        receiver_module = self._receiver_module(receiver_type, caller)
        if self._proxied(receiver_type, receiver_module):
            return True
        receiver_ancestors = self.ancestors(receiver_type, receiver_module)
        # A subtype body with an unresolved signature may not override the
        # contract: it proves nothing (S9).
        bodies = {
            (info.owner, info.module)
            for target in targets
            for info in [self.methods.get(target.id)]
            if info is not None and (None not in info.parameters or (info.owner, info.module) in receiver_ancestors)
        }
        visible = self.visible_modules(caller.module) | {receiver_module}
        for owner, module, path, node in self._concrete_subtypes(receiver_type, receiver_module):
            if module not in visible:
                continue
            if (owner, module) not in bodies and self._generated_member(path, node, name, arity):
                return True
            if not bodies & self.ancestors(owner, module):
                return True
        return False

    def _proxied(self, receiver_type: str, receiver_module: str) -> bool:
        """Whether a ``@FeignClient`` proxy can be the receiver at run time."""
        key = (receiver_type, receiver_module)
        return any(key in self.ancestors(*client) for client in self.proxied_interfaces)

    def _complete_override_signatures(self) -> None:
        """Give an override the signature of the one contract it can match.

        A parameter type imported through an external wildcard (``import
        org.springframework.http.*``) cannot be resolved from source, while
        the interface may import it explicitly. Java overrides by erased
        signature, so a concrete method adopts an ancestor's resolved
        signature when exactly one ancestor declaration agrees on every
        resolved type and on the written simple name of every unresolved one.
        """
        for info in list(self.methods.values()):
            if (
                not info.concrete or info.binds_statically
                or all(value is not None for value in info.parameters)
            ):
                continue
            name = info.method.qualified_method.rsplit(".", 1)[-1]
            signatures = {
                contract.parameters
                for base_owner, base_module in self.ancestors(info.owner, info.module)
                if (base_owner, base_module) != (info.owner, info.module)
                for contract in self.by_owner.get((base_owner, name, len(info.parameters), base_module), [])
                if all(value is not None for value in contract.parameters)
                and all(
                    actual == expected if actual is not None
                    else expected is not None and expected.rsplit(".", 1)[-1] == written
                    for actual, expected, written in zip(
                        info.parameters, contract.parameters, info.written, strict=True,
                    )
                )
            }
            if len(signatures) != 1:
                continue
            info.parameters = next(iter(signatures))
            for base_owner, base_module in self.ancestors(info.owner, info.module):
                self.dispatch[(base_owner, base_module, name, info.parameters)].append(info)

    def _dispatch_owners(self, info: _Method) -> set[tuple[str, str]]:
        """The types whose contract ``info`` can implement.

        A static method only hides an ancestor's homonym and a private one
        is not inherited: both answer only for their own type (S2, S4).
        """
        if info.binds_statically:
            return {(info.owner, info.module)}
        return self.ancestors(info.owner, info.module)

    def _index_inherited_implementations(self) -> None:
        """Associate inherited public bodies with contracts on source subtypes."""
        by_type: dict[tuple[str, str], list[_Method]] = defaultdict(list)
        for info in self.methods.values():
            by_type[(info.owner, info.module)].append(info)
        for owner, locations in self.types.items():
            for path, node in locations:
                if node.type != "class_declaration":
                    continue
                ancestors = self.ancestors(owner, self.path_modules.get(path, ""))
                signatures: dict[tuple[str, tuple[str | None, ...]], list[_Method]] = defaultdict(list)
                for ancestor in ancestors:
                    for info in by_type.get(ancestor, []):
                        name = info.method.qualified_method.rsplit(".", 1)[-1]
                        signatures[(name, info.parameters)].append(info)
                for (name, parameters), declarations in signatures.items():
                    if any(parameter is None for parameter in parameters):
                        continue
                    bodies = [info for info in declarations if info.concrete and not any(
                        other is not info
                        and (info.owner, info.module) != (other.owner, other.module)
                        and (info.owner, info.module) in self.ancestors(other.owner, other.module)
                        for other in declarations
                    )]
                    for info in bodies:
                        if info.binds_statically:
                            continue
                        modifiers = next((child for child in info.node.children if child.type == "modifiers"), None)
                        words = set(parser.node_text(self.units[info.method.path].source, modifiers).split()) if modifiers else set()
                        if "public" not in words or words & {"static", "private"}:
                            continue
                        for base_owner, base_module in ancestors:
                            key = (base_owner, base_module, name, parameters)
                            if info not in self.dispatch[key]:
                                self.dispatch[key].append(info)
                            self.inherited_dispatch[(base_owner, base_module)].add(info.method.id)

    def _drop_unreachable_defaults(self) -> None:
        """Remove interface default bodies that no source type inherits.

        Java's "class wins" rule: a method a class declares or inherits from
        its superclass chain, abstract or concrete, takes precedence over an
        interface default, and a more specific interface's declaration
        overrides a default of its superinterface (S12). A default body
        stays a target when some source class, record or enum inherits it,
        when an anonymous class may, when no source type implements the
        interface at all, or when its signature is not fully resolved.
        """
        defaults = [
            info for info in self.methods.values()
            if info.concrete and not info.binds_statically
            and None not in info.parameters
            and self._owner_kind(info) == "interface_declaration"
        ]
        if not defaults:
            return
        classes = [
            (owner, self.path_modules.get(path, ""))
            for owner, locations in self.types.items()
            for path, node in locations
            if node.type in {"class_declaration", "record_declaration", "enum_declaration"}
        ]
        anonymous = {
            (supertype, module)
            for supertype in self.anonymous_supertypes
            for path, _node in self.types.get(supertype, [])
            for module in [self.path_modules.get(path, "")]
        }
        for info in defaults:
            key = (info.owner, info.module)
            if any(key in self.ancestors(*supertype) for supertype in anonymous):
                continue
            users = [user for user in classes if key in self.ancestors(*user)]
            if not users or any(self._inherits_default(user, info) for user in users):
                continue
            name = info.method.qualified_method.rsplit(".", 1)[-1]
            for (_owner, _module, method_name, parameters), targets in self.dispatch.items():
                if method_name == name and parameters == info.parameters and info in targets:
                    targets.remove(info)
            for implemented in self.inherited_dispatch.values():
                implemented.discard(info.method.id)

    @staticmethod
    def _owner_kind(info: _Method) -> str | None:
        owner_node = parser.enclosing(info.node, *_TYPES)
        return owner_node.type if owner_node is not None else None

    def _inherits_default(self, user: tuple[str, str], default: _Method) -> bool:
        """Whether the class ``user`` gets ``default``'s body (JLS 8.4.8)."""
        name = default.method.qualified_method.rsplit(".", 1)[-1]
        arity = len(default.parameters)

        def declares(owner: tuple[str, str]) -> bool | None:
            """Whether ``owner`` declares the signature; ``None`` if unknown."""
            declarations = self.by_owner.get((owner[0], name, arity, owner[1]), [])
            if any(candidate.parameters == default.parameters for candidate in declarations):
                return True
            if any(None in candidate.parameters for candidate in declarations):
                return None
            return False

        current: tuple[str, str] | None = user
        seen: set[tuple[str, str]] = set()
        while current is not None and current not in seen:
            seen.add(current)
            verdict = declares(current)
            if verdict is None:
                return True
            if verdict:
                return False
            current = self._superclass(*current)
        defaulting = (default.owner, default.module)
        for interface in self.ancestors(*user):
            if interface == defaulting or defaulting not in self.ancestors(*interface):
                continue
            verdict = declares(interface)
            if verdict is None:
                return True
            if verdict:
                # A subinterface redeclares it: its declaration wins.
                return False
        return True

    def owner(self, path: str, node: Node) -> str:
        names: list[str] = []
        current: Node | None = node
        while current is not None:
            if current.type in _TYPES:
                names.append(parser.declaration_name(current, self.units[path].source) or "")
            current = current.parent
        return ".".join(filter(None, [self.units[path].package, *reversed(names)]))

    def _type_candidates(self, qualified_name: str, path: str) -> list[tuple[str, Node]]:
        """Return visible declarations, preferring the caller's build module.

        Java source-only analysis can see two modules that declare the same
        qualified type, even though a normal build would select one through
        its module classpath.  The source module is the strongest available
        discriminator for internal flow discovery.  If it still leaves more
        than one candidate, resolution remains conservative.
        """
        candidates = self.types.get(qualified_name, [])
        module = self.path_modules.get(path)
        if module is None:
            return candidates
        same_module = [candidate for candidate in candidates if self.path_modules.get(candidate[0]) == module]
        candidates = same_module or candidates
        if len(candidates) > 1 and not is_test_source(path):
            # Production code never sees its module's test source set.
            production = [candidate for candidate in candidates if not is_test_source(candidate[0])]
            candidates = production or candidates
        return candidates

    def resolve(
        self, path: str, text: str, owner: str, *, allow_ambiguous: bool = False,
        at: Node | None = None,
    ) -> str | None:
        """The qualified source or external type ``text`` names in ``path``.

        ``at`` is the node where the name is written. A type parameter of an
        enclosing method or class declared there hides every type of the
        same simple name, so the name has no type (S8). A local class is
        visible only from its declaration to the end of its block, or from
        inside itself (S7).
        """
        text = re.sub(r"<.*>", "", text).strip()
        if at is not None and text.split(".", 1)[0].split("[", 1)[0] in self._type_parameters(at):
            return None
        if text.endswith("[]"):
            element = self.resolve(path, text[:-2], owner, allow_ambiguous=allow_ambiguous, at=at)
            return element + "[]" if element else None
        if text in _PRIMITIVES:
            return text
        unit = self.units[path]
        if (
            text in self.types and ("." in text or not unit.package)
            and not self._hidden_local(text, path, owner, at)
        ):
            return text if allow_ambiguous or len(self._type_candidates(text, path)) == 1 else None
        scope = owner
        while scope and scope != unit.package:
            nested = f"{scope}.{text}"
            if nested in self.types and not self._hidden_local(nested, path, owner, at):
                return nested if allow_ambiguous or len(self._type_candidates(nested, path)) == 1 else None
            scope = scope.rpartition(".")[0]
        first, *rest = text.split(".")
        if first in unit.imports:
            imported = ".".join([unit.imports[first], *rest])
            return imported if allow_ambiguous or len(self._type_candidates(imported, path)) <= 1 else None
        local = f"{unit.package}.{text}" if unit.package else text
        if local in self.types and not self._hidden_local(local, path, owner, at):
            return local if allow_ambiguous or len(self._type_candidates(local, path)) == 1 else None
        candidates = {
            f"{package}.{text}"
            for package in unit.wildcards
            if f"{package}.{text}" in self.types
            and (allow_ambiguous or len(self._type_candidates(f"{package}.{text}", path)) == 1)
        }
        if text in _JAVA_LANG:
            candidates.add(f"java.lang.{text}")
        # ``import java.util.concurrent.*`` makes ``Callable`` a JDK type
        # whose members are known; any other external wildcard stays open.
        candidates.update(
            f"{package}.{text}" for package in unit.wildcards
            if f"{package}.{text}" in _KNOWN_EXTERNAL_MEMBERS or f"{package}.{text}" in _KNOWN_EXTERNAL_SUPERTYPES
        )
        if len(candidates) == 1:
            candidate = next(iter(candidates))
            return candidate if allow_ambiguous or len(self.types.get(candidate, [None])) == 1 else None
        if not candidates and re.fullmatch(r"[\w$]+(?:\.[\w$]+)+", text):
            return text
        return None

    def _type_parameters(self, at: Node) -> set[str]:
        """Type parameter names declared by the methods and types enclosing ``at``."""
        names: set[str] = set()
        current: Node | None = at
        while current is not None:
            if current.type in _GENERIC_DECLARATIONS:
                parameters = current.child_by_field_name("type_parameters")
                for parameter in parameters.named_children if parameters is not None else []:
                    ident = next((
                        child for child in parameter.named_children
                        if child.type in {"type_identifier", "identifier"}
                    ), None)
                    if parameter.type == "type_parameter" and ident is not None:
                        names.add(ident.text.decode("utf-8") if ident.text is not None else "")
            current = current.parent
        return names

    def _hidden_local(self, qualified_name: str, path: str, owner: str, at: Node | None) -> bool:
        """Whether a local class in ``qualified_name`` is out of scope here.

        A local class is no member of its enclosing type: it is visible from
        its declaration to the end of the enclosing block, and inside itself.
        """
        if not self.local_types:
            return False
        parts = qualified_name.split(".")
        for end in range(len(parts), 0, -1):
            prefix = ".".join(parts[:end])
            scopes = self.local_types.get(prefix)
            if not scopes:
                continue
            if owner == prefix or owner.startswith(prefix + "."):
                return False
            return not (at is not None and any(
                scope_path == path and declaration.start_byte <= at.start_byte < block.end_byte
                for scope_path, declaration, block in scopes
            ))
        return False

    def ancestors(self, owner: str, module: str) -> set[tuple[str, str]]:
        key = (owner, module)
        if key not in self._ancestors:
            seen: set[tuple[str, str]] = set()
            pending = [key]
            while pending:
                current = pending.pop()
                if current not in seen:
                    seen.add(current)
                    pending.extend(self.bases.get(current, ()))
            self._ancestors[key] = seen
        return self._ancestors[key]

    def implementations(self, contract: _Method) -> list[_Method]:
        if contract.binds_statically:
            # No subtype body, even a homonym, overrides a static or
            # private method.
            return [contract] if contract.concrete else []
        name = contract.method.qualified_method.rsplit(".", 1)[-1]
        if not any(parameter is None for parameter in contract.parameters):
            return [
                *self.dispatch.get((contract.owner, contract.module, name, contract.parameters), []),
                *self._unresolved_overrides(contract, name),
            ]
        # Generic contracts can expose a type variable in the source AST
        # while a concrete implementation has a resolved parameter type.
        # Gather compatible arities and let the caller reject ambiguity.
        candidates = {
            candidate.method.id: candidate
            for parameters in self._dispatch_signatures.get(
                (contract.owner, contract.module, name, len(contract.parameters)), ()
            )
            for candidate in self.dispatch[(contract.owner, contract.module, name, parameters)]
            if all(
                expected is None or actual == expected
                for expected, actual in zip(contract.parameters, candidate.parameters)
            )
        }
        # A concrete body with an unresolved parameter type is never indexed
        # for dispatch, yet it still implements itself.
        if contract.concrete:
            candidates.setdefault(contract.method.id, contract)
        for override in self._unresolved_overrides(contract, name):
            candidates.setdefault(override.method.id, override)
        return list(candidates.values())

    def _unresolved_overrides(self, contract: _Method, name: str) -> list[_Method]:
        """Subtype bodies that may override ``contract`` but whose signature
        stays unresolved (varargs, an external wildcard import): possible
        implementors, never proof of a unique one (S9, ADR-81)."""
        key = (contract.owner, contract.module)
        return [
            info for info in self._unresolved_bodies.get((name, len(contract.parameters)), [])
            if info is not contract and (info.owner, info.module) != key
            and key in self.ancestors(info.owner, info.module)
            and all(
                expected == actual if expected is not None and actual is not None
                else expected_written == actual_written
                for expected, actual, expected_written, actual_written in zip(
                    contract.parameters, info.parameters, contract.written, info.written, strict=True,
                )
            )
        ]

    def constant_body_overrides(self, method: IntegrationMethod) -> list[IntegrationMethod]:
        """Bodies of enum constants that override ``method`` (H9).

        A typed engine that does not model a constant body reports a call
        on the enum type as ``exact`` to the enum's own body; these bodies
        are the other possible receivers.
        """
        contract = self.methods.get(method.id)
        if contract is None or contract.binds_statically:
            return []
        return [
            candidate.method for candidate in self.implementations(contract)
            if candidate.method.id != method.id and candidate.concrete
            and candidate.node.parent is not None
            and candidate.node.parent.type == "class_body"
            and candidate.node.parent.parent is not None
            and candidate.node.parent.parent.type == "enum_constant"
        ]

    def bridge(self, method: IntegrationMethod) -> IntegrationMethod | None:
        contract = self.methods.get(method.id)
        if contract is None or contract.concrete:
            return None
        candidates = self.implementations(contract)
        return candidates[0].method if len(candidates) == 1 else None

    def target(
        self, receiver_type: str, name: str, arity: int, module: str, path: str,
        argument_types: tuple[str | None, ...] | None = None,
    ) -> IntegrationMethod | None:
        candidates = self.possible_targets(
            receiver_type, name, arity, module, path, argument_types=argument_types,
        )
        return candidates[0] if len(candidates) == 1 else None

    def possible_targets(
        self, receiver_type: str, name: str, arity: int, module: str, path: str,
        argument_types: tuple[str | None, ...] | None = None,
    ) -> list[IntegrationMethod]:
        """Return all source-backed targets compatible with a receiver call."""
        return self._candidate_targets(
            receiver_type, name, arity, module, path, argument_types=argument_types,
        )[0]

    def _select_overload(
        self, contracts: list[_Method], argument_types: tuple[str | None, ...] | None,
    ) -> list[_Method]:
        """Keep the same-arity overload Java selects for the argument types.

        Java tries three phases (JLS 15.12.2): identity, widening primitive
        and subtyping first; then boxing and unboxing too; varargs last, a
        shape this arity-keyed lookup leaves out. The first phase with an
        applicable overload chooses the most specific one. A phase decides
        only when every argument and parameter type is known, so unknown
        arguments never narrow. Otherwise an overload survives when no known
        argument is provably incompatible with its parameter.
        """
        if len({candidate.parameters for candidate in contracts}) <= 1 or argument_types is None:
            return contracts
        signatures = {
            candidate.parameters for candidate in contracts
            if len(candidate.parameters) == len(argument_types)
        }
        known = None not in argument_types and all(None not in signature for signature in signatures)
        for boxing in (False, True) if known else ():
            verdicts = {
                signature: [
                    self._applicable(parameter, argument, boxing=boxing)
                    for parameter, argument in zip(signature, argument_types, strict=True)
                ]
                for signature in signatures
            }
            if any(False not in verdict and None in verdict for verdict in verdicts.values()):
                # An overload may apply in this phase: the choice is open.
                break
            applicable = [signature for signature, verdict in verdicts.items() if all(verdict)]
            if not applicable:
                continue
            specific = [
                signature for signature in applicable
                if all(
                    other == signature or all(
                        self._applicable(wider, narrower, boxing=False) is True
                        for narrower, wider in zip(signature, other, strict=True)
                    )
                    for other in applicable
                )
            ]
            if len(specific) != 1:
                return contracts
            return [candidate for candidate in contracts if candidate.parameters == specific[0]]
        selected = [
            candidate for candidate in contracts
            if len(candidate.parameters) == len(argument_types)
            and not any(
                self._applicable(parameter, argument, boxing=True) is False
                for parameter, argument in zip(candidate.parameters, argument_types, strict=True)
            )
        ]
        if len({candidate.parameters for candidate in selected}) == 1:
            return selected
        return contracts

    def _applicable(self, parameter: str | None, argument: str | None, *, boxing: bool) -> bool | None:
        """Whether an argument type converts to a parameter type on invocation.

        ``None`` when the sources cannot tell: an external type's hierarchy
        is unknown. Primitives, their boxes, ``String``, arrays and source
        types are decided (JLS 5.3), boxing and unboxing only when allowed.
        """
        if parameter is None or argument is None:
            return None
        if parameter == argument or parameter == "java.lang.Object" and argument not in _PRIMITIVES:
            return True
        if argument in _PRIMITIVES:
            if parameter in _PRIMITIVES:
                return parameter in _WIDENING.get(argument, ())
            if not boxing or argument not in _BOXED:
                return False
            return parameter in _FIXED_SUPERTYPES[_BOXED[argument]] or parameter == _BOXED[argument]
        if parameter in _PRIMITIVES:
            unboxed = _UNBOXED.get(argument)
            if not boxing or unboxed is None:
                return False
            return parameter == unboxed or parameter in _WIDENING.get(unboxed, ())
        if argument in _FIXED_SUPERTYPES:
            return parameter in _FIXED_SUPERTYPES[argument]
        if argument.endswith("[]") or parameter.endswith("[]"):
            if argument.endswith("[]") and parameter.endswith("[]"):
                element = self._applicable(parameter[:-2], argument[:-2], boxing=False)
                return element if parameter[:-2] not in _PRIMITIVES and argument[:-2] not in _PRIMITIVES else False
            return None if parameter not in self.types and parameter not in _FIXED_SUPERTYPES else False
        if argument in self.types:
            modules = {self.path_modules.get(path, "") for path, _node in self.types[argument]}
            ancestors = {
                ancestor for module in modules for ancestor in self.ancestors(argument, module)
            }
            if any(owner == parameter for owner, _module in ancestors):
                return True
            externals = {base for ancestor in ancestors for base in self.external_bases.get(ancestor, ())}
            if parameter in externals:
                return True
            if parameter in self.types or parameter in _FIXED_SUPERTYPES:
                # No external type extends a source type, and a source type
                # is never a ``String`` or a box.
                return False
            return None if externals else False
        # An external argument: only a source type, ``String`` or a box is
        # provably out of its reach.
        if parameter in self.types or parameter in _FIXED_SUPERTYPES:
            return False
        return None

    def _most_specific(self, contracts: list[_Method]) -> _Method | None:
        """The one declaration no other candidate overrides, if unique."""
        unique = {candidate.method.id: candidate for candidate in contracts}
        specific = [
            candidate for candidate in unique.values()
            if not any(
                other is not candidate
                and (other.owner, other.module) != (candidate.owner, candidate.module)
                and (candidate.owner, candidate.module) in self.ancestors(other.owner, other.module)
                for other in unique.values()
            )
        ]
        return specific[0] if len(specific) == 1 else None

    def _candidate_targets(
        self, receiver_type: str, name: str, arity: int, module: str, path: str,
        argument_types: tuple[str | None, ...] | None = None,
    ) -> tuple[list[IntegrationMethod], bool, _Method | None]:
        """Return compatible targets, whether module preference pruned some,
        and the declaration the call statically binds to, if unique.

        Preferring the caller's module is a heuristic, not a proof: a call
        whose candidates it pruned never becomes an exact dispatch.
        """
        receiver_locations = self._type_candidates(receiver_type, path)
        receiver_is_interface = any(node.type == "interface_declaration" for _path, node in receiver_locations)
        receiver_module = (
            self.path_modules.get(receiver_locations[0][0], module)
            if len(receiver_locations) == 1 else module
        )
        receiver_bases = self.ancestors(receiver_type, receiver_module)
        contracts = [candidate for owner in receiver_bases
                     for candidate in self.by_owner.get((owner[0], name, arity, owner[1]), [])]
        if len(receiver_locations) > 1:
            contracts = list(self._by_owner_name.get((receiver_type, name, arity), []))
        if not contracts:
            keys = sorted(
                (key for key in (
                    (owner, name, arity, owner_module) for owner, owner_module in receiver_bases
                ) if key in self._by_owner_order),
                key=self._by_owner_order.__getitem__,
            )
            contracts = [candidate for key in keys for candidate in self.by_owner[key]]
        if not contracts:
            # Java's third phase: a varargs method takes any arity from its
            # fixed parameter count up, when no fixed arity applies (S14).
            contracts = [
                candidate for owner, owner_module in receiver_bases
                for candidate in self.varargs_by_owner.get((owner, name, owner_module), [])
                if len(candidate.parameters) - 1 <= arity
            ]
        contracts = self._select_overload(contracts, argument_types)
        if len({candidate.parameters for candidate in contracts}) != 1:
            return [], False, None
        declaration = self._most_specific(contracts)
        candidates = {candidate.method.id: candidate for contract in contracts
                      for candidate in self.implementations(contract)
                      if any(
                          ancestor_owner == receiver_type
                          for ancestor_owner, _ancestor_module in self.ancestors(
                              candidate.owner, candidate.module
                          )
                      )
                      or (candidate.owner, candidate.module) in receiver_bases
                      or candidate.method.id in self.inherited_dispatch.get((receiver_type, receiver_module), set())}
        for contract in contracts:
            modifiers = next(
                (child for child in contract.node.children if child.type == "modifiers"), None
            )
            if (
                contract.concrete and modifiers is not None
                and re.search(r"\bfinal\b", parser.node_text(
                    self.units[contract.method.path].source, modifiers
                ))
            ):
                # A final inherited body remains a unique target even when a
                # generic parameter cannot be resolved to a concrete type.
                candidates[contract.method.id] = contract
        receiver_modules = {
            self.path_modules.get(candidate_path)
            for candidate_path, _ in self._type_candidates(receiver_type, path)
        }
        receiver_modules.discard(None)
        preferred_modules = receiver_modules or {module}
        local_owner_candidates = [
            candidate for candidate in candidates.values()
            if candidate.owner == receiver_type and candidate.module in preferred_modules
        ]
        inherited_candidates = [
            candidate for candidate in candidates.values()
            if candidate.owner != receiver_type
        ]
        candidates = {
            candidate.method.id: candidate
            for candidate in [*local_owner_candidates, *inherited_candidates]
        }
        # Overrides present on the declared receiver hide ancestor bodies.
        # Possible overrides on other runtime subtypes remain ambiguous.
        hidden: set[str] = set()
        for candidate in candidates.values():
            if (candidate.owner, candidate.module) in receiver_bases:
                hidden.update(
                    ancestor_owner
                    for ancestor_owner, _ancestor_module in self.ancestors(
                        candidate.owner, candidate.module
                    )
                    if ancestor_owner != candidate.owner
                )
        remaining = [candidate for candidate in candidates.values() if candidate.owner not in hidden]
        local_remaining = [candidate for candidate in remaining if candidate.module == module]
        if local_remaining and (not inherited_candidates or receiver_is_interface):
            pruned = len(local_remaining) < len(remaining)
            remaining = local_remaining
        else:
            pruned = False
        return [candidate.method for candidate in remaining], pruned, declaration

    def expression_type(
        self, info: _Method, node: Node | None, invocation: Node, depth: int = 0,
    ) -> str | None:
        """Return the static type of a simple expression, or None."""
        if node is None or depth > 16:
            return None
        source = self.units[info.method.path].source
        if node.type in _LITERAL_TYPES:
            return _LITERAL_TYPES[node.type]
        if node.type in _INTEGER_LITERALS or node.type in _FLOATING_LITERALS:
            return _literal_type(node.type, parser.node_text(source, node))
        if (
            node.type == "unary_expression" and node.named_children
            and parser.node_text(source, node).lstrip()[:1] in {"-", "+"}
        ):
            operand = self.expression_type(info, node.named_children[-1], invocation, depth + 1)
            # Unary numeric promotion (JLS 5.6).
            if operand in {"byte", "short", "char"}:
                return "int"
            return operand if operand in {"int", "long", "float", "double"} else None
        if node.type in {"object_creation_expression", "cast_expression"}:
            type_node = node.child_by_field_name("type")
            return self.resolve(
                info.method.path, parser.node_text(source, type_node), info.owner,
                allow_ambiguous=True, at=type_node,
            ) if type_node is not None else None
        if node.type == "parenthesized_expression" and node.named_children:
            return self.expression_type(info, node.named_children[0], invocation, depth + 1)
        if node.type == "ternary_expression":
            # Typed only when both branches have the same type.
            branches = {
                self.expression_type(info, node.child_by_field_name(branch), invocation, depth + 1)
                for branch in ("consequence", "alternative")
            }
            return branches.pop() if len(branches) == 1 else None
        if node.type in {"identifier", "this", "method_invocation", "field_access", "array_access"}:
            return self.receiver_type(info, node, invocation, depth + 1)
        return None

    def variable_type(self, info: _Method, name: str, invocation: Node, *, field_only: bool = False) -> str | None:
        """The declared type of the variable ``name`` visible at ``invocation``.

        Java resolves a simple name lexically, innermost scope first: the
        statements and parameters of every enclosing block, lambda and
        method, anonymous and local class methods included; then the fields
        of each enclosing class, declared or inherited, before those of its
        outer classes. ``field_only`` (``this.name``) reads only the fields
        of the innermost class. A declaration whose type is not written
        still shadows the outer ones (S5, S6, S14). An untyped lambda
        parameter takes a type only from a JDK callback (BL-14).
        """
        scope = invocation.parent
        while scope is not None:
            if not field_only:
                declarator = self._declarator(info, scope, name, invocation)
                if declarator is not None:
                    return self._declarator_type(info, declarator)
            if scope.type in _TYPES or _is_anonymous_body(scope):
                declared, field_type = self._class_scope_field(info, scope, name)
                if declared or field_only:
                    return field_type
            scope = scope.parent
        return None

    def _declarator(self, info: _Method, scope: Node, name: str, invocation: Node) -> _Declarator | None:
        """How ``scope`` declares ``name`` for ``invocation``: as a variable
        the scope itself introduces, or as a preceding local."""
        declarator = self._scoped_declaration(info, scope, name, invocation)
        if declarator is not None:
            return declarator
        source = self.units[info.method.path].source
        for declaration in reversed(scope.named_children):
            if declaration.type != "local_variable_declaration" or declaration.end_byte > invocation.start_byte:
                continue
            type_node = declaration.child_by_field_name("type")
            for child in declaration.named_children:
                ident = child.child_by_field_name("name")
                if ident is not None and parser.node_text(source, ident) == name and type_node is not None:
                    return _Declarator(type_node, child.child_by_field_name("value"))
        return None

    def _declarator_type(self, info: _Method, declarator: _Declarator) -> str | None:
        if declarator.lambda_slot is not None:
            return self._lambda_parameter_type(info, *declarator.lambda_slot)
        if declarator.element_of is not None:
            # ``for (var c : circles)``: the element type the iterable's
            # written type argument gives, read in the loop header.
            view = self._generic_view(info, declarator.element_of, declarator.element_of)
            return view[1][0] if view is not None and view[0] == "iterable" else None
        type_node = declarator.type_node
        if type_node is None:
            return None
        path = info.method.path
        text = parser.node_text(self.units[path].source, type_node).strip()
        if text == "var":
            # ``var`` takes the type of its initializer.
            return self._initializer_type(info, declarator.value)
        return self.resolve(path, text, info.owner, allow_ambiguous=True, at=type_node)

    def _lambda_parameter_type(self, info: _Method, lambda_node: Node, position: int, count: int) -> str | None:
        """The type of an untyped lambda parameter, when its target proves it.

        The lambda must be a direct argument of a JDK collection, stream,
        optional or map method whose functional parameter is fixed by the
        receiver's written type arguments, as ``Iterable<T>.forEach`` takes
        a ``Consumer<? super T>``. Any other target leaves it unknown.
        """
        arguments = lambda_node.parent
        call = arguments.parent if arguments is not None and arguments.type == "argument_list" else None
        if call is None or call.type != "method_invocation":
            return None
        receiver, name, call_arguments = parser.invocation_parts(call, self.units[info.method.path].source)
        index = next((
            number for number, argument in enumerate(call_arguments)
            if (argument.start_byte, argument.end_byte) == (lambda_node.start_byte, lambda_node.end_byte)
        ), None)
        if index is None or receiver is None:
            return None
        view = self._generic_view(info, receiver, call)
        if view is None:
            return None
        kind, type_arguments = view
        slots = _LAMBDA_PARAMETERS.get((kind, name, len(call_arguments), index, count))
        return type_arguments[slots[position]] if slots is not None else None

    def _generic_view(
        self, info: _Method, node: Node | None, invocation: Node, depth: int = 0,
    ) -> tuple[str, tuple[str | None, ...]] | None:
        """The JDK container kind of an expression and its type arguments.

        Read from the written type of a variable, field or ``this.field``,
        then through calls that keep the arguments (``stream()``,
        ``filter``, ``findFirst``, ``keySet()``). ``None`` for anything else.
        """
        if node is None or depth > 16:
            return None
        source = self.units[info.method.path].source
        if node.type == "parenthesized_expression" and node.named_children:
            return self._generic_view(info, node.named_children[0], invocation, depth + 1)
        if node.type == "method_invocation":
            receiver, name, arguments = parser.invocation_parts(node, source)
            inner = self._generic_view(info, receiver, node, depth + 1)
            rule = _GENERIC_CALLS.get((inner[0], name, len(arguments))) if inner is not None else None
            if inner is None or rule is None:
                return None
            return rule[0], tuple(inner[1][index] for index in rule[1])
        field_only = False
        if node.type == "field_access":
            target = node.child_by_field_name("object")
            field = node.child_by_field_name("field")
            if target is None or target.type != "this" or field is None:
                return None
            name, field_only = parser.node_text(source, field), True
        elif node.type == "identifier":
            name = parser.node_text(source, node)
        else:
            return None
        written = self._written_variable(info, name, invocation, field_only=field_only)
        if written is None:
            return None
        path, type_node, owner = written
        container = self.resolve(
            path, parser.node_text(self.units[path].source, type_node), owner, allow_ambiguous=True, at=type_node,
        )
        # The written declaration must be the one the type pass reads.
        if container not in _GENERIC_CONTAINERS or container != self.variable_type(
            info, name, invocation, field_only=field_only,
        ):
            return None
        kind, arity = _GENERIC_CONTAINERS[container]
        type_arguments = parser.child_by_type(type_node, "type_arguments")
        written_arguments = type_arguments.named_children if type_arguments is not None else []
        if len(written_arguments) != arity:
            return None
        return kind, tuple(self._type_argument(path, argument, owner) for argument in written_arguments)

    def _type_argument(self, path: str, argument: Node, owner: str) -> str | None:
        """A written type argument; ``? extends X`` gives ``X``, any other
        wildcard nothing."""
        if argument.type == "wildcard":
            bound = argument.named_children[-1] if argument.named_children else None
            text = parser.node_text(self.units[path].source, argument)
            if bound is None or not re.match(r"\?\s*extends\b", text):
                return None
            argument = bound
        return self.resolve(
            path, parser.node_text(self.units[path].source, argument), owner, allow_ambiguous=True, at=argument,
        )

    def _written_variable(
        self, info: _Method, name: str, invocation: Node, *, field_only: bool = False,
    ) -> tuple[str, Node, str] | None:
        """The written type of the variable ``name`` at ``invocation``:
        ``(path, type node, owner)``, following :meth:`variable_type`'s
        lexical lookup, or ``None`` when it is not written."""
        path = info.method.path
        scope = invocation.parent
        while scope is not None:
            if not field_only:
                declarator = self._declarator(info, scope, name, invocation)
                if declarator is not None:
                    if declarator.type_node is None or declarator.lambda_slot is not None:
                        return None
                    return path, declarator.type_node, info.owner
            if _is_anonymous_body(scope):
                declared, _type = self._class_scope_field(info, scope, name)
                if declared or field_only:
                    return None
            elif scope.type in _TYPES:
                owner = self.owner(path, scope)
                for level in self._ancestor_levels(owner, self.path_modules.get(path, info.module)):
                    found = [
                        (location, type_node, candidate)
                        for candidate, candidate_module in level
                        for location, declaration in self._module_locations(candidate, candidate_module)
                        for type_node in self._field_type_nodes(location, declaration, name)
                    ]
                    if found:
                        location, type_node, candidate = found[0]
                        return (location, type_node, candidate) if len(found) == 1 and type_node else None
                if field_only:
                    return None
            scope = scope.parent
        return None

    def _class_scope_field(self, info: _Method, scope: Node, name: str) -> tuple[bool, str | None]:
        """A field ``name`` that the class ``scope`` declares or inherits.

        ``scope`` is a named type declaration or an anonymous class body.
        An anonymous class's own fields come first, then those of its source
        supertype; an external supertype is assumed to declare none.
        """
        path = info.method.path
        source = self.units[path].source
        if scope.type in _TYPES:
            return self._member_field(self.owner(path, scope), self.path_modules.get(path, info.module), name)
        found: set[str | None] = set()
        for member in scope.named_children:
            type_node = member.child_by_field_name("type")
            if member.type != "field_declaration" or type_node is None:
                continue
            for child in member.named_children:
                ident = child.child_by_field_name("name")
                if ident is not None and parser.node_text(source, ident) == name:
                    found.add(self.resolve(
                        path, parser.node_text(source, type_node), info.owner, allow_ambiguous=True,
                        at=type_node,
                    ))
        if found:
            return True, next(iter(found)) if len(found) == 1 else None
        type_node = scope.parent.child_by_field_name("type") if scope.parent is not None else None
        supertype = self.resolve(
            path, parser.node_text(source, type_node), info.owner, allow_ambiguous=True, at=type_node,
        ) if type_node is not None else None
        if supertype is None or supertype not in self.types:
            return False, None
        return self._member_field(supertype, self._receiver_module(supertype, info), name)

    def _ancestor_levels(self, owner: str, module: str) -> list[list[tuple[str, str]]]:
        """``owner`` and its source ancestors, grouped by hierarchy distance."""
        key = (owner, module)
        levels = self._levels.get(key)
        if levels is None:
            levels = [[key]]
            seen = {key}
            while True:
                following = []
                for current in levels[-1]:
                    for base in sorted(self.bases.get(current, ())):
                        if base not in seen:
                            seen.add(base)
                            following.append(base)
                if not following:
                    break
                levels.append(following)
            self._levels[key] = levels
        return levels

    def _module_locations(self, owner: str, module: str) -> list[tuple[str, Node]]:
        locations = self.types.get(owner, [])
        same = [location for location in locations if self.path_modules.get(location[0], "") == module]
        return same or locations

    def _member_field(self, owner: str, module: str, name: str) -> tuple[bool, str | None]:
        """A field ``name`` of a source type, the nearest declaration first.

        A field hides any field of the same name farther up the hierarchy:
        the type itself is searched, then its direct supertypes, and so on
        (S11). Fields, interface constants, enum constants and record
        components count. Several declarations at one distance leave the
        type unknown, as Java reports them ambiguous.
        """
        for level in self._ancestor_levels(owner, module):
            found: set[str | None] = set()
            for candidate, candidate_module in level:
                for path, declaration in self._module_locations(candidate, candidate_module):
                    found.update(self._declared_fields(path, declaration, candidate, name))
            if found:
                return True, next(iter(found)) if len(found) == 1 else None
        return False, None

    def _declared_fields(self, path: str, declaration: Node, owner: str, name: str) -> set[str | None]:
        """Types of the fields ``name`` one type declaration writes."""
        source = self.units[path].source
        return {
            owner if type_node is None else self.resolve(
                path, parser.node_text(source, type_node), owner, allow_ambiguous=True, at=type_node,
            )
            for type_node in self._field_type_nodes(path, declaration, name)
        }

    def _field_type_nodes(self, path: str, declaration: Node, name: str) -> list[Node | None]:
        """Written types of the fields ``name`` one type declaration holds;
        ``None`` stands for an enum constant, typed by the enum itself."""
        source = self.units[path].source
        found: list[Node | None] = []
        if declaration.type == "record_declaration":
            parameters = declaration.child_by_field_name("parameters")
            for parameter in parameters.named_children if parameters is not None else []:
                ident = parameter.child_by_field_name("name")
                type_node = parameter.child_by_field_name("type")
                if ident is not None and type_node is not None and parser.node_text(source, ident) == name:
                    found.append(type_node)
        body = declaration.child_by_field_name("body")
        members = list(body.named_children) if body is not None else []
        for member in list(members):
            if member.type == "enum_body_declarations":
                members.extend(member.named_children)
        for member in members:
            if member.type in {"field_declaration", "constant_declaration"}:
                type_node = member.child_by_field_name("type")
                for child in member.named_children:
                    ident = child.child_by_field_name("name")
                    if ident is not None and type_node is not None and parser.node_text(source, ident) == name:
                        found.append(type_node)
            elif member.type == "enum_constant" and declaration.type == "enum_declaration":
                # Enum constants are fields of the enum type itself.
                ident = member.child_by_field_name("name")
                if ident is not None and parser.node_text(source, ident) == name:
                    found.append(None)
        return found

    def _initializer_type(self, info: _Method, value: Node | None) -> str | None:
        """The type of a ``var`` initializer, read where it is written.

        Names in the initializer resolve at the initializer's position, not
        at the call site: a later local never shadows the field it names, and
        two locals cannot type each other in a cycle. A declaration already
        being typed has no type, which ends any remaining cycle (S1, S2).
        """
        if value is None:
            return None
        key = (info.method.path, value.start_byte)
        if key in self._typing:
            return None
        self._typing.add(key)
        try:
            return self.expression_type(info, value, value)
        finally:
            self._typing.discard(key)

    def _scoped_declaration(
        self, info: _Method, scope: Node, name: str, invocation: Node,
    ) -> _Declarator | None:
        """A variable that ``scope`` itself declares for the code it encloses.

        Returns ``None`` when ``scope`` does not declare ``name``. A
        declarator without a written type (multi-catch, varargs), an untyped
        lambda parameter or a ``var`` loop variable still shadows fields.
        """
        source = self.units[info.method.path].source

        def named(node: Node | None) -> bool:
            return node is not None and parser.node_text(source, node) == name

        if scope.type == "enhanced_for_statement":
            body = scope.child_by_field_name("body")
            if named(scope.child_by_field_name("name")) and body is not None and (
                body.start_byte <= invocation.start_byte < body.end_byte
            ):
                type_node = scope.child_by_field_name("type")
                if type_node is not None and parser.node_text(source, type_node).strip() == "var":
                    return _Declarator(element_of=scope.child_by_field_name("value"))
                return _Declarator(type_node)
        elif scope.type == "catch_clause":
            parameter = parser.child_by_type(scope, "catch_formal_parameter")
            if parameter is not None and named(parameter.child_by_field_name("name")):
                catch_type = parser.child_by_type(parameter, "catch_type")
                alternatives = catch_type.named_children if catch_type is not None else []
                return _Declarator(alternatives[0] if len(alternatives) == 1 else None)
        elif scope.type == "try_with_resources_statement":
            resources = parser.child_by_type(scope, "resource_specification")
            for resource in resources.named_children if resources is not None else []:
                if (
                    resource.type == "resource"
                    and resource.end_byte <= invocation.start_byte
                    and named(resource.child_by_field_name("name"))
                ):
                    return _Declarator(resource.child_by_field_name("type"), resource.child_by_field_name("value"))
        elif scope.type == "lambda_expression":
            parameters = scope.child_by_field_name("parameters")
            if parameters is not None and parameters.type == "identifier":
                if named(parameters):
                    return _Declarator(lambda_slot=(scope, 0, 1))
            elif parameters is not None:
                untyped = [parameter for parameter in parameters.named_children if parameter.type == "identifier"]
                for parameter in parameters.named_children:
                    if parameter.type == "identifier" and named(parameter):
                        return _Declarator(lambda_slot=(scope, untyped.index(parameter), len(untyped)))
                    if parameter.type == "formal_parameter" and named(parameter.child_by_field_name("name")):
                        return _Declarator(parameter.child_by_field_name("type"))
        elif scope.type in _PATTERN_SCOPES:
            # A pattern variable where its ``instanceof`` is proven, or in
            # its ``switch`` case (JLS 6.3.1).
            child = invocation
            while child.parent is not None and child.parent != scope:
                child = child.parent
            type_node = patterns.flow_binding(source, scope, child, name) if child.parent == scope else None
            if type_node is not None:
                return _Declarator(type_node)
        elif scope.type in {"method_declaration", "constructor_declaration"}:
            # ``info`` itself, an anonymous class method in its body, or the
            # method enclosing a local class.
            parameters = scope.child_by_field_name("parameters")
            for parameter in parameters.named_children if parameters is not None else []:
                if parameter.type == "formal_parameter" and named(parameter.child_by_field_name("name")):
                    return _Declarator(parameter.child_by_field_name("type"))
                if parameter.type == "spread_parameter" and any(
                    named(child.child_by_field_name("name"))
                    for child in parameter.named_children if child.type == "variable_declarator"
                ):
                    # A varargs parameter is an array; it shadows outer names.
                    return _Declarator()
        return None

    def receiver_type(self, info: _Method, receiver: Node | None, invocation: Node, depth: int = 0) -> str | None:
        """Read declared receiver/return types without evaluating method bodies."""
        if depth > 16:
            return None
        source = self.units[info.method.path].source
        if receiver is None or receiver.type == "this":
            return info.owner
        if receiver.type == "super":
            superclass = self._superclass(info.owner, info.module)
            return superclass[0] if superclass is not None else None
        if receiver.type == "identifier":
            name = parser.node_text(source, receiver)
            declared = self.variable_type(info, name, invocation)
            if declared is not None or not name[:1].isupper():
                return declared
            # ``Type.method()``: a capitalized name that is no variable is a
            # static call on that source type.
            static_owner = self.resolve(info.method.path, name, info.owner, at=receiver)
            return static_owner if static_owner in self.types else None
        if receiver.type in {"object_creation_expression", "cast_expression", "parenthesized_expression"}:
            return self.expression_type(info, receiver, invocation, depth + 1)
        if receiver.type == "array_access":
            # ``values[0].name()``: the element type of the array.
            array = self.expression_type(info, receiver.child_by_field_name("array"), invocation, depth + 1)
            return array[:-2] if array is not None and array.endswith("[]") else None
        if receiver.type == "field_access" and (
            (this := receiver.child_by_field_name("field")) is not None and this.type == "this"
        ):
            return self._qualified_this(info, receiver)
        if receiver.type == "field_access" and parser.node_text(source, receiver).startswith("this."):
            return self.variable_type(info, parser.node_text(source, receiver)[5:], invocation, field_only=True)
        if receiver.type == "field_access":
            # ``obj.field`` or ``Type.CONSTANT``: the declared field type.
            target = receiver.child_by_field_name("object")
            field = receiver.child_by_field_name("field")
            owner = self.receiver_type(info, target, invocation, depth + 1) if target is not None else None
            if owner is None or field is None or owner not in self.types:
                return None
            return self._field_type(owner, parser.node_text(source, field), info.module)
        if receiver.type != "method_invocation":
            return None
        parent_receiver, name, arguments = parser.invocation_parts(receiver, source)
        if (name, len(arguments)) in _OBJECT_RETURNS:
            # ``java.lang.Object`` fixes these return types for every receiver.
            return _OBJECT_RETURNS[(name, len(arguments))]
        if (name, len(arguments)) in _ACCESSOR_CALLS:
            # ``circles.get(0)``, ``maybe.get()``: the element type a JDK
            # container's written type arguments give (BL-27).
            view = self._generic_view(info, parent_receiver, receiver, depth + 1)
            index = _ELEMENT_ACCESSORS.get((view[0], name, len(arguments))) if view is not None else None
            if view is not None and index is not None:
                return view[1][index]
        owner = self.receiver_type(info, parent_receiver, receiver, depth + 1)
        if owner is None:
            return None
        locations = self._type_candidates(owner, info.method.path)
        if len(locations) != 1:
            return None
        module = self.path_modules.get(locations[0][0], info.module)
        declarations = [candidate for candidate in self.by_owner.get((owner, name, len(arguments), module), [])]
        if not declarations:
            declarations = [candidate for base, base_module in self.ancestors(owner, module)
                            for candidate in self.by_owner.get((base, name, len(arguments), base_module), [])]
        if not declarations and not arguments:
            # A record component or a Lombok getter is an implicit accessor;
            # a declared accessor of the same name was found above and wins.
            return (
                self._record_component_type(owner, name, locations[0][0])
                or self._lombok_getter_type(owner, name, module)
            )
        if len({candidate.parameters for candidate in declarations}) != 1:
            return None
        returns: set[str | None] = set()
        for candidate in declarations:
            type_node = candidate.node.child_by_field_name("type")
            returns.add(self.resolve(
                candidate.method.path,
                parser.node_text(self.units[candidate.method.path].source, type_node),
                candidate.owner,
                at=type_node,
            ) if type_node is not None else None)
        return next(iter(returns)) if len(returns) == 1 else None

    def _qualified_this(self, info: _Method, receiver: Node) -> str | None:
        """``Outer.this``: the named type, when it encloses the expression."""
        target = receiver.child_by_field_name("object")
        if target is None:
            return None
        path = info.method.path
        named = self.resolve(path, parser.node_text(self.units[path].source, target), info.owner, at=target)
        scope = receiver.parent
        while scope is not None:
            if scope.type in _TYPES and self.owner(path, scope) == named:
                return named
            scope = scope.parent
        return None

    def _record_component_type(self, owner: str, name: str, path: str) -> str | None:
        """Declared type of the record component ``name`` of ``owner``."""
        for declaration_path, declaration in self._type_candidates(owner, path):
            if declaration.type != "record_declaration":
                continue
            source = self.units[declaration_path].source
            parameters = declaration.child_by_field_name("parameters")
            for parameter in parameters.named_children if parameters is not None else []:
                ident = parameter.child_by_field_name("name")
                type_node = parameter.child_by_field_name("type")
                if (
                    parameter.type == "formal_parameter" and ident is not None and type_node is not None
                    and parser.node_text(source, ident) == name
                ):
                    return self.resolve(
                        declaration_path, parser.node_text(source, type_node), owner,
                        allow_ambiguous=True, at=type_node,
                    )
        return None

    def _lombok_getter_type(self, owner: str, name: str, module: str) -> str | None:
        """Declared field type behind a getter that Lombok generates.

        ``@Data``, ``@Value`` or a class-level ``@Getter`` imported from
        ``lombok`` generates ``getX()`` (``isX()`` for a ``boolean``) for every
        non-static field; a field-level ``@Getter`` generates it for that
        field. Fields of source ancestors count, under their own annotations.
        """
        if name.startswith("get") and len(name) > 3:
            suffix, boolean_only = name[3:], False
        elif name.startswith("is") and len(name) > 2:
            suffix, boolean_only = name[2:], True
        else:
            return None
        # The nearest getter hides any farther up the hierarchy (S11).
        for level in self._ancestor_levels(owner, module):
            found: set[str | None] = set()
            locations = [
                (candidate, path, declaration) for candidate, candidate_module in level
                for path, declaration in self._module_locations(candidate, candidate_module)
            ]
            for candidate, path, declaration in locations:
                if declaration.type != "class_declaration":
                    continue
                source = self.units[path].source
                class_level = self._lombok_annotated(path, declaration)
                body = declaration.child_by_field_name("body")
                for member in body.named_children if body is not None else []:
                    if member.type != "field_declaration":
                        continue
                    modifiers = parser.modifiers_node(member)
                    if modifiers is not None and "static" in parser.node_text(source, modifiers).split():
                        continue
                    if not class_level and not self._lombok_annotated(path, member, {"Getter"}):
                        continue
                    type_node = member.child_by_field_name("type")
                    if type_node is None:
                        continue
                    type_text = parser.node_text(source, type_node).strip()
                    if boolean_only and type_text != "boolean":
                        continue
                    for child in member.named_children:
                        ident = child.child_by_field_name("name")
                        field_name = parser.node_text(source, ident) if ident is not None else ""
                        if field_name and field_name[:1].upper() + field_name[1:] == suffix:
                            found.add(self.resolve(path, type_text, candidate, allow_ambiguous=True, at=type_node))
            if found:
                return next(iter(found)) if len(found) == 1 else None
        return None

    def _lombok_annotated(self, path: str, declaration: Node, names: set[str] = _LOMBOK_GETTERS) -> bool:
        unit = self.units[path]
        for annotation in parser.annotations_of(declaration):
            written = parser.node_text(unit.source, annotation.child_by_field_name("name") or annotation)
            simple = written.rsplit(".", 1)[-1]
            if simple not in names:
                continue
            if written == f"lombok.{simple}" or unit.imports.get(simple) == f"lombok.{simple}" or (
                "." not in written and "lombok" in unit.wildcards
            ):
                return True
        return False

    def _field_type(self, owner: str, name: str, module: str) -> str | None:
        """Declared type of a field on a source type or its ancestors."""
        locations = self.types.get(owner, [])
        if len(locations) == 1:
            module = self.path_modules.get(locations[0][0], module)
        return self._member_field(owner, module, name)[1]

    def _receiver_module(self, receiver_type: str, caller: _Method) -> str:
        locations = self._type_candidates(receiver_type, caller.method.path)
        return self.path_modules.get(locations[0][0], caller.module) if len(locations) == 1 else caller.module

    def _declared_concretely(self, method: IntegrationMethod, receiver_type: str, caller: _Method) -> bool:
        """A concrete body on the receiver type or one of its ancestors."""
        info = self.methods.get(method.id)
        if info is None or not info.concrete:
            return False
        if info.owner == receiver_type:
            return True
        module = self._receiver_module(receiver_type, caller)
        return any(owner == info.owner for owner, _module in self.ancestors(receiver_type, module))

    def _unimplemented_contract(
        self, receiver_type: str, name: str, arity: int, caller: _Method,
    ) -> list[IntegrationMethod]:
        """The one abstract source declaration that no source type implements.

        A Spring Data query method (``findByOrderId``) is declared in the
        sources and implemented by the runtime: the call reaches the
        declaration, a possible target, as it does in a typed engine.
        """
        module = self._receiver_module(receiver_type, caller)
        contracts = {
            contract.method.id: contract
            for owner, owner_module in self.ancestors(receiver_type, module)
            for contract in self.by_owner.get((owner, name, arity, owner_module), [])
        }
        if len(contracts) != 1:
            return []
        contract = next(iter(contracts.values()))
        if contract.concrete or self.implementations(contract):
            return []
        return [contract.method]

    def _concrete(self, method: IntegrationMethod) -> bool:
        info = self.methods.get(method.id)
        return info is not None and info.concrete

    def _declared_arities(self, receiver_type: str, name: str, path: str, module: str) -> set[int]:
        """Arities of ``name`` declared on a receiver type and its ancestors."""
        locations = self._type_candidates(receiver_type, path)
        receiver_module = self.path_modules.get(locations[0][0], module) if len(locations) == 1 else module
        owners = {receiver_type, *(owner for owner, _module in self.ancestors(receiver_type, receiver_module))}
        return {arity for owner in owners for arity in self._arities.get((owner, name), ())}

    def _static_import_owner(self, info: _Method, name: str, arity: int) -> str | None:
        """The source type a static import makes ``name`` refer to, if unique.

        Java looks up a simple method name in the enclosing type, its
        ancestors and its outer types before static imports: any declaration
        of that name there, whatever its arity, shadows them. A single
        static import wins over static wildcards; several wildcards that
        declare the name stay ambiguous.
        """
        path = info.method.path
        unit = self.units[path]
        scope = info.owner
        while scope and scope != unit.package:
            if scope in self.types and self._declared_arities(scope, name, path, info.module):
                return None
            scope = scope.rpartition(".")[0]

        def declaring(owners: list[str]) -> set[str]:
            resolved = {self.resolve(path, owner, info.owner) for owner in owners}
            return {
                owner for owner in resolved
                if owner is not None and owner in self.types
                and arity in self._declared_arities(owner, name, path, info.module)
            }

        single = declaring(unit.static_imports.get(name, []))
        if single:
            return next(iter(single)) if len(single) == 1 else None
        wildcard = declaring(unit.static_wildcards)
        return next(iter(wildcard)) if len(wildcard) == 1 else None

    def _may_inherit_unknown(self, owner: str, module: str, name: str) -> bool:
        """Whether an external supertype of ``owner`` may declare ``name``."""
        for ancestor in self.ancestors(owner, module):
            for base in self.external_bases.get(ancestor, ()):
                known = _KNOWN_EXTERNAL_MEMBERS.get(base or "")
                if known is None or name in known:
                    return True
        return False

    def _lexical_owner(self, info: _Method, name: str, invocation: Node) -> str | None:
        """The type an unqualified call ``name(...)`` binds to, if provable.

        Java searches the innermost enclosing type, anonymous classes
        included, whose members (declared or inherited) carry the name, and
        only then the outer types. A scope that inherits from an external
        supertype with unknown members stops the search: that supertype may
        declare the name.
        """
        if name in _OBJECT_METHODS:
            return None
        path = info.method.path
        source = self.units[path].source
        node = invocation.parent
        while node is not None and node != info.node:
            if node.type == "class_body" and node.parent is not None and node.parent.type == "object_creation_expression":
                if any(
                    member.type == "method_declaration" and parser.declaration_name(member, source) == name
                    for member in node.named_children
                ):
                    return None
                type_node = node.parent.child_by_field_name("type")
                supertype = self.resolve(
                    path, parser.node_text(source, type_node), info.owner, allow_ambiguous=True,
                    at=type_node,
                ) if type_node is not None else None
                if supertype is None:
                    return None
                if supertype in self.types:
                    module = self._receiver_module(supertype, info)
                    if (
                        self._declared_arities(supertype, name, path, info.module)
                        or self._may_inherit_unknown(supertype, module, name)
                    ):
                        return None
                else:
                    known = _KNOWN_EXTERNAL_MEMBERS.get(supertype)
                    if known is None or name in known:
                        return None
            node = node.parent
        scope = info.owner
        package = self.units[path].package
        while scope and scope != package and scope in self.types:
            if self._declared_arities(scope, name, path, info.module):
                return scope
            if self._may_inherit_unknown(scope, self._receiver_module(scope, info), name) or (
                name in _ENUM_METHODS
                and any(node.type == "enum_declaration" for _path, node in self._type_candidates(scope, path))
            ):
                return None
            scope = scope.rpartition(".")[0]
        return None

    def visible_modules(self, module: str) -> frozenset[str]:
        """``module`` and its transitive build dependencies (ADR-71).

        A module without a known dependency sees only itself.
        """
        visible = self._visible_modules.get(module)
        if visible is None:
            seen = {module}
            pending = [module]
            while pending:
                for target in self.module_dependencies.get(pending.pop(), ()):
                    if target not in seen:
                        seen.add(target)
                        pending.append(target)
            visible = self._visible_modules[module] = frozenset(seen)
        return visible

    def _external_dispatch(
        self, interface: str, name: str, arity: int, argument_types: tuple[str | None, ...] | None,
        visible: frozenset[str],
    ) -> list[IntegrationMethod]:
        """Source bodies a call on an external interface can dispatch to.

        Each source subtype of the interface contributes the most specific
        concrete, non-static, non-private body of ``name``/``arity`` it
        declares or inherits from a source ancestor. A subtype whose
        same-arity overloads the arguments cannot separate makes the whole
        call unresolved: the implemented signature is unknown. Only bodies
        declared in a ``visible`` module are kept (ADR-71): a library never
        dispatches into a module that depends on it.
        """
        key = (interface, name, arity, argument_types, visible)
        if key in self._external_targets:
            return self._external_targets[key]
        direct = self.external_implementors.get(interface, set())
        bodies: dict[str, IntegrationMethod] = {}
        subtypes = {
            (owner, self.path_modules.get(path, ""))
            for owner, locations in self.types.items()
            for path, _node in locations
        } if direct else set()
        for owner, module in sorted(subtypes):
            ancestors = self.ancestors(owner, module)
            if not ancestors & direct:
                continue
            declarations = self._select_overload([
                candidate for base_owner, base_module in ancestors
                for candidate in self.by_owner.get((base_owner, name, arity, base_module), [])
            ], argument_types)
            if not declarations:
                continue
            if len({candidate.parameters for candidate in declarations}) != 1:
                bodies = {}
                break
            body = self._most_specific([
                candidate for candidate in declarations
                if candidate.concrete and not self._modifiers(candidate) & {"static", "private"}
            ])
            if body is not None and body.method.module in visible:
                bodies[body.method.id] = body.method
        targets = sorted(bodies.values(), key=lambda method: method.id)
        self._external_targets[key] = targets
        return targets

    def _superclass(self, owner: str, module: str) -> tuple[str, str] | None:
        """The one source superclass of a class, if it has one."""
        superclasses = self.superclasses.get((owner, module), set())
        return next(iter(superclasses)) if len(superclasses) == 1 else None

    def _super_base(
        self, info: _Method, receiver: Node, receiver_type: str,
    ) -> tuple[str, str] | None:
        """The type whose members ``super.m()`` or ``X.super.m()`` names.

        ``super`` names the caller's superclass. ``X.super`` names ``X`` when
        ``X`` is an interface the caller implements, and the superclass of
        ``X`` when it is an enclosing class. ``None`` when that type is not a
        unique source type.
        """
        if receiver.type == "super":
            return self._superclass(info.owner, info.module)
        locations = self._type_candidates(receiver_type, info.method.path)
        if len(locations) != 1:
            return None
        module = self.path_modules.get(locations[0][0], info.module)
        if locations[0][1].type == "interface_declaration":
            return receiver_type, module
        return self._superclass(receiver_type, module)

    def _super_target(
        self, base: tuple[str, str], name: str, arity: int,
        argument_types: tuple[str | None, ...] | None,
    ) -> list[IntegrationMethod]:
        """The body a ``super`` call invokes: no dispatch, one declaration.

        Java binds ``super.m()`` to the most specific declaration among
        ``base`` and its ancestors, grandparents included, after overload
        selection by argument type (H5, H6). A private ancestor method is
        not inherited. An abstract or ambiguous result reaches nothing.
        """
        contracts = self._select_overload([
            candidate for owner, module in self.ancestors(*base)
            for candidate in self.by_owner.get((owner, name, arity, module), [])
            if not (candidate.binds_statically and "private" in self._modifiers(candidate))
        ], argument_types)
        if len({candidate.parameters for candidate in contracts}) != 1:
            return []
        declaration = self._most_specific(contracts)
        return [declaration.method] if declaration is not None and declaration.concrete else []

    def _modifiers(self, info: _Method) -> set[str]:
        modifiers = parser.modifiers_node(info.node)
        return set(parser.node_text(self.units[info.method.path].source, modifiers).split()) if modifiers else set()

    def _interface_witness(
        self, receiver_type: str, declaration: _Method | None, target: IntegrationMethod, path: str,
    ) -> IntegrationMethod | None:
        """The abstract source interface method a call names, kept as a witness.

        A typed engine reports the statically bound interface declaration next to
        the concrete dispatch target; the source engine does the same so the
        exported call tree keeps the caller-to-contract hop. A static or
        un-overridden default method that is itself the target, and an
        overridden default body, get no witness.
        """
        if declaration is None or declaration.concrete or declaration.method.id == target.id:
            return None
        locations = self._type_candidates(receiver_type, path)
        if not locations or any(node.type != "interface_declaration" for _path, node in locations):
            return None
        owner_node = parser.enclosing(declaration.node, *_TYPES)
        if owner_node is None or owner_node.type != "interface_declaration":
            return None
        return declaration.method

    def _call_sites(self, info: _Method) -> list[tuple[Node, Node]]:
        """The call sites whose caller is ``info``, with their context.

        A method or a constructor owns the sites of its own body. A type's
        instance initializer code runs in each constructor that does not
        delegate to ``this(...)``, or in the implicit constructor; its
        static initializer code runs in ``<clinit>`` (ADR-75).
        """
        name = info.method.qualified_method.rsplit(".", 1)[-1]
        if info.node.type in _TYPES:
            contexts = initializer_members(
                info.node, self.units[info.method.path].source,
                static=name == STATIC_INITIALIZER,
            )
        else:
            contexts = [info.node]
            if name == CONSTRUCTOR and not delegates_to_this(info.node):
                type_node = parser.enclosing(info.node, *_TYPES)
                if type_node is not None:
                    contexts.extend(initializer_members(
                        type_node, self.units[info.method.path].source, static=False,
                    ))
        return [
            (node, context)
            for context in contexts
            for node in parser.walk(context)
            if node.type in _CALL_SITES | _CONSTRUCTOR_SITES and _call_context(node) == context
        ]

    def _constructor_targets(
        self, info: _Method, invocation: Node,
    ) -> tuple[list[IntegrationMethod], str]:
        """The constructors ``new T(...)``, ``this(...)`` or ``super(...)`` runs.

        The constructor the arguments select is ``exact``; overloads the
        arguments cannot separate are all ``possible``. An anonymous class
        creation, a qualified ``outer.new`` or ``outer.super(...)``, and an
        external type reach nothing (ADR-62, ADR-75).
        """
        path = info.method.path
        source = self.units[path].source
        if invocation.type == "object_creation_expression":
            type_node = invocation.child_by_field_name("type")
            if (
                type_node is None or parser.child_by_type(invocation, "class_body") is not None
                or invocation.named_children[0] != type_node
            ):
                return [], ""
            owner = self.resolve(path, parser.node_text(source, type_node), info.owner, at=type_node)
            if owner is None or owner not in self.types:
                return [], ""
            modules = {
                self.path_modules.get(location, "")
                for location, _node in self._type_candidates(owner, path)
            }
        else:
            keyword = invocation.child_by_field_name("constructor")
            if keyword is None or invocation.child_by_field_name("object") is not None:
                return [], ""
            if keyword.type == "this":
                owner, modules = info.owner, {info.module}
            else:
                parents = self.superclasses.get((info.owner, info.module), set())
                if len(parents) != 1:
                    return [], ""
                owner, parent_module = next(iter(parents))
                modules = {parent_module}
        arguments = parser.argument_nodes(invocation)
        arity = len(arguments)
        contracts = [
            candidate for module in sorted(modules)
            for candidate in self.by_owner.get((owner, CONSTRUCTOR, arity, module), [])
        ] or [
            candidate for module in sorted(modules)
            for candidate in self.varargs_by_owner.get((owner, CONSTRUCTOR, module), [])
            if len(candidate.parameters) - 1 <= arity
        ]
        if not contracts:
            return [], ""
        selected = self._select_overload(contracts, tuple(
            self.expression_type(info, argument, invocation) for argument in arguments
        ))
        return [candidate.method for candidate in selected], (
            "exact" if len(selected) == 1 else "possible"
        )

    def fallback_calls(
        self, resolved_sites: set[tuple[str, int, str]], *, closed_world: bool = False,
        exact_sites: set[tuple[str, int, str]] | None = None,
    ) -> list[MethodCall]:
        """Receiver-typed calls between indexed methods.

        ``closed_world`` applies when this pass is the whole call graph (the
        ``source`` engine): every implementation is then in the sources, so a
        unique implementation of an interface is an exact target. It also
        adds the shapes of the typed engine's query (ADR-70): a possible witness edge
        to the source interface method the call names next to its exact
        implementation, and dispatch on an external interface to its source
        implementations in the caller's visible modules, always possible
        (ADR-71).

        ``exact_sites`` holds the call sites whose concrete target the engine
        resolved exactly. When one of an invocation's candidates is such a
        target, the invocation adds nothing: the engine already chose its
        implementation, and the fallback must not reopen the others.
        """
        exact_sites = exact_sites or set()
        calls: list[MethodCall] = []
        # Witness call -> the exact call it accompanies at the same site.
        self.interface_witnesses = {}
        for info in self.methods.values():
            if not info.concrete:
                continue
            source = self.units[info.method.path].source
            for invocation, context in self._call_sites(info):
                line = invocation.start_point.row + 1
                if invocation.type in _CONSTRUCTOR_SITES:
                    targets, dispatch = self._constructor_targets(info, invocation)
                    calls.extend(
                        MethodCall(
                            info.method.qualified_method, info.method.path, info.method.start_line,
                            target.qualified_method, target.path, target.start_line, line, dispatch,
                        )
                        for target in targets
                        if (info.method.id, line, target.id) not in resolved_sites
                        and not (
                            exact_sites and (info.method.id, line, target.id) in exact_sites
                        )
                    )
                    continue
                in_anonymous_body = _in_anonymous_body(invocation, context)
                receiver: Node | None
                argument_types: tuple[str | None, ...] | None
                if invocation.type == "method_reference":
                    # ``receiver::method`` names a method without its arity:
                    # resolve only when the receiver type declares one arity.
                    parts = invocation.named_children
                    if len(parts) < 2 or parts[-1].type != "identifier":
                        continue
                    receiver, name = parts[0], parser.node_text(source, parts[-1])
                    if in_anonymous_body and _refers_to_anonymous_instance(source, receiver):
                        continue
                    receiver_type = self.receiver_type(info, receiver, invocation)
                    if receiver_type is None:
                        continue
                    arities = self._declared_arities(receiver_type, name, info.method.path, info.module)
                    if len(arities) != 1:
                        continue
                    arity, argument_types = next(iter(arities)), None
                else:
                    receiver, name, arguments = parser.invocation_parts(invocation, source)
                    # An unqualified call binds to the innermost enclosing
                    # type that has the name, possibly an outer type.
                    lexical_owner = (
                        self._lexical_owner(info, name, invocation) if receiver is None else None
                    )
                    if (
                        in_anonymous_body and lexical_owner is None
                        and _refers_to_anonymous_instance(source, receiver)
                    ):
                        # ``this`` is the anonymous instance, whose members
                        # come from an often external supertype.
                        continue
                    receiver_type = lexical_owner or self.receiver_type(info, receiver, invocation)
                    if receiver_type is None:
                        continue
                    arity = len(arguments)
                    argument_types = tuple(
                        self.expression_type(info, argument, invocation) for argument in arguments
                    )
                arguments_count = arity
                pruned = False
                external = False
                declaration: _Method | None = None
                # ``super.m()`` binds statically: no runtime type matters.
                virtual = not (receiver is not None and (
                    receiver.type == "super" or any(child.type == "super" for child in invocation.children)
                ))
                if receiver is not None and not virtual:
                    super_base = self._super_base(info, receiver, receiver_type)
                    if super_base is None:
                        continue
                    receiver_type = super_base[0]
                    targets = self._super_target(super_base, name, arguments_count, argument_types)
                else:
                    targets, pruned, declaration = self._candidate_targets(
                        receiver_type, name, arguments_count, info.module, info.method.path,
                        argument_types=argument_types,
                    )
                    if not targets and receiver is None:
                        static_owner = self._static_import_owner(info, name, arguments_count)
                        if static_owner is not None:
                            receiver_type = static_owner
                            targets, pruned, declaration = self._candidate_targets(
                                receiver_type, name, arguments_count, info.module,
                                info.method.path, argument_types=argument_types,
                            )
                    if not targets and closed_world and receiver_type not in self.types:
                        targets = self._external_dispatch(
                            receiver_type, name, arguments_count, argument_types,
                            self.visible_modules(info.module),
                        )
                        # A library can supply the implementation: one source
                        # body proves nothing (ADR-71).
                        external = bool(targets)
                        # An anonymous class or a lambda is a body no
                        # indexed method stands for: the dispatch stays open.
                        pruned = bool(targets) and (
                            receiver_type in self.anonymous_supertypes or receiver_type in _FUNCTIONAL_JDK
                        )
                    if not targets:
                        targets = self._unimplemented_contract(receiver_type, name, arguments_count, info)
                if not targets:
                    continue
                if exact_sites and any(
                    (info.method.id, line, target.id) in exact_sites for target in targets
                ):
                    continue
                incomplete = virtual and receiver_type in self.types and not any(
                    self.methods[target.id].binds_statically for target in targets if target.id in self.methods
                ) and self._incomplete_implementors(receiver_type, info, name, arguments_count, targets)
                if (
                    incomplete and declaration is not None and not declaration.concrete
                    and all(target.id != declaration.method.id for target in targets)
                ):
                    # The declaration the call names is a target, as a typed
                    # engine reports it when no implementation is exact; on
                    # a Feign client it carries the HTTP call (ADR-81 rule 3).
                    targets = [declaration.method, *targets]
                # A concrete method declared by the receiver's own type is an
                # exact call. Interface dispatch to a unique source
                # implementation is exact only in a closed world, like
                # a typed engine's ``exactVirtualMethod``; otherwise dependencies may
                # add implementations and the call stays possible. A target
                # left unique by preferring the caller's module stays
                # possible: the preference is a heuristic, not a proof. A call
                # on an external interface stays possible (ADR-71). So does a
                # call whose receiver has an implementor without a known
                # body (ADR-81).
                dispatch = (
                    "exact"
                    if len(targets) == 1 and not pruned and not external and not incomplete and (
                        self._declared_concretely(targets[0], receiver_type, info)
                        or closed_world and self._concrete(targets[0])
                    )
                    else "possible"
                )
                site_calls = [
                    MethodCall(
                        info.method.qualified_method, info.method.path, info.method.start_line,
                        target.qualified_method, target.path, target.start_line, line, dispatch,
                    )
                    for target in targets
                    if (info.method.id, line, target.id) not in resolved_sites
                ]
                calls.extend(site_calls)
                witness = (
                    self._interface_witness(receiver_type, declaration, targets[0], info.method.path)
                    if closed_world and dispatch == "exact" and site_calls else None
                )
                if witness is not None:
                    witness_call = replace(
                        site_calls[0], callee=witness.qualified_method, callee_path=witness.path,
                        callee_line=witness.start_line, dispatch_confidence="possible",
                    )
                    calls.append(witness_call)
                    self.interface_witnesses[witness_call] = site_calls[0]
        return calls
