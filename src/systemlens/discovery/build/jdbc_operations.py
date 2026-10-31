"""Literal JDBC statements on ``JdbcTemplate`` receivers.

Only a string literal, a text block, or a ``static final`` constant of the
same class with a literal initializer is classified. Concatenated or
formatted strings, stored procedures, joins, subqueries, and multiple
statements keep an unknown action or resource with a reason.
"""

from dataclasses import dataclass
from typing import Mapping

from tree_sitter import Node

from systemlens.discovery.java import parser as java_parser
from systemlens.domain.data_operations import QueryClassification, classify_sql

JDBC_RECEIVER_TYPES = frozenset({"JdbcTemplate", "NamedParameterJdbcTemplate", "JdbcClient"})
_READ_OPERATIONS = frozenset({
    "query", "queryForObject", "queryForList", "queryForMap", "queryForRowSet",
    "queryForStream",
})
_STATEMENT_OPERATIONS = frozenset({"update", "batchUpdate", "execute"})
_NON_VALUE_ARGUMENTS = frozenset({
    "lambda_expression", "method_reference", "object_creation_expression",
    "array_creation_expression", "array_initializer", "class_literal",
})


@dataclass(frozen=True)
class JdbcOperation:
    action: str
    table: str | None
    key_expression: str | None
    key_column: str | None
    where_columns: tuple[str, ...]
    reason: str | None


def string_literal_value(node: Node, source: bytes) -> str | None:
    """Decode a string literal or text block; ``None`` for anything else."""
    if node.type != "string_literal":
        return None
    fragments = [
        java_parser.node_text(source, child)
        for child in node.children if child.type in {"string_fragment", "multiline_string_fragment"}
    ]
    text = "".join(fragments)
    return text.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\\\", "\\")


def literal_string(
    node: Node, source: bytes, constants: Mapping[str, str],
) -> tuple[str | None, str | None]:
    """Resolve a literal SQL argument; otherwise return ``(None, reason)``."""
    value = string_literal_value(node, source)
    if value is not None:
        return value, None
    if node.type == "identifier":
        constant = constants.get(java_parser.node_text(source, node))
        if constant is not None:
            return constant, None
        return None, "SQL string is not a literal or a same-class constant"
    if node.type == "binary_expression":
        return None, "SQL string is concatenated"
    if node.type == "method_invocation":
        return None, "SQL string is built or formatted at runtime"
    return None, "SQL string is not a literal"


def class_string_constants(class_node: Node, source: bytes) -> dict[str, str]:
    """Map ``static final String`` fields of a class to their literal values."""
    constants: dict[str, str] = {}
    for field in java_parser.walk(class_node):
        if field.type != "field_declaration" or java_parser.enclosing(
            field, "class_declaration", "record_declaration", "enum_declaration", "interface_declaration",
        ) != class_node:
            continue
        modifiers = java_parser.modifiers_node(field)
        words = java_parser.node_text(source, modifiers).split() if modifiers else []
        if "static" not in words or "final" not in words:
            continue
        for declarator in field.children:
            if declarator.type != "variable_declarator":
                continue
            name = declarator.child_by_field_name("name")
            value = declarator.child_by_field_name("value")
            if name is None or value is None:
                continue
            literal = string_literal_value(value, source)
            if literal is not None:
                constants[java_parser.node_text(source, name)] = literal
    return constants


def _key_argument(
    arguments: list[Node], source: bytes, classification: QueryClassification,
) -> str | None:
    """Pick the positional argument bound to the single ``col = ?`` predicate."""
    if classification.key_placeholder is None:
        return None
    values = [
        argument for argument in arguments[1:]
        if argument.type not in _NON_VALUE_ARGUMENTS
    ]
    if len(values) <= classification.key_placeholder:
        return None
    candidate = values[classification.key_placeholder]
    if candidate.type in {"identifier", "string_literal", "decimal_integer_literal", "this"} or (
        candidate.type == "field_access"
    ):
        return java_parser.node_text(source, candidate).strip()
    return None


def classify_jdbc_invocation(
    operation: str, arguments: list[Node], source: bytes, constants: Mapping[str, str],
) -> JdbcOperation | None:
    """Classify one ``JdbcTemplate`` call; ``None`` when it is not a statement."""
    if operation not in _READ_OPERATIONS | _STATEMENT_OPERATIONS:
        return None
    if not arguments:
        return JdbcOperation("unknown", None, None, None, (), "statement argument missing")
    if operation == "batchUpdate" and sum(
        argument.type == "string_literal" for argument in arguments
    ) > 1:
        return JdbcOperation("unknown", None, None, None, (), "multiple SQL statements")
    sql, reason = literal_string(arguments[0], source, constants)
    if sql is None:
        action = "read" if operation in _READ_OPERATIONS else "unknown"
        return JdbcOperation(action, None, None, None, (), reason)
    classification = classify_sql(sql)
    action = classification.action
    if operation in _READ_OPERATIONS and action == "unknown":
        # The API contract makes the call a read even when the statement is
        # a procedure or otherwise unparsed; the resource stays unknown.
        action = "read"
    return JdbcOperation(
        action, classification.target,
        _key_argument(arguments, source, classification) if classification.target else None,
        classification.key_column if classification.target else None,
        classification.where_columns, classification.reason,
    )
