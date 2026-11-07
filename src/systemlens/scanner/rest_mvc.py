"""Inférence des endpoints REST (MVC, Feign, Spring Data REST, OpenAPI,
Gateway, RestTemplate/RestClient, WebClient, WebFlux) (BACKLOG-16).

Point d'entrée public : `infer_framework_endpoints`, l'orchestrateur qui
agrège toutes les stratégies de détection REST pour un module donné."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from collections.abc import Callable

import yaml

from systemlens.discovery.java import parser as java_parser
from systemlens.conventions.strategy1.indexing import is_openapi_declaration_path
from systemlens.discovery.build import maven as maven_module
from systemlens.domain.graph import service_hint_from_host, url_service_hint
from systemlens.domain.models import MessageEndpoint, compute_endpoint_id
from systemlens.discovery.build.modules import discover_rest_controllers, maven_module_dependencies
from systemlens.scanner._core import (
    _build_endpoint,
    _find_first_literal,
    _invocation_receiver,
    _module_for_path,
    _read_snippet,
)
from systemlens.scanner._spring_properties import (
    _load_flat_spring_properties,
    _resolve_value_annotated_variable,
    resolve_spring_property,
)
from systemlens.scanner.java_strings import local_string, locally_declared_names
from systemlens.scanner.rest_client_config import (
    _bean_api_domain,
    _domain_from_api_type_name,
    _hub_rest_api_domains,
    _is_rest_client_configuration,
    _rest_configuration_external_services,
    _trace_rest_client,
)
from systemlens.scanner.rest_inheritance import (
    INHERITED_MAPPING_MARKER,
    MAPPING_ANNOTATIONS,
    RestInheritanceIndex,
    declared_mapping_methods,
    is_concrete_controller,
    is_feign_interface,
    rest_inheritance_index,
    type_at,
)
from systemlens.scanner.rest_paths import join_paths, normalize_path, with_query_params

_PROPERTY_PLACEHOLDER_RE = re.compile(r"^\$\{([^}]+)\}$")
_EMBEDDED_PLACEHOLDER_RE = re.compile(r"\$\{([^${}]+)\}")

# BACKLOG-10 K2 (reliquat) : `@KafkaListener(topics = someVar)` ou
# `kafkaTemplate.send(someVar, ...)` où `someVar` n'est pas un littéral mais
# une variable alimentée ailleurs dans la classe par `@Value("${...}")` —
# retrouver le nom de variable en jeu (pas son contenu, absent du snippet)
# avant de la résoudre contre les champs `@Value` du fichier source.
_MAPPING_ANNOTATION_BLOCK_RE = re.compile(r"@\w+Mapping\s*(?:\((.*?)\))?", re.DOTALL)
_REQUEST_PARAM_RE = re.compile(
    r"@RequestParam\s*(?:\((.*?)\))?\s+[\w<>\[\], ?]+\s+(\w+)", re.DOTALL
)
_NON_PATH_MAPPING_ATTRS = {"method", "produces", "consumes", "headers", "params", "name"}
_NAMED_STRING_ARG_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
_OPENAPI_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_REST_TEMPLATE_CALL_RE = re.compile(
    r"\.(getForObject|getForEntity|postForObject|postForEntity|put|delete)\(\s*(.+?)\s*(?:,|\))",
    re.DOTALL,
)
_REST_TEMPLATE_EXCHANGE_RE = re.compile(
    r"\.exchange\(\s*(.+?)\s*,\s*(?:HttpMethod\.)?([A-Z]+)\s*,",
    re.DOTALL,
)
_URI_CALL_RE = re.compile(r"\.uri\s*\(")
def _mapping_args_have_only_non_path_attrs(args: str) -> bool:
    """`True` si les arguments d'une annotation `@XMapping(...)` ne portent
    aucun chemin explicite (vide, ou uniquement `method=`/`produces=`/...) —
    dans ce cas le chemin effectif de la méthode est vide (hérite du préfixe
    de classe), pas inconnu."""
    args = args.strip()
    if not args:
        return True
    for part in args.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            return False  # argument positionnel : c'est le chemin lui-même
        key = part.split("=", 1)[0].strip()
        if key not in _NON_PATH_MAPPING_ATTRS:
            return False
    return True
def _next_declaration_line(lines: list[str], start_idx: int) -> int | None:
    for idx in range(start_idx, len(lines)):
        stripped = lines[idx].strip()
        if not stripped or stripped.startswith("@"):
            continue
        return idx
    return None
def _named_string_arg(args: str, key: str) -> str | None:
    for match in _NAMED_STRING_ARG_RE.finditer(args):
        if match.group(1) == key:
            return match.group(2)
    return None
def _split_java_concat(expr: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    for ch in expr:
        if quote is not None:
            current.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            current.append(ch)
            continue
        if ch == "+":
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue
        current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


@lru_cache(maxsize=512)
def _local_static_string_initializer(path_str: str, variable_name: str) -> str | None:
    """Return a unique, never-reassigned local/field string initializer.

    This deliberately handles only a literal initializer.  A value supplied
    by a parameter, method call, or a mutable field remains dynamic.  That
    makes a local ``String baseUrl = "http://service/"`` usable as target
    evidence without turning configurable URLs into guessed dependencies.
    """
    try:
        source = Path(path_str).read_bytes()
    except OSError:
        return None
    root = java_parser.java_parser("rest_local_strings").parse(source).root_node
    if root.has_error:
        return None
    initializers: list[str] = []
    reassigned = False
    for node in java_parser.walk(root):
        if node.type == "variable_declarator":
            name_node = node.child_by_field_name("name")
            value_node = node.child_by_field_name("value")
            if (
                name_node is not None
                and java_parser.node_text(source, name_node) == variable_name
                and value_node is not None
                and value_node.type == "string_literal"
            ):
                initializers.append(java_parser.node_text(source, value_node))
        elif node.type == "assignment_expression":
            left = node.child_by_field_name("left")
            if left is None:
                continue
            target = java_parser.node_text(source, left)
            if target == variable_name or target.endswith(f".{variable_name}"):
                reassigned = True
    if reassigned or len(initializers) != 1:
        return None
    return initializers[0]
def _literal_text(part: str) -> str:
    """Content of a quoted Java string part, including a text block."""
    if len(part) >= 6 and part.startswith('"""') and part.endswith('"""'):
        return part[3:-3].strip()
    return part[1:-1]


def _resolve_rest_expression(
    expr: str, repo_root: Path, source_path: str, *, preserve_dynamic_segments: bool = False,
    shadowed: frozenset[str] = frozenset(),
) -> tuple[str, bool]:
    """Resolve literals and local ``@Value`` fields without discarding a host.

    The caller may need the explicit host as service-identity evidence even
    though the endpoint topic itself intentionally stores only the route.
    ``shadowed`` names are declared by the enclosing method (parameters and
    locals); they are never resolved by name against fields or other files.
    """
    resolved_parts: list[str] = []
    dynamic = False
    for part in _split_java_concat(expr.strip()):
        if len(part) >= 2 and part[0] == part[-1] == '"':
            literal = _literal_text(part)
            placeholder = _PROPERTY_PLACEHOLDER_RE.match(literal)
            if placeholder is not None:
                resolved = resolve_spring_property(repo_root, placeholder.group(1), source_path)
                if resolved is None or "${" in resolved:
                    dynamic = True
                    continue
                resolved_parts.append(resolved)
                continue
            if "${" in literal:
                # A placeholder embedded in a longer literal: resolve each
                # key; an unresolved one keeps the literal dynamic.
                unresolved = False

                def substitute(match: re.Match[str]) -> str:
                    nonlocal unresolved
                    value = resolve_spring_property(repo_root, match.group(1), source_path)
                    if value is None or "${" in value:
                        unresolved = True
                        return "{dynamic}" if preserve_dynamic_segments else ""
                    return value

                literal = _EMBEDDED_PLACEHOLDER_RE.sub(substitute, literal)
                if unresolved or "${" in literal:
                    dynamic = True
            resolved_parts.append(literal)
            continue
        if re.fullmatch(r"[A-Za-z_]\w*", part):
            if part in shadowed:
                dynamic = True
                if preserve_dynamic_segments and resolved_parts:
                    resolved_parts.append(f"{{{part}}}")
                continue
            resolved = _resolve_value_annotated_variable(repo_root, source_path, part)
            if resolved is not None and "${" in resolved:
                # The property value is itself configuration-dependent.
                resolved = None
            if resolved is None:
                initializer = _local_static_string_initializer(
                    str(repo_root / source_path), part
                )
                if initializer is not None:
                    resolved = initializer[1:-1]
            if resolved is None:
                dynamic = True
                if preserve_dynamic_segments and resolved_parts:
                    resolved_parts.append(f"{{{part}}}")
                continue
            resolved_parts.append(resolved)
            continue
        dynamic = True
        if preserve_dynamic_segments and resolved_parts:
            resolved_parts.append("{dynamic}")
    raw = "".join(resolved_parts).strip()
    if not raw:
        return "<dynamic>", True
    return raw, dynamic


def _resolve_rest_path_expression(
    expr: str, repo_root: Path, source_path: str, *, preserve_dynamic_segments: bool = False,
    shadowed: frozenset[str] = frozenset(),
) -> tuple[str, bool]:
    raw, dynamic = _resolve_rest_expression(
        expr, repo_root, source_path, preserve_dynamic_segments=preserve_dynamic_segments,
        shadowed=shadowed,
    )
    if raw == "<dynamic>":
        return raw, True
    return normalize_path(raw), dynamic


def _resolved_http_host(
    expr: str, repo_root: Path, source_path: str, shadowed: frozenset[str] = frozenset(),
) -> str | None:
    raw, _dynamic = _resolve_rest_expression(expr, repo_root, source_path, shadowed=shadowed)
    return url_service_hint(raw)


def _resolve_rest_ast_expression(
    expression, source: bytes, repo_root: Path, source_path: str
) -> tuple[str, bool]:
    """Resolve a bounded Java string expression before text fallback.

    The AST resolver handles a literal, a uniquely initialized local and a
    private deterministic helper with literal arguments. Anything it cannot
    prove remains subject to the existing conservative expression resolver,
    which never resolves a parameter or local of the enclosing method by
    name: the AST resolver already rejected it.
    """
    resolved = local_string(source, expression)
    if resolved is not None:
        return normalize_path(resolved), False
    return _resolve_rest_path_expression(
        java_parser.node_text(source, expression), repo_root, source_path,
        preserve_dynamic_segments=True,
        shadowed=locally_declared_names(expression, source),
    )


def _resolved_http_host_ast(expression, source: bytes, repo_root: Path, source_path: str) -> str | None:
    resolved = local_string(source, expression)
    if resolved is not None:
        return url_service_hint(resolved)
    return _resolved_http_host(
        java_parser.node_text(source, expression), repo_root, source_path,
        shadowed=locally_declared_names(expression, source),
    )


def _infer_http_interface_endpoints(repo_root: Path, rel_path: str) -> list[MessageEndpoint]:
    """Extract Spring HTTP interface contracts (`@HttpExchange`)."""
    parsed = java_parser.parse_java(str(repo_root), rel_path)
    if parsed is None:
        return []
    source, root = parsed
    endpoints: list[MessageEndpoint] = []
    methods = {
        "GetExchange": "GET", "PostExchange": "POST", "PutExchange": "PUT",
        "DeleteExchange": "DELETE", "PatchExchange": "PATCH",
    }
    for owner in java_parser.type_declarations(root):
        exchange = next((a for a in java_parser.annotations_of(owner)
                         if java_parser.annotation_name(a, source) == "HttpExchange"), None)
        registration = next((a for a in java_parser.annotations_of(owner)
                             if java_parser.annotation_name(a, source) == "ClientRegistrationId"), None)
        target_node = java_parser.annotation_argument(registration, source) if registration else None
        target = java_parser.string_value(target_node, source) if target_node is not None else None
        # A type-level ``@HttpExchange`` is optional: method-level exchange
        # annotations alone declare a contract with an empty prefix.
        prefix, prefix_dynamic = (
            _ast_mapping_value(exchange, source, repo_root, rel_path, keys=("url", "path", "value"))
            if exchange is not None else ("", False)
        )
        for method in java_parser.walk(owner):
            if method.type != "method_declaration":
                continue
            annotation = next((a for a in java_parser.annotations_of(method)
                               if java_parser.annotation_name(a, source) in methods), None)
            if annotation is None:
                continue
            suffix, suffix_dynamic = _ast_mapping_value(
                annotation, source, repo_root, rel_path, keys=("url", "path", "value"),
            )
            dynamic = prefix_dynamic or suffix_dynamic
            route = join_paths(normalize_path(prefix), normalize_path(suffix))
            if dynamic and (prefix_dynamic or suffix_dynamic):
                route = "<dynamic>"
            snippet = java_parser.node_text(source, method)
            if target is not None:
                snippet += f"\n// systemlens-api-domain:{target}"
            endpoints.append(_build_endpoint(
                repo_root, rel_path, annotation.start_point.row + 1,
                method.end_point.row + 1, "call", "rest",
                f"{methods[java_parser.annotation_name(annotation, source)]} {route}",
                "http-interface", snippet, topic_dynamic=dynamic,
            ))
    return endpoints


@lru_cache(maxsize=1024)
def _class_base_path(repo_root_str: str, source_path: str, start_line: int) -> tuple[str, bool]:
    """Chemin `@RequestMapping` de la classe/interface qui englobe la
    méthode trouvée à `start_line` — renvoie (préfixe, dynamique) ;
    (`""`, `False`) si aucune classe englobante ou aucun `@RequestMapping`
    de classe (rien à préfixer, pas une valeur inconnue)."""
    parsed = java_parser.parse_java(repo_root_str, source_path)
    if parsed is None:
        return "", False
    source, root = parsed
    method = next(
        (
            node for node in java_parser.walk(root)
            if node.type == "method_declaration"
            and any(
                ann.start_point.row + 1 <= start_line <= node.end_point.row + 1
                for ann in java_parser.annotations_of(node)
            )
        ),
        None,
    )
    if method is None:
        return "", False
    owner = java_parser.enclosing(
        method, "class_declaration", "interface_declaration", "record_declaration", "enum_declaration"
    )
    if owner is None:
        return "", False
    mapping = next(
        (ann for ann in java_parser.annotations_of(owner)
         if java_parser.annotation_name(ann, source) == "RequestMapping"),
        None,
    )
    if mapping is not None:
        return _ast_mapping_value(mapping, source, Path(repo_root_str), source_path)
    feign = next(
        (ann for ann in java_parser.annotations_of(owner)
         if java_parser.annotation_name(ann, source) == "FeignClient"),
        None,
    )
    if feign is not None:
        return _ast_feign_base(feign, source, Path(repo_root_str), source_path)
    return "", False
def _extract_rest_path(
    snippet: str,
    repo_root: Path | None = None,
    source_path: str | None = None,
    start_line: int | None = None,
) -> tuple[str, bool]:
    """Renvoie (chemin, dynamique) — jamais résolu silencieusement (même
    esprit que `topic_dynamic` en K2). Fusionne le préfixe `@RequestMapping`
    de la classe englobante (Q24) quand `repo_root`/`source_path`/
    `start_line` sont fournis."""
    prefix, prefix_dynamic = "", False
    if repo_root is not None and source_path is not None and start_line is not None:
        prefix, prefix_dynamic = _class_base_path(str(repo_root), source_path, start_line)

    lines = snippet.splitlines()
    decl_idx = _next_declaration_line(lines, 0)
    annotation_block = "\n".join(lines[:decl_idx]) if decl_idx is not None else snippet
    match = _MAPPING_ANNOTATION_BLOCK_RE.search(annotation_block)
    mapping_args = (match.group(1) or "") if match is not None else None

    if mapping_args is not None:
        literal, method_dynamic = _find_first_literal(mapping_args)
        if literal is None:
            if not _mapping_args_have_only_non_path_attrs(mapping_args or ""):
                return "<dynamic>", True
            literal, method_dynamic = "", False
    else:
        literal, method_dynamic = _find_first_literal(snippet)
        if literal is None:
            return "<dynamic>", True

    if prefix_dynamic:
        return "<dynamic>", True

    method_path = normalize_path(literal) if literal else "/"
    route = method_path if not prefix else join_paths(normalize_path(prefix), method_path)
    query_params = _extract_request_param_names(snippet)
    if query_params:
        route = with_query_params(route, query_params)
    return route, method_dynamic
def _extract_request_param_names(snippet: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for match in _REQUEST_PARAM_RE.finditer(snippet):
        args = match.group(1) or ""
        name = (
            _named_string_arg(args, "name")
            or _named_string_arg(args, "value")
            or _find_first_literal(args)[0]
            or match.group(2)
        )
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names
def _infer_generic_request_mapping_endpoints(repo_root: Path, rel_path: str) -> list[MessageEndpoint]:
    """Infère les annotations de routes Spring et Feign depuis l'AST Java.

    Les anciens parcours ligne par ligne échouaient dès qu'une annotation ou
    une signature était répartie sur plusieurs lignes, ou qu'un commentaire
    imitait une annotation. Ici Tree-sitter associe directement l'annotation
    à sa ``method_declaration``.

    BL-04: a mapping declared on an interface or a base class belongs to the
    controller or Feign client that inherits it (see
    :func:`_inherited_mapping_endpoints`). The declaring type itself keeps
    its mappings only when no inheritor is indexed.
    """
    parsed = java_parser.parse_java(str(repo_root), rel_path)
    if parsed is None:
        return []
    source, root = parsed
    inheritance = rest_inheritance_index(str(repo_root))
    endpoints: list[MessageEndpoint] = []
    for method in java_parser.walk(root):
        if method.type != "method_declaration":
            continue
        owner = java_parser.enclosing(
            method, "class_declaration", "interface_declaration", "record_declaration", "enum_declaration"
        )
        if owner is None:
            continue
        if (
            (rel_path, owner.start_byte) in inheritance.inheritors
            and not is_concrete_controller(owner, source)
            and not is_feign_interface(owner, source)
        ):
            # Its inheritors serve or call these mappings.
            continue
        feign = next(
            (ann for ann in java_parser.annotations_of(owner)
             if java_parser.annotation_name(ann, source) == "FeignClient"),
            None,
        )
        feign_target = (
            _ast_feign_target(feign, source, repo_root, rel_path) if feign is not None else None
        )
        for annotation in java_parser.annotations_of(method):
            methods = _ast_mapping_methods(annotation, source)
            if not methods:
                continue
            route, dynamic = _ast_mapping_route(
                annotation, method, source, repo_root, rel_path, feign
            )
            snippet = java_parser.node_text(source, method)
            if feign_target is not None:
                snippet += f"\n// systemlens-api-domain:{feign_target}"
            for http_method in methods:
                endpoints.append(
                    _build_endpoint(
                        repo_root, rel_path, annotation.start_point.row + 1,
                        method.end_point.row + 1,
                        "call" if feign is not None else "serve", "rest",
                        f"{http_method} {route}", "feign" if feign is not None else "spring",
                        snippet, topic_dynamic=dynamic,
                    )
                )
    for declaration in java_parser.type_declarations(root):
        if (rel_path, declaration.start_byte) in inheritance.declaring:
            endpoints.extend(_inherited_mapping_endpoints(
                repo_root, rel_path, source, declaration, inheritance,
            ))
    return endpoints


def _method_arity(method) -> int:
    parameters = method.child_by_field_name("parameters")
    if parameters is None:
        return 0
    return sum(
        1 for child in parameters.named_children
        if child.type in {"formal_parameter", "spread_parameter"}
    )


def _method_key(method, source: bytes) -> tuple[str | None, int]:
    return java_parser.declaration_name(method, source), _method_arity(method)


def _type_request_mapping(declaration, source: bytes):
    return next(
        (ann for ann in java_parser.annotations_of(declaration)
         if java_parser.annotation_name(ann, source) == "RequestMapping"),
        None,
    )


def _inherited_class_prefix(
    repo_root: Path, rel_path: str, owner_key: tuple[str, int],
    inheritance: RestInheritanceIndex,
) -> tuple[str, bool]:
    """Type-level ``@RequestMapping`` a controller inherits, if any.

    Spring searches the interfaces, then the superclass, nearest first; the
    first type-level mapping wins. Placeholders resolve in the inheritor's
    module, where the application runs.
    """
    for super_key in inheritance.supertypes.get(owner_key, ()):
        found = type_at(repo_root, super_key)
        if found is None:
            continue
        super_source, super_node = found
        mapping = _type_request_mapping(super_node, super_source)
        if mapping is not None:
            return _ast_mapping_value(mapping, super_source, repo_root, rel_path)
    return "", False


def _inherited_mapping_endpoints(
    repo_root: Path, rel_path: str, source: bytes, declaration,
    inheritance: RestInheritanceIndex,
) -> list[MessageEndpoint]:
    """Mappings a controller or Feign client inherits from its supertypes.

    A method the inheritor declares with its own mapping is extracted as its
    own; the nearest supertype declaring a name and arity wins over a farther
    one. The endpoint sits in the inheritor's file, on its overriding method
    when it has one, else on its type declaration's first line. The snippet
    is the declaring method with an ``INHERITED_MAPPING_MARKER`` naming the
    declaring file, which incremental indexing reads back.
    """
    owner_key = (rel_path, declaration.start_byte)
    feign = next(
        (ann for ann in java_parser.annotations_of(declaration)
         if java_parser.annotation_name(ann, source) == "FeignClient"),
        None,
    )
    body = declaration.child_by_field_name("body")
    own_methods = {
        _method_key(member, source): member
        for member in (body.named_children if body is not None else [])
        if member.type == "method_declaration"
    }
    seen = {
        key for key, member in own_methods.items()
        if any(
            java_parser.annotation_name(annotation, source) in MAPPING_ANNOTATIONS
            for annotation in java_parser.annotations_of(member)
        )
    }
    feign_target = None
    if feign is not None:
        base, base_dynamic = _ast_feign_base(feign, source, repo_root, rel_path)
        feign_target = _ast_feign_target(feign, source, repo_root, rel_path)
    else:
        own_mapping = _type_request_mapping(declaration, source)
        base, base_dynamic = (
            _ast_mapping_value(own_mapping, source, repo_root, rel_path)
            if own_mapping is not None
            else _inherited_class_prefix(repo_root, rel_path, owner_key, inheritance)
        )
    endpoints: list[MessageEndpoint] = []
    for super_key in inheritance.declaring.get(owner_key, ()):
        found = type_at(repo_root, super_key)
        if found is None:
            continue
        super_source, super_node = found
        prefix, prefix_dynamic = base, base_dynamic
        super_mapping = _type_request_mapping(super_node, super_source)
        if feign is not None and super_mapping is not None:
            # Feign applies the API interface's own type-level mapping
            # after the client's ``url`` or ``path``.
            super_prefix, super_dynamic = _ast_mapping_value(
                super_mapping, super_source, repo_root, rel_path,
            )
            prefix = join_paths(normalize_path(prefix), normalize_path(super_prefix))
            prefix_dynamic = prefix_dynamic or super_dynamic
        for method in declared_mapping_methods(super_node, super_source):
            key = _method_key(method, super_source)
            if key in seen:
                continue
            seen.add(key)
            override = own_methods.get(key)
            anchor = override if override is not None else declaration
            snippet = (
                f"{java_parser.node_text(super_source, method)}\n"
                f"// {INHERITED_MAPPING_MARKER}{super_key[0]}"
            )
            if feign_target is not None:
                snippet += f"\n// systemlens-api-domain:{feign_target}"
            start_line = anchor.start_point.row + 1
            end_line = anchor.end_point.row + 1 if override is not None else start_line
            for annotation in java_parser.annotations_of(method):
                http_methods = _ast_mapping_methods(annotation, super_source)
                if not http_methods:
                    continue
                path, dynamic = _ast_mapping_value(annotation, super_source, repo_root, rel_path)
                if dynamic or prefix_dynamic:
                    route, dynamic = "<dynamic>", True
                else:
                    route = with_query_params(
                        join_paths(normalize_path(prefix), normalize_path(path)),
                        _ast_request_param_names(method, super_source),
                    )
                for http_method in http_methods:
                    endpoints.append(_build_endpoint(
                        repo_root, rel_path, start_line, end_line,
                        "call" if feign is not None else "serve", "rest",
                        f"{http_method} {route}", "feign" if feign is not None else "spring",
                        snippet, topic_dynamic=dynamic,
                    ))
    return endpoints


def _ast_mapping_value(
    annotation, source: bytes, repo_root: Path | None = None, rel_path: str | None = None,
    keys: tuple[str, ...] = ("path", "value"),
) -> tuple[str, bool]:
    """Return a mapping annotation's explicit path, if it has one.

    An annotation with only non-path attributes has the meaningful empty path
    (it inherits its class route); an expression that cannot be resolved is
    dynamic. An array with one literal is that literal; several candidate
    routes stay dynamic rather than choosing one.
    """
    value = next(
        (
            argument for key in keys
            if (argument := java_parser.annotation_argument(annotation, source, key=key)) is not None
        ),
        None,
    ) or java_parser.annotation_argument(annotation, source)
    if value is None:
        return "", False
    if value.type == "element_value_array_initializer":
        elements = [
            child for child in value.children
            if child.type not in {"{", "}", ",", "line_comment", "block_comment"}
        ]
        if len(elements) == 1:
            value = elements[0]
    if value.type != "string_literal":
        return "", True
    if repo_root is not None and rel_path is not None:
        return _resolve_rest_path_expression(
            java_parser.node_text(source, value), repo_root, rel_path, preserve_dynamic_segments=True
        )
    literal = java_parser.string_value(value, source)
    return literal or "", False
def _ast_mapping_methods(annotation, source: bytes) -> list[str]:
    """HTTP methods represented by a Spring mapping annotation."""
    name = java_parser.annotation_name(annotation, source)
    dedicated = {
        "GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
        "DeleteMapping": "DELETE", "PatchMapping": "PATCH",
    }
    if name in dedicated:
        return [dedicated[name]]
    if name != "RequestMapping":
        return []
    value = java_parser.annotation_argument(annotation, source, key="method")
    if value is None:
        return ["ANY"]
    methods: list[str] = []
    for node in java_parser.walk(value):
        if node.type in {"identifier", "scoped_identifier", "field_access"}:
            candidate = java_parser.node_text(source, node).rsplit(".", 1)[-1]
            if candidate in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}:
                methods.append(candidate)
    return list(dict.fromkeys(methods))
def _ast_mapping_route(
    annotation, method, source: bytes, repo_root: Path, rel_path: str, feign=None
) -> tuple[str, bool]:
    path, dynamic = _ast_mapping_value(annotation, source, repo_root, rel_path)
    owner = java_parser.enclosing(
        method, "class_declaration", "interface_declaration", "record_declaration", "enum_declaration"
    )
    prefix, prefix_dynamic = "", False
    if owner is not None:
        class_mapping = next(
            (
                ann for ann in java_parser.annotations_of(owner)
                if java_parser.annotation_name(ann, source) == "RequestMapping"
            ),
            None,
        )
        if class_mapping is not None:
            prefix, prefix_dynamic = _ast_mapping_value(class_mapping, source, repo_root, rel_path)
        elif feign is not None:
            prefix, prefix_dynamic = _ast_feign_base(feign, source, repo_root, rel_path)
        elif is_concrete_controller(owner, source):
            # BL-04: a controller without its own type-level mapping
            # inherits the one of its interfaces or superclass.
            prefix, prefix_dynamic = _inherited_class_prefix(
                repo_root, rel_path, (rel_path, owner.start_byte),
                rest_inheritance_index(str(repo_root)),
            )
    if dynamic or prefix_dynamic:
        return "<dynamic>", True
    route = join_paths(normalize_path(prefix), normalize_path(path))
    params = _ast_request_param_names(method, source)
    return with_query_params(route, params), False
def _ast_feign_base(annotation, source: bytes, repo_root: Path, rel_path: str) -> tuple[str, bool]:
    """Resolve Feign's optional URL/path base without treating ``name`` as a route."""
    value = (
        java_parser.annotation_argument(annotation, source, key="url")
        or java_parser.annotation_argument(annotation, source, key="path")
    )
    if value is None:
        return "", False
    if value.type != "string_literal":
        return "", True
    return _resolve_rest_path_expression(
        java_parser.node_text(source, value), repo_root, rel_path, preserve_dynamic_segments=True
    )
