"""Count the call-graph shapes of docs/spec-tech/indexing.md in a Java tree.

The counts come from the tree-sitter syntax tree, not from a text search. Types
are matched by simple name, preferring a declaration in the same build module,
so a count that depends on the type hierarchy is an estimate: an ambiguous
simple name is left out. The script reads the sources only and writes nothing.

Test sources (``src/test``) and ``.git`` are skipped. Files under ``old-docs/``
are reported in a separate group because no build module of train-ticket
includes them.

    uv run python scripts/count_call_shapes.py ~/examples/train-ticket
    uv run python scripts/count_call_shapes.py ROOT --json
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import sys

from tree_sitter import Node

from systemlens.discovery.java import parser

_TYPES = {"class_declaration", "interface_declaration", "record_declaration", "enum_declaration"}
_SEPARATE_GROUPS = ("old-docs",)

# Row -> what is counted. "proxy" marks a syntactic estimate of a shape that
# needs types to be counted exactly.
ROWS: dict[str, str] = {
    "S1": "calls on an explicit receiver other than this/super",
    "S2": "static method declarations / static imports / static methods hiding an ancestor's (defect shape)",
    "S3": "unqualified calls in a nested type to a name only an outer type declares",
    "S4": "private methods / private methods with a homonym (name, arity) in an ancestor or subtype (defect shape)",
    "S5": "(type, name, arity) groups with two or more overloads",
    "S6": "lines declaring two methods of one name in one owner, anonymous bodies included (defect shape)",
    "S7": "explicit constructors / `new` of a source type / this(...) or super(...) calls",
    "S8": "calls inside constructors, initializer blocks and field initializers",
    "H1": "source classes extending a source class",
    "H2": "methods overriding a (name, arity) of a source ancestor class",
    "H3": "this.m() or unqualified m() calls to an abstract method of the caller's type",
    "H4": "super.m() calls declared by the direct parent",
    "H5": "super.m() calls declared above the direct parent",
    "H6": "super.m() calls whose name has same-arity overloads in the ancestors",
    "H7": "source interfaces / default methods / static interface methods / abstract classes implementing one",
    "H8": "records implementing an interface",
    "H9": "enum constants with a body",
    "H10": "source types implementing an interface not declared in the sources",
    "H11": "source classes extending a class not declared in the sources",
    "H12": "X.super.m() calls",
    "G1": "types implementing a source interface with type arguments",
    "G2": "classes extending a source class with type arguments",
    "G3": "generic types passing a type variable to a source supertype",
    "G4": "bounded type parameters (class or method)",
    "T1": "calls on a cast / on an array element",
    "T2": "instanceof patterns",
    "T3": "switch type or record patterns",
    "F1": "lambdas / method references",
    "F2": "proxy: source interfaces with one abstract method and a named implementation",
    "F3": "anonymous classes of a source class / of a source interface / of an external type",
    "F4": "anonymous or named Runnable/Callable implementations",
    "M1": "source types implementing an external interface, by module count (same as H10)",
    "M2": "source supertypes extended or implemented from another build module",
    "M3": "qualified type names declared in two or more build modules",
    "W1": "fields annotated @Autowired, @Resource or @Inject",
    "W2": "@Qualifier / @Primary / @Profile / @ConditionalOn* annotations",
    "W3": "publishEvent calls / @EventListener or @TransactionalEventListener methods",
    "W4": "@Async methods",
    "W5": "@Scheduled methods",
    "W6": "@PostConstruct methods / CommandLineRunner or ApplicationRunner implementations",
    "W7": "interfaces extending a *Repository type / their findBy-style methods",
    "W8": "@Mapper interfaces / types annotated @Generated",
    "X1": "Class.forName, ServiceLoader.load, getMethod/getDeclaredMethod/invoke calls",
    "X2": "@Aspect types / @Transactional annotations",
    "X3": "not a call shape: HTTP and Kafka transitions are extractor facts",
}


def _text(source: bytes, node: Node | None) -> str:
    return parser.node_text(source, node) if node is not None else ""


def _simple(type_text: str) -> str:
    return re.sub(r"<.*>", "", type_text).strip().rsplit(".", 1)[-1]


def _modifiers(source: bytes, node: Node) -> set[str]:
    return set(_text(source, parser.modifiers_node(node)).split())


def _annotations(source: bytes, node: Node) -> set[str]:
    return {parser.annotation_name(annotation, source) for annotation in parser.annotations_of(node)}


@dataclass
class _Method:
    name: str
    arity: int
    line: int
    modifiers: set[str]
    concrete: bool
    node: Node


@dataclass
class _Type:
    name: str
    qualified: str
    kind: str
    module: str
    path: str
    node: Node
    source: bytes
    superclass: str | None = None
    superclass_generic: bool = False
    interfaces: list[tuple[str, bool]] = field(default_factory=list)
    type_parameters: set[str] = field(default_factory=set)
    methods: list[_Method] = field(default_factory=list)


def _module(relative: str) -> str:
    marker = "/src/main/java/"
    return relative.split(marker, 1)[0] if marker in relative else relative.split("/", 1)[0]


def _owner_name(source: bytes, node: Node) -> list[str]:
    names = []
    current: Node | None = node
    while current is not None:
        if current.type in _TYPES:
            names.append(parser.declaration_name(current, source) or "")
        current = current.parent
    return list(reversed(names))


def _arity(node: Node) -> int:
    parameters = node.child_by_field_name("parameters")
    return sum(
        child.type in {"formal_parameter", "spread_parameter"}
        for child in (parameters.named_children if parameters is not None else [])
    )


class Corpus:
    def __init__(self, files: list[tuple[str, bytes, Node]]):
        self.files = files
        self.types: list[_Type] = []
        self.by_simple: dict[str, list[_Type]] = defaultdict(list)
        for path, source, root in files:
            package = ""
            for child in root.named_children:
                if child.type == "package_declaration":
                    package = _text(source, child).removeprefix("package").removesuffix(";").strip()
            for node in parser.walk(root):
                if node.type not in _TYPES:
                    continue
                names = _owner_name(source, node)
                info = _Type(
                    names[-1], ".".join(filter(None, [package, *names])), node.type,
                    _module(path), path, node, source,
                )
                for child in node.named_children:
                    if child.type == "superclass" and child.named_children:
                        written = _text(source, child.named_children[0])
                        info.superclass, info.superclass_generic = _simple(written), "<" in written
                    elif child.type in {"super_interfaces", "extends_interfaces"}:
                        for type_list in child.named_children:
                            for item in type_list.named_children if type_list.type == "type_list" else [type_list]:
                                written = _text(source, item)
                                info.interfaces.append((_simple(written), "<" in written))
                    elif child.type == "type_parameters":
                        info.type_parameters = {
                            _text(source, parameter.named_children[0])
                            for parameter in child.named_children if parameter.named_children
                        }
                body = node.child_by_field_name("body")
                members = body.named_children if body is not None else []
                if node.type == "enum_declaration":
                    members = [
                        member for declarations in members if declarations.type == "enum_body_declarations"
                        for member in declarations.named_children
                    ]
                for member in members:
                    if member.type == "method_declaration":
                        info.methods.append(_Method(
                            parser.declaration_name(member, source) or "", _arity(member),
                            member.start_point.row + 1, _modifiers(source, member),
                            member.child_by_field_name("body") is not None, member,
                        ))
                self.types.append(info)
                self.by_simple[info.name].append(info)

    def lookup(self, name: str | None, module: str) -> _Type | None:
        candidates = self.by_simple.get(name or "", [])
        local = [candidate for candidate in candidates if candidate.module == module]
        chosen = local or candidates
        return chosen[0] if len(chosen) == 1 else None

    def parents(self, info: _Type) -> list[_Type]:
        names = ([info.superclass] if info.superclass else []) + [name for name, _ in info.interfaces]
        return [parent for name in names if (parent := self.lookup(name, info.module)) is not None]

    def ancestors(self, info: _Type) -> list[_Type]:
        seen: dict[int, _Type] = {}
        pending = self.parents(info)
        while pending:
            current = pending.pop()
            if id(current) not in seen and current is not info:
                seen[id(current)] = current
                pending.extend(self.parents(current))
        return list(seen.values())

    def class_chain(self, info: _Type) -> list[_Type]:
        """Superclasses, nearest first."""
        chain: list[_Type] = []
        current = self.lookup(info.superclass, info.module) if info.superclass else None
        while current is not None and current not in chain:
            chain.append(current)
            current = self.lookup(current.superclass, current.module) if current.superclass else None
        return chain


def _enclosing_type(node: Node) -> Node | None:
    current = node.parent
    while current is not None:
        if current.type in _TYPES or (
            current.type == "class_body" and current.parent is not None
            and current.parent.type in {"object_creation_expression", "enum_constant"}
        ):
            return current if current.type in _TYPES else current.parent
        current = current.parent
    return None


def _in_initializer(node: Node) -> bool:
    """Whether the nearest enclosing body is a constructor or an initializer."""
    current = node.parent
    while current is not None:
        if current.type == "method_declaration":
            return False
        if current.type in {"constructor_declaration", "static_initializer", "field_declaration"}:
            return True
        if current.type == "block" and current.parent is not None and current.parent.type == "class_body":
            return True
        current = current.parent
    return False


def count(corpus: Corpus) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    types_by_node = {(info.path, info.node.id): info for info in corpus.types}
    subtypes: dict[int, list[_Type]] = defaultdict(list)
    for info in corpus.types:
        for parent in corpus.parents(info):
            subtypes[id(parent)].append(info)

    def type_of(path: str, node: Node) -> _Type | None:
        owner = _enclosing_type(node)
        return types_by_node.get((path, owner.id)) if owner is not None else None

    tally: Counter[str] = Counter()
    super_calls: list[tuple[_Type, str, int]] = []
    for path, source, root in corpus.files:
        for child in root.named_children:
            if child.type == "import_declaration" and _text(source, child).startswith("import static"):
                tally["S2.imports"] += 1
        seen_lines: Counter[tuple[int, str, int]] = Counter()
        for node in parser.walk(root):
            kind = node.type
            if kind == "method_declaration":
                owner = _enclosing_type(node)
                if owner is not None:
                    seen_lines[(owner.id, parser.declaration_name(node, source) or "", node.start_point.row)] += 1
            elif kind == "method_invocation":
                receiver, name, arguments = parser.invocation_parts(node, source)
                has_super = any(child.type == "super" for child in node.children)
                if receiver is not None and receiver.type not in {"this", "super"} and not has_super:
                    tally["S1"] += 1
                if receiver is not None and receiver.type == "cast_expression":
                    tally["T1.cast"] += 1
                if receiver is not None and receiver.type == "parenthesized_expression" and any(
                    child.type == "cast_expression" for child in receiver.named_children
                ):
                    tally["T1.cast"] += 1
                if receiver is not None and receiver.type == "array_access":
                    tally["T1.array"] += 1
                if has_super and receiver is not None and receiver.type != "super":
                    tally["H12"] += 1
                owner = type_of(path, node)
                if receiver is not None and receiver.type == "super" and owner is not None:
                    super_calls.append((owner, name, len(arguments)))
                if owner is not None and (receiver is None or receiver.type == "this"):
                    if any(m.name == name and m.arity == len(arguments) and not m.concrete
                           and "static" not in m.modifiers for m in owner.methods):
                        tally["H3"] += 1
                if receiver is None and owner is not None and owner.node.parent is not None:
                    outer = _enclosing_type(owner.node)
                    outer_type = types_by_node.get((path, outer.id)) if outer is not None else None
                    declared = {m.name for t in [owner, *corpus.ancestors(owner)] for m in t.methods}
                    if outer_type is not None and name not in declared and any(
                        m.name == name for m in outer_type.methods
                    ):
                        tally["S3"] += 1
                if name == "publishEvent":
                    tally["W3.publish"] += 1
                text = _text(source, node)
                if text.startswith(("Class.forName", "ServiceLoader.load")) or name in {
                    "getMethod", "getDeclaredMethod", "invoke",
                }:
                    tally["X1"] += 1
                if _in_initializer(node):
                    tally["S8"] += 1
            elif kind == "constructor_declaration":
                tally["S7.declared"] += 1
            elif kind == "explicit_constructor_invocation":
                tally["S7.this_super"] += 1
            elif kind == "object_creation_expression":
                written = _simple(_text(source, node.child_by_field_name("type")))
                created = corpus.lookup(written, _module(path))
                if created is not None:
                    tally["S7.new"] += 1
                if parser.child_by_type(node, "class_body") is not None:
                    if created is None:
                        tally["F3.external"] += 1
                    elif created.kind == "interface_declaration":
                        tally["F3.interface"] += 1
                    else:
                        tally["F3.class"] += 1
                    if written in {"Runnable", "Callable"}:
                        tally["F4"] += 1
            elif kind == "lambda_expression":
                tally["F1.lambda"] += 1
            elif kind == "method_reference":
                tally["F1.reference"] += 1
            elif kind == "instanceof_expression" and (
                node.child_by_field_name("name") is not None or node.child_by_field_name("pattern") is not None
                or any(child.type in {"type_pattern", "record_pattern"} for child in node.named_children)
            ):
                tally["T2"] += 1
            elif kind == "switch_label" and any(child.type == "pattern" for child in node.named_children):
                tally["T3"] += 1
            elif kind == "type_parameter" and any(child.type == "type_bound" for child in node.named_children):
                tally["G4"] += 1
            elif kind == "enum_constant" and node.child_by_field_name("body") is not None:
                tally["H9"] += 1
            elif kind == "field_declaration":
                if _annotations(source, node) & {"Autowired", "Resource", "Inject"}:
                    tally["W1"] += 1
            if kind in {"method_declaration", "field_declaration", "class_declaration", "interface_declaration",
                        "constructor_declaration", "formal_parameter", "record_declaration"}:
                annotations = _annotations(source, node)
                tally["W2"] += len(annotations & {"Qualifier", "Primary", "Profile"}) + sum(
                    name.startswith("ConditionalOn") for name in annotations
                )
                tally["X2.transactional"] += "Transactional" in annotations
                if kind == "method_declaration":
                    tally["W3.listener"] += bool(annotations & {"EventListener", "TransactionalEventListener"})
                    tally["W4"] += "Async" in annotations
                    tally["W5"] += "Scheduled" in annotations
                    tally["W6.post_construct"] += "PostConstruct" in annotations
                if kind in _TYPES:
                    tally["W8.generated"] += "Generated" in annotations
                    tally["X2.aspect"] += "Aspect" in annotations
                    tally["W8.mapper"] += kind == "interface_declaration" and "Mapper" in annotations
        tally["S6"] += sum(1 for value in seen_lines.values() if value > 1)

    descendants: dict[int, list[_Type]] = defaultdict(list)
    for info in corpus.types:
        for ancestor in corpus.ancestors(info):
            descendants[id(ancestor)].append(info)
    for info in corpus.types:
        parents = corpus.parents(info)
        chain = corpus.class_chain(info)
        ancestors = corpus.ancestors(info)
        if info.kind == "class_declaration" and info.superclass:
            superclass = corpus.lookup(info.superclass, info.module)
            if superclass is not None:
                tally["H1"] += 1
                tally["G2"] += info.superclass_generic
                tally["M2"] += superclass.module != info.module
            elif not corpus.by_simple.get(info.superclass):
                tally["H11"] += 1
        for name, generic in info.interfaces:
            interface = corpus.lookup(name, info.module)
            if interface is not None:
                tally["G1"] += generic and info.kind != "interface_declaration"
                tally["M2"] += interface.module != info.module
            elif not corpus.by_simple.get(name) and info.kind != "interface_declaration":
                tally["H10"] += 1
                tally["H8"] += info.kind == "record_declaration"
            if name in {"Runnable", "Callable"}:
                tally["F4"] += 1
            if name in {"CommandLineRunner", "ApplicationRunner"}:
                tally["W6.runner"] += 1
            if info.kind == "interface_declaration" and name.endswith("Repository"):
                tally["W7.repository"] += 1
                tally["W7.derived"] += sum(
                    bool(re.match(r"(find|read|get|query|count|exists|delete|remove|stream)\w*By", m.name))
                    for m in info.methods
                )
        if info.kind == "record_declaration":
            tally["H8"] += sum(1 for name, _ in info.interfaces if corpus.lookup(name, info.module) is not None)
        if info.type_parameters and any(
            re.search(rf"\b{re.escape(parameter)}\b", re.sub(r"^[^<]*", "", _text(info.source, child)))
            for child in info.node.named_children
            if child.type in {"superclass", "super_interfaces"}
            for parameter in info.type_parameters
        ):
            tally["G3"] += 1
        if info.kind == "interface_declaration":
            tally["H7.interfaces"] += 1
            tally["H7.default"] += sum("default" in m.modifiers for m in info.methods)
            tally["H7.static"] += sum("static" in m.modifiers for m in info.methods)
            abstract = [m for m in info.methods if not m.concrete and "static" not in m.modifiers]
            implementors = [s for s in subtypes[id(info)] if s.kind != "interface_declaration"]
            tally["F2"] += len(abstract) == 1 and bool(implementors)
        if info.kind == "class_declaration" and "abstract" in _modifiers(info.source, info.node) and any(
            parent.kind == "interface_declaration" for parent in parents
        ):
            tally["H7.abstract_bridge"] += 1
        groups = Counter((m.name, m.arity) for m in info.methods)
        tally["S5"] += sum(1 for value in groups.values() if value > 1)
        inherited = {(m.name, m.arity) for ancestor in chain for m in ancestor.methods
                     if not m.modifiers & {"static", "private"}}
        for method in info.methods:
            if "static" in method.modifiers:
                tally["S2.declared"] += 1
                if any((m.name, m.arity) == (method.name, method.arity) and "static" in m.modifiers
                       for ancestor in ancestors for m in ancestor.methods):
                    tally["S2.hiding"] += 1
            if "private" in method.modifiers:
                tally["S4.declared"] += 1
                related = [*ancestors, *descendants[id(info)]]
                if any((m.name, m.arity) == (method.name, method.arity) for t in related for m in t.methods):
                    tally["S4.homonym"] += 1
            if (method.name, method.arity) in inherited and not method.modifiers & {"static", "private"}:
                tally["H2"] += 1
    for owner, name, arity in super_calls:
        chain = corpus.class_chain(owner)
        if not chain:
            continue
        direct = [m for m in chain[0].methods if (m.name, m.arity) == (name, arity)]
        above = [m for ancestor in chain[1:] for m in ancestor.methods if (m.name, m.arity) == (name, arity)]
        if direct:
            tally["H4"] += 1
        elif above:
            tally["H5"] += 1
        if len([m for ancestor in chain for m in ancestor.methods if (m.name, m.arity) == (name, arity)]) > 1:
            tally["H6"] += 1
    qualified_modules: dict[str, set[str]] = defaultdict(set)
    for info in corpus.types:
        qualified_modules[info.qualified].add(info.module)
    tally["M3"] = sum(1 for modules in qualified_modules.values() if len(modules) > 1)
    tally["M1"] = tally["H10"]

    parts = {
        "S2": ["S2.declared", "S2.imports", "S2.hiding"], "S4": ["S4.declared", "S4.homonym"],
        "S7": ["S7.declared", "S7.new", "S7.this_super"],
        "H7": ["H7.interfaces", "H7.default", "H7.static", "H7.abstract_bridge"],
        "T1": ["T1.cast", "T1.array"], "F1": ["F1.lambda", "F1.reference"],
        "F3": ["F3.class", "F3.interface", "F3.external"], "W3": ["W3.publish", "W3.listener"],
        "W6": ["W6.post_construct", "W6.runner"], "W7": ["W7.repository", "W7.derived"],
        "W8": ["W8.mapper", "W8.generated"], "X2": ["X2.aspect", "X2.transactional"],
    }
    for row in ROWS:
        if row == "X3":
            continue
        result[row] = [tally[key] for key in parts.get(row, [row])]
    return result


def collect(root: Path) -> dict[str, list[tuple[str, bytes, Node]]]:
    groups: dict[str, list[tuple[str, bytes, Node]]] = defaultdict(list)
    java = parser.java_parser("count_call_shapes")
    for path in sorted(root.rglob("*.java")):
        relative = path.relative_to(root).as_posix()
        if relative.startswith(".git/") or "/src/test/" in f"/{relative}":
            continue
        group = next((name for name in _SEPARATE_GROUPS if relative.startswith(f"{name}/")), "main")
        source = path.read_bytes()
        groups[group].append((relative, source, java.parse(source).root_node))
    return groups


def main() -> int:
    argument_parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    argument_parser.add_argument("root", type=Path, help="Java source tree, read only")
    argument_parser.add_argument("--json", action="store_true", help="emit machine-readable counts")
    args = argument_parser.parse_args()
    groups = collect(args.root.expanduser().resolve())
    report = {
        group: {"files": len(files), "counts": count(Corpus(files))}
        for group, files in sorted(groups.items())
    }
    if args.json:
        json.dump(report, sys.stdout, indent=2)
        print()
        return 0
    for group, data in report.items():
        print(f"## {group} ({data['files']} files)")
        for row, values in data["counts"].items():
            print(f"{row:4} {' / '.join(map(str, values)):>16}  {ROWS[row]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
