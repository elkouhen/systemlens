"""Materialize Kafka DTO definitions from indexed Java project sources.

This belongs to indexing, not rendering: the resulting definitions are stored
in the architecture snapshot and HTML exports consume only those persisted
facts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from systemlens import java_parser
from systemlens.models import MessageEndpoint
from systemlens.modules import DiscoveredModule, module_identity


def _java_type_references(source_bytes: bytes, type_node) -> list[str]:
    return re.findall(
        r"(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*",
        java_parser.node_text(source_bytes, type_node),
    )


def _java_dto_fields(
    source: str, dto_name: str
) -> tuple[list[dict[str, object]], list[list[str]]]:
    """Extract declared Java class or record fields conservatively."""
    source_bytes = source.encode("utf-8")
    root = java_parser.java_parser("dto_fields").parse(source_bytes).root_node
    if root.has_error:
        return [], []
    declaration = next(
        (
            node
            for node in java_parser.type_declarations(root)
            if java_parser.declaration_name(node, source_bytes) == dto_name
        ),
        None,
    )
    if declaration is None:
        return [], []

    def field(node) -> tuple[dict[str, object], list[str]] | None:
        type_node = node.child_by_field_name("type")
        name_node = node.child_by_field_name("name")
        if type_node is None or name_node is None:
            return None
        return (
            {
                "type": java_parser.node_text(source_bytes, type_node).strip(),
                "name": java_parser.node_text(source_bytes, name_node),
            },
            _java_type_references(source_bytes, type_node),
        )

    if declaration.type == "record_declaration":
        parameters = java_parser.child_by_type(declaration, "formal_parameters")
        if parameters is None:
            return [], []
        values = [
            value
            for parameter in parameters.named_children
            if parameter.type == "formal_parameter"
            if (value := field(parameter)) is not None
        ]
        return (
            [item for item, _references in values],
            [references for _item, references in values],
        )

    fields: list[dict[str, object]] = []
    references_by_field: list[list[str]] = []
    for node in java_parser.walk(declaration):
        if node.type != "field_declaration" or java_parser.enclosing(
            node,
            "class_declaration",
            "interface_declaration",
            "record_declaration",
            "enum_declaration",
        ) != declaration:
            continue
        type_node = node.child_by_field_name("type")
        if type_node is None:
            continue
        field_type = java_parser.node_text(source_bytes, type_node).strip()
        for declarator in node.children:
            if declarator.type != "variable_declarator":
                continue
            name_node = declarator.child_by_field_name("name")
            if name_node is not None:
                fields.append(
                    {
                        "type": field_type,
                        "name": java_parser.node_text(source_bytes, name_node),
                    }
                )
                references_by_field.append(_java_type_references(source_bytes, type_node))
    return fields, references_by_field


def _java_enum_values(source: str, enum_name: str) -> list[str]:
    source_bytes = source.encode("utf-8")
    root = java_parser.java_parser("enum_values").parse(source_bytes).root_node
    if root.has_error:
        return []
    declaration = next(
        (
            node
            for node in java_parser.type_declarations(root)
            if node.type == "enum_declaration"
            if java_parser.declaration_name(node, source_bytes) == enum_name
        ),
        None,
    )
    if declaration is None:
        return []
    body = java_parser.child_by_type(declaration, "enum_body")
    if body is None:
        return []
    return [
        java_parser.node_text(source_bytes, name)
        for node in body.named_children
        if node.type == "enum_constant"
        if (name := node.child_by_field_name("name")) is not None
    ]


def _java_project_dto_names(source: str) -> set[str]:
    source_bytes = source.encode("utf-8")
    root = java_parser.java_parser("dto_names").parse(source_bytes).root_node
    if root.has_error:
        return set()
    return {
        name
        for declaration in java_parser.type_declarations(root)
        if declaration.type
        in {"class_declaration", "record_declaration", "enum_declaration"}
        if (name := java_parser.declaration_name(declaration, source_bytes)) is not None
    }


@dataclass(frozen=True)
class _JavaDtoCandidate:
    qualified_name: str
    name: str
    source_path: str
    source: str
    package: str
    imports: frozenset[str]
    module: str


def _java_package_and_imports(source: str) -> tuple[str, frozenset[str]]:
    source_bytes = source.encode("utf-8")
    root = java_parser.java_parser("dto_context").parse(source_bytes).root_node
    if root.has_error:
        return "", frozenset()
    package = ""
    imports: set[str] = set()
    for node in root.named_children:
        text = java_parser.node_text(source_bytes, node).strip().rstrip(";")
        if node.type == "package_declaration":
            package = text.removeprefix("package").strip()
        elif node.type == "import_declaration" and not text.startswith("import static "):
            imported = text.removeprefix("import").strip()
            if imported:
                imports.add(imported)
    return package, frozenset(imports)


def materialize_kafka_dto_definitions(
    endpoints_by_service: dict[str, list[MessageEndpoint]],
    modules: list[DiscoveredModule],
) -> list[dict[str, object]]:
    """Build the complete persisted Kafka DTO closure for one index snapshot."""
    candidates: dict[str, _JavaDtoCandidate] = {}
    source_contexts: dict[str, tuple[str, frozenset[str]]] = {}
    for module in modules:
        source_root = module.path / "src" / "main" / "java"
        if not source_root.is_dir():
            continue
        for java_path in source_root.glob("**/*.java"):
            try:
                source = java_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            package, imports = _java_package_and_imports(source)
            for dto_name in _java_project_dto_names(source):
                qualified_name = f"{package}.{dto_name}" if package else dto_name
                candidates.setdefault(
                    qualified_name,
                    _JavaDtoCandidate(
                        qualified_name=qualified_name,
                        name=dto_name,
                        source_path=str(java_path.relative_to(module.path)),
                        source=source,
                        package=package,
                        imports=imports,
                        module=module_identity(module),
                    ),
                )
                source_contexts.setdefault(qualified_name, (package, imports))

    candidates_by_name: dict[str, list[_JavaDtoCandidate]] = {}
    for indexed_candidate in candidates.values():
        candidates_by_name.setdefault(indexed_candidate.name, []).append(indexed_candidate)

    def resolve_type(
        type_name: str,
        context: tuple[str, frozenset[str]] | None = None,
    ) -> str | None:
        normalized = type_name.strip().replace("$", ".")
        if normalized in candidates:
            return normalized
        simple_name = normalized.rsplit(".", 1)[-1]
        package, imports = context or ("", frozenset())
        for imported in imports:
            if imported.endswith(".*"):
                qualified_name = f"{imported[:-2]}.{simple_name}"
                if qualified_name in candidates:
                    return qualified_name
            elif imported.rsplit(".", 1)[-1] == simple_name and imported in candidates:
                return imported
        if package:
            qualified_name = f"{package}.{simple_name}"
            if qualified_name in candidates:
                return qualified_name
        matches = candidates_by_name.get(simple_name, [])
        return matches[0].qualified_name if len(matches) == 1 else None

    root_ids: set[str] = set()
    endpoint_root_ids: dict[str, str] = {}
    for endpoints in endpoints_by_service.values():
        for endpoint in endpoints:
            if endpoint.system != "kafka" or not endpoint.message_type:
                continue
            root_id = resolve_type(
                endpoint.message_type,
                source_contexts.get(endpoint.qualified_name or ""),
            ) or f"unresolved:{endpoint.message_type}"
            root_ids.add(root_id)
            endpoint_root_ids[endpoint.id] = root_id

    definitions: dict[str, dict[str, object]] = {}
    pending = sorted(root_ids)
    while pending:
        dto_id = pending.pop(0)
        if dto_id in definitions:
            continue
        candidate: _JavaDtoCandidate | None = candidates.get(dto_id)
        dto_name = (
            candidate.name
            if candidate
            else dto_id.removeprefix("unresolved:").rsplit(".", 1)[-1]
        )
        definition: dict[str, object] = {
            "id": dto_id,
            "name": dto_name,
            "qualified_name": candidate.qualified_name if candidate else None,
            "fields": [],
            "source": candidate.source_path if candidate else None,
            "module": candidate.module if candidate else None,
        }
        if candidate:
            fields, references_by_field = _java_dto_fields(candidate.source, candidate.name)
            for field, references in zip(fields, references_by_field, strict=True):
                nested = sorted(
                    {
                        resolved
                        for reference in references
                        if (
                            resolved := resolve_type(
                                reference, (candidate.package, candidate.imports)
                            )
                        )
                    }
                )
                if nested:
                    field["dto_references"] = nested
                    pending.extend(reference for reference in nested if reference != dto_id)
            definition["fields"] = fields
            if enum_values := _java_enum_values(candidate.source, candidate.name):
                definition["enum_values"] = enum_values
        definitions[dto_id] = definition

    result: list[dict[str, object]] = []
    for dto_id, definition in sorted(definitions.items()):
        matches = [
            (service, endpoint)
            for service, endpoints in endpoints_by_service.items()
            for endpoint in endpoints
            if endpoint_root_ids.get(endpoint.id) == dto_id
        ]
        definition["producers"] = sorted(
            {service for service, endpoint in matches if endpoint.role == "produce"}
        )
        definition["consumers"] = sorted(
            {service for service, endpoint in matches if endpoint.role == "consume"}
        )
        definition["topics"] = sorted({endpoint.topic for _service, endpoint in matches})
        definition["root"] = dto_id in root_ids
        result.append(definition)
    return result