def _ast_feign_target(annotation, source: bytes, repo_root: Path, rel_path: str) -> str | None:
    """Service a ``@FeignClient`` names, or ``None`` (BL-02).

    An explicit ``url`` is the only target evidence when present: its host
    names the service when it is internal-shaped, and the client name is then
    never used. Without ``url``, the ``name`` or ``value`` attribute is the
    load-balanced service id. A literal, a constant or a resolvable property
    placeholder qualifies; it must be one internal-shaped host name. Anything
    unresolved or multi-label gives no target.
    """
    url = java_parser.annotation_argument(annotation, source, key="url")
    if url is not None:
        raw, dynamic = _resolve_rest_expression(
            java_parser.node_text(source, url), repo_root, rel_path
        )
        return None if dynamic else url_service_hint(raw)
    value = (
        java_parser.annotation_argument(annotation, source, key="name")
        or java_parser.annotation_argument(annotation, source, key="value")
        or java_parser.annotation_argument(annotation, source)
    )
    if value is None:
        return None
    raw, dynamic = _resolve_rest_expression(
        java_parser.node_text(source, value), repo_root, rel_path
    )
    if dynamic:
        return None
    if "://" in raw:
        return url_service_hint(raw)
    hint = service_hint_from_host(raw)
    return hint if hint is not None and "." not in hint else None


