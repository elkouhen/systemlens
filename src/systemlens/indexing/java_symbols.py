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

from systemlens.discovery.java import parser
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.domain.module_inventory import ModuleDependency
from systemlens.callgraph.model import MethodCall


_TYPES = {"class_declaration", "interface_declaration", "record_declaration", "enum_declaration"}
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
    "decimal_integer_literal": "int",
    "hex_integer_literal": "int",
    "decimal_floating_point_literal": "double",
    "character_literal": "char",
    "true": "boolean",
    "false": "boolean",
}
_BOXED = {
    "int": "java.lang.Integer", "long": "java.lang.Long", "double": "java.lang.Double",
    "float": "java.lang.Float", "boolean": "java.lang.Boolean", "char": "java.lang.Character",
    "byte": "java.lang.Byte", "short": "java.lang.Short",
}
# Members every type has: an unqualified call to one of them never reaches an
# outer type, and its return type is fixed by ``java.lang.Object``.
_OBJECT_METHODS = {
    "equals", "hashCode", "toString", "getClass", "notify", "notifyAll", "wait", "clone", "finalize",
}
_OBJECT_RETURNS = {("toString", 0): "java.lang.String", ("hashCode", 0): "int", ("equals", 1): "boolean"}
_ENUM_METHODS = {"name", "ordinal", "values", "valueOf", "compareTo", "getDeclaringClass", "describeConstable"}
# Method names of the few external supertypes whose members are fixed by the
# Java SE API. Any other external supertype may declare any name.
_KNOWN_EXTERNAL_MEMBERS: dict[str, frozenset[str]] = {
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


def _argument_matches(parameter: str | None, argument: str | None) -> bool:
    """Whether a known argument type selects a declared parameter type."""
    if parameter is None or argument is None:
        return False
    return parameter == argument or _BOXED.get(argument) == parameter or _BOXED.get(parameter) == argument


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
        self.methods: dict[str, _Method] = {}
        self.by_owner: dict[tuple[str, str, int, str], list[_Method]] = defaultdict(list)
        self.dispatch: dict[tuple[str, str, str, tuple[str | None, ...]], list[_Method]] = defaultdict(list)
        self._ancestors: dict[tuple[str, str], set[tuple[str, str]]] = {}
        self.inherited_dispatch: dict[tuple[str, str], set[str]] = defaultdict(set)
        # Supertypes outside the sources, ``None`` when the name is unresolved.
        self.external_bases: dict[tuple[str, str], set[str | None]] = defaultdict(set)
        # External interfaces and the source types that implement them directly.
        self.external_implementors: dict[str, set[tuple[str, str]]] = defaultdict(set)
        self.interface_witnesses: dict[MethodCall, MethodCall] = {}
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
                elif node.type == "object_creation_expression" and parser.child_by_type(node, "class_body"):
                    type_node = node.child_by_field_name("type")
                    if type_node is not None:
                        anonymous = self.resolve(
                            path, parser.node_text(source, type_node), self.owner(path, node),
                            allow_ambiguous=True,
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
                if node.type == "method_declaration":
                    key = (self.owner(path, node), parser.declaration_name(node, source) or "", node.start_point.row + 1)
                    declarations[path][key].append(node)
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
                            )
                            if base is not None and base in self.types and base != owner:
                                base_locations = self._type_candidates(base, path)
                                for base_path, _base_node in base_locations:
                                    base_module = self.path_modules.get(base_path, "")
                                    self.bases[(owner, self.path_modules.get(path, ""))].add(
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
                if len(matching) != 1:
                    continue
                node = matching[0]
                owner_node = parser.enclosing(node, *_TYPES)
                if owner_node is None:
                    continue
                owner = self.owner(path, owner_node)
                if len(self._type_candidates(owner, path)) != 1:
                    continue
                parameters = node.child_by_field_name("parameters")
                parameter_types = []
                written: list[str] = []
                for parameter in parameters.named_children if parameters is not None else []:
                    if parameter.type not in {"formal_parameter", "spread_parameter"}:
                        continue
                    parameter_type = parameter.child_by_field_name("type")
                    # Varargs and unresolved generic parameters are intentionally
                    # not treated as an ordinary overload signature.
                    resolved_type = (
                        self.resolve(path, parser.node_text(source, parameter_type), owner)
                        if parameter_type is not None and parameter.type == "formal_parameter" else None
                    )
                    dimensions = parameter.child_by_field_name("dimensions")
                    if resolved_type is not None and dimensions is not None:
                        resolved_type += "[]" * parser.node_text(source, dimensions).count("[")
                    parameter_types.append(resolved_type)
                    written.append(_written_type(source, parameter))
                info = _Method(
                    method, method.module, owner, node, tuple(parameter_types),
                    node.child_by_field_name("body") is not None, tuple(written),
                )
                self.methods[method.id] = info
                self.by_owner[(owner, name, len(parameter_types), method.module)].append(info)
                if info.concrete and all(value is not None for value in info.parameters):
                    for base_owner, base_module in self.ancestors(owner, info.module):
                        self.dispatch[(base_owner, base_module, name, info.parameters)].append(info)

        self._complete_override_signatures()
        self._index_inherited_implementations()
        # ``dispatch`` is complete: index its signatures by name and arity so
        # a generic contract reads only the candidates it can match.
        self._dispatch_signatures: dict[
            tuple[str, str, str, int], list[tuple[str | None, ...]]
        ] = defaultdict(list)
        for dispatch_owner, dispatch_module, dispatch_name, signature in self.dispatch:
            self._dispatch_signatures[
                (dispatch_owner, dispatch_module, dispatch_name, len(signature))
            ].append(signature)

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
            if not info.concrete or all(value is not None for value in info.parameters):
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
                        modifiers = next((child for child in info.node.children if child.type == "modifiers"), None)
                        words = set(parser.node_text(self.units[info.method.path].source, modifiers).split()) if modifiers else set()
                        if "public" not in words or words & {"static", "private"}:
                            continue
                        for base_owner, base_module in ancestors:
                            key = (base_owner, base_module, name, parameters)
                            if info not in self.dispatch[key]:
                                self.dispatch[key].append(info)
                            self.inherited_dispatch[(base_owner, base_module)].add(info.method.id)

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
        return same_module or candidates

    def resolve(
        self, path: str, text: str, owner: str, *, allow_ambiguous: bool = False,
    ) -> str | None:
        text = re.sub(r"<.*>", "", text).strip()
        if text.endswith("[]"):
            element = self.resolve(path, text[:-2], owner, allow_ambiguous=allow_ambiguous)
            return element + "[]" if element else None
        if text in _PRIMITIVES:
            return text
        unit = self.units[path]
        if text in self.types and ("." in text or not unit.package):
            return text if allow_ambiguous or len(self._type_candidates(text, path)) == 1 else None
        scope = owner
        while scope and scope != unit.package:
            nested = f"{scope}.{text}"
            if nested in self.types:
                return nested if allow_ambiguous or len(self._type_candidates(nested, path)) == 1 else None
            scope = scope.rpartition(".")[0]
        first, *rest = text.split(".")
        if first in unit.imports:
            imported = ".".join([unit.imports[first], *rest])
            return imported if allow_ambiguous or len(self._type_candidates(imported, path)) <= 1 else None
        local = f"{unit.package}.{text}" if unit.package else text
        if local in self.types:
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
        name = contract.method.qualified_method.rsplit(".", 1)[-1]
        if not any(parameter is None for parameter in contract.parameters):
            return self.dispatch.get((contract.owner, contract.module, name, contract.parameters), [])
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
        return list(candidates.values())

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
        """Keep the same-arity overload the known argument types select.

        An exact (or boxed) match wins, like the most specific overload in
        Java; otherwise a source argument may select a parameter declared by
        one of its ancestors; otherwise an overload survives when no known
        argument is provably incompatible with its parameter. Unknown
        arguments never narrow: ambiguity is returned unchanged.
        """
        if len({candidate.parameters for candidate in contracts}) <= 1 or argument_types is None:
            return contracts
        for compatible in (
            _argument_matches,
            self._argument_assignable,
            lambda parameter, argument: not self._argument_excluded(parameter, argument),
        ):
            selected = [
                candidate for candidate in contracts
                if len(candidate.parameters) == len(argument_types)
                and all(
                    compatible(parameter, argument)
                    for parameter, argument in zip(candidate.parameters, argument_types, strict=True)
                )
            ]
            if len({candidate.parameters for candidate in selected}) == 1:
                return selected
        return contracts

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
            contracts = [
                candidate
                for (owner, candidate_name, candidate_arity, _candidate_module), values in self.by_owner.items()
                if owner == receiver_type
                and candidate_name == name
                and candidate_arity == arity
                for candidate in values
            ]
        if not contracts:
            contracts = [
                candidate
                for (owner, candidate_name, candidate_arity, _candidate_module), values in self.by_owner.items()
                if (owner, _candidate_module) in receiver_bases
                and candidate_name == name and candidate_arity == arity
                for candidate in values
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
        if node.type in {"object_creation_expression", "cast_expression"}:
            type_node = node.child_by_field_name("type")
            return self.resolve(
                info.method.path, parser.node_text(source, type_node), info.owner,
                allow_ambiguous=True,
            ) if type_node is not None else None
        if node.type == "parenthesized_expression" and node.named_children:
            return self.expression_type(info, node.named_children[0], invocation, depth + 1)
        if node.type in {"identifier", "this", "method_invocation", "field_access", "array_access"}:
            return self.receiver_type(info, node, invocation, depth + 1)
        return None

    def variable_type(self, info: _Method, name: str, invocation: Node, *, field_only: bool = False) -> str | None:
        path = info.method.path
        source = self.units[path].source
        parameters = info.node.child_by_field_name("parameters")
        for parameter in parameters.named_children if parameters is not None and not field_only else []:
            ident = parameter.child_by_field_name("name")
            type_node = parameter.child_by_field_name("type")
            if ident is not None and parser.node_text(source, ident) == name and type_node is not None:
                return self.resolve(
                    path, parser.node_text(source, type_node), info.owner,
                    allow_ambiguous=True,
                )
        # Only enclosing lexical blocks can contribute local declarations.
        scope = invocation.parent if not field_only else None
        while scope is not None and scope != info.node:
            declared, scoped_type = self._scoped_declaration(info, scope, name, invocation)
            if declared:
                return scoped_type
            for declaration in reversed(scope.named_children):
                if declaration.type != "local_variable_declaration" or declaration.end_byte > invocation.start_byte:
                    continue
                type_node = declaration.child_by_field_name("type")
                for child in declaration.named_children:
                    ident = child.child_by_field_name("name")
                    if ident is not None and parser.node_text(source, ident) == name and type_node is not None:
                        if parser.node_text(source, type_node).strip() == "var":
                            # ``var`` takes the type of its initializer.
                            return self.expression_type(
                                info, child.child_by_field_name("value"), invocation,
                            )
                        return self.resolve(
                            path, parser.node_text(source, type_node), info.owner,
                            allow_ambiguous=True,
                        )
            scope = scope.parent
        owners = [info.owner] + sorted(
            (owner for owner, _module in self.ancestors(info.owner, info.module) if owner != info.owner),
            key=len,
            reverse=True,
        )
        for owner in owners:
            found: set[str | None] = set()
            for field_path, declaration in self.types.get(owner, []):
                body = declaration.child_by_field_name("body")
                field_source = self.units[field_path].source
                for field in body.named_children if body is not None else []:
                    if field.type != "field_declaration":
                        continue
                    type_node = field.child_by_field_name("type")
                    for child in field.named_children:
                        ident = child.child_by_field_name("name")
                        if ident is not None and parser.node_text(field_source, ident) == name and type_node is not None:
                            found.add(self.resolve(
                                field_path, parser.node_text(field_source, type_node), owner,
                                allow_ambiguous=True,
                            ))
            if found:
                return next(iter(found)) if len(found) == 1 else None
        return None

    def _scoped_declaration(
        self, info: _Method, scope: Node, name: str, invocation: Node,
    ) -> tuple[bool, str | None]:
        """A variable that ``scope`` itself declares for the code it encloses.

        Returns ``(True, type)`` when ``scope`` declares ``name``; the type is
        ``None`` when it is not written (``var``, an untyped lambda
        parameter, a multi-catch). Such a declaration still shadows fields.
        """
        path = info.method.path
        source = self.units[path].source

        def typed(type_node: Node | None, value: Node | None = None) -> tuple[bool, str | None]:
            if type_node is None:
                return True, None
            text = parser.node_text(source, type_node).strip()
            if text == "var":
                return True, self.expression_type(info, value, invocation) if value is not None else None
            return True, self.resolve(path, text, info.owner, allow_ambiguous=True)

        def named(node: Node | None) -> bool:
            return node is not None and parser.node_text(source, node) == name

        if scope.type == "enhanced_for_statement":
            body = scope.child_by_field_name("body")
            if named(scope.child_by_field_name("name")) and body is not None and (
                body.start_byte <= invocation.start_byte < body.end_byte
            ):
                type_node = scope.child_by_field_name("type")
                if type_node is not None and parser.node_text(source, type_node).strip() == "var":
                    return True, None
                return typed(type_node)
        elif scope.type == "catch_clause":
            parameter = parser.child_by_type(scope, "catch_formal_parameter")
            if parameter is not None and named(parameter.child_by_field_name("name")):
                catch_type = parser.child_by_type(parameter, "catch_type")
                alternatives = catch_type.named_children if catch_type is not None else []
                return typed(alternatives[0]) if len(alternatives) == 1 else (True, None)
        elif scope.type == "try_with_resources_statement":
            resources = parser.child_by_type(scope, "resource_specification")
            for resource in resources.named_children if resources is not None else []:
                if (
                    resource.type == "resource"
                    and resource.end_byte <= invocation.start_byte
                    and named(resource.child_by_field_name("name"))
                ):
                    return typed(resource.child_by_field_name("type"), resource.child_by_field_name("value"))
        elif scope.type == "lambda_expression":
            parameters = scope.child_by_field_name("parameters")
            if parameters is not None and parameters.type == "identifier":
                if named(parameters):
                    return True, None
            elif parameters is not None:
                for parameter in parameters.named_children:
                    if parameter.type == "identifier" and named(parameter):
                        return True, None
                    if parameter.type == "formal_parameter" and named(parameter.child_by_field_name("name")):
                        return typed(parameter.child_by_field_name("type"))
        elif scope.type == "method_declaration":
            # The method of an anonymous class inside ``info``'s body.
            parameters = scope.child_by_field_name("parameters")
            for parameter in parameters.named_children if parameters is not None else []:
                if parameter.type == "formal_parameter" and named(parameter.child_by_field_name("name")):
                    return typed(parameter.child_by_field_name("type"))
        return False, None

    def receiver_type(self, info: _Method, receiver: Node | None, invocation: Node, depth: int = 0) -> str | None:
        """Read declared receiver/return types without evaluating method bodies."""
        if depth > 16:
            return None
        source = self.units[info.method.path].source
        if receiver is None or receiver.type in {"this", "super"}:
            return info.owner
        if receiver.type == "identifier":
            name = parser.node_text(source, receiver)
            declared = self.variable_type(info, name, invocation)
            if declared is not None or not name[:1].isupper():
                return declared
            # ``Type.method()``: a capitalized name that is no variable is a
            # static call on that source type.
            static_owner = self.resolve(info.method.path, name, info.owner)
            return static_owner if static_owner in self.types else None
        if receiver.type in {"object_creation_expression", "cast_expression", "parenthesized_expression"}:
            return self.expression_type(info, receiver, invocation, depth + 1)
        if receiver.type == "array_access":
            # ``values[0].name()``: the element type of the array.
            array = self.expression_type(info, receiver.child_by_field_name("array"), invocation, depth + 1)
            return array[:-2] if array is not None and array.endswith("[]") else None
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
            ) if type_node is not None else None)
        return next(iter(returns)) if len(returns) == 1 else None

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
                        allow_ambiguous=True,
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
        for candidate in [owner, *sorted(base for base, _module in self.ancestors(owner, module) if base != owner)]:
            found: set[str | None] = set()
            for path, declaration in self.types.get(candidate, []):
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
                            found.add(self.resolve(path, type_text, candidate, allow_ambiguous=True))
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

    def _argument_assignable(self, parameter: str | None, argument: str | None) -> bool:
        if _argument_matches(parameter, argument):
            return True
        if parameter is None or argument is None or argument not in self.types:
            return False
        modules = {self.path_modules.get(path, "") for path, _node in self.types[argument]}
        return any(
            owner == parameter or parameter in self.external_bases.get((owner, owner_module), ())
            for module in modules
            for owner, owner_module in self.ancestors(argument, module)
        )

    def _argument_excluded(self, parameter: str | None, argument: str | None) -> bool:
        """Whether a known argument provably cannot be passed to a parameter.

        Only primitives and their boxes, ``String`` and source types are
        compared: no external type can be a subtype of a source type, and a
        source type is never a primitive or a ``String``. Primitive widening
        and boxing are left compatible.
        """
        if parameter is None or argument is None or self._argument_assignable(parameter, argument):
            return False

        def kind(type_name: str) -> str | None:
            if type_name in _PRIMITIVES or type_name in _BOXED.values():
                return "primitive"
            if type_name == "java.lang.String":
                return "string"
            return "source" if type_name in self.types else None

        parameter_kind, argument_kind = kind(parameter), kind(argument)
        if parameter_kind is None or argument_kind is None:
            return False
        return not parameter_kind == argument_kind == "primitive"

    def _field_type(self, owner: str, name: str, module: str) -> str | None:
        """Declared type of a field on a source type or its ancestors."""
        for candidate in [owner, *sorted(base for base, _module in self.ancestors(owner, module))]:
            found: set[str | None] = set()
            for path, declaration in self.types.get(candidate, []):
                body = declaration.child_by_field_name("body")
                source = self.units[path].source
                for member in body.named_children if body is not None else []:
                    if member.type not in {"field_declaration", "constant_declaration"}:
                        continue
                    type_node = member.child_by_field_name("type")
                    for child in member.named_children:
                        ident = child.child_by_field_name("name")
                        if ident is not None and type_node is not None and parser.node_text(source, ident) == name:
                            found.add(self.resolve(path, parser.node_text(source, type_node), candidate, allow_ambiguous=True))
                if declaration.type == "enum_declaration" and body is not None:
                    # Enum constants are fields of the enum type itself.
                    for member in body.named_children:
                        if member.type == "enum_constant":
                            ident = member.child_by_field_name("name")
                            if ident is not None and parser.node_text(source, ident) == name:
                                found.add(candidate)
            if found:
                return next(iter(found)) if len(found) == 1 else None
        return None

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
        declaration, a possible target, as it does in CodeQL.
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
        return {
            arity for owner, method_name, arity, _module in self.by_owner
            if owner in owners and method_name == name
        }

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

    def _modifiers(self, info: _Method) -> set[str]:
        modifiers = parser.modifiers_node(info.node)
        return set(parser.node_text(self.units[info.method.path].source, modifiers).split()) if modifiers else set()

    def _interface_witness(
        self, receiver_type: str, declaration: _Method | None, target: IntegrationMethod, path: str,
    ) -> IntegrationMethod | None:
        """The abstract source interface method a call names, kept as a witness.

        CodeQL reports the statically bound interface declaration next to
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

    def fallback_calls(
        self, resolved_sites: set[tuple[str, int, str]], *, closed_world: bool = False,
    ) -> list[MethodCall]:
        """Receiver-typed calls between indexed methods.

        ``closed_world`` applies when this pass is the whole call graph (the
        ``source`` engine): every implementation is then in the sources, so a
        unique implementation of an interface is an exact target. It also
        adds the shapes of the CodeQL query (ADR-70): a possible witness edge
        to the source interface method the call names next to its exact
        implementation, and dispatch on an external interface to its source
        implementations in the caller's visible modules, always possible
        (ADR-71).
        """
        calls: list[MethodCall] = []
        # Witness call -> the exact call it accompanies at the same site.
        self.interface_witnesses = {}
        for info in self.methods.values():
            if not info.concrete:
                continue
            source = self.units[info.method.path].source
            for invocation in parser.walk(info.node):
                if (
                    invocation.type not in {"method_invocation", "method_reference"}
                    or parser.enclosing_method(invocation) != info.node
                ):
                    continue
                line = invocation.start_point.row + 1
                in_anonymous_body = parser.enclosing(invocation, "method_declaration") != info.node
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
                if receiver is not None and receiver.type == "super":
                    direct_bases = self.bases.get((info.owner, info.module), set())
                    targets = [
                        candidate.method
                        for base_owner, base_module in direct_bases
                        for candidate in self.by_owner.get((base_owner, name, arguments_count, base_module), [])
                    ]
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
                # A concrete method declared by the receiver's own type is an
                # exact call. Interface dispatch to a unique source
                # implementation is exact only in a closed world, like
                # CodeQL's ``exactVirtualMethod``; otherwise dependencies may
                # add implementations and the call stays possible. A target
                # left unique by preferring the caller's module stays
                # possible: the preference is a heuristic, not a proof. A call
                # on an external interface stays possible (ADR-71).
                dispatch = (
                    "exact"
                    if len(targets) == 1 and not pruned and not external and (
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
