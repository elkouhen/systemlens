"""Inventaire de tous les modules Maven et Gradle d'un workspace."""

import os
import re
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import yaml

from systemlens.discovery.java import parser as java_parser
from systemlens.discovery.build.data_operations import (
    discover_data_operations,
    module_java_files,
    resolve_repository_interfaces,
)
from systemlens.domain.data_operation_projection import mongo_data_operation
from systemlens.domain.data_operations import DataOperation, sort_data_operations
from systemlens.discovery.build.gradle import gradle_version
from systemlens.discovery.build.maven import (
    MavenDependency,
    maven_dependencies,
    maven_group_id,
    parse_pom,
    pom_version,
)
from systemlens.discovery.build.projects import (
    BuildLayout,
    _is_excluded_maven_module,
    _is_excluded_module,
    cached_build_layout,
    refresh_build_layout,
)
from systemlens.discovery.configuration import service_configuration_example
from systemlens.domain.module_inventory import (
    BlockingPoint,
    DiscoveredModule,
    JpaEntity,
    JpaMethod,
    JpaDto,
    JpaField,
    JavaArchitectureExtension,
    KafkaMethod,
    ModuleDependency,
    MongoField,
    MongoMethod,
    MongoPersistenceClass,
    SourceEvidence,
    module_identity,
)
from systemlens.domain.topic_expressions import spring_topic_reference


def _is_build_output_path(root: Path, path: Path) -> bool:
    """Return whether *path* is nested under Maven or Gradle build output.

    Module discovery is independent from the incremental file inventory, so it
    must enforce the same boundary itself.  Generated ``pom.xml`` files (for
    example OpenAPI output below ``target/generated-sources``) are not source
    modules and must never affect the persisted module graph.
    """
    parts = path.relative_to(root).parts
    return any(
        part in {"target", "build"} and "src" not in parts[:index]
        for index, part in enumerate(parts)
    )


@dataclass(frozen=True)
class _JavaPersistenceCandidate:
    name: str
    qualified_name: str
    path: str
    line: int
    fields: tuple[MongoField, ...]


def _resolve_persistence_field_references(
    field_type: str,
    containing_type: _JavaPersistenceCandidate,
    candidates: dict[str, list[_JavaPersistenceCandidate]],
) -> tuple[str, ...]:
    """Resolve project types conservatively, preferring the containing package."""
    package = containing_type.qualified_name.rpartition(".")[0]
    references: list[str] = []
    for token in re.findall(r"(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*", field_type):
        simple_name = token.rsplit(".", 1)[-1]
        matches = candidates.get(simple_name, [])
        if "." in token:
            matches = [candidate for candidate in matches if candidate.qualified_name == token]
        elif package:
            same_package = [
                candidate for candidate in matches
                if candidate.qualified_name.rpartition(".")[0] == package
            ]
            if len(same_package) == 1:
                matches = same_package
        if len(matches) == 1 and matches[0].qualified_name not in references:
            references.append(matches[0].qualified_name)
    return tuple(references)


def _expand_persistence_classes(
    roots: set[MongoPersistenceClass],
    candidates: dict[str, list[_JavaPersistenceCandidate]],
) -> tuple[MongoPersistenceClass, ...]:
    """Persist the recursive project-type closure for every Mongo root class."""
    candidates_by_qualified_name = {
        candidate.qualified_name: candidate
        for matches in candidates.values()
        for candidate in matches
    }
    root_keys = {(item.collection, item.qualified_name) for item in roots}
    pending = deque(sorted(root_keys))
    scheduled = set(root_keys)
    definitions: dict[tuple[str, str], MongoPersistenceClass] = {}
    while pending:
        collection, qualified_name = pending.popleft()
        key = (collection, qualified_name)
        if key in definitions:
            continue
        candidate = candidates_by_qualified_name.get(qualified_name)
        if candidate is None:
            continue
        fields = tuple(MongoField(
            name=field.name,
            type=field.type,
            references=_resolve_persistence_field_references(
                field.type, candidate, candidates
            ),
        ) for field in candidate.fields)
        definitions[key] = MongoPersistenceClass(
            collection=collection,
            name=candidate.name,
            qualified_name=candidate.qualified_name,
            path=candidate.path,
            line=candidate.line,
            fields=fields,
            root=key in root_keys,
        )
        for reference in fields:
            for qualified_reference in reference.references:
                reference_key = (collection, qualified_reference)
                if reference_key not in scheduled:
                    scheduled.add(reference_key)
                    pending.append(reference_key)
    return tuple(sorted(definitions.values()))


_MONGO_TEMPLATE_OPERATIONS = frozenset({
    "aggregate", "bulkOps", "count", "exists", "find", "findAll", "findAndModify",
    "findAndReplace", "findById", "findOne", "getCollection", "insert", "remove",
    "save", "updateFirst", "updateMulti", "upsert", "watch",
})
_REPOSITORY_OPERATIONS = frozenset({
    "aggregate", "count", "delete", "deleteAll", "deleteAllById", "deleteById",
    "existsById", "find", "findAll", "findById", "findOne", "insert", "insertAll",
    "save", "saveAll",
})
_OPENAPI_FILENAMES = (
    "openapi.yaml", "openapi.yml", "openapi.json", "swagger.yaml", "swagger.yml", "swagger.json",
)
_OPENAPI_DOCUMENT_SUFFIXES = frozenset({".yaml", ".yml", ".json"})
_OPENAPI_RESOURCE_DIRECTORY = ("src", "main", "resources", "openapi")


__all__ = ["_is_excluded_maven_module", "_is_excluded_module"]


def discover_excluded_module_paths(root: Path) -> tuple[Path, ...]:
    """Return build directories excluded from the production index.

    Module inventory omits test, mock and archetype modules, ``buildSrc``,
    included builds and build directories that a declaring build does not
    include (ADR-80). The indexer needs their paths before scanning files:
    an excluded directory can contain ``src/main`` sources and would
    otherwise still produce endpoints and findings without a module.
    """
    return cached_build_layout(root).excluded_paths


def _trace(stage: str, **fields: object) -> None:
    if os.environ.get("SYSTEMLENS_TRACE") != "1":
        return
    details = " ".join(f"{name}={value}" for name, value in fields.items())
    print(f"SYSTEMLENS_TRACE ts={time.monotonic():.6f} stage={stage} {details}".rstrip(), file=sys.stderr, flush=True)


