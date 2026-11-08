"""RabbitMQ and JMS message ports from the Java AST (PDR-15, PDR-16).

``@RabbitListener`` and ``@JmsListener`` methods are ``consume`` endpoints
named by their ``queues`` or ``destination`` value.

A publication through ``RabbitTemplate``, its interface ``AmqpTemplate`` or
``JmsTemplate`` is a ``produce`` endpoint. Its channel is named as a Kafka
topic is: a literal, a source-local constant or a resolvable property;
anything else is dynamic. A Rabbit publication on the default exchange is
named by its routing key, which is the queue name. A publication to a
named exchange is named ``exchange/routing-key``: SystemLens does not read
exchange bindings, so it pairs with no queue. A JMS publication is named by
its destination.
"""

from __future__ import annotations

import re
from pathlib import Path

from tree_sitter import Node

from systemlens.discovery.java import parser as java_parser
from systemlens.domain.models import MessageEndpoint, compute_endpoint_id
from systemlens.scanner._core import _invocation_receiver, _java_qualified_name, _module_for_path
from systemlens.scanner.kafka_ast import _kafka_topic_from_value, _kafka_topics_from_value

RABBIT_SYSTEM = "rabbitmq"
JMS_SYSTEM = "jms"
DYNAMIC_CHANNEL = "<dynamic>"

_RABBIT_TEMPLATES = ("RabbitTemplate", "AmqpTemplate")
_JMS_TEMPLATES = ("JmsTemplate",)
_SEND_METHODS = frozenset({"send", "convertAndSend"})
_CALLBACK_NODES = frozenset({"lambda_expression", "method_reference"})


def _template_receivers(source_text: str, types: tuple[str, ...]) -> set[str]:
    """Names of the variables the file declares with one of ``types``."""
    pattern = re.compile(
        r"\b(?:" + "|".join(types) + r")(?:\s*<[^;{}]+>)?\s+([A-Za-z_]\w*)\b"
    )
    return set(pattern.findall(source_text))


def _is_template(receiver: str | None, declared: set[str], types: tuple[str, ...]) -> bool:
    if not receiver:
        return False
    name = receiver.rsplit(".", 1)[-1]
    return name in declared or any(name.lower().endswith(kind.lower()) for kind in types)


def _endpoint(
    repo_root: Path, rel_path: str, source: bytes, node: Node, role: str, system: str,
    framework: str, channel: str, dynamic: bool, end_node: Node | None = None,
) -> MessageEndpoint:
    start_line = node.start_point.row + 1
    end = end_node or node
    end_line = end.end_point.row + 1
    return MessageEndpoint(
        id=compute_endpoint_id(role, channel, rel_path, start_line, end_line),
        role=role,
        system=system,
        topic=channel,
        topic_dynamic=dynamic,
        source="code",
        framework=framework,
        path=rel_path,
        start_line=start_line,
        end_line=end_line,
        snippet=source[node.start_byte:end.end_byte].decode("utf-8", errors="replace"),
        module=_module_for_path(repo_root, rel_path),
        qualified_name=_java_qualified_name(str(repo_root), rel_path),
    )


def _is_extra_argument(source: bytes, node: Node) -> bool:
    """A post-processor or correlation argument, never a channel or a message."""
    if node.type in _CALLBACK_NODES:
        return True
    text = java_parser.node_text(source, node).lower()
    return "correlation" in text or "postprocessor" in text


def _channels(
    values: list[Node], source: bytes, repo_root: Path, rel_path: str,
) -> list[tuple[str, bool]]:
    return _kafka_topics_from_value(values[0], source, repo_root, rel_path) if values else []


def _rabbit_channels(
    arguments: list[Node], source: bytes, repo_root: Path, rel_path: str,
) -> list[tuple[str, bool]]:
    """The channels of a Rabbit ``send`` or ``convertAndSend`` call.

    One argument uses the template's default exchange and routing key, and
    so does ``convertAndSend(message, postProcessor)``: the channel is
    dynamic. ``(routingKey, message)``, with an optional post-processor or
    correlation argument, publishes on the default exchange. Three or more
    other arguments start with ``(exchange, routingKey)``.
    """
    count = len(arguments)
    if count <= 1 or (count == 2 and _is_extra_argument(source, arguments[1])):
        return [(DYNAMIC_CHANNEL, True)]
    if count == 2 or (count == 3 and _is_extra_argument(source, arguments[2])):
        return _channels(arguments, source, repo_root, rel_path)
    exchange_node = arguments[0]
    if (
        exchange_node.type == "string_literal"
        and java_parser.string_value(exchange_node, source) == ""
    ):
        # The empty name is the default exchange.
        return _channels(arguments[1:], source, repo_root, rel_path)
    exchange, exchange_dynamic = _kafka_topic_from_value(exchange_node, source, repo_root, rel_path)
    if exchange_dynamic:
        return [(DYNAMIC_CHANNEL, True)]
    return [
        (f"{exchange}/{routing_key}", routing_dynamic)
        for routing_key, routing_dynamic in _channels(arguments[1:], source, repo_root, rel_path)
    ]


