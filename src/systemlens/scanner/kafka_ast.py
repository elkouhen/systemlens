"""Inférence des endpoints Kafka au niveau AST (BACKLOG-16).

Détecte `@KafkaListener` (consume), `KafkaTemplate.send/sendDefault`,
`new ProducerRecord<...>`, `MessageBuilder...setHeader(TOPIC,...).build()`
puis `.send(msg)`, `builder.stream(...)`/`KafkaConsumer.subscribe` (consume)
et `KStream.to(...)` (produce). Le type de message est inféré depuis les
signatures/generics de l'AST. Point d'entrée public : `infer_kafka_endpoints`."""

from __future__ import annotations

import re
from pathlib import Path

from tree_sitter import Node

from systemlens.discovery.java import parser as java_parser
from systemlens.domain.models import MessageEndpoint, compute_endpoint_id
from systemlens.scanner._core import (
    _find_first_literal,
    _split_java_type_arguments,
    _invocation_receiver,
    _java_qualified_name,
    _kafka_payload_type,
    _module_for_path,
)
from systemlens.scanner._spring_properties import (
    _resolve_value_annotated_variable,
    resolve_spring_property,
)
from systemlens.scanner.java_strings import local_string, locally_declared_names
from systemlens.scanner.kafka_payload_types import (
    expression_type_text,
    payload_expression_type,
    template_value_type,
    type_variables_in_scope,
)
from systemlens.domain.topic_expressions import spring_topic_reference

_BARE_TOPIC_VAR_RE = re.compile(
    r"(?:topics\s*=\s*|\.send\(\s*|ProducerRecord\(\s*)([A-Za-z_]\w*)\s*[,)]"
)
# BACKLOG Q25 : `KStream.to("topic")`/`.to("topic", Produced.with(...))` —
# le topic suit directement `.to(`, contrairement au premier littéral
# quelconque du snippet (qui peut appartenir à un `.peek(...)` chaîné avant).
_KAFKA_STREAMS_TO_RE = re.compile(r'\.to\(\s*"([^"]*)"\s*(\+)?')
_KAFKA_TEMPLATE_DECLARATION_RE = re.compile(
    r"\bKafkaTemplate(?:\s*<[^;{}]+>)?\s+([A-Za-z_]\w*)\b"
)


def _kafka_template_receivers(source_text: str) -> set[str]:
    """Return locally declared KafkaTemplate variable names.

    A field or constructor parameter is commonly named ``template`` or
    ``producer`` rather than ``kafkaTemplate``.  Inferring the receiver from
    its declaration keeps the detection precise without treating every
    ``send`` call in a Kafka-enabled class as a Kafka publication.
    """
    return set(_KAFKA_TEMPLATE_DECLARATION_RE.findall(source_text))


def _resolve_topic_expression(
    expr: str, repo_root: Path, source_path: str
) -> tuple[str, bool]:
    expr = expr.strip()
    if len(expr) >= 2 and expr[0] == expr[-1] == '"':
        literal = expr[1:-1]
        reference = spring_topic_reference(literal)
        if reference is not None:
            resolved = resolve_spring_property(repo_root, reference.property_key, source_path)
            if resolved is not None:
                return resolved, False
            return reference.display_name, True
        return literal, False
    if re.fullmatch(r"[A-Za-z_]\w*", expr):
        resolved = _resolve_value_annotated_variable(repo_root, source_path, expr)
        if resolved is not None:
            return resolved, False
    return "<dynamic>", True
