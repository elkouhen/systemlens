"""Compare Kafka payload types written on both sides of a topic (ADR-76).

A persisted ``message_type`` keeps the type as the source spells it. Two
spellings name the same payload when their comparison keys are equal: the
key drops package qualifiers, so ``p.OrderEvent`` and ``OrderEvent`` match,
and generic arguments stay part of the type, so ``List<OrderEvent>`` is not
``OrderEvent``.

A type that says nothing about the DTO is unknown rather than known:
``Object``, ``String`` and ``byte[]`` (a serialized payload), wildcards,
raw message or collection containers, and declared type variables. An
unknown type never blocks a producer/consumer pairing.
"""

from __future__ import annotations

import re
from collections.abc import Collection

_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][\w$]*")
_QUALIFIED_RE = re.compile(r"\b(?:[A-Za-z_$][\w$]*\.)+([A-Za-z_$][\w$]*)")
# The static type of a serialized or untyped payload names no DTO.
_NON_INFORMATIVE = frozenset({"Object", "String", "byte[]", "Byte[]", "CharSequence"})
# A raw container hides its element or payload type.
_RAW_CONTAINERS = frozenset({
    "ConsumerRecord", "ConsumerRecords", "Message", "GenericMessage", "ProducerRecord",
    "KafkaTemplate", "KafkaConsumer", "KStream", "KTable",
    "Collection", "Iterable", "List", "Set", "Map", "Optional", "Stream",
})


def _simple_spelling(text: str) -> str:
    return _QUALIFIED_RE.sub(r"\1", re.sub(r"\s+", "", text))


def informative_message_type(
    text: str | None, type_variables: Collection[str] = (),
) -> str | None:
    """Return ``text`` when it names a payload type, else ``None``.

    ``type_variables`` holds the type parameters declared by the enclosing
    method and type declarations of the site that wrote ``text``.
    """
    if not text:
        return None
    simple = _simple_spelling(text)
    if not simple or "?" in simple or "<>" in simple:
        return None
    if simple in _NON_INFORMATIVE or simple in _RAW_CONTAINERS:
        return None
    tokens = set(_IDENTIFIER_RE.findall(simple))
    if "Object" in tokens or tokens & set(type_variables):
        return None
    return text.strip()


def message_type_key(text: str | None) -> str | None:
    """Comparison key of a persisted payload type, ``None`` when unknown."""
    informative = informative_message_type(text)
    return _simple_spelling(informative) if informative is not None else None


def message_types_conflict(produced: str | None, consumed: str | None) -> bool:
    """Whether two known payload types name different DTOs."""
    produced_key, consumed_key = message_type_key(produced), message_type_key(consumed)
    return produced_key is not None and consumed_key is not None and produced_key != consumed_key
