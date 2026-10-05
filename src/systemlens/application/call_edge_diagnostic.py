"""Diagnose one expected Java caller-to-callee edge from a persisted snapshot."""

from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
import re

from systemlens.application.architecture_inventory import ArchitectureInventory
from systemlens.domain.code_flows import CodeFlow, CodeQLCallGraphEdge, IntegrationMethod
from systemlens.indexing.codeql import CodeQLMethod
from systemlens.indexing.java_symbols import JavaSymbols


def _method_fact(method: IntegrationMethod) -> dict[str, object]:
    return {
        "id": method.id,
        "module": method.module,
        "qualified_method": method.qualified_method,
        "path": method.path,
        "start_line": method.start_line,
        "end_line": method.end_line,
        "input_endpoint_ids": list(method.input_endpoint_ids),
        "output_endpoint_ids": list(method.output_endpoint_ids),
    }


def _presence(
    edges: Sequence[CodeQLCallGraphEdge],
    callers: Sequence[IntegrationMethod],
    callees: Sequence[IntegrationMethod],
    *,
    callee_alias_ids: set[str] | None = None,
    codeql_method_ids: set[str] | None = None,
) -> dict[str, object]:
    """Report only presence facts that the persisted snapshot can prove."""
    callee_ids = {method.id for method in callees} | (callee_alias_ids or set())
    incident_method_ids = {
        method_id
        for edge in edges
        if not edge.inferred
        for method_id in (edge.caller_id, edge.callee_id)
    }

    def node(methods: Sequence[IntegrationMethod]) -> dict[str, str]:
        if not methods:
            return {
                "ast": "absent", "codeql": "unknown",
                "proof_ast": "method_not_in_persisted_inventory",
                "proof_codeql": "not_proven",
            }
        if len(methods) != 1:
            return {
                "ast": "present", "codeql": "unknown",
                "proof_ast": "multiple_persisted_integration_methods",
                "proof_codeql": "not_proven",
            }
        method_ids = {methods[0].id}
        if methods[0] in callees:
            method_ids |= callee_alias_ids or set()
        codeql_present = bool(method_ids & (
            codeql_method_ids if codeql_method_ids is not None else incident_method_ids
        ))
        return {
            "ast": "present",
            "codeql": "present" if codeql_present else "unknown",
            "proof_ast": "persisted_integration_method",
            "proof_codeql": (
                (
                    "direct_codeql_method_query" if codeql_method_ids is not None
                    else "incident_non_inferred_call_edge"
                ) if codeql_present else (
                    "not_found_by_direct_codeql_method_query"
                    if codeql_method_ids is not None
                    else "not_proven_by_persisted_non_inferred_edge"
                )
            ),
        }

    matching_edge = (
        len(callers) == 1
        and len(callees) == 1
        and any(
            edge.caller_id == callers[0].id and edge.callee_id in callee_ids
            and not edge.inferred
            for edge in edges
        )
    )
    return {
        "caller_node": node(callers),
        "callee_node": node(callees),
        "edge": {
            "codeql": "present" if matching_edge else "unknown",
            "ast": "unknown",
            "proof_ast": "no_persisted_ast_call_edge_projection",
            "proof_codeql": (
                "persisted_non_inferred_call_edge" if matching_edge
                else "no_matching_persisted_non_inferred_call_edge"
            ),
        },
    }


def _matching_methods(
    methods: Sequence[IntegrationMethod], query: str, *, module: str | None = None,
) -> list[IntegrationMethod]:
    """Resolve an ID, qualified name, or unambiguous qualified-name suffix."""
    candidates = [method for method in methods if module is None or method.module == module]
    query = query.strip()
    exact_id = [method for method in candidates if method.id == query]
    if exact_id:
        return exact_id
    exact_name = [method for method in candidates if method.qualified_method == query]
    if exact_name:
        return exact_name
    suffix = query if query.startswith(".") else f".{query}"
    return sorted(
        (method for method in candidates if method.qualified_method.endswith(suffix)),
        key=lambda method: (method.qualified_method, method.path, method.start_line),
    )


def _interface_implementation_ids(
    inventory: ArchitectureInventory, callee: IntegrationMethod, *, module: str | None = None,
) -> set[str]:
    """Return indexed concrete implementations of an interface method.

    CodeQL may persist a dynamic call against the concrete implementation while
    the user asks for the interface declaration.  Reuse the source-backed Java
    symbol resolver here so method-name coincidence cannot create an alias.
    """
    for root in inventory.source_roots:
        if not root.exists():
            continue
        symbols = JavaSymbols(
            root,
            inventory.integration_methods,
            source_paths=[method.path for method in inventory.integration_methods],
        )
        contract = symbols.methods.get(callee.id)
        if contract is not None and not contract.concrete:
            return {
                candidate.method.id
                for candidate in symbols.implementations(contract)
                if module is None or candidate.method.module == module
            }
    return set()