def _kafka_topic_from_value(value_node, source: bytes, repo_root: Path, rel_path: str) -> tuple[str, bool]:
    """Résout un nœud expression (topic) en (topic, dynamique).

    Littéral `${...}` -> résolu via ``resolve_spring_property`` (jamais au
    hasard) ; identifiant nu -> champ ``@Value`` ; appel imbriqué
    (ex. ``Collections.singletonList("x")``) -> on descend chercher le
    littéral ; sinon ``<dynamic>``."""
    if value_node is None:
        return "<dynamic>", True
    node_type = value_node.type
    if node_type in {"binary_expression", "parenthesized_expression"}:
        resolved_local = local_string(source, value_node)
        if resolved_local is not None:
            reference = spring_topic_reference(resolved_local)
            if reference is not None:
                resolved = resolve_spring_property(repo_root, reference.property_key, rel_path)
                if resolved is not None:
                    return resolved, False
                return reference.display_name, True
            return resolved_local, False
        if node_type in {"binary_expression", "parenthesized_expression"}:
            return "<dynamic>", True
    if node_type == "string_literal":
        literal = java_parser.string_value(value_node, source)
        if literal is None:
            return "<dynamic>", True
        return _topic_from_literal(literal, repo_root, rel_path)
    if node_type == "identifier":
        identifier = java_parser.node_text(source, value_node)
        if identifier in locally_declared_names(value_node, source):
            # A parameter or local shadows every field of that name: only
            # the lexical resolver may read it; a name lookup never does.
            resolved_local = local_string(source, value_node)
            if resolved_local is not None:
                return _topic_from_literal(resolved_local, repo_root, rel_path)
            return "<dynamic>", True
        resolved = _resolve_value_annotated_variable(
            repo_root, rel_path, identifier
        )
        if resolved is not None:
            return (resolved, True) if "${" in resolved else (resolved, False)
        if identifier.isupper():
            resolved_local = local_string(source, value_node)
            if resolved_local is not None:
                return _topic_from_literal(resolved_local, repo_root, rel_path)
        return "<dynamic>", True
    # A method call, lambda or any other expression computes its topic at
    # runtime: a literal somewhere inside it is not the topic.
    return "<dynamic>", True


def _topic_from_literal(literal: str, repo_root: Path, rel_path: str) -> tuple[str, bool]:
    """Resolve a literal topic: a placeholder is a configuration key.

    A placeholder embedded in a longer literal, a nested placeholder or a
    SpEL expression is not a topic name; it stays dynamic with its text as
    evidence. A resolved value still holding ``${`` is likewise dynamic.
    """
    reference = spring_topic_reference(literal)
    if reference is not None:
        resolved = resolve_spring_property(repo_root, reference.property_key, rel_path)
        if resolved is not None and "${" not in resolved:
            return resolved, False
        return (resolved if resolved is not None else reference.display_name), True
    if "${" in literal or literal.lstrip().startswith("#{"):
        return literal, True
    return literal, False
def _kafka_topics_from_value(value_node, source: bytes, repo_root: Path, rel_path: str) -> list[tuple[str, bool]]:
    """Resolve one or more topic expressions without silently dropping arrays.

    Spring's ``@KafkaListener(topics = {"a", "b"})`` is a single annotation
    but represents a dependency on every listed topic.  The generic resolver
    deliberately returns one value for invocation arguments; this companion
    keeps that behaviour while expanding Java array initializers and ternary
    expressions.  Every ternary branch is retained as a separate endpoint.
    """
    if value_node is not None and value_node.type == "ternary_expression":
        topics: list[tuple[str, bool]] = []
        for field in ("consequence", "alternative"):
            branch = value_node.child_by_field_name(field)
            topics.extend(_kafka_topics_from_value(branch, source, repo_root, rel_path))
        return list(dict.fromkeys(topics))
    if value_node is not None and value_node.type in {
        "array_initializer", "element_value_array_initializer"
    }:
        topics = [
            _kafka_topic_from_value(child, source, repo_root, rel_path)
            for child in value_node.children
            if child.type not in {"{", "}", ",", "line_comment", "block_comment"}
        ]
        return list(dict.fromkeys(topics))
    if value_node is not None and value_node.type == "method_invocation":
        receiver, name, arguments = java_parser.invocation_parts(value_node, source)
        receiver_name = java_parser.node_text(source, receiver).rsplit(".", 1)[-1] if receiver else ""
        if (receiver_name, name) in _COLLECTION_FACTORIES and arguments:
            # ``List.of("a", "b")`` subscribes to every listed topic.
            topics = [
                topic
                for argument in arguments
                for topic in _kafka_topics_from_value(argument, source, repo_root, rel_path)
            ]
            return list(dict.fromkeys(topics))
        if (receiver_name, name) == ("Pattern", "compile") and arguments:
            # A pattern subscription is evidence, never an exact topic.
            return [
                (topic, True)
                for topic, _dynamic in _kafka_topics_from_value(arguments[0], source, repo_root, rel_path)
            ]
    return [_kafka_topic_from_value(value_node, source, repo_root, rel_path)]


