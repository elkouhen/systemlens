"""Same-method value dependencies between data reads and writes."""

import re
from typing import Mapping

from tree_sitter import Node

from systemlens.discovery.java import parser

_UNWRAPPING_CALLS = frozenset({"orElseThrow", "orElse", "orElseGet", "get", "getContent"})


def is_reassigned(body: Node, source: bytes, name: str) -> bool:
    """Whether a local or parameter is assigned again anywhere in ``body``."""
    for node in parser.walk(body):
        if node.type == "assignment_expression":
            left = node.child_by_field_name("left")
            if left is not None and parser.node_text(source, left).strip() == name:
                return True
        elif node.type in {"update_expression", "unary_expression"}:
            text = parser.node_text(source, node).strip()
            if re.fullmatch(rf"(?:\+\+|--)?\s*{re.escape(name)}\s*(?:\+\+|--)?", text):
                return True
    return False


_UNWRAPPING_CALLS = frozenset({"orElseThrow", "orElse", "orElseGet", "get", "getContent"})


def _invocation_root(node: Node) -> Node:
    """Follow a fluent chain ``read(...).orElseThrow()`` down to its root call."""
    current = node
    while current.type == "method_invocation":
        name = current.child_by_field_name("name")
        receiver = current.child_by_field_name("object")
        if receiver is None or receiver.type != "method_invocation":
            return current
        if name is not None and name.text is not None and name.text.decode() not in _UNWRAPPING_CALLS:
            return current
        current = receiver
    return current


def _read_site(
    expression: Node, source: bytes, reads: Mapping[tuple[int, str], str],
) -> str | None:
    """Return the read operation ID an expression is the unmodified result of."""
    if expression.type != "method_invocation":
        return None
    root = _invocation_root(expression)
    _receiver, operation, _arguments = parser.invocation_parts(root, source)
    return reads.get((root.start_point.row + 1, operation))


def trace_value_dependencies(
    source: bytes,
    method_node: Node,
    reads: Mapping[tuple[int, str], str],
    writes: Mapping[tuple[int, str], str],
) -> dict[str, tuple[str, ...]]:
    """Link each write to the reads whose unmodified result it writes back.

    ``reads`` and ``writes`` map ``(line, operation)`` to operation IDs of
    one method. A write depends on a read when its first argument is the
    read call itself (through ``Optional`` unwrapping) or a local variable
    initialised from that call and never reassigned. Setter calls on the
    variable are the "modify" of a read-modify-write and do not break the
    dependency; a reassigned variable or any other expression yields none.
    """
    body = method_node.child_by_field_name("body")
    if body is None or not reads or not writes:
        return {}
    locals_by_name: dict[str, list[tuple[int, Node]]] = {}
    for declaration in parser.walk(body):
        if declaration.type != "local_variable_declaration":
            continue
        if parser.enclosing(declaration, "method_declaration") != method_node:
            continue
        for declarator in declaration.named_children:
            if declarator.type != "variable_declarator":
                continue
            name = declarator.child_by_field_name("name")
            value = declarator.child_by_field_name("value")
            if name is not None and value is not None:
                locals_by_name.setdefault(parser.node_text(source, name), []).append(
                    (declaration.start_point.row + 1, value),
                )
    dependencies: dict[str, tuple[str, ...]] = {}
    for invocation in parser.walk(body):
        if invocation.type != "method_invocation":
            continue
        if parser.enclosing_method(invocation) != method_node:
            continue
        _receiver, operation, arguments = parser.invocation_parts(invocation, source)
        write_id = writes.get((invocation.start_point.row + 1, operation))
        if write_id is None or not arguments:
            continue
        argument = arguments[0]
        read_id = _read_site(argument, source, reads)
        if read_id is None and argument.type == "identifier":
            name = parser.node_text(source, argument)
            declarations = [
                value for line, value in locals_by_name.get(name, [])
                if line <= invocation.start_point.row + 1
            ]
            if len(declarations) == 1 and not is_reassigned(body, source, name):
                read_id = _read_site(declarations[0], source, reads)
        if read_id is not None:
            dependencies[write_id] = (read_id,)
    return dependencies