def _ast_request_param_names(method, source: bytes) -> list[str]:
    names: list[str] = []
    params = method.child_by_field_name("parameters")
    if params is None:
        return names
    for parameter in params.children:
        if parameter.type != "formal_parameter":
            continue
        annotation = next(
            (
                ann for ann in java_parser.annotations_of(parameter)
                if java_parser.annotation_name(ann, source) == "RequestParam"
            ),
            None,
        )
        if annotation is None:
            continue
        value = (
            java_parser.annotation_argument(annotation, source, key="name")
            or java_parser.annotation_argument(annotation, source, key="value")
            or java_parser.annotation_argument(annotation, source)
        )
        name = java_parser.string_value(value, source)
        if name is None:
            name_node = parameter.child_by_field_name("name")
            name = java_parser.node_text(source, name_node) if name_node is not None else None
        if name and name not in names:
            names.append(name)
    return names
def _simple_type_name(qualified: str) -> str:
    """`a.b.Foo<Bar>` -> `Foo`."""
    cleaned = qualified.strip().split("<", 1)[0]
    return cleaned.rsplit(".", 1)[-1]
def _pluralize(word: str) -> str:
    """Regular English plural; Spring Data REST also knows irregular words."""
    word = word.strip()
    if not word:
        return word
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    if word.endswith("y") and len(word) > 1 and word[-2].lower() not in "aeiou":
        return word[:-1] + "ies"
    return word + "s"


@lru_cache(maxsize=256)
def _module_has_spring_data_rest(repo_root_str: str, rel_path: str) -> bool:
    """True si le module Maven contenant ce fichier déclare data-rest.

    Spring Data REST n'auto-expose les repositories qu'avec la dépendance
    `spring-boot-starter-data-rest`. Ce portillon évite les faux positifs sur
    les modules JPA purs (ex. `invoicing` ici, dont `InvoiceRepository` n'est
    pas exposé). On remonte au `pom.xml` du module le plus proche.
    """
    repo_root = Path(repo_root_str)
    current = (repo_root / rel_path).parent
    pom: Path | None = None
    while True:
        candidate = current / "pom.xml"
        if candidate.exists():
            pom = candidate
            break
        if current == repo_root or current.parent == current:
            break
        current = current.parent
    if pom is None:
        return False
    return any(
        "data-rest" in dependency.artifact_id for dependency in maven_module_dependencies(pom)
    )
def _infer_spring_data_rest_endpoints(repo_root: Path, rel_path: str) -> list[MessageEndpoint]:
    """Inventorie les endpoints Spring Data REST d'un repository.

    Deux cas :
    - `@RepositoryRestResource(path = "...")` : chemin explicite (l'annotation
      implique la présence de data-rest, pas de portillon classpath) ;
    - interface Spring Data sans `exported=false` et sans `path` : chemin par
      défaut `/<entité-pluriel>`, uniquement si le module déclare data-rest.
      C'est le cas d'un `UserRepository extends JpaRepository<User, ...>` sans
      annotation, qu'aucun littéral de chemin ne permettait jusque-là de lier.
    """
    parsed = java_parser.parse_java(str(repo_root), rel_path)
    if parsed is None:
        return []
    source, root = parsed
    endpoints: list[MessageEndpoint] = []
    for declaration in java_parser.type_declarations(root):
        if declaration.type != "interface_declaration":
            continue
        entity = _repository_entity_type(declaration, source)
        if entity is None:
            continue
        annotation = next(
            (
                ann for ann in java_parser.annotations_of(declaration)
                if java_parser.annotation_name(ann, source) == "RepositoryRestResource"
            ),
            None,
        )
        exported = java_parser.annotation_argument(annotation, source, key="exported") if annotation else None
        if exported is not None and java_parser.node_text(source, exported) == "false":
            continue
        rest_path = java_parser.string_value(
            java_parser.annotation_argument(annotation, source, key="path") if annotation else None,
            source,
        )
        if rest_path is not None:
            assert annotation is not None
            base_path = normalize_path(rest_path)
            snippet = java_parser.node_text(source, annotation)
            decl_line = annotation.start_point.row + 1
            dynamic = False
        else:
            if not _module_has_spring_data_rest(str(repo_root), rel_path):
                continue
            # Spring Data REST uncapitalizes the entity name: ``OrderItem``
            # is served at ``/orderItems``. Its plural comes from an
            # inflector with irregular words (``Child`` at ``/children``)
            # that the regular rules below do not know: the route keeps
            # the regular spelling and stays dynamic (PDR-12).
            base_path = "/" + _pluralize(entity[:1].lower() + entity[1:])
            snippet = java_parser.node_text(source, declaration)
            decl_line = declaration.start_point.row + 1
            dynamic = True

        for topic in (
            f"GET {base_path}",
            f"POST {base_path}",
            f"GET {base_path}/{{id}}",
            f"PUT {base_path}/{{id}}",
            f"PATCH {base_path}/{{id}}",
            f"DELETE {base_path}/{{id}}",
        ):
            endpoints.append(
                _build_endpoint(
                    repo_root,
                    rel_path,
                    decl_line,
                    decl_line,
                    "serve",
                    "rest",
                    topic,
                    "spring-data-rest",
                    snippet,
                    topic_dynamic=dynamic,
                )
            )
        endpoints.extend(
            _infer_spring_data_rest_search_endpoints(
                repo_root, rel_path, source, declaration, base_path, dynamic,
            )
        )
    return endpoints