_COLLECTION_FACTORIES = frozenset({
    ("List", "of"), ("Set", "of"), ("Arrays", "asList"),
    ("Collections", "singletonList"), ("Collections", "singleton"),
})
def _kafka_listener_topics(listener_ann, source: bytes, repo_root: Path, rel_path: str) -> list[tuple[str, bool]]:
    """Extract all concrete topic dependencies of a Spring Kafka listener.

    Besides ``topics``, Spring supports ``topicPartitions``. A ``topicPattern``
    is kept as a dynamic fact: its literal is useful evidence, but it must not
    create an exact producer/consumer dependency in the graph.
    """
    topics_arg = java_parser.annotation_argument(listener_ann, source, key="topics")
    if topics_arg is not None:
        return _kafka_topics_from_value(topics_arg, source, repo_root, rel_path)
    topic_partitions = java_parser.annotation_argument(listener_ann, source, key="topicPartitions")
    if topic_partitions is not None:
        topics: list[tuple[str, bool]] = []
        for node in java_parser.walk(topic_partitions):
            if node.type != "element_value_pair":
                continue
            key = node.child_by_field_name("key")
            if key is None or java_parser.node_text(source, key) != "topic":
                continue
            topics.extend(_kafka_topics_from_value(
                node.child_by_field_name("value"), source, repo_root, rel_path
            ))
        if topics:
            return list(dict.fromkeys(topics))
    topic_pattern = java_parser.annotation_argument(listener_ann, source, key="topicPattern")
    if topic_pattern is not None:
        if topic_pattern.type == "string_literal":
            raw = java_parser.node_text(source, topic_pattern)
            return [(raw[1:-1], True)]
        return [(topic, True) for topic, _dynamic in _kafka_topics_from_value(
            topic_pattern, source, repo_root, rel_path
        )]
    return [("<dynamic>", True)]
def _type_simple_name(source: bytes, type_node) -> str | None:
    """Nom non paramétré d'un type AST : ``ProducerRecord`` pour
    ``ProducerRecord<String, Order>``.

    Avec tree-sitter-java, les arguments génériques sont des enfants du
    ``generic_type`` ; comparer le texte complet du nœud au nom du type
    manquait donc tous les ``new ProducerRecord<K, V>(...)``.
    """
    if type_node is None:
        return None
    text = java_parser.node_text(source, type_node)
    return text.split("<", 1)[0].rsplit(".", 1)[-1].strip() or None
def _declaration_anchor(node):
    """Ancre l'évidence d'un appel dans sa déclaration locale quand il y en a.

    Une invocation imbriquée comme ``builder.stream(...)`` peut commencer à
    la ligne suivant ``KStream<...> joined =``. L'inventaire historique
    pointait la déclaration entière ; conserver cette position évite des
    déplacements artificiels lors de la migration vers l'AST.
    """
    return java_parser.enclosing(node, "local_variable_declaration") or node
def _listener_payload_type(source: bytes, method_node) -> str | None:
    """Type de payload du premier paramètre utile d'un ``@KafkaListener``.

    The first parameter that is not a header or listener infrastructure is
    the payload; when its type names no DTO (``String``, ``Object``, a type
    variable), the payload is unknown rather than taken from a later one.
    """
    params = method_node.child_by_field_name("parameters")
    if params is None:
        return None
    formal = [param for param in params.children if param.type == "formal_parameter"]
    annotated = [
        param for param in formal
        if any(java_parser.annotation_name(ann, source) == "Payload"
               for ann in java_parser.annotations_of(param))
    ]
    # An explicit ``@Payload`` parameter is the payload wherever it sits.
    for param in annotated or formal:
        if any(
            java_parser.annotation_name(ann, source) in {"Header", "Headers"}
            for ann in java_parser.annotations_of(param)
        ):
            continue
        type_node = param.child_by_field_name("type")
        if type_node is None:
            continue
        type_name = java_parser.node_text(source, type_node)
        if type_name in {"Acknowledgment", "Consumer", "ConsumerRecordMetadata"}:
            continue
        return _kafka_payload_type(type_name, type_variables_in_scope(param, source))
    return None
