"""Load a conservative, AI-produced architecture graph manifest.

The manifest is an input adapter only: it is never persisted in the SQLite
source inventory.  Confirmed and proposed relations are projected onto the
existing HTML graph model; ambiguous and unresolved claims become indexing
issues so an AI cannot silently turn a guess into a dependency.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from systemlens.domain.graph import GraphEdge
from systemlens.domain.code_flows import CodeFlow, CodeFlowStep
from systemlens.domain.models import GraphFact, MessageEndpoint, compute_endpoint_id


MANIFEST_FORMAT = "systemlens-ai-graph-v1"
_NODE_KINDS = {"service", "external_service", "topic", "collection"}
_EDGE_KINDS = {"http", "event", "data"}
_STATUSES = {"confirmed", "proposed", "ambiguous", "unresolved"}
_CONFIDENCES = {"high", "medium", "low", "unknown"}
_FACT_NODE_KINDS = _NODE_KINDS | {"data_schema", "message_channel"}
_FACT_EDGE_KINDS = _EDGE_KINDS | {"serves", "calls", "reads", "writes", "publishes", "consumes", "provides"}


def _relative_path(value: Any, field: str) -> str:
    path = _required_string(value, field)
    if Path(path).is_absolute() or "\\" in path or ".." in Path(path).parts:
        raise AiGraphError(f"{field} doit être un chemin relatif au projet.")
    return path


class AiGraphError(ValueError):
    """A safe, user-actionable AI graph manifest validation error."""


def graph_facts_manifest(
    facts: list[GraphFact], *, namespace: str, complete: bool = True,
) -> dict[str, Any]:
    """Serialize persisted enrichment facts as a re-importable manifest.

    The manifest keeps the database identity in ``storage_id``. This is an
    export-only compatibility field for facts created through MCP, whose IDs
    are hashes rather than the stable IDs supplied by an AI manifest.
    """
    selected = [fact for fact in facts if fact.namespace == namespace]
    node_facts = [fact for fact in selected if fact.fact_type == "node"]
    node_ids: dict[tuple[str, str], str] = {
        (fact.kind, fact.name or ""): fact.id for fact in node_facts
    }
    nodes: list[dict[str, Any]] = []
    for fact in node_facts:
        node: dict[str, Any] = {
            "id": fact.id,
            "storage_id": fact.id,
            "kind": fact.kind,
            "name": fact.name,
            "status": fact.status,
            "confidence": fact.confidence,
            "evidence": ([{"path": fact.evidence_path, "start_line": fact.evidence_line}]
                         if fact.evidence_path else []),
            "metadata": fact.metadata or {},
            "pass": fact.pass_id,
            "source_revision": fact.source_revision,
        }
        if fact.note is not None:
            node["reason"] = fact.note
        if fact.technology is not None:
            node["technology"] = fact.technology
        if fact.module is not None:
            node["module"] = fact.module
        nodes.append(node)

    synthetic_nodes: dict[tuple[str, str], str] = {}
    edges: list[dict[str, Any]] = []
    for fact in selected:
        if fact.fact_type != "edge":
            continue
        source_key = (fact.source_kind or "", fact.source_name or "")
        target_key = (fact.target_kind or "", fact.target_name or "")
        for key in (source_key, target_key):
            if key not in node_ids:
                synthetic_nodes.setdefault(key, f"ref:{key[0]}:{key[1]}")
        source_id = node_ids[source_key] if source_key in node_ids else synthetic_nodes[source_key]
        target_id = node_ids[target_key] if target_key in node_ids else synthetic_nodes[target_key]
        edge: dict[str, Any] = {
            "id": fact.id,
            "storage_id": fact.id,
            "source": source_id,
            "target": target_id,
            "kind": fact.kind,
            "relation": fact.relation,
            "status": fact.status,
            "confidence": fact.confidence,
            "evidence": ([{"path": fact.evidence_path, "start_line": fact.evidence_line}]
                         if fact.evidence_path else []),
            "metadata": fact.metadata or {},
            "pass": fact.pass_id,
            "source_revision": fact.source_revision,
        }
        if fact.note is not None:
            edge["reason"] = fact.note
        if fact.technology is not None:
            edge["technology"] = fact.technology
        if fact.module is not None:
            edge["module"] = fact.module
        edges.append(edge)

    for (kind, name), node_id in synthetic_nodes.items():
        nodes.append({
            "id": node_id,
            "kind": kind,
            "name": name,
            "status": "confirmed",
            "confidence": "unknown",
            "metadata": {"systemlens_export_reference": True},
        })
    return {
        "format": MANIFEST_FORMAT,
        "generated_by": {"agent": "systemlens", "namespace": namespace},
        "mode": "complete" if complete else "partial",
        "nodes": nodes,
        "edges": edges,
    }


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AiGraphError(f"{field} doit être une chaîne non vide.")
    return value.strip()


def _optional_module(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, field)


def _evidence(value: Any, field: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AiGraphError(f"{field} doit être une liste.")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise AiGraphError(f"{field}[{index}] doit être un objet.")
        path = item.get("path")
        if path is not None:
            path = _required_string(path, f"{field}[{index}].path")
            if Path(path).is_absolute() or "\\" in path:
                raise AiGraphError(f"{field}[{index}].path doit être relatif au projet.")
        result.append(dict(item))
    return result


def load_fact_manifest(
    path: Path, *, namespace: str | None = None, pass_id: str | None = None,
    source_revision: str | None = None,
) -> tuple[list[GraphFact], str, bool]:
    """Load a JSON manifest as replaceable persisted graph facts.

    This is deliberately separate from ``load_ai_graph``: the latter is a
    legacy read-only HTML projection, while this adapter is the persistence
    contract for iterative enrichment.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AiGraphError(f"Impossible de lire le manifeste {path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("format") != MANIFEST_FORMAT:
        raise AiGraphError(f"format attendu: {MANIFEST_FORMAT}")
    generated = document.get("generated_by")
    generated = generated if isinstance(generated, dict) else {}
    resolved_namespace = namespace or generated.get("namespace") or "ai-architecture"
    if not isinstance(resolved_namespace, str) or not resolved_namespace.strip():
        raise AiGraphError("namespace doit être une chaîne non vide.")
    resolved_pass = pass_id or generated.get("pass")
    resolved_revision = source_revision or generated.get("source_revision")
    if resolved_pass is not None and not isinstance(resolved_pass, str):
        raise AiGraphError("pass doit être une chaîne.")
    if resolved_revision is not None and not isinstance(resolved_revision, str):
        raise AiGraphError("source_revision doit être une chaîne.")
    raw_nodes = document.get("nodes")
    raw_edges = document.get("edges")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise AiGraphError("nodes et edges doivent être des listes.")
    nodes: dict[str, dict[str, Any]] = {}
    facts: list[GraphFact] = []
    used_ids: set[str] = set()
    raw_ids: set[str] = set()

    def evidence_fields(raw: dict[str, Any], field: str) -> tuple[str | None, int | None]:
        evidence = _evidence(raw.get(field), field)
        first = evidence[0] if evidence else {}
        line = first.get("start_line")
        return first.get("path"), line if isinstance(line, int) and line >= 1 else None

    def fact_id(raw_id: str, fact_type: str) -> str:
        # The namespace is part of the storage key because graph_facts has a
        # single primary key and must support independent producers.
        return f"{resolved_namespace}::{fact_type}::{raw_id}"

    for index, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict):
            raise AiGraphError(f"nodes[{index}] doit être un objet.")
        raw_id = _required_string(raw.get("id"), f"nodes[{index}].id")
        kind = _required_string(raw.get("kind"), f"nodes[{index}].kind")
        name = _required_string(raw.get("name"), f"nodes[{index}].name")
        module = _optional_module(raw.get("module"), f"nodes[{index}].module")
        if raw_id in raw_ids:
            raise AiGraphError(f"identifiant de nœud dupliqué: {raw_id}")
        nodes[raw_id] = raw
        raw_ids.add(raw_id)
        supplied_storage_id = raw.get("storage_id")
        if supplied_storage_id is not None and not isinstance(supplied_storage_id, str):
            raise AiGraphError(f"nodes[{index}].storage_id doit être une chaîne.")
        stored_id = supplied_storage_id or fact_id(raw_id, "node")
        if stored_id in used_ids:
            raise AiGraphError(f"identifiant de stockage dupliqué: {stored_id}")
        used_ids.add(stored_id)
        evidence_path, evidence_line = evidence_fields(raw, "evidence")
        status = raw.get("status", "confirmed")
        confidence = raw.get("confidence", "unknown")
        if status not in _STATUSES or confidence not in _CONFIDENCES:
            raise AiGraphError(f"nodes[{index}]: status ou confidence invalide.")
        if status in {"ambiguous", "unresolved"} and not raw.get("reason"):
            raise AiGraphError(f"nodes[{index}].reason est requis pour {status}.")
        metadata = raw.get("metadata", {})
        if not isinstance(metadata, dict):
            raise AiGraphError(f"nodes[{index}].metadata doit être un objet.")
        if raw.get("owner") is not None:
            metadata = {**metadata, "owner": raw["owner"]}
        facts.append(GraphFact(
            id=stored_id, fact_type="node", kind=kind, name=name,
            source_kind=None, source_name=None, target_kind=None, target_name=None,
            relation=None, origin="ai", confidence=confidence,
            evidence_path=evidence_path, evidence_line=evidence_line,
            note=raw.get("reason") if isinstance(raw.get("reason"), str) else None,
            technology=raw.get("technology") if isinstance(raw.get("technology"), str) else None,
            metadata=metadata, namespace=resolved_namespace, status=status,
            module=module,
            pass_id=raw.get("pass", resolved_pass),
            source_revision=raw.get("source_revision", resolved_revision),
        ))

    for index, raw in enumerate(raw_edges):
        if not isinstance(raw, dict):
            raise AiGraphError(f"edges[{index}] doit être un objet.")
        raw_id = _required_string(raw.get("id"), f"edges[{index}].id")
        source_id = _required_string(raw.get("source"), f"edges[{index}].source")
        target_id = _required_string(raw.get("target"), f"edges[{index}].target")
        kind = _required_string(raw.get("kind"), f"edges[{index}].kind")
        module = _optional_module(raw.get("module"), f"edges[{index}].module")
        if raw_id in raw_ids:
            raise AiGraphError(f"identifiant de fait dupliqué: {raw_id}")
        if source_id not in nodes or target_id not in nodes:
            raise AiGraphError(f"{raw_id}: source ou target inconnu.")
        relation = raw.get("relation") or kind
        if not isinstance(relation, str) or not relation.strip():
            raise AiGraphError(f"{raw_id}.relation doit être une chaîne non vide.")
        status = raw.get("status", "confirmed")
        confidence = raw.get("confidence", "unknown")
        if status not in _STATUSES or confidence not in _CONFIDENCES:
            raise AiGraphError(f"{raw_id}: status ou confidence invalide.")
        if status in {"ambiguous", "unresolved"} and not raw.get("reason"):
            raise AiGraphError(f"{raw_id}.reason est requis pour {status}.")
        evidence_path, evidence_line = evidence_fields(raw, "evidence")
        metadata = raw.get("metadata", {})
        if not isinstance(metadata, dict):
            raise AiGraphError(f"{raw_id}.metadata doit être un objet.")
        for key in ("channel", "topic", "route", "message_type"):
            if key in raw and key not in metadata:
                metadata[key] = raw[key]
        source, target = nodes[source_id], nodes[target_id]
        supplied_storage_id = raw.get("storage_id")
        if supplied_storage_id is not None and not isinstance(supplied_storage_id, str):
            raise AiGraphError(f"{raw_id}.storage_id doit être une chaîne.")
        stored_id = supplied_storage_id or fact_id(raw_id, "edge")
        if stored_id in used_ids:
            raise AiGraphError(f"identifiant de stockage dupliqué: {stored_id}")
        raw_ids.add(raw_id)
        used_ids.add(stored_id)
        facts.append(GraphFact(
            id=stored_id, fact_type="edge", kind=kind,
            name=None, source_kind=str(source["kind"]), source_name=str(source["name"]),
            target_kind=str(target["kind"]), target_name=str(target["name"]),
            relation=relation, origin="ai", confidence=confidence,
            evidence_path=evidence_path, evidence_line=evidence_line,
            note=raw.get("reason") if isinstance(raw.get("reason"), str) else None,
            technology=raw.get("technology") if isinstance(raw.get("technology"), str) else None,
            metadata=metadata, namespace=resolved_namespace, status=status,
            module=module,
            pass_id=raw.get("pass", resolved_pass),
            source_revision=raw.get("source_revision", resolved_revision),
        ))
    complete = document.get("mode", "partial") == "complete"
    if document.get("mode", "partial") not in {"partial", "complete"}:
        raise AiGraphError("mode doit être 'partial' ou 'complete'.")
    return facts, resolved_namespace, complete