def _infer_spring_data_rest_search_endpoints(
    repo_root: Path, rel_path: str, source: bytes, declaration, base_path: str,
    dynamic: bool = False,
) -> list[MessageEndpoint]:
    """Ressources de recherche Spring Data REST (`GET <base>/search/<méthode>`).

    Toute méthode de requête déclarée directement sur le repository (ex.
    `@Query`, dérivée `findBy...`) est exposée par défaut sous
    `/search/<nom-de-méthode>`, sauf `@RestResource(exported = false)`. Le nom
    de recherche est le nom de méthode Java, sauf `@RestResource(path = ...)`
    explicite.
    """
    body = java_parser.child_by_type(declaration, "interface_body")
    if body is None:
        return []
    endpoints: list[MessageEndpoint] = []
    for method in body.children:
        if method.type != "method_declaration":
            continue
        rest_resource = next(
            (
                ann for ann in java_parser.annotations_of(method)
                if java_parser.annotation_name(ann, source) == "RestResource"
            ),
            None,
        )
        exported = (
            java_parser.annotation_argument(rest_resource, source, key="exported")
            if rest_resource is not None
            else None
        )
        if exported is not None and java_parser.node_text(source, exported) == "false":
            continue
        search_name = java_parser.string_value(
            java_parser.annotation_argument(rest_resource, source, key="path")
            if rest_resource is not None
            else None,
            source,
        )
        if search_name is None:
            search_name = java_parser.declaration_name(method, source)
        if not search_name:
            continue
        decl_line = method.start_point.row + 1
        endpoints.append(
            _build_endpoint(
                repo_root,
                rel_path,
                decl_line,
                method.end_point.row + 1,
                "serve",
                "rest",
                f"GET {base_path}/search/{search_name}",
                "spring-data-rest",
                java_parser.node_text(source, method),
                topic_dynamic=dynamic,
            )
        )
    return endpoints
def _repository_entity_type(declaration, source: bytes) -> str | None:
    """Entity argument of a Spring Data repository interface, via AST types."""
    for node in java_parser.walk(declaration):
        if node.type != "generic_type":
            continue
        type_node = next(
            (child for child in node.children if child.type in {"type_identifier", "scoped_type_identifier"}),
            None,
        )
        type_name = java_parser.node_text(source, type_node) if type_node is not None else ""
        if not type_name.endswith("Repository"):
            continue
        arguments = next((child for child in node.children if child.type == "type_arguments"), None)
        if arguments is None:
            continue
        for child in arguments.children:
            if child.type not in {"<", ",", ">"}:
                return _simple_type_name(java_parser.node_text(source, child))
    return None
def _infer_swagger_endpoint(repo_root: Path, rel_path: str) -> list[MessageEndpoint]:
    parsed = java_parser.parse_java(str(repo_root), rel_path)
    if parsed is None:
        return []
    source, root = parsed
    for declaration in java_parser.type_declarations(root):
        annotation = next(
            (
                ann for ann in java_parser.annotations_of(declaration)
                if java_parser.annotation_name(ann, source) == "EnableSwagger2"
            ),
            None,
        )
        if annotation is not None:
            return [
                _build_endpoint(
                    repo_root,
                    rel_path,
                    annotation.start_point.row + 1,
                    annotation.end_point.row + 1,
                    "serve",
                    "rest",
                    "GET /swagger-ui.html",
                    "swagger-ui",
                    java_parser.node_text(source, annotation),
                )
            ]
    return []


@lru_cache(maxsize=64)
def _openapi_generator_contract_owners(repo_root_str: str) -> dict[str, str | None]:
    """Map each plugin-referenced contract path to its implementing module.

    The contract file frequently lives in a different (often shared
    ``model-*``) Maven module than the one whose ``pom.xml`` configures
    ``openapi-generator-maven-plugin`` and whose ``@RestController``s
    actually serve it. Scanning the contract file directly (as a plain
    ``*.yaml``/``*.json`` candidate) must still attribute its endpoints to
    that implementing module, not to whichever module physically encloses
    the file, or the operations silently vanish under a non-runtime
    library module that is never rendered as a service.
    """
    repo_root = Path(repo_root_str).resolve()
    owners: dict[str, str | None] = {}
    for pom_path in sorted(repo_root.rglob("pom.xml")):
        if {"target", "build"}.intersection(pom_path.relative_to(repo_root).parts):
            continue
        try:
            module_dir = pom_path.parent
            if not discover_rest_controllers(module_dir, set()):
                continue
            implementing_module = _module_for_path(
                repo_root, pom_path.relative_to(repo_root).as_posix()
            )
            for module_relative in maven_module.detect_openapi_generator_input_specs(pom_path):
                contract_rel_path = (
                    (module_dir / module_relative).resolve().relative_to(repo_root).as_posix()
                )
                owners[contract_rel_path] = implementing_module
        except ValueError:
            continue
    return owners
def _openapi_generator_contract_paths(repo_root_str: str) -> tuple[str, ...]:
    return tuple(sorted(_openapi_generator_contract_owners(repo_root_str)))
def _is_strategy1_openapi_declaration_path(rel_path: str) -> bool:
    """Compatibility façade for the moved Strategy1 declaration rule."""
    return is_openapi_declaration_path(rel_path)
def _is_openapi_contract_path(repo_root: Path, rel_path: str) -> bool:
    path = repo_root / rel_path
    if "openapi" in {part.casefold() for part in path.parts}:
        return True
    return path.name in {
        "openapi.yaml", "openapi.yml", "openapi.json",
        "swagger.yaml", "swagger.yml", "swagger.json",
    } or rel_path in _openapi_generator_contract_paths(str(repo_root))
def _infer_openapi_generator_endpoints(repo_root: Path, rel_path: str) -> list[MessageEndpoint]:
    """Inventory operations from a plugin-generated contract, attributed to
    the implementing service even when the contract file itself physically
    lives in a different (often shared ``model-*``) Maven module.

    Grouping endpoints by ``module`` (``graph.group_endpoints_by_module``)
    would otherwise silently move these operations onto the contract's own
    module -- which is frequently a non-runtime library never rendered as a
    service -- making them vanish instead of showing under the service that
    actually serves them.
    """
    path = repo_root / rel_path
    if path.name != "pom.xml":
        return []
    if not discover_rest_controllers(path.parent, set()):
        return []
    implementing_module = _module_for_path(repo_root, rel_path)
    endpoints: list[MessageEndpoint] = []
    for module_relative in maven_module.detect_openapi_generator_input_specs(path):
        try:
            contract_rel_path = (
                (path.parent / module_relative).resolve().relative_to(repo_root.resolve()).as_posix()
            )
        except ValueError:
            continue
        endpoints.extend(
            replace(endpoint, module=implementing_module) if endpoint.module != implementing_module else endpoint
            for endpoint in _infer_openapi_endpoints(repo_root, contract_rel_path)
        )
    return endpoints
def _infer_openapi_endpoints(
    repo_root: Path, rel_path: str, *, force_contract: bool = False
) -> list[MessageEndpoint]:
    """Inventory literal operations declared by a production OpenAPI contract.

    Some Spring projects generate their controller interfaces from OpenAPI and
    only implement those interfaces in ``src/main``.  In that layout there is
    no method-level Spring annotation to scan in the checked-in Java sources,
    while the contract remains the authoritative local evidence.  Keep the
    contract file and the operation line as evidence rather than attributing
    the route to an implementation method that does not declare it.
    """
    path = repo_root / rel_path
    if not force_contract and not _is_openapi_contract_path(repo_root, rel_path):
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        document = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    except (OSError, ValueError, yaml.YAMLError):
        return []
    if not isinstance(document, dict) or not isinstance(document.get("paths"), dict):
        return []

    endpoints: list[MessageEndpoint] = []
    lines = text.splitlines()
    for raw_route, operations in document["paths"].items():
        if not isinstance(raw_route, str) or not isinstance(operations, dict):
            continue
        route = normalize_path(raw_route)
        route_line = next(
            (
                index + 1
                for index, line in enumerate(lines)
                if line.lstrip().startswith(f"{raw_route}:")
                or line.lstrip().startswith(f'"{raw_route}":')
            ),
            1,
        )
        for raw_method in operations:
            method = str(raw_method).lower()
            if method not in _OPENAPI_HTTP_METHODS:
                continue
            method_line = next(
                (
                    index + 1
                    for index in range(route_line, len(lines))
                    if lines[index].strip() in (f"{method}:", f'"{method}":')
                ),
                route_line,
            )
            snippet = _read_snippet(repo_root, rel_path, method_line, method_line)
            endpoints.append(
                _build_endpoint(
                    repo_root, rel_path, method_line, method_line, "serve", "rest",
                    f"{method.upper()} {route}", "openapi", snippet,
                )
            )
    return endpoints
def _infer_openapi_endpoints_attributed(repo_root: Path, rel_path: str) -> list[MessageEndpoint]:
    """Like `_infer_openapi_endpoints`, reattributed to the module that
    configures the contract via the generator plugin when it differs from
    the module that physically encloses the contract file.

    A contract file discovered by direct file scan (as a plain ``*.yaml``/
    ``*.json`` candidate, not through the referencing ``pom.xml``) would
    otherwise keep ``_build_endpoint``'s default module attribution -- the
    contract's own enclosing module -- even when a *different* module's
    ``openapi-generator-maven-plugin`` configuration is what actually
    serves it. That silently moves the endpoints onto a module that is
    frequently a non-runtime shared library, never rendered as a service.
    """
    endpoints = _infer_openapi_endpoints(repo_root, rel_path)
    if not endpoints:
        return endpoints
    try:
        resolved_rel_path = (
            (repo_root / rel_path).resolve().relative_to(repo_root.resolve()).as_posix()
        )
    except ValueError:
        return endpoints
    owner = _openapi_generator_contract_owners(str(repo_root.resolve())).get(resolved_rel_path)
    if owner is None:
        return endpoints
    return [
        replace(endpoint, module=owner) if endpoint.module != owner else endpoint
        for endpoint in endpoints
    ]
