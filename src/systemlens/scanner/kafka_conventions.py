"""Inférence Kafka par convention de nommage et manifestes (BACKLOG-16).

Regroupe la convention Strategy1 (nom de méthode `send*`, clé Kafka
constante) et les inférences basées sur des manifestes déclaratifs
(tableau Markdown de topics, graphe de flux JSON) qui complètent la
détection AST de `kafka_ast.py` quand le code source n'est pas
disponible/analysable."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

from systemlens.discovery.java import parser as java_parser
from systemlens.domain.models import MessageEndpoint, compute_endpoint_id
from systemlens.scanner.kafka_ast import (
    _listener_payload_type,
    _producer_record_payload_type,
    _producer_send_payload_type,
    _kafka_endpoint,
    _kafka_topic_from_value,
    _type_simple_name,
)
from systemlens.scanner.kafka_payload_types import payload_expression_type
from systemlens.scanner._spring_properties import (
    normalize_strategy1_kafka_key,
    remove_strategy1_kafka_prefix,
    strategy1_kafka_topics,
)
from systemlens.domain.topic_expressions import spring_topic_reference

_STRATEGY1_PRODUCER_RE = re.compile(r"\bgetTopics\s*\(\s*\)\s*\.\s*get([A-Z]\w*)\s*\(\s*\)")
# Spring relaxed binding writes keys in kebab case (``commande-creee``);
# the logical key is normalized like a ``kafka.yml`` key afterwards.
_STRATEGY1_KAFKA_KEY_RE = re.compile(
    r"\$\{\s*((?:[A-Za-z_][\w-]*\.)*(?:kafka\.)?topics\.[A-Za-z_][\w-]*\.nom)(?:\s*:[^}]*)?\s*\}"
)
_STRATEGY1_SEND_METHOD_PREFIX = "envoyerMessageKafka"
def _validate_strategy1_topic(
    topic: str,
    catalog: dict[str, str] | None,
    logical_key: str | None = None,
) -> tuple[str, bool]:
    """Resolve a Strategy1 topic by exact key or exact declared value."""
    if catalog is None:
        return "<dynamic>", True
    if logical_key is not None:
        declared = catalog.get(normalize_strategy1_kafka_key(logical_key))
        if declared is not None:
            return declared, False
    declared = catalog.get(normalize_strategy1_kafka_key(topic))
    if declared is not None:
        return declared, False
    if topic in catalog.values():
        return topic, False
    return "<dynamic>", True


def _strategy1_topic_from_value(
    value_node, source: bytes, repo_root: Path, rel_path: str,
    catalog: dict[str, str] | None,
) -> tuple[str, bool, str | None]:
    """Resolve a Strategy1 `getTopics().getXxx()` argument before fallback."""
    if value_node is not None:
        value = java_parser.node_text(source, value_node)
        if match := _STRATEGY1_PRODUCER_RE.search(value):
            logical_key = match.group(1)
            resolved, dynamic = _validate_strategy1_topic(logical_key, catalog, logical_key)
            return resolved, dynamic, resolved if not dynamic and catalog is not None else logical_key
    topic, dynamic = _kafka_topic_from_value(value_node, source, repo_root, rel_path)
    if dynamic and "${" in topic:
        # A declared value may keep the ``${kafka.prefix-topic}.`` environment
        # prefix; without any other placeholder it is a static topic.
        without_prefix = remove_strategy1_kafka_prefix(topic)
        if "${" not in without_prefix:
            topic, dynamic = without_prefix, False
    if dynamic and value_node is not None and value_node.type == "string_literal":
        literal = java_parser.string_value(value_node, source)
        reference = spring_topic_reference(literal or "")
        if reference is not None and catalog is not None:
            parts = reference.property_key.split(".")
            logical_key = (
                parts[-2]
                if parts and parts[-1].casefold() == "nom" and len(parts) > 1
                else parts[-1]
            )
            declared = catalog.get(normalize_strategy1_kafka_key(logical_key))
            if declared is not None:
                return declared, False, declared
    if not dynamic:
        topic = remove_strategy1_kafka_prefix(topic)
        topic, dynamic = _validate_strategy1_topic(topic, catalog)
    if dynamic and value_node is not None:
        expression = java_parser.node_text(source, value_node)
        enum_constant = expression.rsplit(".", 1)[-1]
        if re.fullmatch(r"(?:FLUX[_A-Z0-9]*_)?[A-Z][A-Z0-9_]*", enum_constant):
            if enum_constant not in {"TOPIC", "NULL", "EMPTY"}:
                resolved, dynamic = _validate_strategy1_topic(enum_constant, catalog, enum_constant)
                return resolved, dynamic, enum_constant
    if not dynamic:
        topic_display = topic
    else:
        topic_display = None
    return topic, dynamic, topic_display


def _strategy1_topic_expression_nodes(value_node, source: bytes, invocation, seen= frozenset()):
    """Return all source expressions that can provide one send topic."""
    if value_node is None:
        return []
    if value_node.type == "parenthesized_expression" and value_node.named_children:
        return _strategy1_topic_expression_nodes(value_node.named_children[0], source, invocation, seen)
    if value_node.type == "ternary_expression":
        return [
            expression
            for field in ("consequence", "alternative")
            for expression in _strategy1_topic_expression_nodes(
                value_node.child_by_field_name(field), source, invocation, seen
            )
        ]
    if value_node.type == "switch_expression":
        switch_block = next(
            (child for child in value_node.named_children if child.type == "switch_block"), None
        )
        expressions = []
        for rule in switch_block.named_children if switch_block is not None else ():
            for child in rule.named_children:
                if child.type in {"switch_label", "block"}:
                    continue
                expression = child.named_children[0] if child.type == "expression_statement" and child.named_children else child
                expressions.extend(_strategy1_topic_expression_nodes(expression, source, invocation, seen))
        return expressions
    if value_node.type != "identifier" or invocation is None:
        return [value_node]
    variable_name = java_parser.node_text(source, value_node)
    if variable_name in seen:
        return [value_node]
    method = java_parser.enclosing(invocation, "method_declaration")
    body = method.child_by_field_name("body") if method is not None else None
    if body is None:
        return [value_node]

    def unique(nodes):
        result = []
        keys = set()
        for node in nodes:
            key = (node.type, node.start_byte, node.end_byte)
            if key not in keys:
                keys.add(key)
                result.append(node)
        return result

    def value_from_assignment(node):
        if node.type == "expression_statement" and node.named_children:
            node = node.named_children[0]
        if node.type == "variable_declarator":
            name = node.child_by_field_name("name")
            value = node.child_by_field_name("value")
        elif node.type == "assignment_expression":
            name = node.child_by_field_name("left")
            value = node.child_by_field_name("right")
            operator = node.child_by_field_name("operator")
            if operator is None or java_parser.node_text(source, operator) != "=":
                return []
        else:
            return []
        if name is None or java_parser.node_text(source, name) != variable_name or value is None:
            return []
        return _strategy1_topic_expression_nodes(value, source, invocation, seen | {variable_name})

    def contains(node, target):
        return node.start_byte <= target.start_byte and target.end_byte <= node.end_byte

    def branch(node, states):
        if node is None:
            return states, False
        if node.type == "block":
            return sequence(list(node.named_children), states)
        return statement(node, states)

    def sequence(statements, states):
        current = states
        for node in statements:
            current, reached = statement(node, current)
            if reached:
                return current, True
        return current, False

    def statement(node, states):
        if contains(node, invocation):
            if node.type == "if_statement":
                branches = []
                for candidate in (
                    node.child_by_field_name("consequence"),
                    node.child_by_field_name("alternative"),
                ):
                    values, reached = branch(candidate, states)
                    if reached:
                        branches.extend(values)
                return unique(branches), True
            if node.type in {"switch_statement", "switch_expression"}:
                switch_block = next(
                    (child for child in node.named_children if child.type == "switch_block"), None
                )
                branches = []
                for group in switch_block.named_children if switch_block is not None else ():
                    values, reached = sequence(list(group.named_children), states)
                    if reached:
                        branches.extend(values)
                return unique(branches), True
            return states, True
        if node.type == "local_variable_declaration":
            values = [value for child in node.named_children for value in value_from_assignment(child)]
            return unique(values) or states, False
        if node.type == "expression_statement":
            values = value_from_assignment(node)
            return unique(values) or states, False
        if node.type == "if_statement":
            branches = []
            for candidate in (
                node.child_by_field_name("consequence"),
                node.child_by_field_name("alternative"),
            ):
                values, reached = branch(candidate, states)
                if reached:
                    return values, True
                branches.extend(values)
            if node.child_by_field_name("alternative") is None:
                branches.extend(states)
            return unique(branches), False
        if node.type in {"switch_statement", "switch_expression"}:
            switch_block = next(
                (child for child in node.named_children if child.type == "switch_block"), None
            )
            branches = []
            for group in switch_block.named_children if switch_block is not None else ():
                values, reached = sequence(list(group.named_children), states)
                if reached:
                    return values, True
                branches.extend(values)
            return unique(branches) or states, False
        if node.type == "block":
            return sequence(list(node.named_children), states)
        return states, False

    states, reached = sequence(list(body.named_children), [value_node])
    if not reached:
        return [value_node]
    return unique(
        expression
        for state in states
        for expression in _strategy1_topic_expression_nodes(
            state, source, invocation, seen | {variable_name}
        )
    ) or [value_node]


def _strategy1_topic_values(
    value_node, source: bytes, repo_root: Path, rel_path: str,
    catalog: dict[str, str] | None, invocation=None,
) -> list[tuple[str, bool, str | None]]:
    """Resolve every statically possible branch of a Strategy1 topic expression."""
    expressions = _strategy1_topic_expression_nodes(value_node, source, invocation)
    if not expressions:
        expressions = [value_node]
    return list(dict.fromkeys(
        _strategy1_topic_from_value(expression, source, repo_root, rel_path, catalog)
        for expression in expressions
    ))


def _strategy1_accessor_key(node, source: bytes) -> str | None:
    """``Xxx`` when ``node`` is exactly ``<receiver>.getTopics().getXxx()``."""
    if node is None:
        return None
    if node.type == "parenthesized_expression" and node.named_children:
        return _strategy1_accessor_key(node.named_children[0], source)
    if node.type != "method_invocation":
        return None
    receiver, name, arguments = java_parser.invocation_parts(node, source)
    if arguments or name is None or receiver is None or receiver.type != "method_invocation":
        return None
    match = re.fullmatch(r"get([A-Z]\w*)", name)
    _inner_receiver, inner_name, inner_arguments = java_parser.invocation_parts(receiver, source)
    if match is None or inner_name != "getTopics" or inner_arguments:
        return None
    return match.group(1)


def _message_topic_headers(message, source: bytes, send_invocation) -> list:
    """Topic values of ``setHeader(KafkaHeaders.TOPIC, value)`` for ``send(message)``.

    The message is built inline or by a local variable of the enclosing
    method; the header value then goes through the Strategy1 branch resolver.
    """
    builders = [message]
    if message.type == "identifier":
        name = java_parser.node_text(source, message)
        method = java_parser.enclosing(send_invocation, "method_declaration")
        builders = [
            value
            for declarator in (java_parser.walk(method) if method is not None else ())
            if declarator.type == "variable_declarator"
            and (ident := declarator.child_by_field_name("name")) is not None
            and java_parser.node_text(source, ident) == name
            and (value := declarator.child_by_field_name("value")) is not None
        ]
    topics = []
    for builder in builders:
        for invocation in java_parser.walk(builder):
            if invocation.type != "method_invocation":
                continue
            _receiver, name, arguments = java_parser.invocation_parts(invocation, source)
            if name != "setHeader" or len(arguments) < 2:
                continue
            header = java_parser.node_text(source, arguments[0])
            if header == "TOPIC" or header.endswith(".TOPIC"):
                topics.extend(_strategy1_topic_expression_nodes(arguments[1], source, send_invocation))
    return topics


def _strategy1_method_payload_type(
    repo_root: Path, rel_path: str, source: bytes, invocation,
) -> str | None:
    """Resolve the DTO from the second argument of a Strategy1 send method.

    The repository convention is positional: the second argument is the DTO
    for ``envoyerMessageKafka``, ``envoyerMessageKafkaRequest`` and
    ``envoyerMessageKafkaReply``. A third argument does not change that rule.
    The argument is typed by the rule shared with ``KafkaTemplate.send``
    (ADR-76).
    """
    arguments = java_parser.argument_nodes(invocation)
    if len(arguments) < 2:
        return None
    return payload_expression_type(repo_root, rel_path, source, arguments[1])


def infer_kafka_topic_strategy1_endpoints(
    repo_root: Path, files: list[str] | None = None
) -> list[MessageEndpoint]:
    """Infer logical Kafka topics from project conventions selected by strategy1.

    Producers use `getTopics().getXxx()` or an `envoyerMessageKafka` method
    family call (`envoyerMessageKafka(topic, payload)`,
    `envoyerMessageKafkaRequest(...)`, `envoyerMessageKafkaReply(...)`, etc.).
    Listeners use a Spring key shaped as `kafka.topics.xxx.<property>`.
    Accessor and property keys must match the exact keys declared in
    `kafka.yml` or `kafka.yaml`. Declared topic values may remove the
    `${kafka.prefix-topic}.` environment prefix.
    """
    if files is None:
        candidate_files = [
            path.relative_to(repo_root).as_posix()
            for path in repo_root.rglob("*.java")
            if path.is_file() and not {"target", "build"}.intersection(path.relative_to(repo_root).parts)
        ]
    else:
        candidate_files = sorted(path for path in files if path.endswith(".java"))

    endpoints: dict[str, MessageEndpoint] = {}
    kafka_catalog = strategy1_kafka_topics(repo_root)
    for rel_path in candidate_files:
        parsed = java_parser.parse_java(str(repo_root), rel_path)
        if parsed is None:
            continue
        source_bytes, root = parsed
        strategy_send_nodes = [
            candidate
            for candidate in java_parser.walk(root)
            if candidate.type == "method_invocation"
            and (candidate_name := java_parser.invocation_parts(candidate, source_bytes)[1]) is not None
            and candidate_name.startswith(_STRATEGY1_SEND_METHOD_PREFIX)
            and len(java_parser.argument_nodes(candidate)) >= 2
        ]
        # A producer is a send call whose topic argument is the accessor
        # expression; a bean, a log line or a comment that mentions the
        # accessor publishes nothing (K1).
        for node in java_parser.walk(root):
            payload_type: str | None
            if node.type == "method_invocation":
                _object_node, name, args = java_parser.invocation_parts(node, source_bytes)
                if name != "send" or not args:
                    continue
                topic_expressions = (
                    _strategy1_topic_expression_nodes(args[0], source_bytes, node)
                    if len(args) >= 2
                    else _message_topic_headers(args[0], source_bytes, node)
                )
                payload_type = _producer_send_payload_type(repo_root, rel_path, source_bytes, node)
            elif (
                node.type == "object_creation_expression"
                and _type_simple_name(source_bytes, node.child_by_field_name("type")) == "ProducerRecord"
            ):
                args = java_parser.argument_nodes(node)
                if len(args) < 2:
                    continue
                topic_expressions = _strategy1_topic_expression_nodes(args[0], source_bytes, node)
                payload_type = _producer_record_payload_type(repo_root, rel_path, source_bytes, node)
            else:
                continue
            logical_keys = dict.fromkeys(
                key
                for expression in topic_expressions
                if (key := _strategy1_accessor_key(expression, source_bytes)) is not None
            )
            for logical_key in logical_keys:
                topic, topic_dynamic = _validate_strategy1_topic(logical_key, kafka_catalog, logical_key)
                endpoint = _kafka_endpoint(
                    repo_root, rel_path, source_bytes, node, "produce", "kafka-topic-strategy1",
                    topic, topic_dynamic, payload_type,
                )
                endpoint = replace(endpoint, topic_display=topic if topic_dynamic else logical_key)
                endpoints[endpoint.id] = endpoint
        # Listeners come from the AST, so a commented-out annotation is not
        # a consumer, and the payload is the listener parameter (K2).
        for declaration in java_parser.walk(root):
            if declaration.type == "method_declaration":
                listener_payload = _listener_payload_type(source_bytes, declaration)
            elif declaration.type == "class_declaration":
                handlers = [
                    member for member in java_parser.walk(declaration)
                    if member.type == "method_declaration"
                    and java_parser.enclosing(member, "class_declaration") is declaration
                    and any(java_parser.annotation_name(ann, source_bytes) == "KafkaHandler"
                            for ann in java_parser.annotations_of(member))
                ]
                listener_payload = (
                    _listener_payload_type(source_bytes, handlers[0]) if len(handlers) == 1 else None
                )
            else:
                continue
            for annotation_node in java_parser.annotations_of(declaration):
                if java_parser.annotation_name(annotation_node, source_bytes) != "KafkaListener":
                    continue
                topics_node = java_parser.annotation_argument(annotation_node, source_bytes, key="topics")
                if topics_node is None:
                    continue
                topics_value = "\n".join(
                    java_parser.node_text(source_bytes, literal)
                    for literal in java_parser.walk(topics_node)
                    if literal.type == "string_literal"
                )
                for key_match in _STRATEGY1_KAFKA_KEY_RE.finditer(topics_value):
                    property_key = key_match.group(1)
                    property_parts = property_key.split(".")
                    logical_key = (
                        property_parts[-2]
                        if property_parts[-1].casefold() == "nom" and len(property_parts) > 1
                        else property_parts[-1]
                    )
                    resolved = (
                        kafka_catalog.get(normalize_strategy1_kafka_key(logical_key))
                        if kafka_catalog is not None
                        else None
                    )
                    endpoint = _kafka_endpoint(
                        repo_root, rel_path, source_bytes, annotation_node, "consume",
                        "kafka-topic-strategy1", resolved or "<dynamic>", resolved is None,
                        listener_payload,
                    )
                    endpoint = replace(endpoint, topic_display=resolved or logical_key)
                    endpoints[endpoint.id] = endpoint
        for node in strategy_send_nodes:
            _object_node, method_name, args = java_parser.invocation_parts(node, source_bytes)
            if (
                method_name is None
                or not method_name.startswith(_STRATEGY1_SEND_METHOD_PREFIX)
                or len(args) < 2
            ):
                continue
            for topic, dynamic, topic_display in _strategy1_topic_values(
                args[0], source_bytes, repo_root, rel_path, kafka_catalog, node
            ):
                endpoint = _kafka_endpoint(
                    repo_root,
                    rel_path,
                    source_bytes,
                    node,
                    "produce",
                    "kafka-topic-strategy1",
                    topic,
                    dynamic,
                    _strategy1_method_payload_type(repo_root, rel_path, source_bytes, node),
                )
                endpoint = replace(endpoint, topic_display=topic_display)
                endpoints[endpoint.id] = endpoint
    return list(endpoints.values())
def apply_kafka_topic_strategy1(
    endpoints: list[MessageEndpoint], strategy_endpoints: list[MessageEndpoint],
    repo_root: Path | None = None,
) -> list[MessageEndpoint]:
    """Replace standard Kafka extraction without discarding known payload types."""
    catalog = strategy1_kafka_topics(repo_root) if repo_root is not None else None
    validated_endpoints = []
    for endpoint in endpoints:
        if catalog is None or endpoint.system != "kafka" or endpoint.role != "produce":
            validated_endpoints.append(endpoint)
            continue
        # A literal topic is validated by its full key or declared value
        # only; its last segment is not a logical key.
        topic, dynamic = _validate_strategy1_topic(endpoint.topic, catalog)
        validated_endpoints.append(
            replace(
                endpoint,
                topic=topic,
                topic_dynamic=dynamic,
                topic_display=endpoint.topic_display or endpoint.topic,
            )
        )
    normalized_strategy_endpoints = [
        replace(
            endpoint,
            topic=(
                endpoint.topic
                if "." in endpoint.topic
                else endpoint.topic
            ),
            topic_display=endpoint.topic_display or endpoint.topic,
        )
        if endpoint.system == "kafka" and not endpoint.topic_dynamic
        else endpoint
        for endpoint in strategy_endpoints
    ]
    generic_by_site: dict[tuple[str, str, int], list[MessageEndpoint]] = {}
    for endpoint in validated_endpoints:
        if endpoint.system == "kafka":
            generic_by_site.setdefault(
                (endpoint.role, endpoint.path, endpoint.start_line), []
            ).append(endpoint)
    covered_sites = {
        (endpoint.role, endpoint.path, endpoint.start_line)
        for endpoint in normalized_strategy_endpoints
    }
    retained = [
        endpoint
        for endpoint in validated_endpoints
        if endpoint.system != "kafka"
        or (endpoint.role, endpoint.path, endpoint.start_line) not in covered_sites
    ]
    enriched_strategy_endpoints = []
    for endpoint in normalized_strategy_endpoints:
        if endpoint.message_type is None:
            message_types = {
                candidate.message_type
                for candidate in generic_by_site.get(
                    (endpoint.role, endpoint.path, endpoint.start_line), []
                )
                if candidate.message_type is not None
            }
            if len(message_types) == 1:
                endpoint = replace(endpoint, message_type=next(iter(message_types)))
        enriched_strategy_endpoints.append(endpoint)
    return [*retained, *enriched_strategy_endpoints]
_MARKDOWN_MODULE_HEADING_RE = re.compile(r"^\s*###\s+(.+?)\s*$")
_MARKDOWN_BOLD_SECTION_RE = re.compile(r"^\s*\*\*(Producer|Consumer)\*\*\s*$", re.IGNORECASE)
_MARKDOWN_CODE_RE = re.compile(r"^`(.*)`$")
def _clean_markdown_table_cell(value: str) -> str:
    cleaned = value.strip()
    code = _MARKDOWN_CODE_RE.match(cleaned)
    if code is not None:
        return code.group(1).strip()
    return cleaned
def _normalize_markdown_header(value: str) -> str:
    normalized = _clean_markdown_table_cell(value).lower()
    normalized = normalized.replace("é", "e").replace("è", "e").replace("ê", "e")
    return " ".join(normalized.split())
def _split_markdown_table_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    cells = [_clean_markdown_table_cell(cell) for cell in stripped.strip("|").split("|")]
    return cells
def _is_markdown_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)
def _build_markdown_topic_manifest_endpoint(
    rel_path: str,
    line_no: int,
    line: str,
    module: str | None,
    role: str,
    topic: str,
) -> MessageEndpoint:
    return MessageEndpoint(
        id=compute_endpoint_id(role, topic, rel_path, line_no, line_no),
        role=role,
        system="kafka",
        topic=topic,
        topic_dynamic=False,
        source="manifest",
        framework="markdown-topic-manifest",
        path=rel_path,
        start_line=line_no,
        end_line=line_no,
        snippet=line.strip(),
        module=module,
        qualified_name=None,
    )
def _parse_markdown_topic_manifest(repo_root: Path, rel_path: str) -> list[MessageEndpoint]:
    try:
        lines = (repo_root / rel_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    endpoints: list[MessageEndpoint] = []
    module: str | None = None
    role: str | None = None
    header: list[str] | None = None
    topic_index: int | None = None
    physical_index: int | None = None

    for idx, line in enumerate(lines, start=1):
        heading = _MARKDOWN_MODULE_HEADING_RE.match(line)
        if heading is not None:
            module = heading.group(1).strip()
            role = None
            header = None
            topic_index = None
            physical_index = None
            continue

        section = _MARKDOWN_BOLD_SECTION_RE.match(line)
        if section is not None:
            role = "produce" if section.group(1).lower() == "producer" else "consume"
            header = None
            topic_index = None
            physical_index = None
            continue

        cells = _split_markdown_table_row(line)
        if cells is None:
            continue
        if _is_markdown_separator_row(cells):
            continue
        if role is None:
            continue

        normalized_cells = [_normalize_markdown_header(cell) for cell in cells]
        if "topic" in normalized_cells and "nom physique" in normalized_cells:
            header = cells
            topic_index = normalized_cells.index("topic")
            physical_index = normalized_cells.index("nom physique")
            continue
        if header is None or topic_index is None or physical_index is None:
            continue
        if max(topic_index, physical_index) >= len(cells):
            continue

        physical_name = cells[physical_index].strip()
        logical_name = cells[topic_index].strip()
        topic = physical_name or logical_name
        if not topic:
            continue
        endpoints.append(
            _build_markdown_topic_manifest_endpoint(
                rel_path, idx, line, module, role, topic
            )
        )

    return endpoints
def infer_markdown_topic_manifest_endpoints(
    repo_root: Path, files: list[str] | None = None
) -> list[MessageEndpoint]:
    if files is None:
        candidate_files = [
            path.relative_to(repo_root).as_posix()
            for path in repo_root.rglob("*.md")
            if path.is_file()
            and not {"target", "build"}.intersection(path.relative_to(repo_root).parts)
        ]
    else:
        candidate_files = sorted(path for path in files if path.endswith(".md"))

    inferred: dict[str, MessageEndpoint] = {}
    for rel_path in candidate_files:
        for endpoint in _parse_markdown_topic_manifest(repo_root, rel_path):
            inferred[endpoint.id] = endpoint
    return list(inferred.values())
def _json_manifest_line_number(text: str, section: str, module: str, topic: str) -> int:
    """Return the best-effort line of a topic declaration in a JSON manifest."""
    section_index = text.find(json.dumps(section, ensure_ascii=False))
    module_index = text.find(json.dumps(module, ensure_ascii=False), max(section_index, 0))
    topic_index = text.find(json.dumps(topic, ensure_ascii=False), max(module_index, 0))
    if topic_index < 0:
        return 1
    return text.count("\n", 0, topic_index) + 1
def _parse_json_kafka_flow_graph_manifest(repo_root: Path, rel_path: str) -> list[MessageEndpoint]:
    """Parse the `topics`/`producers`/`consumers` JSON flow-graph schema."""
    try:
        text = (repo_root / rel_path).read_text(encoding="utf-8", errors="replace")
        data = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []

    raw_topics = data.get("topics")
    if not isinstance(raw_topics, dict):
        return []
    topics = {
        logical: physical.strip() or logical
        for logical, physical in raw_topics.items()
        if isinstance(logical, str) and isinstance(physical, str)
    }

    endpoints: dict[str, MessageEndpoint] = {}
    for section, role in (("producers", "produce"), ("consumers", "consume")):
        declarations = data.get(section)
        if not isinstance(declarations, dict):
            continue
        for module, declared_topics in declarations.items():
            if not isinstance(module, str) or not isinstance(declared_topics, list):
                continue
            for logical_topic in declared_topics:
                if not isinstance(logical_topic, str) or not logical_topic.strip():
                    continue
                topic = topics.get(logical_topic, logical_topic)
                line_no = _json_manifest_line_number(text, section, module, logical_topic)
                endpoint = MessageEndpoint(
                    id=compute_endpoint_id(
                        role, topic, f"{rel_path}:{module}", line_no, line_no
                    ),
                    role=role,
                    system="kafka",
                    topic=topic,
                    topic_dynamic=False,
                    source="manifest",
                    framework="json-kafka-flow-graph",
                    path=rel_path,
                    start_line=line_no,
                    end_line=line_no,
                    snippet=f"{section}.{module}: {logical_topic} -> {topic}",
                    module=module,
                    qualified_name=None,
                )
                endpoints[endpoint.id] = endpoint
    return list(endpoints.values())
def infer_json_kafka_flow_graph_endpoints(
    repo_root: Path, files: list[str] | None = None
) -> list[MessageEndpoint]:
    """Infer Kafka endpoints from compatible JSON flow graph manifests."""
    if files is None:
        candidate_files = [
            path.relative_to(repo_root).as_posix()
            for path in repo_root.rglob("*.json")
            if path.is_file()
            and not {"target", "build"}.intersection(path.relative_to(repo_root).parts)
        ]
    else:
        candidate_files = sorted(path for path in files if path.endswith(".json"))

    inferred: dict[str, MessageEndpoint] = {}
    for rel_path in candidate_files:
        for endpoint in _parse_json_kafka_flow_graph_manifest(repo_root, rel_path):
            inferred[endpoint.id] = endpoint
    return list(inferred.values())