def _jms_channels(
    method: str, arguments: list[Node], source: bytes, repo_root: Path, rel_path: str,
) -> list[tuple[str, bool]]:
    """The destinations of a JMS ``send`` or ``convertAndSend`` call.

    One argument, or ``convertAndSend(message, postProcessor)``, uses the
    template's default destination: the channel is dynamic. In
    ``send(destination, creator)`` the callback is the message creator.
    """
    if len(arguments) <= 1 or (
        method == "convertAndSend" and len(arguments) == 2 and arguments[1].type in _CALLBACK_NODES
    ):
        return [(DYNAMIC_CHANNEL, True)]
    return _channels(arguments, source, repo_root, rel_path)


# Listener annotation -> (system, framework, channel attribute, handler).
_LISTENERS = {
    "RabbitListener": (RABBIT_SYSTEM, "spring-amqp", "queues", "RabbitHandler"),
    "JmsListener": (JMS_SYSTEM, "spring-jms", "destination", None),
}


def _listener_endpoints(
    repo_root: Path, rel_path: str, source: bytes, root: Node,
) -> list[MessageEndpoint]:
    """Consume endpoints of ``@RabbitListener`` and ``@JmsListener`` (PDR-16).

    The channel is the ``queues`` or ``destination`` value, resolved as a
    Kafka topic is; a listener without it, such as one declaring its
    bindings, is dynamic. A class-level ``@RabbitListener`` sits on the
    class; each of its ``@RabbitHandler`` methods receives it.
    """
    found: list[MessageEndpoint] = []
    for declaration in java_parser.walk(root):
        if declaration.type not in {"method_declaration", "class_declaration"}:
            continue
        for annotation in java_parser.annotations_of(declaration):
            listener = _LISTENERS.get(java_parser.annotation_name(annotation, source) or "")
            if listener is None:
                continue
            system, framework, attribute, handler = listener
            if declaration.type == "class_declaration" and handler is None:
                continue
            value = java_parser.annotation_argument(annotation, source, attribute)
            if value is None and attribute == "queues":
                value = java_parser.annotation_argument(annotation, source)
            channels = (
                _kafka_topics_from_value(value, source, repo_root, rel_path)
                if value is not None else [(DYNAMIC_CHANNEL, True)]
            )
            end_node = declaration if declaration.type == "method_declaration" else None
            found.extend(
                _endpoint(
                    repo_root, rel_path, source, annotation, "consume", system, framework,
                    channel, dynamic, end_node=end_node,
                )
                for channel, dynamic in channels
            )
    return found


def infer_broker_endpoints(repo_root: Path, files: list[str]) -> list[MessageEndpoint]:
    """Rabbit and JMS message ports of the given Java files."""
    endpoints: dict[str, MessageEndpoint] = {}
    for rel_path in sorted(path for path in files if path.endswith(".java")):
        parsed = java_parser.parse_java(str(repo_root), rel_path)
        if parsed is None:
            continue
        source, root = parsed
        text = source.decode("utf-8", errors="replace")
        if any(annotation in text for annotation in _LISTENERS):
            for endpoint in _listener_endpoints(repo_root, rel_path, source, root):
                endpoints[endpoint.id] = endpoint
        has_rabbit = any(kind in text for kind in _RABBIT_TEMPLATES)
        has_jms = any(kind in text for kind in _JMS_TEMPLATES)
        if not (has_rabbit or has_jms):
            continue
        rabbit_receivers = _template_receivers(text, _RABBIT_TEMPLATES) if has_rabbit else set()
        jms_receivers = _template_receivers(text, _JMS_TEMPLATES) if has_jms else set()
        for node in java_parser.walk(root):
            if node.type != "method_invocation":
                continue
            object_node, method, arguments = java_parser.invocation_parts(node, source)
            if method not in _SEND_METHODS:
                continue
            receiver = _invocation_receiver(source, object_node)
            if has_rabbit and _is_template(receiver, rabbit_receivers, _RABBIT_TEMPLATES):
                system, framework = RABBIT_SYSTEM, "spring-amqp"
                channels = _rabbit_channels(arguments, source, repo_root, rel_path)
            elif has_jms and _is_template(receiver, jms_receivers, _JMS_TEMPLATES):
                system, framework = JMS_SYSTEM, "spring-jms"
                channels = _jms_channels(method, arguments, source, repo_root, rel_path)
            else:
                continue
            for channel, dynamic in channels:
                endpoint = _endpoint(
                    repo_root, rel_path, source, node, "produce", system, framework, channel, dynamic,
                )
                endpoints[endpoint.id] = endpoint
    return list(endpoints.values())