@lru_cache(maxsize=256)
def _strategy1_openapi_contracts(repo_root_str: str, api_name: str) -> tuple[str, ...]:
    """Find OpenAPI contracts published by a Strategy1 declaration.

    A Strategy1 ``*.rest`` file is a publication declaration, not the
    contract itself. A matching ``<api>.yaml``/``.yml``/``.json`` document
    may live anywhere in the repository. A ``model-<api>`` module is a
    broader, explicit convention: every YAML or JSON document below its
    ``src/main/resources/openapi`` directory is a candidate. This lets a
    single published API include several independently named contracts.
    Callers still parse each candidate as OpenAPI before publishing an
    endpoint.
    """
    repo_root = Path(repo_root_str).resolve()
    contracts: set[str] = set()
    model_module_name = f"model-{api_name}"
    for path in sorted(repo_root.rglob("*")):
        try:
            if (not path.is_file() or {"target", "build"}.intersection(path.relative_to(repo_root).parts)
                    or path.suffix.casefold() not in {".json", ".yaml", ".yml"}):
                continue
            contract_name = path.stem.casefold().replace("_", "-")
            relative_path = path.resolve().relative_to(repo_root)
            parts = relative_path.parts
            is_model_contract = any(
                part.casefold().replace("_", "-") == model_module_name
                and parts[index + 1:index + 5] == ("src", "main", "resources", "openapi")
                for index, part in enumerate(parts)
            )
            if contract_name == api_name or is_model_contract:
                contracts.add(path.resolve().relative_to(repo_root).as_posix())
        except (OSError, ValueError):
            continue
    return tuple(sorted(contracts))
def _infer_strategy1_declared_openapi_publications(
    repo_root: Path, declaration_rel_path: str
) -> list[MessageEndpoint]:
    """Attribute a ``*.rest`` declaration to its same-named local contract.

    A Strategy1 declaration is deliberately not parsed as OpenAPI.  It tells
    us which microservice publishes the contract with the same base name,
    wherever that contract physically lives in the indexed repository.
    Endpoints keep the declaration path so their lifecycle follows the
    publishing service during incremental indexing.
    """
    api_name = Path(declaration_rel_path).stem.casefold().replace("_", "-")
    publisher_module = _module_for_path(repo_root, declaration_rel_path)
    endpoints: list[MessageEndpoint] = []
    for contract_rel_path in _strategy1_openapi_contracts(str(repo_root.resolve()), api_name):
        for contract_endpoint in _infer_openapi_endpoints(
            repo_root, contract_rel_path, force_contract=True
        ):
            endpoints.append(
                replace(
                    contract_endpoint,
                    id=compute_endpoint_id(
                        contract_endpoint.role,
                        contract_endpoint.topic,
                        declaration_rel_path,
                        1,
                        1,
                    ),
                    path=declaration_rel_path,
                    start_line=1,
                    end_line=1,
                    snippet=(
                        f"Publication OpenAPI declaree par {declaration_rel_path}\n"
                        f"systemlens-openapi-contract:{contract_rel_path}\n"
                        f"{contract_endpoint.snippet}"
                    ),
                    module=publisher_module,
                    qualified_name=None,
                )
            )
    return endpoints
def _infer_actuator_endpoint(repo_root: Path, rel_path: str) -> list[MessageEndpoint]:
    path = repo_root / rel_path
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    value = _load_flat_spring_properties(str(path)).get("management.endpoints.web.exposure.include")
    if value is None or "*" not in value:
        return []
    start_line = 1
    for idx, line in enumerate(text.splitlines(), start=1):
        if "management.endpoints.web.exposure.include" in line:
            start_line = idx
            break
    return [
        _build_endpoint(
            repo_root,
            rel_path,
            start_line,
            start_line,
            "serve",
            "rest",
            "GET /actuator/**",
            "spring-actuator",
            "management.endpoints.web.exposure.include=*",
        )
    ]


@lru_cache(maxsize=512)
def _file_uses_resttemplate(repo_root_str: str, rel_path: str) -> bool:
    path = Path(repo_root_str) / rel_path
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return (
        "org.springframework.web.client.RestTemplate" in text
        or "new RestTemplate(" in text
        or " RestTemplate " in text
    )


@lru_cache(maxsize=512)
def _file_uses_restclient(repo_root_str: str, rel_path: str) -> bool:
    path = Path(repo_root_str) / rel_path
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "org.springframework.web.client.RestClient" in text or "RestClient " in text
def _uri_argument(snippet: str) -> str | None:
    """Extrait l'argument de `.uri(...)` en tenant compte des appels imbriqués."""
    match = _URI_CALL_RE.search(snippet)
    if match is None:
        return None
    start = match.end()
    depth = 1
    quote: str | None = None
    escaped = False
    for index in range(start, len(snippet)):
        char = snippet[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return snippet[start:index]
    return None
def _extract_restclient_path(
    snippet: str, repo_root: Path, source_path: str
) -> tuple[str, bool] | None:
    expr = _uri_argument(snippet)
    if expr is None:
        return None
    return _resolve_rest_path_expression(
        expr, repo_root, source_path, preserve_dynamic_segments=True
    )
def _extract_resttemplate_path(
    snippet: str, repo_root: Path, source_path: str
) -> tuple[str, bool] | None:
    match = _REST_TEMPLATE_CALL_RE.search(snippet)
    if match is not None:
        return _resolve_rest_path_expression(match.group(2), repo_root, source_path)
    exchange_match = _REST_TEMPLATE_EXCHANGE_RE.search(snippet)
    if exchange_match is not None:
        return _resolve_rest_path_expression(exchange_match.group(1), repo_root, source_path)
    return None
def _infer_resttemplate_exchange_endpoints(
    repo_root: Path, rel_path: str
) -> list[MessageEndpoint]:
    """Infer RestTemplate calls from ``method_invocation`` AST nodes.

    This covers the regular convenience methods as well as ``exchange``. The
    former used to depend on a rule-engine match; taking their first
    argument from the invocation node means nested calls and line wrapping no
    longer affect which expression is considered the URL.
    """
    if not _file_uses_resttemplate(str(repo_root), rel_path):
        return []
    parsed = java_parser.parse_java(str(repo_root), rel_path)
    if parsed is None:
        return []
    source, root = parsed
    direct_methods = {
        "getForObject": "GET", "getForEntity": "GET",
        "postForObject": "POST", "postForEntity": "POST",
        "put": "PUT", "delete": "DELETE",
    }
    inferred: list[MessageEndpoint] = []
    for invocation in java_parser.walk(root):
        if invocation.type != "method_invocation":
            continue
        receiver, method_name, args = java_parser.invocation_parts(invocation, source)
        if method_name not in {*direct_methods, "exchange"} or receiver is None or not args:
            continue
        receiver_name = _invocation_receiver(source, receiver)
        if receiver_name is None or "resttemplate" not in receiver_name.lower():
            continue
        if method_name == "exchange":
            if len(args) < 2:
                continue
            http_method = java_parser.node_text(source, args[1]).rsplit(".", 1)[-1]
            if http_method not in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}:
                # The verb is computed at runtime; the route is still evidence.
                http_method = "ANY"
        else:
            http_method = direct_methods[method_name]
        route, dynamic = _resolve_rest_ast_expression(args[0], source, repo_root, rel_path)
        snippet = java_parser.node_text(source, invocation)
        host = _resolved_http_host_ast(args[0], source, repo_root, rel_path)
        if host is not None:
            snippet = f"{snippet}\n// systemlens-api-domain:{host}"
        inferred.append(
            _build_endpoint(
                repo_root, rel_path, invocation.start_point.row + 1,
                invocation.end_point.row + 1, "call", "rest", f"{http_method} {route}",
                "resttemplate", snippet, topic_dynamic=dynamic,
            )
        )
    return inferred
def _infer_webclient_endpoints(repo_root: Path, rel_path: str) -> list[MessageEndpoint]:
    """Infer fluent WebClient calls by linking ``.uri`` to its AST receiver."""
    parsed = java_parser.parse_java(str(repo_root), rel_path)
    if parsed is None:
        return []
    source, root = parsed
    if b"WebClient" not in source:
        return []
    endpoints: list[MessageEndpoint] = []
    for invocation in java_parser.walk(root):
        if invocation.type != "method_invocation":
            continue
        receiver, name, args = java_parser.invocation_parts(invocation, source)
        if name != "uri" or receiver is None or not args:
            continue
        verb_call = _webclient_verb_invocation(receiver, source)
        if verb_call is None:
            continue
        http_method, anchor = verb_call
        route, dynamic = _resolve_rest_ast_expression(args[0], source, repo_root, rel_path)
        snippet = java_parser.node_text(source, invocation)
        host = _resolved_http_host_ast(args[0], source, repo_root, rel_path)
        if host is not None:
            snippet = f"{snippet}\n// systemlens-api-domain:{host}"
        endpoints.append(
            _build_endpoint(
                repo_root, rel_path, anchor.start_point.row + 1, invocation.end_point.row + 1,
                "call", "rest", f"{http_method} {route}", "webclient",
                snippet, topic_dynamic=dynamic,
            )
        )
    return endpoints


@lru_cache(maxsize=2048)
def _openapi_generator_contract_for_source(repo_root: Path, rel_path: str) -> str | None:
    """Return the exact generator input contract for a Java source file."""
    source_path = (repo_root / rel_path).resolve()
    try:
        source_path.relative_to(repo_root.resolve())
    except ValueError:
        return None
    for directory in (source_path.parent, *source_path.parents):
        if directory != repo_root.resolve() and repo_root.resolve() not in directory.parents:
            continue
        pom_path = directory / "pom.xml"
        if not pom_path.is_file():
            continue
        specs = maven_module.detect_openapi_generator_input_specs(pom_path)
        if not specs:
            continue
        if len(specs) != 1:
            return None
        try:
            return (pom_path.parent / specs[0]).resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            return None
    return None


def _infer_openapi_generated_client_endpoints(
    repo_root: Path, rel_path: str
) -> list[MessageEndpoint]:
    """Extract literal operations from generated OpenAPI ``ApiClient`` calls.

    Generated clients commonly centralize the HTTP call as
    ``apiClient.invokeAPI("/route", HttpMethod.GET, ...)``.  Only that
    receiver shape and literal route/method pair are accepted.  The generator
    input contract is retained as evidence when Maven identifies exactly one
    local contract for the source module.
    """
    parsed = java_parser.parse_java(str(repo_root), rel_path)
    if parsed is None:
        return []
    source, root = parsed
    contract = _openapi_generator_contract_for_source(repo_root, rel_path)
    endpoints: list[MessageEndpoint] = []
    for invocation in java_parser.walk(root):
        if invocation.type != "method_invocation":
            continue
        receiver, name, args = java_parser.invocation_parts(invocation, source)
        if name != "invokeAPI" or receiver is None or len(args) < 2:
            continue
        receiver_text = java_parser.node_text(source, receiver)
        if "apiclient" not in receiver_text.casefold():
            continue
        route = java_parser.string_value(args[0], source)
        method = java_parser.node_text(source, args[1]).rsplit(".", 1)[-1].upper()
        if route is None or method.casefold() not in _OPENAPI_HTTP_METHODS:
            continue
        snippet = java_parser.node_text(source, invocation)
        if contract is not None:
            snippet = f"{snippet}\n// systemlens-openapi-contract:{contract}"
        endpoints.append(
            _build_endpoint(
                repo_root,
                rel_path,
                invocation.start_point.row + 1,
                invocation.end_point.row + 1,
                "call",
                "rest",
                f"{method} {normalize_path(route)}",
                "openapi-generated-client",
                snippet,
            )
        )
    return endpoints


