"""Scope of Java pattern variables (JLS 6.3.1), shared by the type passes.

A pattern variable of ``instanceof`` is in scope where its condition is
proven true or false through parentheses, ``!``, ``&&`` and ``||``; a
``switch`` pattern binds in its rule or statement group, guard included.
Any other expression binds nothing, so callers keep the name unresolved
or fall back to their own lookup.
"""

from tree_sitter import Node

from systemlens.discovery.java import parser

_ABRUPT = {"return_statement", "throw_statement", "break_statement", "continue_statement"}


def pattern_binding(source: bytes, pattern: Node, name: str) -> Node | None:
    """The written type node of pattern variable ``name`` bound by ``pattern``.

    A ``var`` record component returns its ``var`` type node.
    """
    if pattern.type == "instanceof_expression":
        ident = pattern.child_by_field_name("name")
        if ident is not None:
            return pattern.child_by_field_name("right") if parser.node_text(source, ident) == name else None
        inner = pattern.child_by_field_name("pattern")
        return pattern_binding(source, inner, name) if inner is not None else None
    for item in parser.walk(pattern):
        if item.type not in {"type_pattern", "record_pattern_component"}:
            continue
        parts = [part for part in item.named_children if part.type != "modifiers"]
        if len(parts) >= 2 and parts[-1].type == "identifier" and parser.node_text(source, parts[-1]) == name:
            return parts[-2]
    return None


def binds(source: bytes, condition: Node | None, name: str, when: bool) -> Node | None:
    """Type node of ``name`` when ``condition`` evaluating to ``when`` binds it.

    Follows JLS 6.3.1 for parentheses, ``!``, ``&&`` and ``||`` only;
    any other expression binds nothing.
    """
    if condition is None:
        return None
    kind = condition.type
    if kind == "parenthesized_expression":
        return binds(source, next(iter(condition.named_children), None), name, when)
    if kind == "instanceof_expression":
        return pattern_binding(source, condition, name) if when else None
    if kind == "unary_expression":
        operator = condition.child_by_field_name("operator")
        if operator is not None and parser.node_text(source, operator) == "!":
            return binds(source, condition.child_by_field_name("operand"), name, not when)
        return None
    if kind == "binary_expression":
        operator = condition.child_by_field_name("operator")
        text = parser.node_text(source, operator) if operator is not None else ""
        if (text, when) in {("&&", True), ("||", False)}:
            return (
                binds(source, condition.child_by_field_name("left"), name, when)
                or binds(source, condition.child_by_field_name("right"), name, when)
            )
    return None


def completes_abruptly(statement: Node | None) -> bool:
    """Whether ``statement`` surely ends in ``return``, ``throw``, ``break`` or ``continue``."""
    if statement is None:
        return False
    if statement.type == "block":
        statements = [item for item in statement.named_children if "comment" not in item.type]
        return bool(statements) and statements[-1].type in _ABRUPT
    return statement.type in _ABRUPT


def flow_binding(source: bytes, scope: Node, child: Node, name: str) -> Node | None:
    """Type node of a pattern variable ``name`` that ``scope`` introduces into
    ``child``, its direct child on the way to the use."""
    kind = scope.type
    if kind in {"if_statement", "while_statement", "ternary_expression"}:
        condition = scope.child_by_field_name("condition")
        if child == scope.child_by_field_name("consequence") or (
            kind == "while_statement" and child == scope.child_by_field_name("body")
        ):
            return binds(source, condition, name, True)
        if child == scope.child_by_field_name("alternative"):
            return binds(source, condition, name, False)
        return None
    if kind == "binary_expression" and child == scope.child_by_field_name("right"):
        operator = scope.child_by_field_name("operator")
        text = parser.node_text(source, operator) if operator is not None else ""
        if text in {"&&", "||"}:
            return binds(source, scope.child_by_field_name("left"), name, text == "&&")
        return None
    if kind in {"switch_rule", "switch_block_statement_group"}:
        for label in scope.named_children:
            if label.type == "switch_label":
                found = pattern_binding(source, label, name)
                if found is not None:
                    return found
        if kind == "switch_rule":
            return None
    if kind in {"block", "switch_block_statement_group", "constructor_body"}:
        # ``if (!(x instanceof T v)) return;`` binds ``v`` in the statements after it.
        for statement in scope.named_children:
            if statement.start_byte >= child.start_byte:
                break
            if (
                statement.type == "if_statement"
                and statement.child_by_field_name("alternative") is None
                and completes_abruptly(statement.child_by_field_name("consequence"))
            ):
                found = binds(source, statement.child_by_field_name("condition"), name, False)
                if found is not None:
                    return found
    return None