def _producer_send_payload_type(repo_root: Path, rel_path: str, source: bytes, invocation) -> str | None:
    """Type de payload d'un appel d'envoi Kafka (``send``, ``sendDefault``).

    Spring Kafka places the value last in every topic-based overload, including
    ``send(topic, key, value)`` and ``send(topic, partition, key, value)``.
    Resolve that argument first so a key (often ``String``) is never reported
    as the message payload. A single-argument ``send`` carries a
    ``Message<T>`` or a ``ProducerRecord<K, V>``. When the value names no DTO,
    the value type ``V`` of the receiving ``KafkaTemplate<K, V>`` is the
    fallback (ADR-76).
    """
    arguments = java_parser.argument_nodes(invocation)
    is_call = invocation.type == "method_invocation"
    value: Node | None
    if is_call and len(arguments) == 1:
        value = arguments[0]
    else:
        # Only the last argument is the value; a preceding key or partition
        # is never reported when the value itself cannot be typed.
        value = arguments[-1] if len(arguments) >= 2 else None
    payload = (
        payload_expression_type(repo_root, rel_path, source, value)
        if value is not None else None
    )
    if payload is None and is_call:
        payload = template_value_type(
            repo_root, rel_path, source, invocation.child_by_field_name("object")
        )
    return payload
_HEADER_TYPES = frozenset({"Header", "Headers", "RecordHeader", "RecordHeaders"})
_TIMESTAMP_CALLS = frozenset({"currentTimeMillis", "toEpochMilli", "timestamp"})


def _is_headers_argument(repo_root: Path, rel_path: str, source: bytes, node: Node) -> bool:
    """Whether ``node`` is typed as Kafka headers (``Headers``, ``Iterable<Header>``)."""
    text = expression_type_text(repo_root, rel_path, source, node)
    if text is None:
        return False
    names = {name.rsplit(".", 1)[-1] for name in re.findall(r"[A-Za-z_$][\w$.]*", text)}
    return bool(names & _HEADER_TYPES)


def _is_timestamp_argument(repo_root: Path, rel_path: str, source: bytes, node: Node) -> bool:
    """Whether ``node`` is a ``long`` epoch timestamp, as written or typed."""
    if node.type == "decimal_integer_literal":
        return java_parser.node_text(source, node).rstrip().endswith(("L", "l"))
    if node.type == "method_invocation":
        _receiver, name, arguments = java_parser.invocation_parts(node, source)
        if name in _TIMESTAMP_CALLS and not arguments:
            return True
    text = expression_type_text(repo_root, rel_path, source, node)
    return text is not None and text.rsplit(".", 1)[-1] in {"long", "Long"}


def _producer_record_value(repo_root: Path, rel_path: str, source: bytes, creation: Node) -> Node | None:
    """Value argument of ``new ProducerRecord(...)`` chosen by constructor arity.

    Kafka's constructors are ``(topic, value)``, ``(topic, key, value)``,
    ``(topic, partition, key, value)``, ``(topic, partition, key, value,
    headers)``, ``(topic, partition, timestamp, key, value)`` and
    ``(topic, partition, timestamp, key, value, headers)``. The two
    five-argument forms are told apart by a headers-typed last argument or a
    timestamp third argument; otherwise the value is unknown.
    """
    arguments = java_parser.argument_nodes(creation)
    if len(arguments) in {2, 3, 4}:
        return arguments[-1]
    if len(arguments) == 6:
        return arguments[4]
    if len(arguments) == 5:
        if _is_headers_argument(repo_root, rel_path, source, arguments[4]):
            return arguments[3]
        if _is_timestamp_argument(repo_root, rel_path, source, arguments[2]):
            return arguments[4]
    return None


def _producer_record_payload_type(repo_root: Path, rel_path: str, source: bytes, creation: Node) -> str | None:
    """Payload type of ``new ProducerRecord(...)``.

    A written ``ProducerRecord<K, V>`` wins, on the ``new`` expression or on
    the declaration it initializes; a diamond falls back to the value
    argument picked by constructor arity, never to the headers.
    """
    written = payload_expression_type(repo_root, rel_path, source, creation)
    if written is not None:
        return written
    declarator = creation.parent
    if (
        declarator is not None
        and declarator.type == "variable_declarator"
        and declarator.child_by_field_name("value") == creation
        and declarator.parent is not None
    ):
        type_node = declarator.parent.child_by_field_name("type")
        if type_node is not None and java_parser.node_text(source, type_node).strip() != "var":
            declared = _kafka_payload_type(
                java_parser.node_text(source, type_node), type_variables_in_scope(declarator, source)
            )
            if declared is not None:
                return declared
    value = _producer_record_value(repo_root, rel_path, source, creation)
    return payload_expression_type(repo_root, rel_path, source, value) if value is not None else None