@lru_cache(maxsize=64)
def _strategy1_openapi_operations(
    repo_root_str: str,
) -> dict[str, tuple[tuple[str, str, str], ...]]:
    """Index unambiguous ``operationId`` routes from Strategy1 contracts."""
    repo_root = Path(repo_root_str)
    operations: dict[str, list[tuple[str, str, str]]] = {}
    for path in repo_root.rglob("*"):
        relative = path.relative_to(repo_root)
        if (
            not path.is_file()
            or path.suffix.casefold() not in {".json", ".yaml", ".yml"}
            or {"target", "build"}.intersection(relative.parts)
            or "openapi" not in {part.casefold() for part in relative.parts}
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            document = json.loads(text) if path.suffix.casefold() == ".json" else yaml.safe_load(text)
        except (OSError, ValueError, yaml.YAMLError):
            continue
        if not isinstance(document, dict) or not isinstance(document.get("paths"), dict):
            continue
        contract = relative.as_posix()
        for raw_route, route_operations in document["paths"].items():
            if not isinstance(raw_route, str) or not isinstance(route_operations, dict):
                continue
            for raw_method, operation in route_operations.items():
                if (
                    str(raw_method).casefold() not in _OPENAPI_HTTP_METHODS
                    or not isinstance(operation, dict)
                    or not isinstance(operation.get("operationId"), str)
                ):
                    continue
                operations.setdefault(operation["operationId"], []).append(
                    (contract, str(raw_method).upper(), normalize_path(raw_route))
                )
    return {name: tuple(values) for name, values in operations.items()}


def _infer_strategy1_openapi_operation_endpoints(
    repo_root: Path, rel_path: str
) -> list[MessageEndpoint]:
    """Resolve Strategy1 adapter calls by generated API type and operationId."""
    parsed = java_parser.parse_java(str(repo_root), rel_path)
    if parsed is None:
        return []
    source, root = parsed
    known_domains = _hub_rest_api_domains(str(repo_root.resolve()))
    operations = _strategy1_openapi_operations(str(repo_root.resolve()))
    if not known_domains or not operations:
        return []
    source_text = source.decode("utf-8", errors="replace")
    api_variables = {
        match.group("name"): match.group("type").rsplit(".", 1)[-1]
        for match in re.finditer(
            r"\b(?P<type>[A-Za-z_$][\w$.]*Api)\s+(?P<name>[A-Za-z_$][\w$]*)\b",
            source_text,
        )
    }
    endpoints: list[MessageEndpoint] = []
    for invocation in java_parser.walk(root):
        if invocation.type != "method_invocation":
            continue
        receiver, method_name, _args = java_parser.invocation_parts(invocation, source)
        if receiver is None or method_name not in operations:
            continue
        receiver_name = java_parser.node_text(source, receiver)
        api_type = api_variables.get(receiver_name)
        if api_type is None:
            continue
        domain = _domain_from_api_type_name(api_type, known_domains)
        candidates = operations[method_name]
        if domain is None or len(candidates) != 1:
            continue
        contract, http_method, route = candidates[0]
        snippet = java_parser.node_text(source, invocation)
        snippet = (
            f"{snippet}\n// systemlens-api-domain:{domain}"
            f"\n// systemlens-openapi-contract:{contract}"
        )
        endpoints.append(
            _build_endpoint(
                repo_root,
                rel_path,
                invocation.start_point.row + 1,
                invocation.end_point.row + 1,
                "call",
                "rest",
                f"{http_method} {route}",
                "strategy1-openapi-operation",
                snippet,
            )
        )
    return endpoints


def _restclient_base_argument(invocation, source: bytes):
    """Base URL argument of ``baseUrl(x)`` or ``RestClient.create(x)``."""
    receiver, name, args = java_parser.invocation_parts(invocation, source)
    if len(args) != 1:
        return None
    if name == "baseUrl":
        return args[0]
    if (
        name == "create" and receiver is not None
        and java_parser.node_text(source, receiver).rsplit(".", 1)[-1] == "RestClient"
    ):
        return args[0]
    return None


def _restclient_bound_name(invocation, source: bytes) -> tuple[object | None, str] | None:
    """Variable a client builder chain is assigned to, with its scope.

    The scope is the enclosing method or constructor for a local, ``None``
    for a field. A chain that is returned or passed on binds no name.
    """
    top = invocation
    while (
        top.parent is not None and top.parent.type == "method_invocation"
        and top.parent.child_by_field_name("object") == top
    ):
        top = top.parent
    parent = top.parent
    if parent is None:
        return None
    if parent.type == "variable_declarator":
        name_node = parent.child_by_field_name("name")
        if name_node is None:
            return None
        declaration = parent.parent
        scope = (
            java_parser.enclosing(parent, "method_declaration", "constructor_declaration")
            if declaration is not None and declaration.type == "local_variable_declaration"
            else None
        )
        return scope, java_parser.node_text(source, name_node)
    if parent.type == "assignment_expression":
        left = parent.child_by_field_name("left")
        if left is None:
            return None
        if left.type == "field_access":
            target = left.child_by_field_name("object")
            field = left.child_by_field_name("field")
            if target is None or target.type != "this" or field is None:
                return None
            return None, java_parser.node_text(source, field)
        if left.type == "identifier":
            name = java_parser.node_text(source, left)
            if name in locally_declared_names(parent, source):
                return (
                    java_parser.enclosing(parent, "method_declaration", "constructor_declaration"),
                    name,
                )
            return None, name
    return None


def _restclient_chain_root(receiver, source: bytes) -> tuple[object | None, str | None]:
    """Inline base argument of a call chain, or the client name it starts from."""
    node = receiver
    while node is not None and node.type == "method_invocation":
        base = _restclient_base_argument(node, source)
        if base is not None:
            return base, None
        node = node.child_by_field_name("object")
    if node is None:
        return None, None
    if node.type == "identifier":
        return None, java_parser.node_text(source, node)
    if node.type == "field_access":
        target = node.child_by_field_name("object")
        field = node.child_by_field_name("field")
        if target is not None and target.type == "this" and field is not None:
            return None, "this." + java_parser.node_text(source, field)
    return None, None


def _restclient_base(argument, source: bytes, repo_root: Path, rel_path: str) -> tuple[str | None, str] | None:
    """``(host, base path)`` of a resolved base URL, or ``None``."""
    raw = local_string(source, argument)
    if raw is None:
        raw, dynamic = _resolve_rest_expression(
            java_parser.node_text(source, argument), repo_root, rel_path,
            shadowed=locally_declared_names(argument, source),
        )
        if dynamic:
            return None
    return url_service_hint(raw), normalize_path(raw)


def _infer_restclient_endpoints(repo_root: Path, rel_path: str) -> list[MessageEndpoint]:
    """Infer Spring ``RestClient`` fluent calls from ``.uri(...)`` invocations.

    BL-08: a call takes the base URL of its own client only: a ``baseUrl``
    or ``RestClient.create`` in its own chain, else the one bound to the
    variable the chain starts from. A local binding of the enclosing method
    wins over a field. A name bound to several bases, declared without a
    resolvable base, or not found has no base. The base host is target
    evidence and its path prefixes a relative route; an absolute ``uri``
    ignores the base.
    """
    if not _file_uses_restclient(str(repo_root), rel_path):
        return []
    parsed = java_parser.parse_java(str(repo_root), rel_path)
    if parsed is None:
        return []
    source, root = parsed
    bindings: dict[tuple[object | None, str], set[tuple[str | None, str] | None]] = {}
    for candidate in java_parser.walk(root):
        if candidate.type != "method_invocation":
            continue
        argument = _restclient_base_argument(candidate, source)
        if argument is None:
            continue
        bound = _restclient_bound_name(candidate, source)
        if bound is None:
            continue
        bindings.setdefault(bound, set()).add(
            _restclient_base(argument, source, repo_root, rel_path)
        )

    def base_of(name: str, at) -> tuple[str | None, str] | None:
        explicit_field = name.startswith("this.")
        name = name.removeprefix("this.")
        scope = java_parser.enclosing(at, "method_declaration", "constructor_declaration")
        local = not explicit_field and name in locally_declared_names(at, source)
        found = bindings.get((scope if local else None, name), set())
        return next(iter(found)) if len(found) == 1 else None

    endpoints: list[MessageEndpoint] = []
    for invocation in java_parser.walk(root):
        if invocation.type != "method_invocation":
            continue
        receiver, name, args = java_parser.invocation_parts(invocation, source)
        if name != "uri" or receiver is None or not args:
            continue
        verb_call = _restclient_verb_invocation(receiver, source)
        if verb_call is None:
            continue
        http_method, anchor = verb_call
        route, dynamic = _resolve_rest_ast_expression(args[0], source, repo_root, rel_path)
        snippet = java_parser.node_text(source, invocation)
        host = _resolved_http_host_ast(args[0], source, repo_root, rel_path)
        # The base applies to a relative URI only: one whose leading part
        # is known and is not a URL, or a ``UriBuilder`` lambda.
        written = local_string(source, args[0])
        if written is None and args[0].type != "lambda_expression":
            leading = _split_java_concat(java_parser.node_text(source, args[0]).strip())[:1]
            leading_raw, leading_dynamic = (
                _resolve_rest_expression(
                    leading[0], repo_root, rel_path,
                    shadowed=locally_declared_names(args[0], source),
                ) if leading else ("", True)
            )
            written = None if leading_dynamic else leading_raw
        relative = host is None and (
            args[0].type == "lambda_expression"
            or (written is not None and "://" not in written)
        )
        if relative:
            inline, client = _restclient_chain_root(receiver, source)
            base = (
                _restclient_base(inline, source, repo_root, rel_path) if inline is not None
                else base_of(client, invocation) if client is not None
                else None
            )
            if base is not None:
                host, base_path = base
                if route != "<dynamic>":
                    route = join_paths(base_path, route)
        if host is not None:
            snippet = f"{snippet}\n// systemlens-api-domain:{host}"
        endpoints.append(
            _build_endpoint(
                repo_root, rel_path, anchor.start_point.row + 1,
                invocation.end_point.row + 1, "call", "rest",
                f"{http_method} {route}", "restclient", snippet,
                topic_dynamic=dynamic,
            )
        )
    return endpoints


_FLUENT_HTTP_VERBS = {"get", "post", "put", "delete", "patch", "head", "options"}


def _fluent_verb_invocation(node, source: bytes):
    """Nearest request verb in a fluent receiver chain, with its AST node.

    ``client.get()`` names the verb directly; ``client.method(HttpMethod.POST)``
    names it through the enum constant, and a computed method is ``ANY``.
    """
    if node.type != "method_invocation":
        return None
    receiver, name, args = java_parser.invocation_parts(node, source)
    if name in _FLUENT_HTTP_VERBS:
        return name.upper(), node
    if name == "method" and len(args) == 1:
        verb = java_parser.node_text(source, args[0]).rsplit(".", 1)[-1]
        return (verb if verb.lower() in _FLUENT_HTTP_VERBS else "ANY"), node
    return _fluent_verb_invocation(receiver, source) if receiver is not None else None


def _restclient_verb_invocation(node, source: bytes):
    """Find a Spring ``RestClient`` request verb in a fluent receiver chain."""
    return _fluent_verb_invocation(node, source)


def _webclient_verb_invocation(node, source: bytes):
    """Find a Spring ``WebClient`` request verb in a fluent receiver chain."""
    return _fluent_verb_invocation(node, source)
def _infer_configured_api_client_endpoints(
    repo_root: Path, rel_path: str
) -> list[MessageEndpoint]:
    """Émet les dépendances configurées d'une configuration REST Strategy1.

    La convention strategy1 ne dépend ni du nom de factory ni de la forme du
    bean : toute constante majuscule avec underscore présente dans une classe
    ``Rest*Config*`` désigne le microservice homologue en kebab-case.
    """
    parsed = java_parser.parse_java(str(repo_root), rel_path)
    if parsed is None:
        return []
    source, root = parsed
    endpoints: dict[str, MessageEndpoint] = {}

    def client_contract_markers(return_type: str, domain: str) -> str:
        api_names = {
            return_type.removesuffix("Api").casefold().replace("_", "-"),
            domain.removeprefix("domain-").casefold().replace("_", "-"),
        }
        contracts = sorted({
            contract
            for api_name in api_names
            if api_name
            for contract in _strategy1_openapi_contracts(str(repo_root.resolve()), api_name)
        })
        return "".join(f"\nsystemlens-openapi-contract:{contract}" for contract in contracts)

    for type_node in java_parser.type_declarations(root):
        if not _is_rest_client_configuration(
            java_parser.declaration_name(type_node, source)
        ):
            continue
        configuration = java_parser.node_text(source, type_node)
        known_domains = _hub_rest_api_domains(str(repo_root.resolve()))
        for method_node in java_parser.walk(type_node):
            if method_node.type != "method_declaration":
                continue
            if not any(
                java_parser.annotation_name(annotation, source) == "Bean"
                for annotation in java_parser.annotations_of(method_node)
            ):
                continue
            return_node = method_node.child_by_field_name("type")
            if return_node is None:
                continue
            return_type = java_parser.node_text(source, return_node).rsplit(".", 1)[-1]
            if return_type in {"WebClient", "ExchangeFilterFunction"}:
                continue
            bean_domain = _bean_api_domain(method_node, source, "<strategy1>", known_domains)
            if bean_domain is None:
                continue
            endpoint = _build_endpoint(
                repo_root,
                rel_path,
                method_node.start_point.row + 1,
                method_node.end_point.row + 1,
                "call",
                "rest",
                "ANY <dynamic>",
                "configured-api-client-factory",
                (
                    f"{configuration}\nsystemlens-api-domain:{bean_domain}"
                    f"{client_contract_markers(return_type, bean_domain)}"
                ),
                topic_dynamic=True,
            )
            endpoints[endpoint.id] = endpoint
            _trace_rest_client(
                "rest_client.search.configuration_dependency",
                path=rel_path,
                line=method_node.start_point.row + 1,
                domain=bean_domain,
            )
        for service, line in _rest_configuration_external_services(type_node, source):
            endpoint = _build_endpoint(
                repo_root,
                rel_path,
                line,
                line,
                "call",
                "rest",
                "ANY <dynamic>",
                "configured-api-client-domain",
                f"{configuration}\nsystemlens-external-microservice:{service}",
                topic_dynamic=True,
            )
            endpoints[endpoint.id] = endpoint
            _trace_rest_client(
                "rest_client.search.external_configuration_dependency",
                path=rel_path,
                line=line,
                service=service,
            )
    return list(endpoints.values())


RestEndpointExtractor = Callable[[Path, str], list[MessageEndpoint]]


def _rest_endpoint_extractors(
    *, configured_api_client_strategy1: bool,
) -> tuple[RestEndpointExtractor, ...]:
    """Return the ordered REST extractor registry for one indexing profile."""
    extractors: list[RestEndpointExtractor] = [
        _infer_generic_request_mapping_endpoints,
        _infer_http_interface_endpoints,
        _infer_spring_data_rest_endpoints,
        _infer_swagger_endpoint,
        _infer_resttemplate_exchange_endpoints,
        _infer_restclient_endpoints,
        _infer_webclient_endpoints,
        _infer_spring_cloud_gateway_routes,
        _infer_spring_webflux_routes,
    ]
    if configured_api_client_strategy1:
        extractors.extend((
            _infer_configured_api_client_endpoints,
            _infer_openapi_generated_client_endpoints,
            _infer_strategy1_openapi_operation_endpoints,
        ))
    return tuple(extractors)


def _infer_spring_cloud_gateway_routes(repo_root: Path, rel_path: str) -> list[MessageEndpoint]:
    parsed = java_parser.parse_java(str(repo_root), rel_path)
    if parsed is None:
        return []
    source, root = parsed
    inferred: list[MessageEndpoint] = []
    for invocation in java_parser.walk(root):
        if invocation.type != "method_invocation":
            continue
        _receiver, name, args = java_parser.invocation_parts(invocation, source)
        # ``route(r -> ...)`` and ``route("id", r -> ...)`` both declare a route.
        route_lambda = next((arg for arg in args if arg.type == "lambda_expression"), None)
        if name != "route" or route_lambda is None:
            continue
        path_value: str | None = None
        http_method: str | None = None
        has_uri = False
        filter_calls: list[tuple[int, str, list[str] | None]] = []
        for call in java_parser.walk(route_lambda):
            if call.type != "method_invocation":
                continue
            _call_receiver, call_name, call_args = java_parser.invocation_parts(call, source)
            if call_name == "path" and call_args and call_args[0].type == "string_literal":
                path_value = java_parser.string_value(call_args[0], source)
            elif call_name == "method" and call_args:
                candidate = java_parser.string_value(call_args[0], source)
                if candidate is None:
                    candidate = java_parser.node_text(source, call_args[0]).rsplit(".", 1)[-1]
                if candidate in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}:
                    http_method = candidate
            elif call_name == "uri":
                has_uri = True
            elif call_name in _GATEWAY_DSL_PATH_FILTERS:
                call_name_node = call.child_by_field_name("name")
                filter_calls.append((
                    call_name_node.start_byte if call_name_node is not None else call.start_byte,
                    _GATEWAY_DSL_PATH_FILTERS[call_name],
                    _gateway_dsl_filter_args(call_args, source),
                ))
        if path_value is None or not has_uri:
            continue
        # Like a YAML route without a method predicate, a Java route
        # without ``.method(...)`` proxies every verb.
        http_method = http_method or "ANY"
        route = normalize_path(path_value)
        # A chained filter call encloses the calls before it: source order
        # is the order of the filter names.
        filters = [(name, args) for _start, name, args in sorted(filter_calls, key=lambda item: item[0])]
        target = (
            _apply_gateway_filters(route, filters)
            if all(args is not None for _name, args in filters) else None
        )
        # In ``builder.routes().route(a).route(b)`` the invocation of ``b``
        # spans the whole chain before it. The route's own evidence starts at
        # its ``route`` name, so each route keeps its own ``uri(...)`` target
        # and its own line instead of the chain's first one.
        name_node = invocation.child_by_field_name("name")
        start_node = name_node if name_node is not None else invocation
        snippet = source[start_node.start_byte:invocation.end_byte].decode(
            "utf-8", errors="replace"
        )
        for role, topic_route, dynamic in (
            ("serve", route, False),
            ("call", target or "<dynamic>", target is None),
        ):
            inferred.append(
                _build_endpoint(
                    repo_root, rel_path, start_node.start_point.row + 1,
                    invocation.end_point.row + 1, role, "rest", f"{http_method} {topic_route}",
                    "spring-cloud-gateway", snippet, topic_dynamic=dynamic,
                )
            )
    return inferred


