"""Conservative value origins for scalar keys along resolved Java calls."""

import re
from pathlib import Path
from typing import Sequence

from tree_sitter import Node

from systemlens.discovery.java import parser
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.indexing.codeql import CodeQLCall


_SCALAR_TYPES = {
    "byte", "short", "int", "long", "float", "double", "char", "boolean",
    "String", "java.lang.String", "Byte", "Short", "Integer", "Long",
    "Float", "Double", "Character", "Boolean", "java.lang.Byte",
    "java.lang.Short", "java.lang.Integer", "java.lang.Long", "java.lang.Float",
    "java.lang.Double", "java.lang.Character", "java.lang.Boolean",
}
_JAVA_LITERAL = re.compile(
    r'(?:"(?:[^"\\\r\n]|\\.)*"|\'(?:[^\'\\\r\n]|\\.)\'|'
    r'(?:0[xX][0-9a-fA-F](?:_?[0-9a-fA-F])*|'
    r'0[bB][01](?:_?[01])*|0(?:_?[0-7])*|[1-9](?:_?[0-9])*)(?:[lL])?|'
    r'true|false)'
)


def is_java_literal(expression: str) -> bool:
    """Whether an expression is one supported, exact Java key literal."""
    return _JAVA_LITERAL.fullmatch(expression.strip()) is not None


def _method_node(source: bytes, root: Node, method: IntegrationMethod) -> Node | None:
    expected_name = method.qualified_method.rsplit(".", 1)[-1]
    matches = []
    for node in parser.walk(root):
        if node.type != "method_declaration":
            continue
        name = node.child_by_field_name("name")
        if (
            name is not None
            and parser.node_text(source, name) == expected_name
            and node.start_point.row + 1 == method.start_line
        ):
            matches.append(node)
    return matches[0] if len(matches) == 1 else None


def _parameter_index(source: bytes, method_node: Node, expression: str) -> int | None:
    if re.fullmatch(r"[A-Za-z_$][\w$]*", expression) is None:
        return None
    parameters = method_node.child_by_field_name("parameters")
    body = method_node.child_by_field_name("body")
    if parameters is None or body is None:
        return None
    candidates: list[tuple[int, Node, Node]] = []
    for index, parameter in enumerate(parameters.named_children):
        if parameter.type not in {"formal_parameter", "spread_parameter"}:
            continue
        name = parameter.child_by_field_name("name")
        parameter_type = parameter.child_by_field_name("type")
        if name is not None and parameter_type is not None and (
            parser.node_text(source, name) == expression
        ):
            candidates.append((index, parameter, parameter_type))
    if len(candidates) != 1:
        return None
    index, _parameter, parameter_type = candidates[0]
    type_name = parser.node_text(source, parameter_type).strip()
    if type_name not in _SCALAR_TYPES:
        return None
    if _is_reassigned(body, source, expression):
        return None
    return index


def _expression_origin(
    source: bytes, method_node: Node, expression: str, usage_line: int,
    visited: frozenset[str] = frozenset(),
) -> tuple[str, str | int] | None:
    """Resolve one literal, unchanged parameter, or final scalar local alias."""
    expression = expression.strip()
    if is_java_literal(expression):
        return "literal", expression
    parameter_index = _parameter_index(source, method_node, expression)
    if parameter_index is not None:
        return "parameter", parameter_index
    if re.fullmatch(r"[A-Za-z_$][\w$]*", expression) is None or expression in visited:
        return None
    body = method_node.child_by_field_name("body")
    if body is None:
        return None
    declarations: list[tuple[Node, Node]] = []
    for declaration in parser.walk(body):
        if declaration.type != "local_variable_declaration":
            continue
        if parser.enclosing(declaration, "method_declaration") != method_node:
            continue
        if declaration.start_point.row + 1 > usage_line:
            continue
        modifiers = parser.child_by_type(declaration, "modifiers")
        if modifiers is None or "final" not in parser.node_text(source, modifiers).split():
            continue
        type_node = declaration.child_by_field_name("type")
        if type_node is None or parser.node_text(source, type_node).strip() not in _SCALAR_TYPES:
            continue
        for declarator in declaration.named_children:
            if declarator.type != "variable_declarator":
                continue
            name = declarator.child_by_field_name("name")
            value = declarator.child_by_field_name("value")
            if name is not None and value is not None and (
                parser.node_text(source, name) == expression
            ):
                declarations.append((declaration, value))
    if len(declarations) != 1:
        return None
    declaration, value = declarations[0]
    return _expression_origin(
        source, method_node, parser.node_text(source, value),
        declaration.start_point.row + 1, visited | {expression},
    )