def _kafka_endpoint(
    repo_root: Path, rel_path: str, source: bytes, node, role: str, framework: str,
    topic: str, topic_dynamic: bool, message_type: str | None, end_node=None,
) -> MessageEndpoint:
    start_line = node.start_point.row + 1
    end_node = end_node or node
    end_line = end_node.end_point.row + 1
    snippet = source[node.start_byte : end_node.end_byte].decode("utf-8", errors="replace")
    return MessageEndpoint(
        id=compute_endpoint_id(role, topic, rel_path, start_line, end_line),
        role=role,
        system="kafka",
        topic=topic,
        topic_dynamic=topic_dynamic,
        source="code",
        framework=framework,
        path=rel_path,
        start_line=start_line,
        end_line=end_line,
        snippet=snippet,
        module=_module_for_path(repo_root, rel_path),
        qualified_name=_java_qualified_name(str(repo_root), rel_path),
        message_type=message_type,
    )
def _message_builder_topic_for(
    source: bytes, send_invocation, var_name: str, repo_root: Path, rel_path: str
) -> tuple[str, bool] | None:
    """Topic posé par ``MessageBuilder...setHeader(TOPIC, ...).build()`` pour
    la variable ``var_name`` déclarée dans la même méthode que l'envoi.

    Scope-aware : la variable du message est locale à la méthode englobant le
    ``.send(msg)``, donc on cherche le ``variable_declarator`` correspondant
    dans cette méthode (et pas globalement — plusieurs méthodes peuvent
    réutiliser le même nom ``message``)."""
    method = java_parser.enclosing(send_invocation, "method_declaration")
    if method is None:
        return None
    for declarator in java_parser.walk(method):
        if declarator.type != "variable_declarator":
            continue
        name_node = declarator.child_by_field_name("name")
        if name_node is None or java_parser.node_text(source, name_node) != var_name:
            continue
        initializer = declarator.child_by_field_name("value")
        if initializer is None:
            continue
        for invocation in java_parser.walk(initializer):
            if invocation.type != "method_invocation":
                continue
            _, method_name, args = java_parser.invocation_parts(invocation, source)
            if method_name != "setHeader" or len(args) < 2:
                continue
            header = java_parser.node_text(source, args[0])
            if header == "TOPIC" or header.endswith(".TOPIC"):
                return _kafka_topic_from_value(args[1], source, repo_root, rel_path)
    return None
_DEFAULT_TOPIC_PROPERTIES = ("spring.kafka.template.default-topic", "spring.kafka.template.defaultTopic")


def _template_default_topic(
    repo_root: Path, rel_path: str, source: bytes, root: Node, receiver: str,
) -> tuple[str, bool]:
    """Default topic of the template that a ``sendDefault`` call uses.

    ``sendDefault`` has no topic argument. A ``setDefaultTopic(...)`` on the
    same receiver name in the file wins; otherwise the Spring Boot property
    ``spring.kafka.template.default-topic`` is read. Several different
    values, or none, leave the topic dynamic.
    """
    assigned: set[tuple[str, bool]] = set()
    for node in java_parser.walk(root):
        if node.type != "method_invocation":
            continue
        object_node, name, arguments = java_parser.invocation_parts(node, source)
        if (
            name == "setDefaultTopic"
            and len(arguments) == 1
            and _invocation_receiver(source, object_node) == receiver
        ):
            assigned.add(_kafka_topic_from_value(arguments[0], source, repo_root, rel_path))
    if len(assigned) == 1:
        return next(iter(assigned))
    if assigned:
        return "<dynamic>", True
    for key in _DEFAULT_TOPIC_PROPERTIES:
        configured = resolve_spring_property(repo_root, key, rel_path)
        if configured:
            return (configured, True) if "${" in configured else (configured, False)
    return "<dynamic>", True


# ``Serdes.X()`` factories and the type they (de)serialize.
_SERDES_FACTORIES = {
    "ByteArray": "byte[]", "ByteBuffer": "ByteBuffer", "Bytes": "Bytes", "Double": "Double",
    "Float": "Float", "Integer": "Integer", "Long": "Long", "Short": "Short", "String": "String",
    "UUID": "UUID", "Void": "Void",
}


def _last_type_argument(text: str | None) -> str | None:
    if not text or "<" not in text:
        return None
    inner = text[text.find("<") + 1:text.rfind(">")]
    arguments = _split_java_type_arguments(inner)
    return arguments[-1] if arguments and arguments[-1].strip() else None