def _starts_application(
    module_dir: Path, module_roots: frozenset[Path] | set[Path] = frozenset(),
) -> SourceEvidence | None:
    """Return whether this build module owns a Spring Boot entry point.

    The decision belongs to module parsing, independently of Maven/Gradle. It
    uses Java method boundaries from Tree-sitter and only recognises a
    ``main`` method that invokes ``SpringApplication.run``. Sources below a
    nested build module (``module_roots``) belong to that module: an
    aggregator never inherits its children's entry point.
    """
    source_roots = sorted(
        source_root for source_root in module_dir.glob("**/src/main/java")
        if not any(
            module_dir in nested.parents
            and (nested == source_root or nested in source_root.parents)
            for nested in module_roots
        )
    )
    if not source_roots:
        return None
    _trace("module.entrypoint.parser.begin", module=module_dir)
    parser = _java_parser("entrypoint")
    _trace("module.entrypoint.parser.end", module=module_dir, roots=len(source_roots))
    for source_root in source_roots:
        for path in source_root.rglob("*.java"):
            try:
                source = path.read_bytes()
            except OSError:
                continue
            _trace("module.entrypoint.parse.begin", module=module_dir, path=path)
            tree = parser.parse(source)
            _trace("module.entrypoint.parse.end", module=module_dir, path=path)
            if tree.root_node.has_error:
                continue
            for node in _walk(tree.root_node):
                if node.type != "method_declaration":
                    continue
                name = node.child_by_field_name("name")
                if name is None or _node_text(source, name) != "main":
                    continue
                for invocation in _walk(node):
                    if invocation.type == "method_invocation" and "SpringApplication.run" in _node_text(source, invocation):
                        return _source_evidence(source, invocation, _module_relative(module_dir, path))
    return None


# Traversée et moteur tree-sitter : factorisés dans `java_parser` (utilisés
# aussi par `scanner.py`). Les alias conservent les noms privés historiques
# pour limiter le bruit de renommage dans ce module.
_walk = java_parser.walk
_node_text = java_parser.node_text
_enclosing_method_name = java_parser.enclosing_method_name
_java_parser = java_parser.java_parser


def _source_evidence(source: bytes, node, rel_path: str) -> SourceEvidence:
    start_line, end_line, snippet, source_hash = java_parser.evidence_fields(
        source, node, rel_path
    )
    return SourceEvidence(
        start_line=start_line,
        end_line=end_line,
        snippet=snippet,
        source_hash=source_hash,
    )


def _first_string_value(node, source: bytes) -> str | None:
    """Return the first string literal nested in a parsed Java expression."""
    if node is None:
        return None
    for child in _walk(node):
        literal = java_parser.string_value(child, source)
        if literal is not None:
            return literal
    return None


def _has_parse_error(node) -> bool:
    """Whether a syntax subtree contains an error or a missing token."""
    return any(child.type == "ERROR" or child.is_missing for child in _walk(node))


def _kafka_topic_value(node, source: bytes) -> str | None:
    literal = _first_string_value(node, source)
    if literal is None:
        return None
    reference = spring_topic_reference(literal)
    if reference is not None:
        return reference.display_name
    return literal


def _module_relative(module_dir: Path, path: Path) -> str:
    return path.relative_to(module_dir).as_posix()


def _mongo_collection_literal(invocation, source: bytes) -> str | None:
    """Return a literal trailing Mongo collection argument, when present."""
    arguments = java_parser.argument_nodes(invocation)
    if not arguments:
        return None
    return java_parser.string_value(arguments[-1], source)