def _is_reassigned(body: Node, source: bytes, name: str) -> bool:
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


def _call_argument(
    root: Node, source: bytes, caller_node: Node, call: CodeQLCall,
    callee_name: str, index: int,
) -> str | None:
    candidates: list[list[Node]] = []
    for node in parser.walk(root):
        if node.type != "method_invocation":
            continue
        if not (caller_node.start_byte <= node.start_byte and node.end_byte <= caller_node.end_byte):
            continue
        if not (node.start_point.row + 1 <= call.call_line <= node.end_point.row + 1):
            continue
        _receiver, operation, arguments = parser.invocation_parts(node, source)
        if operation == callee_name:
            candidates.append(arguments)
    if len(candidates) != 1 or index >= len(candidates[0]):
        return None
    return parser.node_text(source, candidates[0][index]).strip()


def trace_scalar_key_identity(
    repo_root: Path,
    route: Sequence[tuple[IntegrationMethod, CodeQLCall, bool]],
    entry: IntegrationMethod,
    current: IntegrationMethod,
    expression: str | None,
    resource_identity: str | None,
) -> str | None:
    """Trace a scalar parameter through unique resolved calls to its entry."""
    if not expression or not resource_identity:
        return None
    key_expression = expression.strip()
    if is_java_literal(key_expression):
        return f"{resource_identity}:literal:{key_expression}"

    current_node_data = parser.parse_java(str(repo_root.resolve()), current.path)
    if current_node_data is None:
        return None
    source, root = current_node_data
    method_node = _method_node(source, root, current)
    if method_node is None:
        return None
    origin = _expression_origin(source, method_node, key_expression, current.end_line)
    if origin is None:
        return None
    if origin[0] == "literal":
        value = origin[1]
        assert isinstance(value, str)
        return f"{resource_identity}:literal:{value}"
    parameter_index = origin[1]
    assert isinstance(parameter_index, int)

    for index in reversed(range(len(route))):
        target, call, _inferred = route[index]
        if target.id != current.id:
            return None
        caller = entry if index == 0 else route[index - 1][0]
        if (
            call.caller != caller.qualified_method
            or call.caller_path.replace("\\", "/").removeprefix("./")
            != caller.path.replace("\\", "/").removeprefix("./")
            or not caller.start_line <= call.call_line <= caller.end_line
        ):
            return None
        caller_data = parser.parse_java(str(repo_root.resolve()), caller.path)
        if caller_data is None:
            return None
        caller_source, caller_root = caller_data
        caller_node = _method_node(caller_source, caller_root, caller)
        if caller_node is None:
            return None
        argument = _call_argument(
            caller_root, caller_source, caller_node, call,
            target.qualified_method.rsplit(".", 1)[-1], parameter_index,
        )
        if argument is None:
            return None
        if is_java_literal(argument):
            return f"{resource_identity}:literal:{argument}"
        caller_origin = _expression_origin(
            caller_source, caller_node, argument, call.call_line,
        )
        if caller_origin is None:
            return None
        if caller_origin[0] == "literal":
            value = caller_origin[1]
            assert isinstance(value, str)
            return f"{resource_identity}:literal:{value}"
        parameter_index = caller_origin[1]
        assert isinstance(parameter_index, int)
        current = caller

    return f"{resource_identity}:parameter:{current.id}:{parameter_index}"