def _serde_value_type(repo_root: Path, rel_path: str, source: bytes, node: Node | None) -> str | None:
    """Type a value serde handles: ``new JsonSerde<>(Order.class)``, ``Serde<Order>``."""
    if node is None:
        return None
    if node.type == "object_creation_expression":
        type_node = node.child_by_field_name("type")
        written = _last_type_argument(java_parser.node_text(source, type_node)) if type_node is not None else None
        if written is not None:
            return written
        first = next(iter(java_parser.argument_nodes(node)), None)
        if first is not None and first.type == "class_literal":
            return java_parser.node_text(source, first).rsplit(".class", 1)[0].strip()
        return None
    if node.type == "method_invocation":
        receiver, name, arguments = java_parser.invocation_parts(node, source)
        if (
            receiver is not None and java_parser.node_text(source, receiver).rsplit(".", 1)[-1] == "Serdes"
            and not arguments and name in _SERDES_FACTORIES
        ):
            return _SERDES_FACTORIES[name]
    typed = expression_type_text(repo_root, rel_path, source, node)
    if typed is not None and typed.split("<", 1)[0].rsplit(".", 1)[-1].endswith("Serde"):
        return _last_type_argument(typed)
    return None


def _streams_options_value_type(
    repo_root: Path, rel_path: str, source: bytes, node: Node | None, options: str,
) -> str | None:
    """Value type of a ``Consumed`` or ``Produced`` argument, through its serde."""
    current = node
    while current is not None and current.type == "method_invocation":
        receiver, name, arguments = java_parser.invocation_parts(current, source)
        if (
            name == "with" and receiver is not None and len(arguments) >= 2
            and java_parser.node_text(source, receiver).rsplit(".", 1)[-1] == options
        ):
            return _serde_value_type(repo_root, rel_path, source, arguments[1])
        current = receiver
    if node is not None and node.type in {"identifier", "field_access"}:
        typed = expression_type_text(repo_root, rel_path, source, node)
        if typed is not None and typed.split("<", 1)[0].rsplit(".", 1)[-1] == options:
            return _last_type_argument(typed)
    return None


def _is_stream_type(text: str | None) -> bool:
    return text is not None and text.split("<", 1)[0].rsplit(".", 1)[-1].strip() in {"KStream", "KTable"}


def _streams_source_payload_type(repo_root: Path, rel_path: str, source: bytes, call: Node) -> str | None:
    """Payload of ``builder.stream(...)`` from what the source writes for it.

    In order: explicit type arguments (``builder.<K, V>stream``), the value
    serde of a ``Consumed`` argument, then the ``KStream<K, V>`` or
    ``KTable<K, V>`` declaration that this call initializes directly. The
    enclosing method's return type is never evidence (K6).
    """
    scope = type_variables_in_scope(call, source)
    type_arguments = call.child_by_field_name("type_arguments")
    if type_arguments is not None:
        written = _last_type_argument(java_parser.node_text(source, type_arguments))
        if written is not None:
            return _kafka_payload_type(written, scope)
    for argument in java_parser.argument_nodes(call)[1:]:
        serde = _streams_options_value_type(repo_root, rel_path, source, argument, "Consumed")
        if serde is not None:
            return _kafka_payload_type(serde, scope)
    declarator = call.parent
    if (
        declarator is not None and declarator.type == "variable_declarator"
        and declarator.child_by_field_name("value") == call and declarator.parent is not None
    ):
        type_node = declarator.parent.child_by_field_name("type")
        declared = java_parser.node_text(source, type_node) if type_node is not None else None
        if _is_stream_type(declared):
            return _kafka_payload_type(declared, scope)
    return None


def _streams_sink_payload_type(repo_root: Path, rel_path: str, source: bytes, call: Node) -> str | None:
    """Payload of ``stream.to(...)``: the ``Produced`` serde or the receiver's ``KStream<K, V>``."""
    scope = type_variables_in_scope(call, source)
    for argument in java_parser.argument_nodes(call)[1:]:
        serde = _streams_options_value_type(repo_root, rel_path, source, argument, "Produced")
        if serde is not None:
            return _kafka_payload_type(serde, scope)
    receiver = call.child_by_field_name("object")
    typed = expression_type_text(repo_root, rel_path, source, receiver) if receiver is not None else None
    if _is_stream_type(typed):
        return _kafka_payload_type(typed, scope)
    return None