def _direct_codeql_method_ids(
    methods: Sequence[IntegrationMethod],
    codeql_methods: Sequence[CodeQLMethod],
) -> set[str]:
    """Join selected persisted methods with methods returned directly by CodeQL."""
    facts = {
        (method.qualified_method, method.path, method.start_line)
        for method in codeql_methods
    }
    return {
        method.id for method in methods
        if (method.qualified_method, method.path, method.start_line) in facts
    }


def _signature_settings(signature: str | None) -> dict[str, object]:
    settings: dict[str, object] = {}
    if not signature:
        return settings
    for key, value in re.findall(r"(?:^|\|)([^=|]+)=([^|]+)", signature):
        normalized = key.replace("-", "_")
        if normalized == "hops":
            try:
                settings["max_hops"] = int(value)
            except ValueError:
                settings["max_hops"] = value
        elif value in {"True", "False"}:
            settings[normalized] = value == "True"
        else:
            settings[normalized] = value
    return settings


def _effective_edges(
    edges: Sequence[CodeQLCallGraphEdge], edge_confidence: object,
) -> list[CodeQLCallGraphEdge]:
    if edge_confidence != "exact":
        return list(edges)
    return [
        edge for edge in edges
        if edge.dispatch_confidence == "exact" and not edge.inferred
    ]


def _reachable(
    starts: set[str], target: str, edges: Sequence[CodeQLCallGraphEdge],
) -> bool:
    if target in starts:
        return True
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        adjacency[edge.caller_id].add(edge.callee_id)
    seen = set(starts)
    queue = deque(starts)
    while queue:
        current = queue.popleft()
        for candidate in adjacency.get(current, set()):
            if candidate == target:
                return True
            if candidate not in seen:
                seen.add(candidate)
                queue.append(candidate)
    return False


def _can_reach_any(
    start: str, targets: set[str], edges: Sequence[CodeQLCallGraphEdge],
) -> bool:
    if start in targets:
        return True
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        adjacency[edge.caller_id].add(edge.callee_id)
    seen = {start}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for candidate in adjacency.get(current, set()):
            if candidate in targets:
                return True
            if candidate not in seen:
                seen.add(candidate)
                queue.append(candidate)
    return False


def _flow_uses_edge(
    flow: CodeFlow,
    caller: IntegrationMethod,
    callee: IntegrationMethod,
    callee_alias_names: set[str] | None = None,
) -> bool:
    route = [flow.method]
    route.extend(step.name for step in flow.steps if step.kind == "method_call")
    callee_names = {callee.qualified_method}
    callee_names.update(callee_alias_names or set())
    return any(
        left == caller.qualified_method and right in callee_names
        for left, right in zip(route, route[1:])
    )