# BL-06: gateway filters that change the forwarded path, with the argument
# names of their long YAML form, in shortcut order.
_GATEWAY_PATH_FILTER_ARGS: dict[str, tuple[str, ...]] = {
    "StripPrefix": ("parts",),
    "RewritePath": ("regexp", "replacement"),
    "PrefixPath": ("prefix",),
    "SetPath": ("template",),
}
_GATEWAY_DSL_PATH_FILTERS = {
    "stripPrefix": "StripPrefix", "rewritePath": "RewritePath",
    "prefixPath": "PrefixPath", "setPath": "SetPath",
}
_JAVA_NAMED_GROUP_RE = re.compile(r"\(\?<([A-Za-z][A-Za-z0-9]*)>")
_GATEWAY_REPLACEMENT_TOKEN_RE = re.compile(r"\$\\?\{([A-Za-z][A-Za-z0-9]*)\}|\$(\d)|\\(.)")


def _gateway_dsl_filter_args(arguments, source: bytes) -> list[str] | None:
    """Literal arguments of a Java DSL filter call, or ``None``."""
    values: list[str] = []
    for argument in arguments:
        if argument.type == "string_literal":
            value = java_parser.string_value(argument, source)
        elif argument.type == "decimal_integer_literal":
            value = java_parser.node_text(source, argument)
        else:
            value = None
        if value is None:
            return None
        values.append(value)
    return values


def _gateway_filters(items: object) -> list[tuple[str, list[str] | None]]:
    """Path filters of a YAML filter list, in order.

    Each filter is ``(name, arguments)``; the arguments are ``None`` when a
    long-form filter misses one or a shortcut has a wrong count.
    """
    filters: list[tuple[str, list[str] | None]] = []
    if not isinstance(items, list):
        return filters
    for item in items:
        if isinstance(item, str):
            name, separator, value = item.partition("=")
            name = name.strip()
            if name not in _GATEWAY_PATH_FILTER_ARGS:
                continue
            # Spring splits a shortcut on every comma.
            args: list[str] | None = [part.strip() for part in value.split(",")] if separator else []
            if args is not None and len(args) != len(_GATEWAY_PATH_FILTER_ARGS[name]):
                args = None
            filters.append((name, args))
        elif isinstance(item, dict) and item.get("name") in _GATEWAY_PATH_FILTER_ARGS:
            name = str(item["name"])
            raw = item.get("args")
            keys = _GATEWAY_PATH_FILTER_ARGS[name]
            values = [
                raw.get(key, raw.get(f"_genkey_{index}")) if isinstance(raw, dict) else None
                for index, key in enumerate(keys)
            ]
            filters.append((
                name,
                None if any(value is None for value in values) else [str(value) for value in values],
            ))
    return filters


def _rewrite_gateway_path(path: str, regexp: str, replacement: str) -> str | None:
    """Apply ``RewritePath`` to a concrete path, as Java's ``replaceAll``."""
    try:
        compiled = re.compile(_JAVA_NAMED_GROUP_RE.sub(r"(?P<\1>", regexp))
    except re.error:
        return None

    def expand(match: re.Match[str]) -> str:
        def token(found: re.Match[str]) -> str:
            name, number, escaped = found.groups()
            if escaped is not None:
                return escaped
            return match.group(name if name is not None else int(number)) or ""

        return _GATEWAY_REPLACEMENT_TOKEN_RE.sub(token, replacement)

    try:
        return compiled.sub(expand, path)
    except (IndexError, re.error):
        return None