def infer_kafka_endpoints(repo_root: Path, files: list[str] | None = None) -> list[MessageEndpoint]:
    """Découvre tous les endpoints Kafka depuis le code Java via tree-sitter.

    Source unique des endpoints Kafka (P2) : ``@KafkaListener`` (consume),
    ``KafkaTemplate.send/sendDefault`` (produce), ``new ProducerRecord<...>``
    (produce), ``MessageBuilder...setHeader(TOPIC,...).build()`` puis
    ``.send(msg)`` (produce), ``builder.stream(...)``/``KafkaConsumer.subscribe``
    (consume) et ``KStream.to(...)`` (produce). Le type de message est inféré
    depuis les signatures/generics de l'AST."""
    if files is None:
        candidate_files = [
            path.relative_to(repo_root).as_posix()
            for path in repo_root.rglob("*.java")
            if path.is_file() and not {"target", "build"}.intersection(path.relative_to(repo_root).parts)
        ]
    else:
        candidate_files = sorted(path for path in files if path.endswith(".java"))

    endpoints: dict[str, MessageEndpoint] = {}
    for rel_path in candidate_files:
        parsed = java_parser.parse_java(str(repo_root), rel_path)
        if parsed is None:
            continue
        source, root = parsed
        source_text = source.decode("utf-8", errors="replace")
        kafka_template_receivers = _kafka_template_receivers(source_text)
        has_kafka_streams = "KStream" in source_text or "StreamsBuilder" in source_text
        has_kafka_consumer = "KafkaConsumer" in source_text
        has_stream_bridge = "StreamBridge" in source_text

        def add(node, role: str, framework: str, topic: str, dynamic: bool, message_type: str | None) -> None:
            endpoint = _kafka_endpoint(
                repo_root, rel_path, source, node, role, framework, topic, dynamic, message_type
            )
            endpoints[endpoint.id] = endpoint

        for declaration in java_parser.walk(root):
            if declaration.type == "method_declaration":
                payload_type = _listener_payload_type(source, declaration)
                end_node = declaration
            elif declaration.type == "class_declaration":
                # A class-level listener dispatches to its ``@KafkaHandler``
                # methods; the payload is known when there is one handler.
                handlers = [
                    member for member in java_parser.walk(declaration)
                    if member.type == "method_declaration"
                    and java_parser.enclosing(member, "class_declaration") is declaration
                    and any(java_parser.annotation_name(ann, source) == "KafkaHandler"
                            for ann in java_parser.annotations_of(member))
                ]
                payload_type = _listener_payload_type(source, handlers[0]) if len(handlers) == 1 else None
                end_node = None
            else:
                continue
            # ``@KafkaListener`` is repeatable: every annotation is a listener.
            for listener_ann in java_parser.annotations_of(declaration):
                if java_parser.annotation_name(listener_ann, source) != "KafkaListener":
                    continue
                for topic, dynamic in _kafka_listener_topics(listener_ann, source, repo_root, rel_path):
                    endpoint = _kafka_endpoint(
                        repo_root,
                        rel_path,
                        source,
                        listener_ann,
                        "consume",
                        "spring-kafka",
                        topic,
                        dynamic,
                        payload_type,
                        end_node=end_node,
                    )
                    endpoints[endpoint.id] = endpoint

        for node in java_parser.walk(root):
            if node.type == "method_invocation":
                object_node, method_name, args = java_parser.invocation_parts(node, source)
                receiver = _invocation_receiver(source, object_node)
                if (
                    method_name in {"send", "sendDefault"}
                    and receiver
                    and (
                        receiver.lower().endswith("kafkatemplate")
                        or receiver.rsplit(".", 1)[-1] in kafka_template_receivers
                    )
                ):
                    if method_name == "sendDefault":
                        # The topic is the template's default, never the
                        # key; the value is the last argument.
                        if args:
                            topic, dynamic = _template_default_topic(
                                repo_root, rel_path, source, root, receiver.rsplit(".", 1)[-1]
                            )
                            add(node, "produce", "spring-kafka", topic, dynamic,
                                _producer_send_payload_type(repo_root, rel_path, source, node))
                    elif len(args) >= 2:
                        for topic, dynamic in _kafka_topics_from_value(
                            args[0], source, repo_root, rel_path
                        ):
                            add(node, "produce", "spring-kafka", topic, dynamic,
                                _producer_send_payload_type(repo_root, rel_path, source, node))
                    elif len(args) == 1 and args[0].type == "identifier":
                        built = _message_builder_topic_for(
                            source, node, java_parser.node_text(source, args[0]), repo_root, rel_path
                        )
                        if built is not None:
                            topic, dynamic = built
                            add(node, "produce", "spring-kafka", topic, dynamic,
                                _producer_send_payload_type(repo_root, rel_path, source, node))
                elif method_name == "send" and receiver and receiver.lower().endswith("streambridge") and has_stream_bridge and args:
                    for topic, dynamic in _kafka_topics_from_value(
                        args[0], source, repo_root, rel_path
                    ):
                        add(node, "produce", "spring-cloud-stream", topic, dynamic,
                            _producer_send_payload_type(repo_root, rel_path, source, node))
                elif method_name == "to" and has_kafka_streams and args:
                    for topic, dynamic in _kafka_topics_from_value(
                        args[0], source, repo_root, rel_path
                    ):
                        add(node, "produce", "kafka-streams", topic, dynamic,
                            _streams_sink_payload_type(repo_root, rel_path, source, node))
                elif method_name == "stream" and receiver == "builder" and has_kafka_streams and args:
                    for topic, dynamic in _kafka_topics_from_value(
                        args[0], source, repo_root, rel_path
                    ):
                        add(_declaration_anchor(node), "consume", "kafka-streams", topic, dynamic,
                            _streams_source_payload_type(repo_root, rel_path, source, node))
                elif (
                    method_name == "subscribe"
                    and receiver
                    and receiver.lower().endswith("consumer")
                    and has_kafka_consumer
                    and args
                ):
                    for topic, dynamic in _kafka_topics_from_value(
                        args[0], source, repo_root, rel_path
                    ):
                        add(node, "consume", "kafka-clients", topic, dynamic, None)
            elif node.type == "object_creation_expression":
                type_node = node.child_by_field_name("type")
                if _type_simple_name(source, type_node) == "ProducerRecord":
                    first_arg = next(iter(java_parser.argument_nodes(node)), None)
                    for topic, dynamic in _kafka_topics_from_value(
                        first_arg, source, repo_root, rel_path
                    ):
                        add(node, "produce", "kafka-clients", topic, dynamic,
                            _producer_record_payload_type(repo_root, rel_path, source, node))
    return list(endpoints.values())
