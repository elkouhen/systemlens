"""Request mappings inherited from an interface or a base class (BL-04).

Spring MVC registers a mapping declared on an interface or a superclass
method for the concrete controller that inherits it, and Spring Cloud
OpenFeign builds a client method from the API interface a ``@FeignClient``
extends. This module indexes those links once per indexing run:

* an *inheritor* is a concrete ``@RestController`` or ``@Controller`` class,
  or a ``@FeignClient`` interface;
* a *declaring type* is one of its source supertypes, at any depth, that
  declares at least one method with a Spring mapping annotation.

Supertypes are resolved through the same import, package and wildcard rule
as Kafka payload typing (ADR-76). A supertype that does not resolve to one
source declaration is not a link: nothing is guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from tree_sitter import Node

from systemlens.discovery.java import parser as java_parser
from systemlens.scanner.kafka_payload_types import _java_paths_by_stem, _Typed, _Typer

MAPPING_ANNOTATIONS = frozenset({
    "RequestMapping", "GetMapping", "PostMapping", "PutMapping", "DeleteMapping", "PatchMapping",
})
CONTROLLER_ANNOTATIONS = frozenset({"RestController", "Controller"})
INHERITED_MAPPING_MARKER = "systemlens-inherited-mapping:"
_MAX_DEPTH = 8
_INHERITOR_TYPES = frozenset({"class_declaration", "interface_declaration"})

# A type is identified by its file and the start byte of its declaration.
TypeKey = tuple[str, int]


@dataclass(frozen=True)
class RestInheritanceIndex:
    """Links between inheritors and the declaring types they inherit from.

    ``supertypes`` lists, per inheritor, every source supertype in Spring's
    search order (interfaces before the superclass, nearest first), and
    ``declaring`` the subset that declares mapping methods. ``inheritors``
    is the reverse of ``declaring``.
    """

    supertypes: dict[TypeKey, tuple[TypeKey, ...]]
    declaring: dict[TypeKey, tuple[TypeKey, ...]]
    inheritors: dict[TypeKey, tuple[TypeKey, ...]]

    def links(self) -> set[tuple[str, str]]:
        """``(inheritor path, declaring path)`` pairs."""
        return {
            (inheritor[0], declared[0])
            for inheritor, declared_types in self.declaring.items()
            for declared in declared_types
        }


def is_feign_interface(declaration: Node, source: bytes) -> bool:
    return declaration.type == "interface_declaration" and any(
        java_parser.annotation_name(annotation, source) == "FeignClient"
        for annotation in java_parser.annotations_of(declaration)
    )


def is_concrete_controller(declaration: Node, source: bytes) -> bool:
    if declaration.type != "class_declaration":
        return False
    modifiers = java_parser.modifiers_node(declaration)
    if modifiers is not None and any(child.type == "abstract" for child in modifiers.children):
        return False
    return any(
        java_parser.annotation_name(annotation, source) in CONTROLLER_ANNOTATIONS
        for annotation in java_parser.annotations_of(declaration)
    )


def declared_mapping_methods(declaration: Node, source: bytes) -> list[Node]:
    """Methods of the type's own body that carry a mapping annotation."""
    body = declaration.child_by_field_name("body")
    return [
        member for member in (body.named_children if body is not None else [])
        if member.type == "method_declaration"
        and any(
            java_parser.annotation_name(annotation, source) in MAPPING_ANNOTATIONS
            for annotation in java_parser.annotations_of(member)
        )
    ]


def type_at(repo_root: Path, key: TypeKey) -> tuple[bytes, Node] | None:
    """The source and declaration node of an indexed type."""
    parsed = java_parser.parse_java(str(repo_root), key[0])
    if parsed is None:
        return None
    source, root = parsed
    node = next(
        (
            node for node in java_parser.type_declarations(root)
            if node.start_byte == key[1]
        ),
        None,
    )
    return (source, node) if node is not None else None


def _ordered_supertypes(typer: _Typer, path: str, declaration: Node) -> list[tuple[str, Node]]:
    """Direct source supertypes, interfaces before the superclass."""
    parsed = java_parser.parse_java(typer.root, path)
    if parsed is None:
        return []
    source = parsed[0]
    interfaces: list[tuple[str, Node]] = []
    superclass: list[tuple[str, Node]] = []
    for child in declaration.named_children:
        if child.type not in {"superclass", "super_interfaces", "extends_interfaces"}:
            continue
        found = superclass if child.type == "superclass" else interfaces
        for type_node in child.named_children:
            candidates = type_node.named_children if type_node.type == "type_list" else [type_node]
            for candidate in candidates:
                resolved = typer._resolve(_Typed(java_parser.node_text(source, candidate), path))
                if resolved is not None:
                    found.append(resolved)
    return [*interfaces, *superclass]