def diagnose_call_edge(
    inventory: ArchitectureInventory,
    caller_query: str,
    callee_query: str,
    *,
    module: str | None = None,
    snapshot_metadata: Mapping[str, str | None] | None = None,
    codeql_methods: Sequence[CodeQLMethod] | None = None,
) -> dict[str, object]:
    """Classify where one expected call edge disappears from the pipeline."""
    metadata = dict(snapshot_metadata or {})
    settings = _signature_settings(metadata.get("code_flow_signature"))
    snapshot = {
        "call_graph_status": metadata.get("codeql_call_graph_status") or "unknown",
        "flow_snapshot_status": metadata.get("code_flow_snapshot_status") or "unknown",
        **settings,
    }
    all_callers = _matching_methods(inventory.integration_methods, caller_query)
    selected_module = module
    if selected_module is None and len(all_callers) == 1:
        selected_module = all_callers[0].module
    callers = _matching_methods(
        inventory.integration_methods, caller_query, module=selected_module,
    )
    callees = _matching_methods(
        inventory.integration_methods, callee_query, module=selected_module,
    )
    callee_alias_ids = (
        _interface_implementation_ids(
            inventory, callees[0], module=selected_module,
        )
        if len(callees) == 1 else set()
    )
    codeql_method_ids = None
    if codeql_methods is not None:
        selected_methods = [*callers, *callees]
        selected_methods.extend(
            method for method in inventory.integration_methods
            if method.id in callee_alias_ids
        )
        codeql_method_ids = _direct_codeql_method_ids(
            selected_methods, codeql_methods,
        )
    result: dict[str, object] = {
        "kind": "call_edge_diagnostic",
        "caller_query": caller_query,
        "callee_query": callee_query,
        "module": selected_module,
        "caller_candidates": [_method_fact(method) for method in callers],
        "callee_candidates": [_method_fact(method) for method in callees],
        "snapshot": snapshot,
        "edges": [],
        "flow_ids": [],
        "presence": _presence(
            inventory.codeql_call_edges,
            callers,
            callees,
            callee_alias_ids=callee_alias_ids,
            codeql_method_ids=codeql_method_ids,
        ),
    }

    if not callers or not callees:
        missing = "caller" if not callers else "callee"
        result.update({
            "status": f"{missing}_not_indexed",
            "stage": "method_inventory",
            "conclusion": (
                f"La méthode {missing} ne figure pas dans l'inventaire AST persisté. "
                "CodeQL ne peut pas être rattaché à cette méthode."
            ),
            "recommended_action": (
                "Relancez `systemlens index --full`, puis vérifiez les diagnostics "
                "d'extraction si la méthode reste absente."
            ),
        })
        return result
    if len(callers) != 1 or len(callees) != 1:
        result.update({
            "status": "method_selection_ambiguous",
            "stage": "method_selection",
            "conclusion": (
                "La requête correspond à plusieurs méthodes. Utilisez un nom qualifié "
                "complet ou l'identifiant de méthode affiché dans les candidats."
            ),
            "recommended_action": "Relancez la commande avec l'identifiant d'un candidat.",
        })
        return result

    caller, callee = callers[0], callees[0]
    callee_ids = {callee.id} | callee_alias_ids
    callee_alias_names = {
        method.qualified_method
        for method in inventory.integration_methods
        if method.id in callee_alias_ids
    }
    matching_edges = [
        edge for edge in inventory.codeql_call_edges
        if edge.caller_id == caller.id and edge.callee_id in callee_ids
    ]
    result["edges"] = [
        {
            "caller_id": edge.caller_id,
            "callee_id": edge.callee_id,
            "path": edge.path,
            "line": edge.line,
            "dispatch_confidence": edge.dispatch_confidence,
            "inferred": edge.inferred,
        }
        for edge in matching_edges
    ]
    if not matching_edges:
        engine = settings.get("engine")
        available = settings.get("available")
        if snapshot["flow_snapshot_status"] == "partial" or snapshot["call_graph_status"] == "partial":
            status = "snapshot_incomplete"
            stage = "snapshot"
            conclusion = "Le snapshot est partiel et peut ne pas contenir cette arête."
            action = "Reprenez ou relancez l'indexation CodeQL avant de conclure sur l'appel."
        elif engine == "none":
            status = "call_graph_disabled"
            stage = "codeql_extraction_or_join"
            conclusion = "Le snapshot a été créé sans moteur de graphe d'appels."
            action = "Relancez l'indexation avec `--call-graph-engine codeql`."
        elif available is False:
            status = "codeql_unavailable"
            stage = "codeql_extraction_or_join"
            conclusion = "CodeQL était indisponible pendant la création du snapshot."
            action = "Exécutez `systemlens doctor`, puis relancez l'indexation."
        else:
            status = "edge_not_persisted"
            stage = "codeql_extraction_or_join"
            conclusion = (
                "Aucune arête A vers B n'a été persistée. Le snapshot actuel ne conserve "
                "pas les lignes CodeQL rejetées, donc il ne sépare pas une omission CodeQL "
                "d'un échec de rattachement CodeQL vers AST."
            )
            action = (
                "Relancez l'indexation avec `--codeql-progress`; utilisez une base CodeQL "
                "conservée pour comparer ensuite la requête brute."
            )
        result.update({
            "status": status,
            "stage": stage,
            "conclusion": conclusion,
            "recommended_action": action,
        })
        return result

    edge_confidence = settings.get("edge_confidence")
    accepted = _effective_edges(matching_edges, edge_confidence)
    if not accepted:
        result.update({
            "status": "edge_filtered_by_confidence",
            "stage": "flow_reconstruction",
            "conclusion": (
                "L'arête est persistée avec une résolution possible ou inférée, tandis que "
                "le snapshot de flux accepte seulement les arêtes exactes."
            ),
            "recommended_action": (
                "Relancez l'indexation avec `--codeql-edge-confidence possible` si cette "
                "incertitude est acceptable."
            ),
        })
        return result

    flow_ids = [
        flow.id for flow in inventory.code_flows
        if _flow_uses_edge(flow, caller, callee, callee_alias_names)
    ]
    result["flow_ids"] = flow_ids
    if flow_ids:
        result.update({
            "status": "edge_used_in_flow",
            "stage": "complete",
            "conclusion": "L'arête est persistée et utilisée par au moins un flux IN vers OUT.",
            "recommended_action": "Inspectez les identifiants de flux retournés.",
        })
        return result

    if snapshot["flow_snapshot_status"] == "partial":
        result.update({
            "status": "flow_snapshot_incomplete",
            "stage": "snapshot",
            "conclusion": (
                "L'arête est persistée, mais le snapshot des flux est partiel et peut ne pas "
                "contenir le chemin IN vers OUT correspondant."
            ),
            "recommended_action": "Reprenez ou relancez l'indexation CodeQL.",
        })
        return result

    effective = _effective_edges(inventory.codeql_call_edges, edge_confidence)
    input_methods = {
        method.id for method in inventory.integration_methods
        if method.input_endpoint_ids
    }
    output_methods = {
        method.id for method in inventory.integration_methods
        if method.output_endpoint_ids
    }
    has_input_path = _reachable(input_methods, caller.id, effective)
    has_output_path = any(
        _can_reach_any(callee_id, output_methods, effective)
        for callee_id in callee_ids
    )
    result["flow_context"] = {
        "caller_reachable_from_input": has_input_path,
        "callee_can_reach_output": has_output_path,
    }
    if not has_input_path:
        status = "edge_outside_input_path"
        conclusion = (
            "L'arête est persistée, mais aucune méthode portant un port IN n'atteint "
            "la méthode appelante. Elle ne peut donc pas apparaître dans un flux IN vers OUT."
        )
        action = "Vérifiez l'extraction du port IN et les arêtes situées avant la méthode appelante."
    elif not has_output_path:
        status = "edge_outside_output_path"
        conclusion = (
            "L'arête est persistée, mais la méthode appelée n'atteint aucune méthode portant "
            "un port OUT. Elle ne peut donc pas apparaître dans un flux IN vers OUT."
        )
        action = "Vérifiez l'extraction du port OUT et les arêtes situées après la méthode appelée."
    else:
        status = "edge_not_materialized"
        conclusion = (
            "L'arête et son contexte IN vers OUT sont persistés, mais aucun flux conservé ne "
            "l'utilise. Vérifiez la limite de profondeur et la déduplication des flux."
        )
        action = "Exécutez `systemlens flows list --module MODULE --explain`."
    result.update({
        "status": status,
        "stage": "flow_reconstruction",
        "conclusion": conclusion,
        "recommended_action": action,
    })
    return result