def _apply_gateway_filters(
    route: str, filters: list[tuple[str, list[str] | None]]
) -> str | None:
    """Forwarded path of a gateway route pattern, or ``None`` when unknown.

    The pattern's ``**``, ``*`` and ``{var}`` segments become opaque tokens.
    A filter applies statically only when its result does not depend on
    what such a segment matches: ``StripPrefix`` may drop literal and
    single-segment tokens but not ``**``; ``RewritePath`` must match the
    tokenized pattern, or match nothing when it has no token, and keep every
    token whole and in order. ``PrefixPath`` and ``SetPath`` always apply.
    """
    segments = [segment for segment in route.split("/") if segment]
    tokens: dict[str, str] = {}
    parts: list[str] = []
    for segment in segments:
        if segment == "**" or "*" in segment or "{" in segment:
            token = f"\x00{len(tokens)}\x00"
            tokens[token] = segment
            parts.append(token)
        else:
            parts.append(segment)
    path = "/" + "/".join(parts)
    for name, args in filters:
        if args is None:
            return None
        if name == "StripPrefix":
            try:
                count = int(args[0])
            except ValueError:
                return None
            kept = [part for part in path.split("/") if part]
            if any(tokens.get(part) == "**" for part in kept[:count]):
                return None
            path = "/" + "/".join(kept[count:])
        elif name == "RewritePath":
            had_tokens = [token for token in tokens if token in path]
            if had_tokens and re.search(
                _JAVA_NAMED_GROUP_RE.sub(r"(?P<\1>", args[0]), path
            ) is None:
                return None
            rewritten = _rewrite_gateway_path(path, args[0], args[1])
            if rewritten is None:
                return None
            if rewritten.count("\x00") != 2 * len(
                [token for token in tokens if token in rewritten]
            ):
                return None
            order = [token for token in had_tokens if token in rewritten]
            if sorted(order, key=rewritten.index) != order:
                return None
            path = rewritten
        elif name == "PrefixPath":
            path = join_paths(normalize_path(args[0]), path)
        elif name == "SetPath":
            path = normalize_path(args[0])
    for token, segment in tokens.items():
        path = path.replace(token, segment)
    return normalize_path(path)
def _gateway_route_entries(data: object) -> list[dict[str, object]]:
    if not isinstance(data, dict):
        return []
    spring = data.get("spring")
    if not isinstance(spring, dict):
        return []
    cloud = spring.get("cloud")
    if not isinstance(cloud, dict):
        return []
    gateway = cloud.get("gateway")
    if not isinstance(gateway, dict):
        return []
    routes = gateway.get("routes")
    if not isinstance(routes, list):
        server = gateway.get("server")
        webflux = server.get("webflux") if isinstance(server, dict) else None
        routes = webflux.get("routes") if isinstance(webflux, dict) else None
    return [route for route in routes if isinstance(route, dict)] if isinstance(routes, list) else []
def _gateway_paths(route: dict[str, object]) -> list[str]:
    predicates = route.get("predicates")
    if not isinstance(predicates, list):
        return []
    paths: list[str] = []
    for predicate in predicates:
        if isinstance(predicate, str) and predicate.startswith("Path="):
            paths.extend(path.strip() for path in predicate[5:].split(",") if path.strip())
        elif isinstance(predicate, dict) and predicate.get("name") == "Path":
            args = predicate.get("args")
            if isinstance(args, dict):
                value = args.get("_genkey_0") or args.get("patterns")
                if isinstance(value, str):
                    paths.append(value)
    return paths
def _gateway_default_filters(document: object) -> object:
    gateway = document.get("spring", {}) if isinstance(document, dict) else {}
    for key in ("cloud", "gateway"):
        gateway = gateway.get(key, {}) if isinstance(gateway, dict) else {}
    return gateway.get("default-filters") if isinstance(gateway, dict) else None


def _gateway_uri_lines(text: str) -> list[list[int | None]]:
    """Line of each route's ``uri`` value, per YAML document.

    The composed node tree keeps source positions, quoted values included.
    A document whose routes cannot be read gives an empty list.
    """
    try:
        nodes = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
    except yaml.YAMLError:
        return []

    def child(node, key: str):
        if not isinstance(node, yaml.MappingNode):
            return None
        return next(
            (value for name, value in node.value
             if isinstance(name, yaml.ScalarNode) and name.value == key),
            None,
        )

    lines: list[list[int | None]] = []
    for node in nodes:
        gateway = child(child(child(node, "spring"), "cloud"), "gateway")
        routes = child(gateway, "routes")
        if not isinstance(routes, yaml.SequenceNode):
            routes = child(child(child(gateway, "server"), "webflux"), "routes")
        document_lines: list[int | None] = []
        if isinstance(routes, yaml.SequenceNode):
            for route in routes.value:
                if not isinstance(route, yaml.MappingNode):
                    continue
                uri = child(route, "uri")
                document_lines.append(uri.start_mark.line + 1 if uri is not None else None)
        lines.append(document_lines)
    return lines


def _infer_spring_cloud_gateway_yaml_routes(repo_root: Path, rel_path: str) -> list[MessageEndpoint]:
    path = repo_root / rel_path
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        documents = list(yaml.safe_load_all(text))
    except (OSError, yaml.YAMLError):
        return []
    uri_lines = _gateway_uri_lines(text)

    inferred: list[MessageEndpoint] = []
    for document_index, document in enumerate(documents):
        routes = _gateway_route_entries(document)
        lines = uri_lines[document_index] if document_index < len(uri_lines) else []
        if len(lines) != len(routes):
            lines = [None] * len(routes)
        default_filters = _gateway_filters(_gateway_default_filters(document))
        for route, line in zip(routes, lines):
            uri = route.get("uri")
            if not isinstance(uri, str) or not uri.startswith(("lb://", "http://", "https://")):
                continue
            line_no = line or 1
            filters = [*default_filters, *_gateway_filters(route.get("filters"))]
            filter_text = "; ".join(
                f"{name}={','.join(args) if args is not None else '?'}"
                for name, args in filters
            )
            for public_path in _gateway_paths(route):
                public_route = normalize_path(public_path)
                target_path = _apply_gateway_filters(public_route, filters)
                snippet = f"Path={public_route}; {filter_text + '; ' if filter_text else ''}uri={uri}"
                inferred.append(
                    _build_endpoint(
                        repo_root, rel_path, line_no, line_no, "serve", "rest",
                        f"ANY {public_route}", "spring-cloud-gateway", snippet,
                    )
                )
                inferred.append(
                    _build_endpoint(
                        repo_root, rel_path, line_no, line_no, "call", "rest",
                        f"ANY {target_path or '<dynamic>'}", "spring-cloud-gateway", snippet,
                        topic_dynamic=target_path is None,
                    )
                )
    return inferred


def _infer_spring_webflux_routes(repo_root: Path, rel_path: str) -> list[MessageEndpoint]:
    parsed = java_parser.parse_java(str(repo_root), rel_path)
    if parsed is None:
        return []
    source, root = parsed
    inferred: list[MessageEndpoint] = []
    for invocation in java_parser.walk(root):
        if invocation.type != "method_invocation":
            continue
        _receiver, name, args = java_parser.invocation_parts(invocation, source)
        if name not in {"route", "andRoute"} or not args or args[0].type != "method_invocation":
            continue
        _predicate_receiver, predicate, predicate_args = java_parser.invocation_parts(args[0], source)
        if predicate not in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}:
            continue
        if not predicate_args or predicate_args[0].type != "string_literal":
            continue
        path = java_parser.string_value(predicate_args[0], source)
        if path is None:
            continue
        inferred.append(
            _build_endpoint(
                repo_root, rel_path, invocation.start_point.row + 1,
                invocation.end_point.row + 1, "serve", "rest",
                f"{predicate} {normalize_path(path)}", "spring-webflux",
                java_parser.node_text(source, invocation),
            )
        )
    return inferred
def infer_framework_endpoints(
    repo_root: Path,
    files: list[str] | None = None,
    *,
    configured_api_client_strategy1: bool = False,
) -> list[MessageEndpoint]:
    """Infère les endpoints des frameworks connus.

    Les conventions applicatives de constantes majuscules avec underscore des
    classes ``Rest*Config*`` ne sont actives qu'avec ``strategy1``.
    """
    if files is None:
        candidate_files = [
            path.relative_to(repo_root).as_posix() for path in repo_root.rglob("*")
            if path.is_file() and not {"target", "build"}.intersection(path.relative_to(repo_root).parts)
        ]
    else:
        candidate_files = sorted(files)

    strategy1_contract_paths: set[str] = set()
    if configured_api_client_strategy1:
        declaration_paths = [
            path.relative_to(repo_root).as_posix()
            for path in repo_root.rglob("*")
            if path.is_file()
            and not {"target", "build"}.intersection(path.relative_to(repo_root).parts)
            and _is_strategy1_openapi_declaration_path(
                path.relative_to(repo_root).as_posix()
            )
        ]
        for declaration_path in declaration_paths:
            api_name = Path(declaration_path).stem.casefold().replace("_", "-")
            strategy1_contract_paths.update(
                _strategy1_openapi_contracts(str(repo_root.resolve()), api_name)
            )

    inferred: dict[str, MessageEndpoint] = {}
    extractors = _rest_endpoint_extractors(
        configured_api_client_strategy1=configured_api_client_strategy1,
    )
    for rel_path in candidate_files:
        if rel_path.endswith(".java"):
            for extractor in extractors:
                for endpoint in extractor(repo_root, rel_path):
                    inferred[endpoint.id] = endpoint
        elif rel_path.endswith("pom.xml"):
            for endpoint in _infer_openapi_generator_endpoints(repo_root, rel_path):
                inferred[endpoint.id] = endpoint
        elif rel_path.endswith((".properties", ".yml", ".yaml")):
            endpoints = (
                _infer_actuator_endpoint(repo_root, rel_path)
                + _infer_spring_cloud_gateway_yaml_routes(repo_root, rel_path)
            )
            if rel_path not in strategy1_contract_paths:
                endpoints.extend(_infer_openapi_endpoints_attributed(repo_root, rel_path))
            for endpoint in endpoints:
                inferred[endpoint.id] = endpoint
        elif rel_path.endswith(".json"):
            endpoints = []
            if rel_path not in strategy1_contract_paths:
                endpoints = _infer_openapi_endpoints_attributed(repo_root, rel_path)
            for endpoint in endpoints:
                inferred[endpoint.id] = endpoint
        elif configured_api_client_strategy1 and _is_strategy1_openapi_declaration_path(rel_path):
            for endpoint in _infer_strategy1_declared_openapi_publications(repo_root, rel_path):
                inferred[endpoint.id] = endpoint
    endpoints = list(inferred.values())
    # A generated OpenAPI client exposes the same interaction twice: once at
    # the adapter's typed operation call and once inside the generated
    # ``ApiClient.invokeAPI`` implementation. Prefer the adapter evidence when
    # both describe the same module and route; otherwise one HTTP interaction
    # would produce two output ports and corrupt the O/I label sequence.
    strategy1_operation_routes = {
        (endpoint.module, endpoint.topic)
        for endpoint in endpoints
        if endpoint.framework == "strategy1-openapi-operation"
    }
    return [
        endpoint
        for endpoint in endpoints
        if not (
            endpoint.framework == "openapi-generated-client"
            and (endpoint.module, endpoint.topic) in strategy1_operation_routes
        )
    ]
