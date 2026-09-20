"""Bounded, source-local evaluation of constant Java string expressions."""

from __future__ import annotations

from tree_sitter import Node

from systemlens.discovery.java import parser as java


def local_string(source: bytes, expression: Node, *, depth: int = 0,
                 parameters: dict[str, str] | None = None) -> str | None:
    """Resolve literals, lexical locals and private/static single-return helpers.

    No Java is executed. Assignments, shadowing, overloads, arbitrary receivers,
    conditional bodies and recursion beyond eight steps remain unresolved.
    """
    if depth >= 8:
        return None
    parameters = parameters or {}

    def evaluate(node: Node, bindings: dict[str, str] | None = None) -> str | None:
        return local_string(source, node, depth=depth + 1,
                            parameters=parameters if bindings is None else bindings)

    if expression.type == "string_literal":
        return java.string_value(expression, source)
    if expression.type == "parenthesized_expression" and len(expression.named_children) == 1:
        return evaluate(expression.named_children[0])
    if expression.type == "binary_expression":
        operator = expression.child_by_field_name("operator")
        left = expression.child_by_field_name("left")
        right = expression.child_by_field_name("right")
        if operator is None or operator.text != b"+" or left is None or right is None:
            return None
        a, b = evaluate(left), evaluate(right)
        return a + b if a is not None and b is not None else None
    owner = java.enclosing(expression, "class_declaration", "record_declaration")
    if owner is None:
        return None
    if expression.type == "identifier":
        name = java.node_text(source, expression)
        if name in parameters:
            return parameters[name]
        method = java.enclosing(expression, "method_declaration", "constructor_declaration")
        # A method parameter shadows every field of the same name.
        if method is not None:
            params = method.child_by_field_name("parameters")
            if params is not None and any(
                (n := p.child_by_field_name("name")) is not None and n.text == expression.text
                for p in params.named_children
            ):
                return None
        candidates = []
        for node in java.walk(owner):
            if node.type != "variable_declarator":
                continue
            if java.enclosing(node, "class_declaration", "record_declaration") != owner:
                continue
            name_node = node.child_by_field_name("name")
            if name_node is None or name_node.text != expression.text:
                continue
            declaration_method = java.enclosing(node, "method_declaration", "constructor_declaration")
            if declaration_method is not None:
                if declaration_method != method or node.start_byte >= expression.start_byte:
                    continue
                block = java.enclosing(node, "block")
                if block is None or not (block.start_byte <= expression.start_byte < block.end_byte):
                    continue
            candidates.append(node)
        if len(candidates) != 1:
            return None
        declaration = candidates[0]
        scope = method if java.enclosing(declaration, "method_declaration", "constructor_declaration") else owner
        if scope is None:
            return None
        for node in java.walk(scope):
            if node.type == "assignment_expression":
                left = node.child_by_field_name("left")
                if left is not None and java.node_text(source, left).rsplit(".", 1)[-1] == name:
                    return None
            elif node.type == "update_expression" and any(n.text == expression.text for n in node.named_children):
                return None
        value = declaration.child_by_field_name("value")
        return evaluate(value) if value is not None else None
    if expression.type == "method_invocation":
        receiver, name, arguments = java.invocation_parts(expression, source)
        if receiver is not None and receiver.type != "this":
            return None
        methods = [n for n in java.walk(owner) if n.type == "method_declaration"
                   and java.enclosing(n, "class_declaration", "record_declaration") == owner
                   and java.declaration_name(n, source) == name]
        if len(methods) != 1:
            return None
        method = methods[0]
        modifiers = java.modifiers_node(method)
        if modifiers is None or not any(n.text in {b"private", b"static"} for n in modifiers.children):
            return None
        body = method.child_by_field_name("body")
        params = method.child_by_field_name("parameters")
        return_type = method.child_by_field_name("type")
        if body is None or params is None or return_type is None or return_type.text not in {b"String", b"java.lang.String"}:
            return None
        statements = [n for n in body.named_children if n.type not in {"line_comment", "block_comment"}]
        if len(statements) != 1 or statements[0].type != "return_statement":
            return None
        if len(params.named_children) != len(arguments):
            return None
        bindings = {}
        for param, argument in zip(params.named_children, arguments, strict=True):
            param_name = param.child_by_field_name("name")
            param_type = param.child_by_field_name("type")
            value = evaluate(argument)
            if param_name is None or param_type is None or param_type.text not in {b"String", b"java.lang.String"} or value is None:
                return None
            bindings[java.node_text(source, param_name)] = value
        returned = statements[0].named_children
        return evaluate(returned[0], bindings) if len(returned) == 1 else None
    return None