def _supertype_chain(typer: _Typer, path: str, declaration: Node) -> list[tuple[str, Node]]:
    chain: list[tuple[str, Node]] = []
    seen: set[TypeKey] = {(path, declaration.start_byte)}

    def visit(current_path: str, current: Node, depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        for super_path, super_node in _ordered_supertypes(typer, current_path, current):
            key = (super_path, super_node.start_byte)
            if key in seen:
                continue
            seen.add(key)
            chain.append((super_path, super_node))
            visit(super_path, super_node, depth + 1)

    visit(path, declaration, 0)
    return chain


@lru_cache(maxsize=4)
def rest_inheritance_index(repo_root_str: str) -> RestInheritanceIndex:
    """Index every inheritor of a declaring type in the repository.

    Only files whose text names a controller or Feign annotation and an
    ``extends`` or ``implements`` clause are parsed, so the cost is one text
    read per Java file plus one parse per candidate.
    """
    repo_root = Path(repo_root_str)
    typer = _Typer(repo_root)
    supertypes: dict[TypeKey, tuple[TypeKey, ...]] = {}
    declaring: dict[TypeKey, tuple[TypeKey, ...]] = {}
    inheritors: dict[TypeKey, list[TypeKey]] = {}
    for paths in _java_paths_by_stem(repo_root_str).values():
        for path in paths:
            try:
                text = (repo_root / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "Controller" not in text and "FeignClient" not in text:
                continue
            if "extends" not in text and "implements" not in text:
                continue
            parsed = java_parser.parse_java(repo_root_str, path)
            if parsed is None:
                continue
            source, root = parsed
            for declaration in java_parser.type_declarations(root):
                if declaration.type not in _INHERITOR_TYPES:
                    continue
                if not (
                    is_concrete_controller(declaration, source)
                    or is_feign_interface(declaration, source)
                ):
                    continue
                chain = _supertype_chain(typer, path, declaration)
                if not chain:
                    continue
                key = (path, declaration.start_byte)
                supertypes[key] = tuple((p, n.start_byte) for p, n in chain)
                declared: list[TypeKey] = []
                for super_path, super_node in chain:
                    super_parsed = java_parser.parse_java(repo_root_str, super_path)
                    if super_parsed is None:
                        continue
                    if declared_mapping_methods(super_node, super_parsed[0]):
                        declared.append((super_path, super_node.start_byte))
                if declared:
                    declaring[key] = tuple(declared)
                    for declared_key in declared:
                        inheritors.setdefault(declared_key, []).append(key)
    return RestInheritanceIndex(
        supertypes=supertypes,
        declaring=declaring,
        inheritors={key: tuple(sorted(values)) for key, values in inheritors.items()},
    )


def inherited_mapping_rescan_paths(
    repo_root: Path,
    touched: set[str],
    current_paths: set[str],
    previous_links: set[tuple[str, str]],
) -> set[str]:
    """Files to rescan because an inherited-mapping partner was touched.

    A touched file (changed or deleted) invalidates every file linked to it
    by an inherited mapping, before the change (``previous_links``, read
    from the persisted markers) or after it (the current index). Only
    existing files are returned; one level of links bounds the rescan.
    """
    if not any(path.endswith(".java") for path in touched):
        return set()
    links = previous_links | rest_inheritance_index(str(repo_root)).links()
    linked = {
        partner
        for inheritor, declared in links
        for touched_path, partner in ((inheritor, declared), (declared, inheritor))
        if touched_path in touched
    }
    return (linked & current_paths) - touched


def previous_inherited_mapping_links(snippets: list[tuple[str, str]]) -> set[tuple[str, str]]:
    """``(inheritor path, declaring path)`` pairs from persisted snippets."""
    links: set[tuple[str, str]] = set()
    for path, snippet in snippets:
        index = snippet.find(INHERITED_MAPPING_MARKER)
        if index < 0:
            continue
        declared = snippet[index + len(INHERITED_MAPPING_MARKER):].split()[0:1]
        if declared:
            links.add((path, declared[0]))
    return links


def clear_rest_inheritance_cache() -> None:
    rest_inheritance_index.cache_clear()