def _mongo_entity_class_literal(invocation, source: bytes) -> str | None:
    """Return the Java type used as an unambiguous ``Type.class`` argument."""
    matches = {
        match.group(1).rsplit(".", 1)[-1]
        for argument in java_parser.argument_nodes(invocation)
        for match in re.finditer(
            r"\b((?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*)\s*\.\s*class\b",
            _node_text(source, argument),
        )
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _invocation_receiver_and_operation(invocation, source: bytes) -> tuple[str, str] | None:
    """Extract a direct Java call receiver and its operation from the AST."""
    receiver, operation, _arguments = java_parser.invocation_parts(invocation, source)
    if receiver is None:
        return None
    receiver_text = _node_text(source, receiver)
    return receiver_text.rsplit(".", 1)[-1], operation


def _enclosing_type_start(node) -> int | None:
    """Identify the nearest Java type, so equal field names do not cross classes."""
    current = node.parent
    while current is not None:
        if current.type in {
            "class_declaration", "interface_declaration", "record_declaration", "enum_declaration",
        }:
            return current.start_byte
        current = current.parent
    return None


def _type_identifiers(node, source: bytes) -> list[str]:
    return [
        _node_text(source, child)
        for child in _walk(node)
        if child.type in {"type_identifier", "scoped_type_identifier"}
    ]


def _repository_entity(type_node, source: bytes) -> str | None:
    identifiers = _type_identifiers(type_node, source)
    if len(identifiers) >= 2 and identifiers[0].rsplit(".", 1)[-1] in {
        "MongoRepository", "ReactiveMongoRepository"
    }:
        return identifiers[1].rsplit(".", 1)[-1]
    return None


def _module_files(module_dir: Path, module_roots: set[Path], pattern: str):
    """Yield files owned by this module, never files of nested modules."""
    for path in sorted(module_dir.rglob(pattern)):
        if not path.is_file():
            continue
        if _is_build_output_path(module_dir, path):
            continue
        if any(
            parent in module_roots
            and parent != module_dir
            and module_dir in parent.parents
            for parent in path.parents
        ):
            continue
        yield path


class KafkaArchitectureExtension:
    """Extrait les usages Kafka sans dépendre des extracteurs Mongo/JPA."""

    name = "kafka"

    def extract(self, files: list[tuple[str, bytes]]) -> tuple[KafkaMethod, ...]:
        parser = _java_parser("kafka")
        methods: set[KafkaMethod] = set()
        for rel, source in files:
            source_text = source.decode("utf-8", errors="replace")
            tree = parser.parse(source)
            if tree.root_node.has_error:
                continue
            has_kafka_consumer = "KafkaConsumer" in source_text
            has_kafka_producer = "KafkaProducer" in source_text
            for method_node in _walk(tree.root_node):
                if method_node.type != "method_declaration":
                    continue
                method_name_node = method_node.child_by_field_name("name")
                if method_name_node is None:
                    continue
                method_name = _node_text(source, method_name_node)
                for annotation_node in java_parser.annotations_of(method_node):
                    annotation_name = java_parser.annotation_name(annotation_node, source)
                    if annotation_name in {"KafkaListener", "KafkaHandler"}:
                        topic_node = (
                            java_parser.annotation_argument(annotation_node, source, "topics")
                            or java_parser.annotation_argument(annotation_node, source, "topic")
                            or java_parser.annotation_argument(annotation_node, source, "value")
                            or java_parser.annotation_argument(annotation_node, source)
                        )
                        methods.add(KafkaMethod(
                            role="receive", mechanism="spring-kafka-listener", method=method_name,
                            path=rel, line=method_node.start_point.row + 1,
                            topic=_kafka_topic_value(topic_node, source),
                            evidence=_source_evidence(source, annotation_node, rel),
                        ))
                    if annotation_name == "SendTo":
                        methods.add(KafkaMethod(
                            role="send", mechanism="spring-kafka-send-to", method=method_name,
                            path=rel, line=method_node.start_point.row + 1,
                            topic=_kafka_topic_value(
                                java_parser.annotation_argument(annotation_node, source), source
                            ),
                            evidence=_source_evidence(source, annotation_node, rel),
                        ))
                for invocation in _walk(method_node):
                    if invocation.type != "method_invocation":
                        continue
                    call = _invocation_receiver_and_operation(invocation, source)
                    if call is None:
                        continue
                    receiver, operation = call
                    topic = _kafka_topic_value(
                        java_parser.argument_nodes(invocation)[0], source
                    ) if java_parser.argument_nodes(invocation) else None
                    mechanism: str | None = None
                    role: str | None = None
                    if operation in {"send", "sendDefault"} and (
                        receiver.lower().endswith("kafkatemplate") or "KafkaTemplate" in source_text
                    ):
                        role, mechanism = "send", "spring-kafka-template"
                    elif operation == "send" and has_kafka_producer:
                        role, mechanism = "send", "kafka-clients-producer"
                    elif operation == "send" and receiver.lower().endswith("streambridge"):
                        role, mechanism = "send", "spring-cloud-stream"
                    elif operation == "to" and ("StreamsBuilder" in source_text or "KStream" in source_text):
                        role, mechanism = "send", "kafka-streams"
                    elif operation == "poll" and has_kafka_consumer:
                        role, mechanism = "receive", "kafka-clients-poll"
                    elif operation == "stream" and ("StreamsBuilder" in source_text or "KStream" in source_text):
                        role, mechanism = "receive", "kafka-streams"
                    if role is not None and mechanism is not None:
                        methods.add(KafkaMethod(
                            role=role, mechanism=mechanism, method=method_name,
                            path=rel, line=invocation.start_point.row + 1, topic=topic,
                            evidence=_source_evidence(source, invocation, rel),
                        ))
        return tuple(sorted(methods, key=lambda item: (item.path, item.line, item.role, item.mechanism)))


JAVA_ARCHITECTURE_EXTENSIONS: tuple[JavaArchitectureExtension, ...] = (KafkaArchitectureExtension(),)


def _extract_java_architecture(
    module_dir: Path, module_roots: set[Path]
) -> tuple[tuple[str, ...], tuple[MongoMethod, ...], tuple[MongoPersistenceClass, ...], tuple[KafkaMethod, ...], tuple[BlockingPoint, ...]]:
    """Extract Mongo facts from Java syntax trees.

    This deliberately uses AST node boundaries rather than line regexes: comments,
    strings and nested expressions cannot masquerade as annotations or calls. Symbol
    resolution remains out of scope, so each extracted method retains its receiver and
    an optional collection only when a literal is statically present.
    """
    _trace("module.architecture.parser.begin", module=module_dir)
    parser = _java_parser("architecture")
    _trace("module.architecture.parser.end", module=module_dir)
    java_files = list(_module_files(module_dir, module_roots, "*.java"))
    production_files: list[tuple[Path, str, bytes]] = []
    collections: set[str] = set()
    entity_collections: dict[str, str] = {}
    persistence_classes: set[MongoPersistenceClass] = set()
    persistence_candidates: dict[str, list[_JavaPersistenceCandidate]] = {}
    repository_entities: dict[str, str] = {}
    for path in java_files:
        rel = _module_relative(module_dir, path)
        segments = rel.split("/")
        if any(
            segment == "src" and index + 1 < len(segments)
            and (segments[index + 1] == "test" or segments[index + 1].endswith("Test"))
            for index, segment in enumerate(segments)
        ):
            continue
        try:
            source = path.read_bytes()
        except OSError:
            continue
        production_files.append((path, rel, source))
        root_node = parser.parse(source).root_node
        package = ""
        for child in root_node.named_children:
            if child.type == "package_declaration":
                package = (
                    _node_text(source, child).strip().rstrip(";")
                    .removeprefix("package").strip()
                )
                break
        for declaration in java_parser.type_declarations(root_node):
            if _has_parse_error(declaration):
                continue
            declaration_name = java_parser.declaration_name(declaration, source)
            if declaration_name is None:
                continue
            fields: list[MongoField] = []
            if declaration.type == "record_declaration":
                parameters = java_parser.child_by_type(declaration, "formal_parameters")
                for parameter in parameters.named_children if parameters is not None else ():
                    if parameter.type != "formal_parameter":
                        continue
                    type_node = parameter.child_by_field_name("type")
                    name_node = parameter.child_by_field_name("name")
                    if type_node is not None and name_node is not None:
                        fields.append(MongoField(
                            _node_text(source, name_node),
                            _node_text(source, type_node).strip(),
                        ))
            for field_node in _walk(declaration):
                if field_node.type != "field_declaration" or java_parser.enclosing(
                    field_node, "class_declaration", "record_declaration", "enum_declaration"
                ) != declaration:
                    continue
                type_node = field_node.child_by_field_name("type")
                if type_node is None:
                    continue
                for declarator in field_node.children:
                    if declarator.type == "variable_declarator":
                        name_node = declarator.child_by_field_name("name")
                        if name_node is not None:
                            fields.append(MongoField(
                                _node_text(source, name_node),
                                _node_text(source, type_node).strip(),
                            ))
            candidate = _JavaPersistenceCandidate(
                name=declaration_name,
                qualified_name=f"{package}.{declaration_name}" if package else declaration_name,
                path=rel,
                line=declaration.start_point.row + 1,
                fields=tuple(fields),
            )
            persistence_candidates.setdefault(declaration_name, []).append(candidate)
            document_annotation = next(
                (
                    annotation
                    for annotation in java_parser.annotations_of(declaration)
                    if java_parser.annotation_name(annotation, source) == "Document"
                ),
                None,
            )
            if document_annotation is not None:
                collection = _first_string_value(
                    java_parser.annotation_argument(document_annotation, source, "collection")
                    or java_parser.annotation_argument(document_annotation, source),
                    source,
                )
                if collection is None:
                    collection = declaration_name[:1].lower() + declaration_name[1:]
                if collection:
                    collections.add(collection)
                    entity_collections[declaration_name] = collection
                    persistence_classes.add(MongoPersistenceClass(
                        collection=collection,
                        **candidate.__dict__,
                    ))
            if declaration.type == "interface_declaration":
                entity = _repository_entity(declaration, source)
                if entity is not None:
                    repository_entities[declaration_name] = entity
    # Interfaces that reach MongoRepository through another interface of the
    # module, in any generic order, resolve through the shared resolver.
    for qualified, repository in resolve_repository_interfaces([
        (rel, source, parser.parse(source).root_node) for _path, rel, source in production_files
    ]).items():
        simple = qualified.rsplit(".", 1)[-1]
        if repository.technology == "mongodb" and repository.entity_text and simple not in repository_entities:
            repository_entities[simple] = repository.entity_text.rsplit(".", 1)[-1]

    for entity in sorted(set(repository_entities.values())):
        candidates = persistence_candidates.get(entity, [])
        if len(candidates) != 1:
            continue
        collection = entity_collections.setdefault(
            entity, entity[:1].lower() + entity[1:]
        )
        collections.add(collection)
        persistence_classes.add(MongoPersistenceClass(
            collection=collection,
            **candidates[0].__dict__,
        ))

    methods: set[MongoMethod] = set()
    kafka_methods = next(
        extension.extract([(rel, source) for _, rel, source in production_files])
        for extension in JAVA_ARCHITECTURE_EXTENSIONS
        if extension.name == "kafka"
    )
    blocking_points: set[BlockingPoint] = set()
    for path, rel, source in production_files:
        _trace("module.architecture.parse.begin", module=module_dir, path=rel)
        tree = parser.parse(source)
        _trace("module.architecture.parse.end", module=module_dir, path=rel)
        _trace("module.architecture.root.begin", module=module_dir, path=rel)
        root_node = tree.root_node
        _trace("module.architecture.root.end", module=module_dir, path=rel, node_type=root_node.type)
        _trace("module.architecture.error_check.begin", module=module_dir, path=rel)
        has_error = root_node.has_error
        _trace("module.architecture.error_check.end", module=module_dir, path=rel, has_error=has_error)
        mongo_imports = {
            _node_text(source, child).removeprefix("import ").removesuffix(";").strip()
            for child in root_node.named_children
            if child.type == "import_declaration"
        }
        repository_receivers: dict[tuple[int, str], str | None] = {}
        template_receivers: set[tuple[int, str]] = set()
        _trace("module.architecture.walk_metadata.begin", module=module_dir, path=rel)
        for node in _walk(root_node):
            if node.type == "field_declaration":
                if _has_parse_error(node):
                    continue
                owner_type = _enclosing_type_start(node)
                if owner_type is None:
                    continue
                type_node = node.child_by_field_name("type")
                if type_node is None:
                    continue
                type_names = _type_identifiers(type_node, source)
                type_name = type_names[0].rsplit(".", 1)[-1] if type_names else ""
                is_mongo_template_type = (
                    type_name in {
                        "MongoTemplate", "MongoOperations",
                        "ReactiveMongoTemplate", "ReactiveMongoOperations",
                    }
                    and (
                        f"org.springframework.data.mongodb.core.{type_name}" in mongo_imports
                        or "org.springframework.data.mongodb.core.*" in mongo_imports
                        or _node_text(source, type_node)
                        == f"org.springframework.data.mongodb.core.{type_name}"
                    )
                )
                if is_mongo_template_type:
                    for declarator in node.children:
                        if declarator.type == "variable_declarator":
                            receiver_node = declarator.child_by_field_name("name")
                            if receiver_node is not None:
                                template_receivers.add((owner_type, _node_text(source, receiver_node)))
                entity = repository_entities.get(type_name) or _repository_entity(type_node, source)
                if entity is None:
                    continue
                for declarator in node.children:
                    if declarator.type != "variable_declarator":
                        continue
                    receiver_node = declarator.child_by_field_name("name")
                    if receiver_node is None:
                        continue
                    receiver = _node_text(source, receiver_node)
                    repository_receivers[(owner_type, receiver)] = (
                        entity_collections.get(entity) if entity is not None else None
                    )
        _trace("module.architecture.walk_metadata.end", module=module_dir, path=rel)
        _trace("module.architecture.walk_methods.begin", module=module_dir, path=rel)
        for method_node in _walk(root_node):
            if method_node.type != "method_declaration":
                continue
            if _has_parse_error(method_node):
                continue
            method_name_node = method_node.child_by_field_name("name")
            if method_name_node is None:
                continue
            method_name = _node_text(source, method_name_node)
            method_text = _node_text(source, method_node)
            for invocation in _walk(method_node):
                if invocation.type != "method_invocation":
                    continue
                call = _invocation_receiver_and_operation(invocation, source)
                if call is None:
                    continue
                receiver, operation = call
                blocking_mechanism: str | None = None
                detail: str | None = None
                if receiver == "Thread" and operation == "sleep":
                    blocking_mechanism, detail = "thread-sleep", "Thread.sleep"
                elif operation == "wait":
                    blocking_mechanism, detail = "object-wait", "Object.wait"
                elif operation == "get" and any(token in receiver.lower() for token in ("future", "result", "promise")):
                    blocking_mechanism, detail = "future-get", "Future.get sans analyse de timeout"
                elif operation == "join" and any(token in receiver.lower() for token in ("thread", "future", "task")):
                    blocking_mechanism, detail = "thread-or-future-join", f"{receiver}.join"
                elif operation in {"lock", "lockInterruptibly"}:
                    blocking_mechanism, detail = "jvm-lock", operation
                elif operation in {"findAndModify", "findOneAndUpdate"} and "lock" in method_text.casefold():
                    blocking_mechanism, detail = "mongo-pessimistic-lock", operation
                if blocking_mechanism is not None and detail is not None:
                    blocking_points.add(BlockingPoint(
                        mechanism=blocking_mechanism, method=method_name, path=rel,
                        line=invocation.start_point.row + 1, detail=detail,
                        evidence=_source_evidence(source, invocation, rel),
                    ))
            for statement in _walk(method_node):
                if statement.type == "synchronized_statement":
                    blocking_points.add(BlockingPoint(
                        mechanism="jvm-synchronized", method=method_name, path=rel,
                        line=statement.start_point.row + 1, detail="synchronized block",
                        evidence=_source_evidence(source, statement, rel),
                    ))
        _trace("module.architecture.walk_methods.end", module=module_dir, path=rel)
        _trace("module.architecture.walk_mongo.begin", module=module_dir, path=rel)
        for node in _walk(root_node):
            if node.type != "method_invocation":
                continue
            if _has_parse_error(node):
                continue
            call = _invocation_receiver_and_operation(node, source)
            if call is None:
                continue
            receiver, operation = call
            receiver_node, _operation, arguments = java_parser.invocation_parts(node, source)
            if receiver_node is None or _node_text(source, receiver_node) not in {
                receiver, f"this.{receiver}",
            }:
                continue
            owner_type = _enclosing_type_start(node)
            if owner_type is None:
                continue
            receiver_key = (owner_type, receiver)
            is_template = receiver_key in template_receivers
            is_repository = receiver_key in repository_receivers
            if (is_template and operation in _MONGO_TEMPLATE_OPERATIONS) or (
                is_repository and operation in _REPOSITORY_OPERATIONS
            ):
                entity = _mongo_entity_class_literal(node, source) if is_template else None
                collection = (
                    repository_receivers.get(receiver_key)
                    if is_repository
                    else _mongo_collection_literal(node, source)
                )
                candidates = persistence_candidates.get(entity or "", [])
                if collection is None and entity is not None:
                    collection = entity_collections.get(entity)
                    if collection is None and len(candidates) == 1:
                        collection = entity[:1].lower() + entity[1:]
                if collection:
                    collections.add(collection)
                    if len(candidates) == 1:
                        persistence_classes.add(MongoPersistenceClass(
                            collection=collection,
                            **candidates[0].__dict__,
                        ))
                methods.add(MongoMethod(
                    operation=operation,
                    receiver=receiver,
                    path=rel,
                    line=node.start_point.row + 1,
                    collection=collection,
                    evidence=_source_evidence(source, node, rel),
                    owner_method=_enclosing_method_name(source, node),
                    key_expression=(
                        java_parser.node_text(source, arguments[0]).strip()
                        if operation in {"findById", "existsById", "deleteById"}
                        and arguments else ""
                    ),
                ))
        _trace("module.architecture.walk_mongo.end", module=module_dir, path=rel)
    _trace("module.architecture.files.end", module=module_dir)
    _trace("module.architecture.sort_collections.begin", module=module_dir, count=len(collections))
    sorted_collections = tuple(sorted(collections))
    _trace("module.architecture.sort_collections.end", module=module_dir)
    _trace("module.architecture.sort_methods.begin", module=module_dir, count=len(methods))
    sorted_methods = tuple(sorted(
        methods, key=lambda item: (item.path, item.line, item.operation, item.key_expression),
    ))
    _trace("module.architecture.sort_methods.end", module=module_dir)
    _trace("module.architecture.sort_blocking.begin", module=module_dir, count=len(blocking_points))
    sorted_blocking_points = tuple(sorted(
        blocking_points, key=lambda item: (item.path, item.line, item.mechanism)
    ))
    _trace("module.architecture.sort_blocking.end", module=module_dir)
    _trace("module.architecture.end", module=module_dir)
    expanded_persistence_classes = _expand_persistence_classes(
        persistence_classes, persistence_candidates
    )
    return (
        sorted_collections,
        sorted_methods,
        expanded_persistence_classes,
        kafka_methods,
        sorted_blocking_points,
    )


def _discover_openapi_files(
    module_dir: Path,
    module_roots: set[Path],
    *,
    rest_controllers: tuple[str, ...] = (),
    build_system: str | None = None,
) -> tuple[str, ...]:
    """Discover valid OpenAPI documents owned by one build module.

    Conventional ``openapi.*`` and ``swagger.*`` file names remain supported
    wherever they are stored. In addition, a module may expose any number of
    independently named contracts under ``src/main/resources/openapi``.
    """
    contracts: set[str] = set()
    for path in _module_files(module_dir, module_roots, "*"):
        relative_path = _module_relative(module_dir, path)
        parts = Path(relative_path).parts
        candidate = (
            path.name.casefold() in _OPENAPI_FILENAMES
            or (
                path.suffix.casefold() in _OPENAPI_DOCUMENT_SUFFIXES
                and parts[:4] == _OPENAPI_RESOURCE_DIRECTORY
            )
        )
        if not candidate:
            continue
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(document, dict) and ("openapi" in document or "swagger" in document):
            contracts.add(relative_path)
    pom_path = module_dir / "pom.xml"
    if build_system == "maven" and rest_controllers and pom_path.is_file():
        from systemlens.discovery.build.maven import detect_openapi_generator_input_specs

        contracts.update(detect_openapi_generator_input_specs(pom_path))
    return tuple(sorted(contracts))


def discover_rest_controllers(module_dir: Path, module_roots: set[Path]) -> tuple[str, ...]:
    """Return controller source files belonging to a module.

    This is deliberately a small public discovery API: both the module
    inventory and endpoint scanner need the same definition of a REST-capable
    module. Keeping it here prevents their source-set filtering from
    diverging.
    """
    """Détecte les classes Java annotées avec @RestController.

    Retourne une tuple de chaînes au format "ClassName (relative/path.java)".
    """
    parser = _java_parser("rest_controllers")
    controller_classes: list[str] = []

    for java_file in _module_files(module_dir, module_roots, "*.java"):
        try:
            source = java_file.read_bytes()
        except OSError:
            continue
        root_node = parser.parse(source).root_node
        if root_node.has_error:
            continue
        rel_path = _module_relative(module_dir, java_file)
        for declaration in java_parser.type_declarations(root_node):
            if declaration.type != "class_declaration":
                continue
            if not any(
                java_parser.annotation_name(annotation, source) == "RestController"
                for annotation in java_parser.annotations_of(declaration)
            ):
                continue
            class_name = java_parser.declaration_name(declaration, source) or "Unknown"
            controller_classes.append(f"{class_name} ({rel_path})")

    return tuple(sorted(set(controller_classes)))


def discover_jpa_entities(
    module_dir: Path, module_roots: set[Path], repo_root: Path
) -> tuple[JpaEntity, ...]:
    """Inventory source-declared JPA entities without inferring table names."""
    parser = _java_parser("jpa_entities")
    entities: set[JpaEntity] = set()
    for java_file in _module_files(module_dir, module_roots, "*.java"):
        source_parts = java_file.relative_to(module_dir).parts
        if source_parts[0] in {"test", "tests"} or any(
            part == "src" and index + 1 < len(source_parts)
            and (source_parts[index + 1] == "test" or source_parts[index + 1].endswith("Test"))
            for index, part in enumerate(source_parts)
        ):
            continue
        try:
            source = java_file.read_bytes()
        except OSError:
            continue
        if not any(token in source for token in (
            b"import jakarta.persistence.Entity;", b"import javax.persistence.Entity;",
            b"import jakarta.persistence.*;", b"import javax.persistence.*;",
            b"@jakarta.persistence.Entity", b"@javax.persistence.Entity",
        )):
            continue
        root = parser.parse(source).root_node
        if root.has_error:
            continue
        imports = {
            java_parser.node_text(source, node).strip()
            for node in root.named_children if node.type == "import_declaration"
        }
        imported_entity = bool(imports & {
            "import jakarta.persistence.Entity;", "import javax.persistence.Entity;",
            "import jakarta.persistence.*;", "import javax.persistence.*;",
        })

        def is_jpa_annotation(annotation) -> bool:
            name_node = annotation.child_by_field_name("name")
            if name_node is None:
                return False
            raw_name = java_parser.node_text(source, name_node)
            return raw_name in {
                "jakarta.persistence.Entity", "javax.persistence.Entity",
            } or (raw_name == "Entity" and imported_entity)

        package = next((
            java_parser.node_text(source, node).removeprefix("package").removesuffix(";").strip()
            for node in root.named_children if node.type == "package_declaration"
        ), "")
        for declaration in java_parser.type_declarations(root):
            if declaration.parent != root or declaration.type != "class_declaration" or not any(
                is_jpa_annotation(annotation)
                for annotation in java_parser.annotations_of(declaration)
            ):
                continue
            name = java_parser.declaration_name(declaration, source)
            if name:
                fields: list[JpaField] = []
                version_field: str | None = None
                for field_node in _walk(declaration):
                    if field_node.type != "field_declaration" or java_parser.enclosing(
                        field_node, "class_declaration", "record_declaration", "enum_declaration"
                    ) != declaration:
                        continue
                    type_node = field_node.child_by_field_name("type")
                    if type_node is None:
                        continue
                    versioned = any(
                        java_parser.annotation_name(annotation, source) == "Version"
                        for annotation in java_parser.annotations_of(field_node)
                    )
                    for declarator in field_node.children:
                        if declarator.type != "variable_declarator":
                            continue
                        name_node = declarator.child_by_field_name("name")
                        if name_node is not None:
                            fields.append(JpaField(
                                java_parser.node_text(source, name_node),
                                java_parser.node_text(source, type_node).strip(),
                            ))
                            if versioned and version_field is None:
                                version_field = java_parser.node_text(source, name_node)
                entities.add(JpaEntity(
                    qualified_name=f"{package}.{name}" if package else name,
                    path=java_file.relative_to(repo_root).as_posix(),
                    line=declaration.start_point.row + 1,
                    fields=tuple(fields),
                    version_field=version_field,
                ))
    return tuple(sorted(entities))


def _production_java_files(module_dir: Path, module_roots: set[Path]) -> list[Path]:
    files: list[Path] = []
    for java_file in _module_files(module_dir, module_roots, "*.java"):
        parts = java_file.relative_to(module_dir).parts
        if parts[0] in {"test", "tests"} or any(
            part == "src" and index + 1 < len(parts)
            and (parts[index + 1] == "test" or parts[index + 1].endswith("Test"))
            for index, part in enumerate(parts)
        ):
            continue
        files.append(java_file)
    return files


def discover_module_data_operations(
    module_dir: Path, module_roots: set[Path], repo_root: Path, module: str,
    entities: tuple[JpaEntity, ...], mongo_methods: tuple[MongoMethod, ...] = (),
) -> tuple[DataOperation, ...]:
    """Typed JPA, EntityManager, JDBC and Mongo operations of one module."""
    parsed = module_java_files(repo_root, _production_java_files(module_dir, module_roots))
    operations = list(discover_data_operations(module, parsed, entities))
    module_prefix = module_dir.resolve().relative_to(repo_root.resolve())
    for method in mongo_methods:
        projected = mongo_data_operation(method, module, (module_prefix / method.path).as_posix())
        if projected is not None:
            operations.append(projected)
    return tuple(sort_data_operations(operations))


def jpa_methods_from_operations(
    operations: tuple[DataOperation, ...], module_dir: Path, repo_root: Path,
) -> tuple[JpaMethod, ...]:
    """Project resolved JPA operations onto the legacy module inventory."""
    module_prefix = module_dir.resolve().relative_to(repo_root.resolve()).as_posix()
    results: set[JpaMethod] = set()
    for operation in operations:
        if operation.technology != "jpa" or operation.action == "unknown" or not operation.resource:
            continue
        if not (operation.resource_identity or "").startswith("jpa:"):
            continue
        path = operation.path
        if module_prefix not in {"", "."} and path.startswith(f"{module_prefix}/"):
            path = path[len(module_prefix) + 1:]
        results.add(JpaMethod(
            operation.operation, operation.resource, operation.receiver or "",
            path, operation.line, operation.owner_method, operation.key_expression or "",
        ))
    return tuple(sorted(results))


def discover_jpa_methods(
    module_dir: Path, module_roots: set[Path], repo_root: Path,
    entities: tuple[JpaEntity, ...],
) -> tuple[JpaMethod, ...]:
    """Resolve CRUD and derived calls through source-declared repositories."""
    operations = discover_module_data_operations(module_dir, module_roots, repo_root, module_dir.name, entities)
    return jpa_methods_from_operations(operations, module_dir, repo_root)


def discover_jpa_dtos(
    module_dir: Path,
    module_roots: set[Path],
    repo_root: Path,
    entities: tuple[JpaEntity, ...],
    rest_controllers: tuple[str, ...],
    openapi_files: tuple[str, ...],
) -> tuple[JpaDto, ...]:
    """Inventory DTOs tied to JPA entities or undocumented REST controllers.

    A DTO is accepted only when its name uses a conventional DTO suffix. An
    entity association requires an imported, same-package, or qualified entity
    type reference. Controller DTOs are collected from method signatures only
    when the module has no OpenAPI contract, preserving the contract boundary.
    """
    parser = _java_parser("jpa_dtos")
    entity_by_simple = {entity.qualified_name.rsplit(".", 1)[-1]: entity for entity in entities}
    candidates: dict[str, tuple[Path, object, bytes, str, set[str]]] = {}
    dto_suffixes = ("DTO", "Dto", "Request", "Response", "Command", "Query", "Payload")
    controller_names = {item.split(" (", 1)[0] for item in rest_controllers}
    for java_file in _module_files(module_dir, module_roots, "*.java"):
        relative_parts = java_file.relative_to(module_dir).parts
        if any(part in {"test", "tests"} for part in relative_parts):
            continue
        try:
            source = java_file.read_bytes()
        except OSError:
            continue
        root = parser.parse(source).root_node
        if root.has_error:
            continue
        package = next((
            java_parser.node_text(source, node).removeprefix("package").removesuffix(";").strip()
            for node in root.named_children if node.type == "package_declaration"
        ), "")
        imports = {
            java_parser.node_text(source, node).strip().removeprefix("import ").removesuffix(";")
            for node in root.named_children if node.type == "import_declaration"
        }
        for declaration in java_parser.type_declarations(root):
            if declaration.parent != root:
                continue
            name = java_parser.declaration_name(declaration, source)
            if not name or not name.endswith(dto_suffixes):
                continue
            qualified = f"{package}.{name}" if package else name
            candidates[qualified] = (java_file, declaration, source, package, imports)

    def referenced_entity_names(source: bytes, package: str, imports: set[str]) -> tuple[str, ...]:
        text = source.decode("utf-8", errors="replace")
        matches: list[str] = []
        for simple, entity in entity_by_simple.items():
            qualified = entity.qualified_name
            visible = (
                qualified in imports
                or f"{package}.{simple}" == qualified
                or qualified in text
            )
            if visible and re.search(rf"\b{re.escape(simple)}\b", text):
                matches.append(qualified)
        return tuple(sorted(matches))

    def referenced_dtos(type_text: str) -> set[str]:
        return {
            qualified
            for qualified in candidates
            if re.search(rf"\b{re.escape(qualified.rsplit('.', 1)[-1])}\b", type_text)
        }

    results: dict[str, set[str]] = {qualified: set() for qualified in candidates}
    entity_links: dict[str, set[str]] = {qualified: set() for qualified in candidates}
    for qualified, (_path, declaration, source, package, imports) in candidates.items():
        linked_entities = referenced_entity_names(source, package, imports)
        entity_links[qualified].update(linked_entities)
        if linked_entities:
            results[qualified].add("jpa_entity")

    if not openapi_files and controller_names:
        for java_file in _module_files(module_dir, module_roots, "*.java"):
            try:
                source = java_file.read_bytes()
            except OSError:
                continue
            root = parser.parse(source).root_node
            if root.has_error:
                continue
            for declaration in java_parser.type_declarations(root):
                name = java_parser.declaration_name(declaration, source)
                if name not in controller_names:
                    continue
                if not any(java_parser.annotation_name(annotation, source) == "RestController"
                           for annotation in java_parser.annotations_of(declaration)):
                    continue
                for method in java_parser.walk(declaration):
                    if method.type != "method_declaration":
                        continue
                    type_node = method.child_by_field_name("type")
                    signature_parts = [java_parser.node_text(source, type_node)] if type_node else []
                    parameters = java_parser.child_by_type(method, "formal_parameters")
                    if parameters:
                        signature_parts.extend(
                            java_parser.node_text(source, parameter)
                            for parameter in parameters.named_children
                        )
                    for dto in set().union(*(referenced_dtos(part) for part in signature_parts)):
                        results[dto].add("rest_controller")

    return tuple(
        JpaDto(
            qualified_name=qualified,
            path=candidates[qualified][0].relative_to(repo_root).as_posix(),
            line=declaration.start_point.row + 1,
            roles=tuple(sorted(roles)),
            entities=tuple(sorted(entity_links[qualified])),
        )
        for qualified, roles in sorted(results.items())
        if roles
    )


def _enrich_module(
    module: DiscoveredModule,
    module_roots: set[Path],
    repo_root: Path,
    *,
    enrich_architecture: bool = True,
) -> DiscoveredModule:
    _trace("module.enrich.begin", module=module.path)
    if enrich_architecture:
        collections, methods, persistence_classes, kafka_methods, blocking_points = _extract_java_architecture(module.path, module_roots)
    else:
        _trace("module.enrich.architecture.disabled", module=module.path)
        collections = ()
        methods = ()
        persistence_classes = ()
        kafka_methods = ()
        blocking_points = ()

    # Détecter les contrôleurs REST
    rest_controllers = discover_rest_controllers(module.path, module_roots)
    jpa_entities = (
        discover_jpa_entities(module.path, module_roots, repo_root)
        if enrich_architecture else ()
    )
    data_operations = (
        discover_module_data_operations(
            module.path, module_roots, repo_root, module_identity(module), jpa_entities, methods,
        )
        if enrich_architecture else ()
    )
    jpa_methods = jpa_methods_from_operations(data_operations, module.path, repo_root)

    openapi_files = _discover_openapi_files(
        module.path,
        module_roots,
        rest_controllers=rest_controllers,
        build_system=module.build_system,
    )
    jpa_dtos = (
        discover_jpa_dtos(
            module.path, module_roots, repo_root, jpa_entities,
            rest_controllers, openapi_files,
        )
        if enrich_architecture else ()
    )

    # Détecter les clients OpenAPI générés (Maven uniquement)
    openapi_generated_clients: tuple[str, ...] = ()
    if module.build_system == "maven":
        from systemlens.discovery.build.maven import detect_openapi_generated_clients
        pom_path = module.path / "pom.xml"
        if pom_path.exists():
            openapi_generated_clients = detect_openapi_generated_clients(pom_path)

    enriched = DiscoveredModule(
        **{**module.__dict__, "mongo_collections": collections, "mongo_methods": methods,
           "mongo_persistence_classes": persistence_classes,
           "jpa_entities": jpa_entities,
           "jpa_methods": jpa_methods,
           "data_operations": data_operations,
           "jpa_dtos": jpa_dtos,
           "openapi_files": openapi_files,
           "kafka_methods": kafka_methods, "blocking_points": blocking_points,
           "rest_controllers": rest_controllers, "openapi_generated_clients": openapi_generated_clients}
    )
    _trace("module.enrich.end", module=module.path)
    return enriched


def deduplicate_openapi_contract_owners(
    modules: list[DiscoveredModule],
) -> list[DiscoveredModule]:
    """Keep each contract on the module that directly contains its file.

    A module can refer to a contract stored higher in a workspace, but the
    contract's source evidence belongs to its direct enclosing build project.
    This prevents an export from listing the same OpenAPI/Swagger file once
    for the owning project and once for a referencing nested module.
    """
    module_paths = {
        module.path.resolve(): module
        for module in modules
    }
    deduplicated: list[DiscoveredModule] = []
    for module in modules:
        owned_files: list[str] = []
        for contract in module.openapi_files:
            contract_path = (module.path / contract).resolve()
            enclosing_projects = [
                project_path
                for project_path in module_paths
                if project_path == contract_path or project_path in contract_path.parents
            ]
            owner_path = max(enclosing_projects, key=lambda path: len(path.parts), default=None)
            if owner_path is not None and owner_path != module.path.resolve():
                continue
            owned_files.append(contract)
        deduplicated.append(
            DiscoveredModule(**{**module.__dict__, "openapi_files": tuple(owned_files)})
        )
    return sorted(deduplicated, key=lambda module: str(module.path))


def discover_modules(
    root: Path,
    *,
    enrich_architecture: bool = True,
    use_tree_sitter: bool = True,
) -> list[DiscoveredModule]:
    """Discover build modules, including libraries and aggregators.

    The build layout is rediscovered here and becomes the layout that file
    ownership reads for the rest of the indexing pass (ADR-80).
    """
    root = root.resolve()
    layout = refresh_build_layout(root)
    modules: list[DiscoveredModule] = []
    # Every build directory is known before entry points are read, so a
    # parent never inherits the Spring Boot ``main`` of a nested module.
    all_module_roots = set(layout.build_directories)
    for project in layout.projects:
        module_dir = project.path
        if project.build_system == "maven":
            pom_path = module_dir / "pom.xml"
            _trace("module.maven.begin", pom=pom_path)
            _artifact_id, _, packaging = parse_pom(pom_path)
            _trace("module.maven.parsed", pom=pom_path, artifact=project.name, packaging=packaging)
            if use_tree_sitter:
                entrypoint = _starts_application(module_dir, all_module_roots)
            else:
                _trace("module.maven.entrypoint.disabled", pom=pom_path)
                entrypoint = None
            _trace("module.maven.entrypoint.end", pom=pom_path, found=entrypoint is not None)
            modules.append(
                DiscoveredModule(
                    name=project.name,
                    path=module_dir,
                    build_system="maven",
                    version=pom_version(pom_path),
                    kind="aggregator" if packaging == "pom" else "library",
                    starts_application=packaging != "pom" and entrypoint is not None,
                    configuration_example=service_configuration_example(module_dir),
                    application_entrypoint=entrypoint,
                    identity=project.identity,
                )
            )
            continue
        has_build_file = any(
            (module_dir / filename).is_file() for filename in ("build.gradle", "build.gradle.kts")
        )
        if use_tree_sitter:
            entrypoint = _starts_application(module_dir, all_module_roots)
        else:
            _trace("module.gradle.entrypoint.disabled", module=module_dir)
            entrypoint = None
        modules.append(
            DiscoveredModule(
                name=project.name,
                path=module_dir,
                build_system="gradle",
                version=gradle_version(module_dir),
                kind=(
                    "library"
                    if has_build_file or (module_dir / "src").is_dir()
                    else "aggregator"
                ),
                starts_application=entrypoint is not None,
                configuration_example=service_configuration_example(module_dir),
                application_entrypoint=entrypoint,
                identity=project.identity,
            )
        )
    # Excluded build directories bound enrichment too: their sources belong
    # to no module.
    module_roots = {module.path for module in modules} | set(layout.excluded_paths)
    _trace("modules.enrich.begin", count=len(modules))
    enriched = sorted(
        (
            _enrich_module(
                module,
                module_roots,
                root,
                enrich_architecture=enrich_architecture and use_tree_sitter,
            )
            for module in modules
        ),
        key=lambda module: str(module.path),
    )
    enriched = deduplicate_openapi_contract_owners(enriched)
    _trace("modules.enrich.end", count=len(enriched))
    return enriched


def discover_module_dependencies(root: Path, modules: list[DiscoveredModule]) -> list[ModuleDependency]:
    """Return the build dependencies whose two ends are local modules.

    A Maven dependency matches a local module by ``artifactId``, and by
    ``groupId`` when the local module declares or inherits one: an external
    ``io.github.resilience4j:core`` is not the local module ``core``. A
    Gradle ``project(':...')`` dependency matches the project path declared
    by ``settings.gradle``, then the directory path, then the project name.
    Test-only dependencies (Maven ``test`` scope; Gradle ``test*``,
    ``androidTest*``, ``testFixtures*`` and other test source-set
    configurations) are not runtime dependencies and give no edge.
    ``provided``, ``runtime``, ``compileOnly`` and ``runtimeOnly`` stay
    edges: they are build or runtime dependencies (ADR-80). External
    dependencies are never added to the graph.
    """
    root = root.resolve()
    layout = cached_build_layout(root)
    candidates_by_name: dict[str, list[DiscoveredModule]] = {}
    for module in modules:
        candidates_by_name.setdefault(module.name, []).append(module)
    group_ids = {
        module.path.resolve(): maven_group_id(module.path / "pom.xml")
        for module in modules
        if module.build_system == "maven"
    }
    declared_gradle_paths = _declared_gradle_projects(layout)
    directory_gradle_paths = {
        ":" + module.path.resolve().relative_to(root).as_posix().replace("/", ":"): module_identity(module)
        for module in modules
        if module.build_system == "gradle" and module.path.resolve() != root
    }
    identities = {module_identity(module) for module in modules}
    gradle_builds = {project.path: project.gradle_build for project in layout.projects}
    dependencies: set[ModuleDependency] = set()
    for module in modules:
        source = module_identity(module)
        resolved_targets: set[str] = set()
        if module.build_system == "maven":
            for dependency in maven_module_dependencies(module.path / "pom.xml"):
                matches = [
                    candidate for candidate in candidates_by_name.get(dependency.artifact_id, [])
                    if _group_matches(dependency, group_ids.get(candidate.path.resolve()))
                ]
                if len(matches) == 1:
                    resolved_targets.add(module_identity(matches[0]))
        else:
            build = gradle_builds.get(module.path.resolve())
            for target in _gradle_module_dependencies(module.path):
                resolved = (
                    declared_gradle_paths.get((build, target)) if build is not None else None
                ) or directory_gradle_paths.get(target)
                if resolved is None:
                    name_matches = candidates_by_name.get(target.rsplit(":", 1)[-1], [])
                    resolved = module_identity(name_matches[0]) if len(name_matches) == 1 else None
                if resolved is not None:
                    resolved_targets.add(resolved)
        for target in resolved_targets:
            if target != source and target in identities:
                dependencies.add(ModuleDependency(source=source, target=target))
    return sorted(dependencies)


def _declared_gradle_projects(layout: BuildLayout) -> dict[tuple[Path, str], str]:
    return {
        (project.gradle_build, project.gradle_path): project.identity
        for project in layout.projects
        if project.gradle_build is not None and project.gradle_path is not None
    }


def _group_matches(dependency: MavenDependency, module_group_id: str | None) -> bool:
    """Whether a dependency's groupId can name a local module.

    A module without a known groupId (a Gradle project, a POM without one)
    matches on the artifactId alone, as does a dependency without a groupId.
    An unresolved ``${...}`` groupId never matches a known one.
    """
    if module_group_id is None or dependency.group_id is None:
        return True
    return dependency.group_id == module_group_id


_TEST_SCOPES = frozenset({"test"})


def maven_module_dependencies(pom_path: Path) -> set[MavenDependency]:
    """Return the direct, non-test Maven dependencies declared by ``pom_path``."""
    return {
        dependency for dependency in maven_dependencies(pom_path)
        if (dependency.scope or "compile").strip().casefold() not in _TEST_SCOPES
    }


_GRADLE_PROJECT_DEPENDENCY_RE = re.compile(
    r"\b([A-Za-z]\w*)\s*\(?\s*"
    r"project\s*\(\s*(?:path\s*[:=]\s*)?['\"](:[^'\"]+)['\"]"
)
_GRADLE_DEPENDENCY_CONFIGURATION_RE = re.compile(
    r"(?:[a-z]\w*?)?(?:api|Api|implementation|Implementation|compileOnly|CompileOnly"
    r"|runtimeOnly|RuntimeOnly|compileOnlyApi|CompileOnlyApi|compile|Compile|runtime|Runtime)"
)


def _is_test_configuration(configuration: str) -> bool:
    """Whether a Gradle configuration only feeds a test source set."""
    if configuration.startswith(("test", "androidTest", "testFixtures")):
        return True
    source_set = re.sub(
        r"(Api|Implementation|CompileOnly|RuntimeOnly|CompileOnlyApi|Compile|Runtime)$",
        "",
        configuration,
    )
    return source_set != configuration and source_set.endswith("Test")


def _gradle_module_dependencies(module_dir: Path) -> set[str]:
    """Return the project paths of the non-test ``project(...)`` dependencies."""
    targets: set[str] = set()
    for filename in ("build.gradle", "build.gradle.kts"):
        path = module_dir / filename
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for configuration, target in _GRADLE_PROJECT_DEPENDENCY_RE.findall(text):
            if _GRADLE_DEPENDENCY_CONFIGURATION_RE.fullmatch(configuration) is None:
                continue
            if _is_test_configuration(configuration):
                continue
            targets.add(target)
    return targets
