"""Static typing of the payload expression of a Kafka send site (ADR-76).

Both producer paths, ``KafkaTemplate.send`` and the Strategy1
``envoyerMessageKafka*`` methods, type their value argument with this one
rule. It reads declared types only: written type text keeps its generic
arguments, so ``Message<T>``, ``KafkaTemplate<K, V>`` and collection element
types stay visible, which the erased call-graph types of
``indexing.java_symbols`` do not offer.

Typed shapes: method, constructor, lambda and for-each parameters; local
variables, including ``var`` from a typed initializer; fields of the
enclosing, outer and source super types, also through ``this.``; ``new``,
casts and ternaries whose branches agree; calls whose source declaration
is unique by name and arity, record accessors and Lombok getters; Lombok
``X.builder()...build()`` chains and ``MessageBuilder.withPayload(p)``.
Anything else is unknown. Source type lookups go through the file's own
declarations, its imports, its package and its wildcard imports, and use
only Java files of the indexed repository.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from tree_sitter import Node

from systemlens.discovery.java import parser as java_parser
from systemlens.domain.message_types import message_type_key
from systemlens.scanner._core import _kafka_payload_type, _split_java_type_arguments

_TYPE_DECLARATIONS = frozenset({
    "class_declaration", "interface_declaration", "record_declaration", "enum_declaration",
})
_GENERIC_SCOPES = _TYPE_DECLARATIONS | {"method_declaration", "constructor_declaration"}
_ELEMENT_CONTAINERS = frozenset({
    "ArrayDeque", "ArrayList", "Collection", "CopyOnWriteArrayList", "Deque", "HashSet",
    "Iterable", "LinkedHashSet", "LinkedList", "List", "NavigableSet", "Optional", "Queue",
    "Set", "SortedSet", "Stream", "TreeSet",
})
_MAP_CONTAINERS = frozenset({"ConcurrentHashMap", "HashMap", "LinkedHashMap", "Map", "SortedMap", "TreeMap"})
# Calls whose single lambda parameter is one element of the receiver.
_ELEMENT_LAMBDAS = frozenset({
    "allMatch", "anyMatch", "filter", "forEach", "forEachOrdered", "ifPresent",
    "noneMatch", "peek", "removeIf",
})
# Stream operations that keep the element type.
_STREAM_PRESERVING = frozenset({
    "distinct", "filter", "limit", "parallel", "peek", "sequential", "skip", "sorted", "unordered",
})
# Template types whose second type argument is the value type.
_TEMPLATES = frozenset({"KafkaOperations", "KafkaTemplate", "ReplyingKafkaTemplate"})
_LOMBOK_BUILDERS = frozenset({"Builder", "SuperBuilder"})
_LOMBOK_GETTERS = frozenset({"Data", "Getter", "Value"})
_MAX_DEPTH = 12


@dataclass(frozen=True)
class _Typed:
    """A type as written, with the file whose imports resolve it."""

    text: str
    path: str


@lru_cache(maxsize=16)
def _java_paths_by_stem(repo_root_str: str) -> dict[str, tuple[str, ...]]:
    """Repository Java files by file stem, outside build output."""
    root = Path(repo_root_str)
    by_stem: dict[str, list[str]] = {}
    for path in root.rglob("*.java"):
        relative = path.relative_to(root)
        if not path.is_file() or {"target", "build"}.intersection(relative.parts):
            continue
        by_stem.setdefault(path.stem, []).append(relative.as_posix())
    return {stem: tuple(sorted(paths)) for stem, paths in by_stem.items()}


def clear_payload_type_caches() -> None:
    _java_paths_by_stem.cache_clear()


def _erased(text: str) -> str:
    return text.split("<", 1)[0].strip()


def _simple(text: str) -> str:
    return _erased(text).rsplit(".", 1)[-1]


def _type_arguments(text: str) -> list[str]:
    start, end = text.find("<"), text.rfind(">")
    if start < 0 or end < start:
        return []
    return _split_java_type_arguments(text[start + 1:end])


def _clean(text: str) -> str:
    text = re.sub(r"@[\w.]+(?:\([^)]*\))?\s*", "", text)
    return re.sub(r"\b(?:final|volatile)\b\s*", "", text).strip()


def type_variables_in_scope(node: Node | None, source: bytes) -> set[str]:
    """Type parameters declared by the method and types enclosing ``node``."""
    names: set[str] = set()
    current = node
    while current is not None:
        if current.type in _GENERIC_SCOPES:
            parameters = current.child_by_field_name("type_parameters")
            for parameter in parameters.named_children if parameters is not None else []:
                name = next(iter(parameter.named_children), None)
                if parameter.type == "type_parameter" and name is not None:
                    names.add(java_parser.node_text(source, name))
        current = current.parent
    return names


class _Typer:
    def __init__(self, repo_root: Path) -> None:
        self.root = str(repo_root)

    # ------------------------------------------------------------ sources
    def _parsed(self, path: str) -> tuple[bytes, Node] | None:
        return java_parser.parse_java(self.root, path)

    def _package(self, path: str) -> str:
        parsed = self._parsed(path)
        if parsed is None:
            return ""
        source, root = parsed
        for child in root.named_children:
            if child.type == "package_declaration":
                return java_parser.node_text(source, child).removeprefix("package").removesuffix(";").strip()
        return ""

    def _imports(self, path: str) -> tuple[dict[str, str], list[str]]:
        parsed = self._parsed(path)
        exact: dict[str, str] = {}
        wildcards: list[str] = []
        if parsed is None:
            return exact, wildcards
        source, root = parsed
        for child in root.named_children:
            if child.type != "import_declaration":
                continue
            imported = java_parser.node_text(source, child).removeprefix("import").removesuffix(";").strip()
            if imported.startswith("static "):
                continue
            if imported.endswith(".*"):
                wildcards.append(imported[:-2])
            else:
                exact[imported.rsplit(".", 1)[-1]] = imported
        return exact, wildcards

    def _declared(self, path: str, source: bytes, type_node: Node | None, scope: Node) -> _Typed | None:
        """A written type, unknown when it mentions a type variable in scope."""
        if type_node is None:
            return None
        text = _clean(java_parser.node_text(source, type_node))
        if not text or text == "var":
            return None
        tokens = set(re.findall(r"[A-Za-z_$][\w$]*", text))
        if tokens & type_variables_in_scope(scope, source):
            return None
        return _Typed(text, path)

    # ------------------------------------------------------- type lookup
    def _qualified(self, qualified: str, near: str) -> tuple[str, Node] | None:
        parts = qualified.split(".")
        found: list[tuple[str, Node]] = []
        for index in range(len(parts) - 1, 0, -1):
            package, top, nested = ".".join(parts[:index]), parts[index], parts[index + 1:]
            for path in _java_paths_by_stem(self.root).get(top, ()):
                if self._package(path) != package:
                    continue
                parsed = self._parsed(path)
                if parsed is None:
                    continue
                source, root = parsed
                declaration = next(
                    (child for child in root.named_children
                     if child.type in _TYPE_DECLARATIONS and java_parser.declaration_name(child, source) == top),
                    None,
                )
                for name in nested:
                    if declaration is None:
                        break
                    body = declaration.child_by_field_name("body")
                    declaration = next(
                        (child for child in (body.named_children if body is not None else [])
                         if child.type in _TYPE_DECLARATIONS
                         and java_parser.declaration_name(child, source) == name),
                        None,
                    )
                if declaration is not None:
                    found.append((path, declaration))
            if found:
                break
        if len(found) > 1:
            # The same name in several modules: prefer the caller's module.
            module = near.split("/src/", 1)[0]
            found = [item for item in found if item[0].split("/src/", 1)[0] == module]
        return found[0] if len(found) == 1 else None

    def _resolve(self, typed: _Typed) -> tuple[str, Node] | None:
        """The source declaration that ``typed`` names, when unique."""
        text = _erased(typed.text)
        if not text or text.endswith("]"):
            return None
        if "." in text:
            exact, _wildcards = self._imports(typed.path)
            first, _, rest = text.partition(".")
            if first in exact:
                return self._qualified(f"{exact[first]}.{rest}", typed.path)
            local = self._resolve(_Typed(first, typed.path))
            if local is not None:
                return self._nested(local, rest.split("."))
            return self._qualified(text, typed.path)
        parsed = self._parsed(typed.path)
        if parsed is None:
            return None
        source, root = parsed
        declared = [
            node for node in java_parser.type_declarations(root)
            if java_parser.declaration_name(node, source) == text
        ]
        if declared:
            return (typed.path, declared[0]) if len(declared) == 1 else None
        exact, wildcards = self._imports(typed.path)
        if text in exact:
            return self._qualified(exact[text], typed.path)
        package = self._package(typed.path)
        same_package = self._qualified(f"{package}.{text}" if package else text, typed.path) if package else None
        if same_package is not None:
            return same_package
        candidates = [
            found for wildcard in wildcards
            if (found := self._qualified(f"{wildcard}.{text}", typed.path)) is not None
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _nested(self, declaration: tuple[str, Node], names: list[str]) -> tuple[str, Node] | None:
        path, node = declaration
        parsed = self._parsed(path)
        if parsed is None:
            return None
        source = parsed[0]
        for name in names:
            body = node.child_by_field_name("body")
            match = next(
                (child for child in (body.named_children if body is not None else [])
                 if child.type in _TYPE_DECLARATIONS and java_parser.declaration_name(child, source) == name),
                None,
            )
            if match is None:
                return None
            node = match
        return path, node

    def _supertypes(self, path: str, declaration: Node) -> list[tuple[str, Node]]:
        parsed = self._parsed(path)
        if parsed is None:
            return []
        source = parsed[0]
        found: list[tuple[str, Node]] = []
        for child in declaration.named_children:
            if child.type not in {"superclass", "super_interfaces", "extends_interfaces"}:
                continue
            for type_node in child.named_children:
                candidates = type_node.named_children if type_node.type == "type_list" else [type_node]
                for candidate in candidates:
                    resolved = self._resolve(_Typed(java_parser.node_text(source, candidate), path))
                    if resolved is not None:
                        found.append(resolved)
        return found

    # --------------------------------------------------------- members
    def _field(self, declaration: tuple[str, Node], name: str, depth: int) -> _Typed | None:
        """Declared type of field ``name`` on a type or its source supertypes."""
        if depth > _MAX_DEPTH:
            return None
        path, node = declaration
        parsed = self._parsed(path)
        if parsed is None:
            return None
        source = parsed[0]
        if node.type == "record_declaration":
            parameters = node.child_by_field_name("parameters")
            for parameter in parameters.named_children if parameters is not None else []:
                ident = parameter.child_by_field_name("name")
                if ident is not None and java_parser.node_text(source, ident) == name:
                    return self._declared(path, source, parameter.child_by_field_name("type"), parameter)
        body = node.child_by_field_name("body")
        members = list(body.named_children) if body is not None else []
        if body is not None and node.type == "enum_declaration":
            members = [
                member for child in members
                for member in ([child] if child.type != "enum_body_declarations" else child.named_children)
            ]
        for member in members:
            if member.type not in {"field_declaration", "constant_declaration"}:
                continue
            for declarator in member.named_children:
                ident = declarator.child_by_field_name("name") if declarator.type == "variable_declarator" else None
                if ident is not None and java_parser.node_text(source, ident) == name:
                    return self._declared(path, source, member.child_by_field_name("type"), member)
        for supertype in self._supertypes(path, node):
            found = self._field(supertype, name, depth + 1)
            if found is not None:
                return found
        return None

    def _method_return(
        self, declaration: tuple[str, Node], name: str, arity: int, depth: int,
    ) -> tuple[bool, _Typed | None]:
        """``(declared, type)`` of the one method ``name``/``arity`` of a type.

        ``declared`` is false when no source declaration exists, so the
        caller may try an implicit accessor; an overload of the same arity
        makes the result unknown.
        """
        if depth > _MAX_DEPTH:
            return False, None
        path, node = declaration
        parsed = self._parsed(path)
        if parsed is None:
            return False, None
        source = parsed[0]
        body = node.child_by_field_name("body")
        members = list(body.named_children) if body is not None else []
        if node.type == "enum_declaration":
            members = [
                member for child in members
                for member in ([child] if child.type != "enum_body_declarations" else child.named_children)
            ]
        candidates = []
        for member in members:
            if member.type != "method_declaration" or java_parser.declaration_name(member, source) != name:
                continue
            parameters = member.child_by_field_name("parameters")
            count = len([
                child for child in (parameters.named_children if parameters is not None else [])
                if child.type in {"formal_parameter", "spread_parameter"}
            ])
            if count == arity:
                candidates.append(member)
        if candidates:
            if len(candidates) != 1:
                return True, None
            return_type = candidates[0].child_by_field_name("type")
            if return_type is None or return_type.type == "void_type":
                return True, None
            return True, self._declared(path, source, return_type, candidates[0])
        results = [self._method_return(supertype, name, arity, depth + 1) for supertype in self._supertypes(path, node)]
        declared = [typed for found, typed in results if found]
        if declared:
            return True, declared[0] if len(declared) == 1 else None
        return False, None

    def _lombok(self, path: str, declaration: Node, names: frozenset[str]) -> bool:
        parsed = self._parsed(path)
        if parsed is None:
            return False
        source = parsed[0]
        exact, wildcards = self._imports(path)
        for annotation in java_parser.annotations_of(declaration):
            name_node = annotation.child_by_field_name("name")
            written = java_parser.node_text(source, name_node) if name_node is not None else ""
            simple = written.rsplit(".", 1)[-1]
            if simple in names and (
                written == f"lombok.{simple}" or exact.get(simple) == f"lombok.{simple}"
                or ("." not in written and "lombok" in wildcards)
            ):
                return True
        return False

    def _accessor(self, declaration: tuple[str, Node], name: str, depth: int) -> _Typed | None:
        """Type of a record accessor or a Lombok getter ``name()``."""
        if depth > _MAX_DEPTH:
            return None
        path, node = declaration
        if node.type == "record_declaration":
            return self._field(declaration, name, depth + 1)
        if name.startswith("get") and len(name) > 3:
            field = name[3].lower() + name[4:]
        elif name.startswith("is") and len(name) > 2:
            field = name[2].lower() + name[3:]
        else:
            return None
        parsed = self._parsed(path)
        if parsed is None:
            return None
        source = parsed[0]
        class_level = node.type == "class_declaration" and self._lombok(path, node, _LOMBOK_GETTERS)
        body = node.child_by_field_name("body")
        for member in body.named_children if body is not None else []:
            if member.type != "field_declaration":
                continue
            modifiers = java_parser.modifiers_node(member)
            if modifiers is not None and "static" in java_parser.node_text(source, modifiers).split():
                continue
            if not class_level and not self._lombok(path, member, frozenset({"Getter"})):
                continue
            for declarator in member.named_children:
                ident = declarator.child_by_field_name("name") if declarator.type == "variable_declarator" else None
                if ident is not None and java_parser.node_text(source, ident) == field:
                    return self._declared(path, source, member.child_by_field_name("type"), member)
        for supertype in self._supertypes(path, node):
            found = self._accessor(supertype, name, depth + 1)
            if found is not None:
                return found
        return None

    # ----------------------------------------------------- expressions
    def expression(self, path: str, source: bytes, node: Node | None, depth: int = 0) -> _Typed | None:
        if node is None or depth > _MAX_DEPTH:
            return None
        kind = node.type
        if kind == "parenthesized_expression":
            inner = next(iter(node.named_children), None)
            return self.expression(path, source, inner, depth + 1)
        if kind in {"string_literal", "text_block"}:
            return _Typed("String", path)
        if kind in {"object_creation_expression", "cast_expression"}:
            return self._declared(path, source, node.child_by_field_name("type"), node)
        if kind == "ternary_expression":
            branches = [node.child_by_field_name("consequence"), node.child_by_field_name("alternative")]
            # ``null`` takes the type of the other branch, as in Java.
            typed = [
                self.expression(path, source, branch, depth + 1)
                for branch in branches if branch is not None and branch.type != "null_literal"
            ]
            if not typed or any(item is None for item in typed):
                return None
            keys = {message_type_key(item.text) or _clean(item.text) for item in typed if item is not None}
            return typed[0] if len(keys) == 1 else None
        if kind == "identifier":
            return self._identifier(path, source, node, java_parser.node_text(source, node), depth + 1)
        if kind == "field_access":
            return self._field_access(path, source, node, depth + 1)
        if kind == "method_invocation":
            return self._invocation(path, source, node, depth + 1)
        if kind == "array_access":
            array = self.expression(path, source, node.child_by_field_name("array"), depth + 1)
            return _Typed(array.text[:-2], array.path) if array is not None and array.text.endswith("[]") else None
        return None

    def _enclosing_type(self, path: str, node: Node) -> tuple[str, Node] | None:
        owner = java_parser.enclosing(node, *_TYPE_DECLARATIONS)
        return (path, owner) if owner is not None else None

    def _identifier(self, path: str, source: bytes, node: Node, name: str, depth: int) -> _Typed | None:
        """Type of a name from the closest declaration that encloses ``node``."""
        child, scope = node, node.parent
        while scope is not None:
            if scope.type == "lambda_expression":
                declared, typed = self._lambda_parameter(path, source, scope, name, depth)
                if declared:
                    return typed
            elif scope.type == "enhanced_for_statement":
                body = scope.child_by_field_name("body")
                ident = scope.child_by_field_name("name")
                if (
                    child == body and ident is not None
                    and java_parser.node_text(source, ident) == name
                ):
                    type_node = scope.child_by_field_name("type")
                    if type_node is not None and java_parser.node_text(source, type_node).strip() == "var":
                        iterable = self.expression(path, source, scope.child_by_field_name("value"), depth + 1)
                        return self._element(iterable)
                    return self._declared(path, source, type_node, scope)
            elif scope.type == "catch_clause":
                parameter = java_parser.child_by_type(scope, "catch_formal_parameter")
                ident = parameter.child_by_field_name("name") if parameter is not None else None
                if ident is not None and java_parser.node_text(source, ident) == name:
                    return None
            elif scope.type in {"method_declaration", "constructor_declaration"}:
                parameters = scope.child_by_field_name("parameters")
                for parameter in parameters.named_children if parameters is not None else []:
                    if parameter.type not in {"formal_parameter", "spread_parameter"}:
                        continue
                    ident = parameter.child_by_field_name("name")
                    if parameter.type == "spread_parameter":
                        declarator = java_parser.child_by_type(parameter, "variable_declarator")
                        ident = declarator.child_by_field_name("name") if declarator is not None else None
                    if ident is None or java_parser.node_text(source, ident) != name:
                        continue
                    type_node = parameter.child_by_field_name("type") or next(
                        (item for item in parameter.named_children if item.type not in {"modifiers", "variable_declarator"}),
                        None,
                    )
                    typed = self._declared(path, source, type_node, scope)
                    if typed is not None and parameter.type == "spread_parameter":
                        return _Typed(typed.text + "[]", typed.path)
                    return typed
            elif scope.type in _TYPE_DECLARATIONS:
                field = self._field((path, scope), name, depth + 1)
                if field is not None:
                    return field
            if scope.type not in _TYPE_DECLARATIONS:
                for statement in reversed(scope.named_children):
                    if statement.type != "local_variable_declaration" or statement.end_byte > node.start_byte:
                        continue
                    for declarator in statement.named_children:
                        ident = declarator.child_by_field_name("name") if declarator.type == "variable_declarator" else None
                        if ident is None or java_parser.node_text(source, ident) != name:
                            continue
                        type_node = statement.child_by_field_name("type")
                        if type_node is not None and java_parser.node_text(source, type_node).strip() == "var":
                            return self.expression(path, source, declarator.child_by_field_name("value"), depth + 1)
                        return self._declared(path, source, type_node, statement)
            child, scope = scope, scope.parent
        return None

    def _lambda_parameter(
        self, path: str, source: bytes, lambda_node: Node, name: str, depth: int,
    ) -> tuple[bool, _Typed | None]:
        parameters = lambda_node.child_by_field_name("parameters")
        if parameters is None:
            return False, None
        names: list[Node] = []
        if parameters.type == "identifier":
            names = [parameters]
        else:
            for parameter in parameters.named_children:
                if parameter.type == "identifier":
                    names.append(parameter)
                elif parameter.type == "formal_parameter":
                    ident = parameter.child_by_field_name("name")
                    if ident is not None and java_parser.node_text(source, ident) == name:
                        return True, self._declared(path, source, parameter.child_by_field_name("type"), lambda_node)
        position = next((index for index, ident in enumerate(names) if java_parser.node_text(source, ident) == name), None)
        if position is None:
            return False, None
        arguments = lambda_node.parent
        call = arguments.parent if arguments is not None and arguments.type == "argument_list" else None
        if call is None or call.type != "method_invocation":
            return True, None
        receiver, method, call_arguments = java_parser.invocation_parts(call, source)
        if len(call_arguments) != 1 or receiver is None:
            return True, None
        receiver_type = self.expression(path, source, receiver, depth + 1)
        if receiver_type is None:
            return True, None
        if method == "forEach" and len(names) == 2 and _simple(receiver_type.text) in _MAP_CONTAINERS:
            arguments_types = _type_arguments(receiver_type.text)
            return True, _Typed(arguments_types[position], receiver_type.path) if len(arguments_types) == 2 else None
        if method in _ELEMENT_LAMBDAS and len(names) == 1:
            return True, self._element(receiver_type)
        return True, None

    def _element(self, typed: _Typed | None) -> _Typed | None:
        """Element type of an array, a collection, a stream or an optional."""
        if typed is None:
            return None
        if typed.text.endswith("[]"):
            return _Typed(typed.text[:-2], typed.path)
        arguments = _type_arguments(typed.text)
        if _simple(typed.text) in _ELEMENT_CONTAINERS and len(arguments) == 1:
            element = re.sub(r"^\?\s+extends\s+", "", arguments[0])
            return _Typed(element, typed.path) if "?" not in element else None
        return None

    def _field_access(self, path: str, source: bytes, node: Node, depth: int) -> _Typed | None:
        target = node.child_by_field_name("object")
        field = node.child_by_field_name("field")
        if target is None or field is None:
            return None
        name = java_parser.node_text(source, field)
        if target.type == "this":
            owner = self._enclosing_type(path, node)
            return self._field(owner, name, depth + 1) if owner is not None else None
        if target.type == "super":
            owner = self._enclosing_type(path, node)
            supertypes = self._supertypes(*owner) if owner is not None else []
            return self._field(supertypes[0], name, depth + 1) if len(supertypes) == 1 else None
        declaration = self._receiver_declaration(
            path, source, target, self.expression(path, source, target, depth + 1),
        )
        return self._field(declaration, name, depth + 1) if declaration is not None else None

    def _receiver_declaration(
        self, path: str, source: bytes, receiver: Node, typed: _Typed | None,
    ) -> tuple[str, Node] | None:
        """Source declaration of a receiver: its value type or a type name.

        ``typed`` is the receiver's value type; a capitalized name that
        types as no value is read as a static reference to a type.
        """
        if typed is not None:
            return self._resolve(typed)
        if receiver.type in {"identifier", "field_access", "scoped_identifier"}:
            text = java_parser.node_text(source, receiver)
            if all(part[:1].isalpha() for part in text.split(".")) and text.rsplit(".", 1)[-1][:1].isupper():
                return self._resolve(_Typed(text, path))
        return None

    def _invocation(self, path: str, source: bytes, call: Node, depth: int) -> _Typed | None:
        receiver, name, arguments = java_parser.invocation_parts(call, source)
        if name == "build" and not arguments and receiver is not None:
            built = self._built(path, source, receiver, depth + 1)
            if built is not None:
                return built
        if name == "toString" and not arguments:
            return _Typed("String", path)
        if receiver is None or receiver.type == "this":
            owner = self._enclosing_type(path, call)
            while owner is not None:
                declared, typed = self._method_return(owner, name, len(arguments), depth + 1)
                if declared:
                    return typed
                owner = self._enclosing_type(path, owner[1])
            return None
        if receiver.type == "super":
            owner = self._enclosing_type(path, call)
            supertypes = self._supertypes(*owner) if owner is not None else []
            if len(supertypes) != 1:
                return None
            return self._method_return(supertypes[0], name, len(arguments), depth + 1)[1]
        receiver_type = self.expression(path, source, receiver, depth + 1)
        if receiver_type is not None:
            container = self._container_call(receiver_type, name, len(arguments))
            if container is not None:
                return container
        declaration = self._receiver_declaration(path, source, receiver, receiver_type)
        if declaration is None:
            return None
        declared, typed = self._method_return(declaration, name, len(arguments), depth + 1)
        if declared:
            return typed
        return self._accessor(declaration, name, depth + 1) if not arguments else None

    def _container_call(self, receiver: _Typed, name: str, arity: int) -> _Typed | None:
        simple = _simple(receiver.text)
        element = self._element(receiver)
        if element is None:
            return None
        if name in {"stream", "parallelStream"} and arity == 0 and simple != "Optional":
            return _Typed(f"Stream<{element.text}>", receiver.path)
        if simple == "Stream" and name in _STREAM_PRESERVING:
            return receiver
        if simple == "Stream" and name in {"findAny", "findFirst"} and arity == 0:
            return _Typed(f"Optional<{element.text}>", receiver.path)
        if simple == "Optional" and name in {"get", "orElseThrow"} and arity in {0, 1}:
            return element
        if simple in {"ArrayList", "LinkedList", "List", "CopyOnWriteArrayList"} and name == "get" and arity == 1:
            return element
        if name in {"getFirst", "getLast", "peekFirst", "peekLast", "poll", "peek"} and arity == 0:
            return element
        return None

    def _built(self, path: str, source: bytes, receiver: Node, depth: int) -> _Typed | None:
        """Type built by a Lombok builder or a ``MessageBuilder`` chain."""
        current: Node | None = receiver
        while current is not None and current.type == "method_invocation":
            inner, name, arguments = java_parser.invocation_parts(current, source)
            if inner is not None and inner.type == "identifier":
                owner = java_parser.node_text(source, inner)
                if owner == "MessageBuilder" and name == "withPayload" and len(arguments) == 1:
                    payload = self.expression(path, source, arguments[0], depth + 1)
                    return _Typed(f"Message<{payload.text}>", payload.path) if payload is not None else None
                if name == "builder" and not arguments and owner[:1].isupper():
                    declaration = self._resolve(_Typed(owner, path))
                    if declaration is not None and self._lombok(*declaration, _LOMBOK_BUILDERS):
                        return _Typed(owner, path)
                    return None
            if name == "toBuilder" and not arguments and inner is not None:
                built = self.expression(path, source, inner, depth + 1)
                declaration = self._resolve(built) if built is not None else None
                if declaration is not None and self._lombok(*declaration, _LOMBOK_BUILDERS):
                    return built
                return None
            current = inner
        return None


def payload_expression_type(repo_root: Path, path: str, source: bytes, node: Node | None) -> str | None:
    """Kafka payload type of an expression, ``None`` when it names no DTO."""
    typed = _Typer(repo_root).expression(path, source, node)
    return _kafka_payload_type(typed.text) if typed is not None else None


def expression_type_text(repo_root: Path, path: str, source: bytes, node: Node | None) -> str | None:
    """Written type of an expression, informative or not; ``None`` when unknown."""
    typed = _Typer(repo_root).expression(path, source, node)
    return typed.text if typed is not None else None


def template_value_type(repo_root: Path, path: str, source: bytes, receiver: Node | None) -> str | None:
    """Value type ``V`` of the ``KafkaTemplate<K, V>`` that ``receiver`` names."""
    typed = _Typer(repo_root).expression(path, source, receiver)
    if typed is None or _simple(typed.text) not in _TEMPLATES:
        return None
    arguments = _type_arguments(typed.text)
    return _kafka_payload_type(arguments[1]) if len(arguments) >= 2 else None