def load_direct_flow_manifest(
    path: Path,
) -> tuple[list[MessageEndpoint], list[CodeFlow]]:
    """Load optional direct-analysis endpoints and ordered flows.

    The graph manifest keeps topology facts and source-evidenced flow
    projections in one versioned handoff. This loader is used only by the
    import path and never runs a source extractor.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AiGraphError(f"Impossible de lire le manifeste {path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("format") != MANIFEST_FORMAT:
        raise AiGraphError(f"format attendu: {MANIFEST_FORMAT}")
    raw_endpoints = document.get("endpoints", [])
    raw_flows = document.get("flows", [])
    if not isinstance(raw_endpoints, list) or not isinstance(raw_flows, list):
        raise AiGraphError("endpoints et flows doivent être des listes.")

    endpoints: list[MessageEndpoint] = []
    endpoint_ids: set[str] = set()
    for index, raw in enumerate(raw_endpoints):
        if not isinstance(raw, dict):
            raise AiGraphError(f"endpoints[{index}] doit être un objet.")
        endpoint_id = _required_string(raw.get("id"), f"endpoints[{index}].id")
        if endpoint_id in endpoint_ids:
            raise AiGraphError(f"identifiant d'endpoint dupliqué: {endpoint_id}")
        system = _required_string(raw.get("system"), f"endpoints[{index}].system")
        role = _required_string(raw.get("role"), f"endpoints[{index}].role")
        topic = _required_string(raw.get("topic"), f"endpoints[{index}].topic")
        service = _required_string(raw.get("service"), f"endpoints[{index}].service")
        path_value = _relative_path(raw.get("path"), f"endpoints[{index}].path")
        start_line = raw.get("start_line")
        end_line = raw.get("end_line", start_line)
        if not isinstance(start_line, int) or start_line < 1:
            raise AiGraphError(f"endpoints[{index}].start_line doit être positif.")
        if not isinstance(end_line, int) or end_line < start_line:
            raise AiGraphError(f"endpoints[{index}].end_line est invalide.")
        endpoint = MessageEndpoint(
            id=endpoint_id, role=role, system=system, topic=topic,
            topic_dynamic=bool(raw.get("topic_dynamic", False)), source="manifest",
            framework=raw.get("framework") if isinstance(raw.get("framework"), str) else "direct-analysis",
            path=path_value, start_line=start_line, end_line=end_line,
            snippet=str(raw.get("snippet", "direct-analysis")), module=service,
            qualified_name=raw.get("qualified_name") if isinstance(raw.get("qualified_name"), str) else None,
            message_type=raw.get("message_type") if isinstance(raw.get("message_type"), str) else None,
            topic_display=raw.get("topic_display") if isinstance(raw.get("topic_display"), str) else None,
        )
        try:
            endpoint.validate_semantics()
        except ValueError as exc:
            raise AiGraphError(f"endpoints[{index}]: {exc}") from exc
        endpoints.append(endpoint)
        endpoint_ids.add(endpoint_id)

    flows: list[CodeFlow] = []
    flow_ids: set[str] = set()
    for index, raw in enumerate(raw_flows):
        if not isinstance(raw, dict):
            raise AiGraphError(f"flows[{index}] doit être un objet.")
        flow_id = _required_string(raw.get("id"), f"flows[{index}].id")
        if flow_id in flow_ids:
            raise AiGraphError(f"identifiant de flux dupliqué: {flow_id}")
        module = _required_string(raw.get("module"), f"flows[{index}].module")
        method = _required_string(raw.get("method"), f"flows[{index}].method")
        flow_path = _relative_path(raw.get("path"), f"flows[{index}].path")
        start_line = raw.get("start_line")
        end_line = raw.get("end_line", start_line)
        if not isinstance(start_line, int) or start_line < 1:
            raise AiGraphError(f"flows[{index}].start_line doit être positif.")
        if not isinstance(end_line, int) or end_line < start_line:
            raise AiGraphError(f"flows[{index}].end_line est invalide.")
        raw_steps = raw.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise AiGraphError(f"flows[{index}].steps doit être une liste non vide.")
        steps: list[CodeFlowStep] = []
        for step_index, step in enumerate(raw_steps):
            if not isinstance(step, dict):
                raise AiGraphError(f"flows[{index}].steps[{step_index}] doit être un objet.")
            order = step.get("order")
            step_start = step.get("start_line")
            step_end = step.get("end_line", step_start)
            if not isinstance(order, int) or order < 1:
                raise AiGraphError(f"flows[{index}].steps[{step_index}].order est invalide.")
            if not isinstance(step_start, int) or step_start < 1 or not isinstance(step_end, int) or step_end < step_start:
                raise AiGraphError(f"flows[{index}].steps[{step_index}] contient des lignes invalides.")
            step_endpoint_id: str | None = step.get("endpoint_id")
            if step_endpoint_id is not None:
                step_endpoint_id = _required_string(step_endpoint_id, f"flows[{index}].steps[{step_index}].endpoint_id")
                if step_endpoint_id not in endpoint_ids:
                    raise AiGraphError(f"flows[{index}].steps[{step_index}] référence un endpoint inconnu.")
            steps.append(CodeFlowStep(
                order=order,
                kind=_required_string(step.get("kind"), f"flows[{index}].steps[{step_index}].kind"),
                name=_required_string(step.get("name"), f"flows[{index}].steps[{step_index}].name"),
                path=_relative_path(step.get("path"), f"flows[{index}].steps[{step_index}].path"),
                start_line=step_start, end_line=step_end, endpoint_id=step_endpoint_id,
                operation=step.get("operation") if isinstance(step.get("operation"), str) else None,
            ))
        flows.append(CodeFlow(
            id=flow_id, module=module, method=method, path=flow_path,
            start_line=start_line, end_line=end_line,
            status=_required_string(raw.get("status", "potential"), f"flows[{index}].status"),
            confidence=_required_string(raw.get("confidence", "medium"), f"flows[{index}].confidence"),
            reason=_required_string(raw.get("reason", "direct source analysis"), f"flows[{index}].reason"),
            steps=tuple(sorted(steps, key=lambda step: step.order)),
            reconciliation=_required_string(raw.get("reconciliation", "complete"), f"flows[{index}].reconciliation"),
            alternative_count=int(raw.get("alternative_count", 1)),
        ))
        flow_ids.add(flow_id)
    return endpoints, flows


def load_ai_graph(path: Path) -> tuple[dict[str, list[MessageEndpoint]], list[GraphEdge], dict[str, list[str]], list[str]]:
    """Validate and project an AI graph manifest onto the current graph model."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AiGraphError(f"Impossible de lire le manifeste {path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("format") != MANIFEST_FORMAT:
        raise AiGraphError(f"format attendu: {MANIFEST_FORMAT}")

    raw_nodes = document.get("nodes")
    raw_edges = document.get("edges")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise AiGraphError("nodes et edges doivent être des listes.")

    nodes: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict):
            raise AiGraphError(f"nodes[{index}] doit être un objet.")
        node_id = _required_string(raw.get("id"), f"nodes[{index}].id")
        kind = _required_string(raw.get("kind"), f"nodes[{index}].kind")
        if kind not in _NODE_KINDS:
            raise AiGraphError(f"nodes[{index}].kind inconnu: {kind}")
        if node_id in nodes:
            raise AiGraphError(f"identifiant de nœud dupliqué: {node_id}")
        node = dict(raw)
        node["name"] = _required_string(raw.get("name"), f"nodes[{index}].name")
        node["evidence"] = _evidence(raw.get("evidence"), f"nodes[{index}].evidence")
        nodes[node_id] = node

    services: dict[str, list[MessageEndpoint]] = {
        str(node["name"]): []
        for node in nodes.values()
        if node["kind"] in {"service", "external_service"}
    }
    collections: dict[str, list[str]] = {}
    for node in nodes.values():
        if node["kind"] == "collection":
            owner = node.get("owner")
            if not isinstance(owner, str) or owner not in services:
                raise AiGraphError(f"la collection {node['id']} doit avoir un owner service valide.")
            collections.setdefault(owner, []).append(str(node["name"]))

    edges: list[GraphEdge] = []
    issues: list[str] = []
    for index, raw in enumerate(raw_edges):
        if not isinstance(raw, dict):
            raise AiGraphError(f"edges[{index}] doit être un objet.")
        edge_id = _required_string(raw.get("id"), f"edges[{index}].id")
        source_id = _required_string(raw.get("source"), f"edges[{index}].source")
        target_id = _required_string(raw.get("target"), f"edges[{index}].target")
        kind = _required_string(raw.get("kind"), f"edges[{index}].kind")
        status = raw.get("status", "confirmed")
        confidence = raw.get("confidence", "unknown")
        if source_id not in nodes or target_id not in nodes:
            raise AiGraphError(f"{edge_id}: source ou target inconnu.")
        if kind not in _EDGE_KINDS or status not in _STATUSES or confidence not in _CONFIDENCES:
            raise AiGraphError(f"{edge_id}: kind, status ou confidence invalide.")
        evidence = _evidence(raw.get("evidence"), f"edges[{index}].evidence")
        if status in {"ambiguous", "unresolved"}:
            reason = raw.get("reason", "relation non résolue")
            issues.append(f"{edge_id}: {reason}")
            continue
        source = nodes[source_id]
        target = nodes[target_id]
        source_name, target_name = str(source["name"]), str(target["name"])
        if kind == "data":
            if source["kind"] not in {"service", "external_service"} or target["kind"] != "collection":
                raise AiGraphError(f"{edge_id}: une relation data doit viser une collection depuis un service.")
            continue
        if source["kind"] not in {"service", "external_service"} or target["kind"] not in {"service", "external_service"}:
            raise AiGraphError(f"{edge_id}: une relation {kind} doit relier deux services.")
        source_site = _endpoint("call" if kind == "http" else "produce", kind, raw, source_name, edge_id, evidence)
        target_site = _endpoint("serve" if kind == "http" else "consume", kind, raw, target_name, edge_id, evidence)
        edges.append(GraphEdge("rest" if kind == "http" else "kafka", source_name, target_name, source_site, target_site))
    return services, edges, collections, issues


def _endpoint(role: str, kind: str, raw: dict[str, Any], service: str, edge_id: str, evidence: list[dict[str, Any]]) -> MessageEndpoint:
    channel = _required_string(raw.get("channel") or raw.get("label") or edge_id, f"{edge_id}.channel")
    path = evidence[0].get("path", "ai-graph.json") if evidence else "ai-graph.json"
    line = evidence[0].get("start_line", 1) if evidence else 1
    if not isinstance(line, int) or line < 1:
        line = 1
    topic = channel if kind == "event" else str(raw.get("label") or channel)
    return MessageEndpoint(
        id=compute_endpoint_id(role, topic, str(path), line), role=role,
        system="kafka" if kind == "event" else "rest", topic=topic,
        topic_dynamic=False, source="manifest", framework="ai-analysis",
        path=str(path), start_line=line, end_line=line,
        snippet=f"systemlens-ai-edge:{edge_id}",
        message_type=raw.get("message_type") if isinstance(raw.get("message_type"), str) else None,
    )
