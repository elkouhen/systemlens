"""Transient, conservative Java symbols for missing call-graph edges.

This is not a Java compiler: unresolved types, generic substitutions and
ambiguous overloads remain unresolved. Names alone never establish dispatch.
"""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Sequence

from tree_sitter import Node

from systemlens.discovery.java import parser
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.indexing.codeql import CodeQLCall


_TYPES = {"class_declaration", "interface_declaration", "record_declaration", "enum_declaration"}
_PRIMITIVES = {"byte", "short", "int", "long", "float", "double", "char", "boolean", "void"}
_JAVA_LANG = {"String", "Object", "Boolean", "Byte", "Short", "Integer", "Long", "Float", "Double", "Character"}


@dataclass
class _Unit:
    source: bytes
    package: str
    imports: dict[str, str]
    wildcards: list[str]


@dataclass
class _Method:
    method: IntegrationMethod
    owner: str
    node: Node
    parameters: tuple[str | None, ...]
    concrete: bool


class JavaSymbols:
    """Build once per join; index types by FQN and methods by name/arity."""

    def __init__(self, root: Path, methods: list[IntegrationMethod], source_paths: Sequence[str] = ()):
        self.units: dict[str, _Unit] = {}
        self.types: dict[str, list[tuple[str, Node]]] = defaultdict(list)
        self.bases: dict[str, set[str]] = defaultdict(set)
        self.methods: dict[str, _Method] = {}
        self.by_owner: dict[tuple[str, str, int], list[_Method]] = defaultdict(list)
        self.dispatch: dict[tuple[str, str, tuple[str | None, ...]], list[_Method]] = defaultdict(list)
        self._ancestors: dict[str, set[str]] = {}
        self._targets: dict[tuple[str, str, int], IntegrationMethod | None] = {}
        by_path: dict[str, list[IntegrationMethod]] = defaultdict(list)
        for method in methods:
            by_path[method.path].append(method)
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
            for child in tree.named_children:
                text = parser.node_text(source, child)
                if child.type == "package_declaration":
                    package = text.removeprefix("package").removesuffix(";").strip()
                elif child.type == "import_declaration" and "static " not in text:
                    imported = text.removeprefix("import").removesuffix(";").strip()
                    if imported.endswith(".*"):
                        wildcards.append(imported[:-2])
                    else:
                        imports[imported.rsplit(".", 1)[-1]] = imported
            self.units[path] = _Unit(source, package, imports, wildcards)
            nodes = list(parser.walk(tree))
            for node in nodes:
                if node.type in _TYPES:
                    self.types[self.owner(path, node)].append((path, node))
            declarations[path] = defaultdict(list)
            for node in nodes:
                if node.type == "method_declaration":
                    key = (self.owner(path, node), parser.declaration_name(node, source) or "", node.start_point.row + 1)
                    declarations[path][key].append(node)
        for owner, locations in self.types.items():
            if len(locations) != 1:
                continue
            path, node = locations[0]
            for child in node.named_children:
                if child.type not in {"superclass", "super_interfaces", "extends_interfaces"}:
                    continue
                for type_node in child.named_children:
                    candidates = type_node.named_children if type_node.type == "type_list" else [type_node]
                    for candidate in candidates:
                        base = self.resolve(path, parser.node_text(self.units[path].source, candidate), owner)
                        if base is not None and base in self.types and base != owner:
                            self.bases[owner].add(base)
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
                if len(self.types[owner]) != 1:
                    continue
                parameters = node.child_by_field_name("parameters")
                parameter_types = []
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
                info = _Method(method, owner, node, tuple(parameter_types), node.child_by_field_name("body") is not None)
                self.methods[method.id] = info
                self.by_owner[(owner, name, len(parameter_types))].append(info)
                if info.concrete and all(value is not None for value in info.parameters):
                    for base_owner in self.ancestors(owner):
                        self.dispatch[(base_owner, name, info.parameters)].append(info)

    def owner(self, path: str, node: Node) -> str:
        names: list[str] = []
        current: Node | None = node
        while current is not None:
            if current.type in _TYPES:
                names.append(parser.declaration_name(current, self.units[path].source) or "")
            current = current.parent
        return ".".join(filter(None, [self.units[path].package, *reversed(names)]))

    def resolve(self, path: str, text: str, owner: str) -> str | None:
        text = re.sub(r"<.*>", "", text).strip()
        if text.endswith("[]"):
            element = self.resolve(path, text[:-2], owner)
            return element + "[]" if element else None
        if text in _PRIMITIVES:
            return text
        unit = self.units[path]
        if text in self.types and ("." in text or not unit.package):
            return text if len(self.types[text]) == 1 else None
        scope = owner
        while scope and scope != unit.package:
            nested = f"{scope}.{text}"
            if nested in self.types:
                return nested if len(self.types[nested]) == 1 else None
            scope = scope.rpartition(".")[0]
        first, *rest = text.split(".")
        if first in unit.imports:
            imported = ".".join([unit.imports[first], *rest])
            return imported if len(self.types.get(imported, [None])) == 1 else None
        local = f"{unit.package}.{text}" if unit.package else text
        if local in self.types:
            return local if len(self.types[local]) == 1 else None
        candidates = {f"{package}.{text}" for package in unit.wildcards if f"{package}.{text}" in self.types}
        if text in _JAVA_LANG:
            candidates.add(f"java.lang.{text}")
        if len(candidates) == 1:
            candidate = next(iter(candidates))
            return candidate if len(self.types.get(candidate, [None])) == 1 else None
        return None

    def ancestors(self, owner: str) -> set[str]:
        if owner not in self._ancestors:
            seen: set[str] = set()
            pending = [owner]
            while pending:
                current = pending.pop()
                if current not in seen:
                    seen.add(current)
                    pending.extend(self.bases.get(current, ()))
            self._ancestors[owner] = seen
        return self._ancestors[owner]

    def implementations(self, contract: _Method) -> list[_Method]:
        if any(parameter is None for parameter in contract.parameters):
            return []
        name = contract.method.qualified_method.rsplit(".", 1)[-1]
        return self.dispatch.get((contract.owner, name, contract.parameters), [])

    def bridge(self, method: IntegrationMethod) -> IntegrationMethod | None:
        contract = self.methods.get(method.id)
        if contract is None or contract.concrete:
            return None
        candidates = self.implementations(contract)
        return candidates[0].method if len(candidates) == 1 else None

    def target(self, receiver_type: str, name: str, arity: int) -> IntegrationMethod | None:
        key = (receiver_type, name, arity)
        if key in self._targets:
            return self._targets[key]
        self._targets[key] = None
        receiver_bases = self.ancestors(receiver_type)
        contracts = [candidate for owner in receiver_bases
                     for candidate in self.by_owner.get((owner, name, arity), [])]
        signatures = {candidate.parameters for candidate in contracts}
        if len(signatures) != 1 or any(value is None for value in next(iter(signatures), ())):
            return None
        candidates = {candidate.method.id: candidate for contract in contracts
                      for candidate in self.implementations(contract)
                      if receiver_type in self.ancestors(candidate.owner)
                      or candidate.owner in receiver_bases}
        # Overrides present on the declared receiver hide ancestor bodies.
        # Possible overrides on other runtime subtypes remain ambiguous.
        hidden: set[str] = set()
        for candidate in candidates.values():
            if candidate.owner in receiver_bases:
                hidden.update(self.ancestors(candidate.owner) - {candidate.owner})
        remaining = [candidate for candidate in candidates.values() if candidate.owner not in hidden]
        if len(remaining) == 1:
            self._targets[key] = remaining[0].method
        return self._targets[key]

    def variable_type(self, info: _Method, name: str, invocation: Node, *, field_only: bool = False) -> str | None:
        path = info.method.path
        source = self.units[path].source
        parameters = info.node.child_by_field_name("parameters")
        for parameter in parameters.named_children if parameters is not None and not field_only else []:
            ident = parameter.child_by_field_name("name")
            type_node = parameter.child_by_field_name("type")
            if ident is not None and parser.node_text(source, ident) == name and type_node is not None:
                return self.resolve(path, parser.node_text(source, type_node), info.owner)
        # Only enclosing lexical blocks can contribute local declarations.
        scope = invocation.parent if not field_only else None
        while scope is not None and scope != info.node:
            for declaration in reversed(scope.named_children):
                if declaration.type != "local_variable_declaration" or declaration.end_byte > invocation.start_byte:
                    continue
                type_node = declaration.child_by_field_name("type")
                for child in declaration.named_children:
                    ident = child.child_by_field_name("name")
                    if ident is not None and parser.node_text(source, ident) == name and type_node is not None:
                        return self.resolve(path, parser.node_text(source, type_node), info.owner)
            scope = scope.parent
        found: set[str | None] = set()
        for owner in self.ancestors(info.owner):
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
                            found.add(self.resolve(field_path, parser.node_text(field_source, type_node), owner))
        return next(iter(found)) if len(found) == 1 else None

    def fallback_calls(self, resolved_sites: set[tuple[str, int]]) -> list[CodeQLCall]:
        calls: list[CodeQLCall] = []
        for info in self.methods.values():
            if not info.concrete or info.method.output_endpoint_ids:
                continue
            source = self.units[info.method.path].source
            for invocation in parser.walk(info.node):
                if invocation.type != "method_invocation" or parser.enclosing(invocation, "method_declaration") != info.node:
                    continue
                line = invocation.start_point.row + 1
                if (info.method.id, line) in resolved_sites:
                    continue
                receiver, name, arguments = parser.invocation_parts(invocation, source)
                receiver_type: str | None
                if receiver is None or receiver.type == "this":
                    receiver_type = info.owner
                elif receiver.type == "identifier":
                    receiver_type = self.variable_type(info, parser.node_text(source, receiver), invocation)
                elif receiver.type == "field_access" and parser.node_text(source, receiver).startswith("this."):
                    receiver_type = self.variable_type(info, parser.node_text(source, receiver)[5:], invocation, field_only=True)
                else:
                    receiver_type = None
                if receiver_type is None:
                    continue
                target = self.target(receiver_type, name, len(arguments))
                if target is None:
                    continue
                calls.append(CodeQLCall(
                    info.method.qualified_method, info.method.path, info.method.start_line,
                    target.qualified_method, target.path, target.start_line, line, "possible",
                ))
        return calls