def _extract_kafka_topic(
    snippet: str, repo_root: Path, source_path: str | None = None
) -> tuple[str, bool]:
    """Renvoie (topic, dynamique). Un littéral `${propriete.imbriquee}`
    (placeholder Spring, ex. `@KafkaListener(topics = "${app.kafka.topics.
    orders}")`) n'est pas un nom de topic mais une clé de configuration :
    tentative de résolution via `resolve_spring_property` avant de retomber
    sur dynamique si la clé est introuvable — jamais résolu au hasard. Une
    variable (pas de littéral du tout, ex. `topics = ordersTopic`) est
    tentée contre les champs `@Value("${...}")` du même fichier source
    (`_resolve_value_annotated_variable`) avant d'abandonner en dynamique.

    BACKLOG Q25 : `KStream.to("topic")` (Kafka Streams) est souvent chaîné
    après un `.peek(...)` dont le lambda peut lui-même contenir un littéral
    (message de log) — le premier littéral du snippet n'est alors pas le
    topic. Un `.to("...")` capté explicitement prime sur la recherche
    générique du premier littéral."""
    streams_to_match = _KAFKA_STREAMS_TO_RE.search(snippet)
    if streams_to_match is not None:
        return streams_to_match.group(1), streams_to_match.group(2) is not None

    literal, dynamic = _find_first_literal(snippet)
    if literal is None:
        if source_path is not None:
            first_line = snippet.splitlines()[0] if snippet else ""
            var_match = _BARE_TOPIC_VAR_RE.search(first_line)
            if var_match is not None:
                resolved = _resolve_value_annotated_variable(
                    repo_root, source_path, var_match.group(1)
                )
                if resolved is not None:
                    return resolved, False
        return "<dynamic>", True

    reference = spring_topic_reference(literal)
    if reference is not None:
        resolved = resolve_spring_property(repo_root, reference.property_key, source_path)
        if resolved is not None:
            return resolved, False
        return reference.display_name, True

    return literal, dynamic