def render_call_edge_diagnostic_text(result: Mapping[str, object]) -> str:
    """Render one call-edge diagnosis for terminal use."""
    lines = [
        f"Diagnostic d'appel : {result['caller_query']} -> {result['callee_query']}",
        f"Verdict : {result['status']}",
        f"Étage : {result['stage']}",
        str(result["conclusion"]),
        f"Action : {result['recommended_action']}",
    ]
    presence = result.get("presence")
    if isinstance(presence, dict):
        for key, label in (
            ("caller_node", "Nœud caller"),
            ("callee_node", "Nœud callee"),
            ("edge", "Arc caller -> callee"),
        ):
            values = presence.get(key)
            if isinstance(values, dict):
                lines.append(
                    f"- {label} : CodeQL={values.get('codeql', 'unknown')} · "
                    f"AST={values.get('ast', 'unknown')} · "
                    f"preuve CodeQL={values.get('proof_codeql', 'not_proven')} · "
                    f"preuve AST={values.get('proof_ast', 'not_proven')}"
                )
    for role in ("caller", "callee"):
        candidates = result.get(f"{role}_candidates", [])
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            lines.append(
                f"- {role} : {candidate['qualified_method']} "
                f"[{candidate['id']}] ({candidate['path']}:{candidate['start_line']})"
            )
    edges = result.get("edges", [])
    if isinstance(edges, list):
        for edge in edges:
            if isinstance(edge, dict):
                suffix = " inferred" if edge.get("inferred") else ""
                lines.append(
                    f"- edge : {edge['path']}:{edge['line']} "
                    f"confidence={edge['dispatch_confidence']}{suffix}"
                )
    flow_ids = result.get("flow_ids", [])
    if isinstance(flow_ids, list) and flow_ids:
        lines.append(f"Flux : {', '.join(str(flow_id) for flow_id in flow_ids)}")
    return "\n".join(lines)
