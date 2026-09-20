"""Build the browser-facing graph model from an architecture snapshot."""

import re
from pathlib import Path
from typing import Any

import networkx as nx

from systemlens.domain.graph import (
    GraphEdge,
    external_microservice_names,
    graph_edge_rest_resource,
    resolve_rest_target_service,
)
from systemlens.domain.models import (
    ArchitectureRelation,
    ExtractionDiagnostic,
    Finding,
    GraphFact,
    MessageEndpoint,
)
from systemlens.domain.module_inventory import (
    DiscoveredModule,
    ModuleDependency,
    MongoPersistenceClass,
    module_identity,
)
from systemlens.domain.code_flows import CodeFlow, IntegrationMethod
from systemlens.render.namespaces import project_namespace, project_namespace_path
from systemlens.render.software_layers import software_layer
from systemlens.render._graph_view_helpers import (
    _endpoint_vscode_uri,
    _mongodb_collection_nodes,
    _mongodb_visual_graph_edges,
    _openapi_contract_evidence_path,
    _resolve_openapi_contract_owner,
    _rest_resources_served,
    _visual_graph_edges,
    _visual_link_evidence,
    _vscode_file_uri,
    _vscode_uri,
)
from systemlens.render.likec4_export import _complexity_ranking
from systemlens.render.snapshot import kafka_dto_views


def _deduplicated_call_port_links(edges: list[GraphEdge]) -> list[dict[str, str]]:
    """Return one browser call-graph edge per endpoint pair and protocol.

    The architecture snapshot can contain several equivalent evidence rows
    for one call. A NetworkX multigraph gives those rows a canonical directed
    identity before the HTML model is built, preventing duplicate arcs in the
    Flux view while leaving the aggregated evidence on the architecture links.
    """
    graph = nx.MultiDiGraph()
    for edge in edges:
        if (
            edge.to_endpoint is None
            or edge.from_endpoint.system not in {"rest", "kafka"}
            or edge.to_endpoint.system not in {"rest", "kafka"}
            or edge.from_endpoint.role not in {"call", "produce"}
            or edge.to_endpoint.role not in {"serve", "consume"}
        ):
            continue
        source = edge.from_endpoint.id
        target = edge.to_endpoint.id
        graph.add_edge(source, target, key=edge.kind, kind=edge.kind)
    return [
        {
            "source_endpoint_id": source,
            "target_endpoint_id": target,
            "kind": str(kind),
        }
        for source, target, kind in sorted(graph.edges(keys=True))
    ]


def _fact_runtime_namespaces(fact: GraphFact) -> list[str]:
    """Return runtime namespaces from an enrichment fact without guessing."""
    metadata: dict[str, Any] = fact.metadata or {}
    values = metadata.get("namespaces")
    if not isinstance(values, list):
        values = [metadata.get("namespace")]
    return [str(value) for value in values if value]


def _canonical_resource_kind(kind: str | None) -> str:
    """Map persisted relation vocabulary to the visual resource vocabulary."""
    return {
        "topic": "kafka_topic",
        "collection": "mongodb_collection",
        "data_schema": "data_schema",
    }.get(kind or "", kind or "")


def _kafka_message_type_status(
    producer: MessageEndpoint | None, consumer: MessageEndpoint | None,
) -> tuple[str, str | None]:
    """Describe type evidence without making the type part of topic identity.

    Kafka topology is keyed by the concrete topic.  Missing or conflicting
    Java payload declarations therefore annotate a relation but never remove
    the relation itself.
    """
    produced = producer.message_type if producer else None
    consumed = consumer.message_type if consumer else None
    if produced and consumed:
        if produced == consumed:
            return "consistent", None
        return "mismatch", "Types Java producteur/consommateur différents ; lien conservé sur le topic."
    if produced or consumed:
        return "partial", "Type Java connu d'un seul côté ; lien conservé sur le topic."
    return "unknown", "Type Java du message non déterminé ; lien conservé sur le topic."


def _indexing_issues(
    endpoints_by_service: dict[str, list[MessageEndpoint]],
    edges: list[GraphEdge],
    warnings: list[str] | None,
    modules: list[DiscoveredModule],
    source_roots: list[Path] | None,
    root_path: Path | None,
    diagnostics: list[ExtractionDiagnostic] | None = None,
) -> list[dict[str, str]]:
    """Return every unresolved inventory fact suitable for the HTML export."""
    issues: list[dict[str, str]] = []

    def add(
        severity: str, category: str, message: str, endpoint: MessageEndpoint | None = None
    ) -> None:
        issue = {"severity": severity, "category": category, "message": message}
        if endpoint is not None:
            issue["location"] = f"{endpoint.path}:{endpoint.start_line}"
            issue["vscode_uri"] = _endpoint_vscode_uri(
                endpoint, modules, source_roots, root_path
            )
        issues.append(issue)

    for warning in dict.fromkeys(warnings or []):
        add("warning", "Avertissement d'inventaire", warning)
    for diagnostic in diagnostics or []:
        issues.append({
            "severity": diagnostic.severity,
            "category": "Diagnostic d'extraction",
            "message": f"{diagnostic.extractor}: {diagnostic.detail}",
            "location": diagnostic.path,
        })

    matched_http_call_ids = {edge.from_endpoint.id for edge in edges if edge.kind == "rest"}
    service_names = sorted(endpoints_by_service)
    for service, endpoints in sorted(endpoints_by_service.items()):
        for endpoint in sorted(endpoints, key=lambda item: (item.path, item.start_line, item.id)):
            if endpoint.system == "kafka" and endpoint.topic_dynamic:
                add(
                    "warning",
                    "Topic Kafka dynamique",
                    f"{service} : le topic {endpoint.topic!r} ne peut pas etre resolu statiquement.",
                    endpoint,
                )
            if endpoint.system == "kafka" and not endpoint.message_type:
                add(
                    "info",
                    "Type Kafka inconnu",
                    f"{service} : le type Java du message sur {endpoint.topic!r} n'a pas ete deduit.",
                    endpoint,
                )
            if endpoint.system == "rest" and endpoint.role == "call" and endpoint.id not in matched_http_call_ids:
                resolution = resolve_rest_target_service(endpoint, service_names)
                if resolution.status == "ambiguous":
                    add(
                        "warning",
                        "Cible HTTP ambiguë",
                        f"{service} : la cible explicite {resolution.hint!r} correspond à plusieurs microservices.",
                        endpoint,
                    )
                    continue
                add(
                    "warning" if endpoint.topic_dynamic else "info",
                    "Appel HTTP non rapproche",
                    f"{service} : aucun microservice fournisseur n'a ete identifie pour {endpoint.topic!r}.",
                    endpoint,
                )

    severity_rank = {"warning": 0, "info": 1}
    return sorted(
        issues,
        key=lambda item: (severity_rank[item["severity"]], item["category"], item["message"]),
    )


def build_graph_view_model(
    endpoints_by_service: dict[str, list[MessageEndpoint]],
    edges: list[GraphEdge],
    collections_by_service: dict[str, list[str]] | None = None,
    modules_by_service: dict[str, DiscoveredModule] | None = None,
    indexing_warnings: list[str] | None = None,
    build_modules: list[DiscoveredModule] | None = None,
    module_dependencies: list[ModuleDependency] | None = None,
    source_roots: list[Path] | None = None,
    findings_by_service: dict[str, list[Finding]] | None = None,
    root_path: Path | None = None,
    request_reply_strategy1: bool = False,
    diagnostics: list[ExtractionDiagnostic] | None = None,
    kafka_dto_definitions: list[dict[str, object]] | None = None,
    openapi_contracts: list[dict[str, object]] | None = None,
    asyncapi_contracts: list[dict[str, object]] | None = None,
    graph_facts: list[GraphFact] | None = None,
    strategy1: bool = False,
    architecture_relations: list[ArchitectureRelation] | None = None,
    integration_methods: list[IntegrationMethod] | None = None,
    code_flows: list[CodeFlow] | None = None,
) -> dict[str, object]:
    """Render an interactive Sigma.js graph as a self-contained HTML document.

    Sigma.js and Graphology are loaded from their CDNs at viewing time; graph
    data is embedded locally and safely serialized so the generated file
    contains no application data in executable JavaScript.
    """
    external_services = external_microservice_names(edges)
    ordered_services = sorted(set(endpoints_by_service) | external_services)
    kafka_endpoints = [
        endpoint
        for endpoints in endpoints_by_service.values()
        for endpoint in endpoints
        if endpoint.system == "kafka"
    ]
    # Keep concrete topics visible even when no opposite endpoint was found.
    # This is evidence of an integration, not an inferred producer/consumer
    # pairing; the unmatched endpoint is rendered as a partial relation below.
    kafka_topics = sorted({
        *[edge.from_endpoint.topic for edge in edges if edge.kind == "kafka"],
        *[endpoint.topic for endpoint in kafka_endpoints if not endpoint.topic_dynamic],
    })
    topic_message_types: dict[str, dict[str, set[str]]] = {
        topic: {"produce": set(), "consume": set()} for topic in kafka_topics
    }
    published_message_types_by_relation: dict[tuple[str, str], set[str]] = {}
    consumed_message_types_by_relation: dict[tuple[str, str], set[str]] = {}
    for service, endpoints in endpoints_by_service.items():
        for endpoint in endpoints:
            if (
                endpoint.system == "kafka"
                and endpoint.topic in topic_message_types
                and endpoint.message_type
            ):
                topic_message_types[endpoint.topic][endpoint.role].add(endpoint.message_type)
                if endpoint.role == "produce":
                    published_message_types_by_relation.setdefault((service, endpoint.topic), set()).add(
                        endpoint.message_type
                    )
                if endpoint.role == "consume":
                    consumed_message_types_by_relation.setdefault((service, endpoint.topic), set()).add(
                        endpoint.message_type
                    )
    kafka_endpoint_ids_in_edges = {
        endpoint.id
        for edge in edges
        if edge.kind == "kafka"
        for endpoint in (edge.from_endpoint, edge.to_endpoint)
        if endpoint is not None
    }
    module_details = modules_by_service or {}
    all_modules = list({
        module.path.resolve(): module
        for module in [*(build_modules or []), *module_details.values()]
    }.values())
    module_by_identity = {module_identity(module): module for module in all_modules}
    fact_namespaces_by_service: dict[str, set[str]] = {}
    for fact in graph_facts or []:
        fact_names = {value for value in (fact.name, fact.source_name, fact.target_name) if value}
        for service in ordered_services:
            if service in fact_names:
                fact_namespaces_by_service.setdefault(service, set()).add(fact.namespace)
    openapi_specs = {
        (str(contract["module"]), str(contract["path"])): contract["spec"]
        for contract in openapi_contracts or []
    }
    asyncapi_specs = {
        (str(contract["module"]), str(contract["path"])): contract["spec"]
        for contract in asyncapi_contracts or []
    }

    def module_owns_openapi_file(module: DiscoveredModule, path: str) -> bool:
        """Whether ``module`` directly encloses an OpenAPI source file.

        The inventory normally applies this ownership rule. Reapply it while
        building the export so an older snapshot or federated inventory cannot
        render one physical contract for both an enclosing project and a
        nested module that merely references it.
        """
        contract_path = (module.path / path).resolve()
        enclosing_modules = [
            candidate.path.resolve()
            for candidate in all_modules
            if candidate.path.resolve() == contract_path
            or candidate.path.resolve() in contract_path.parents
        ]
        owner_path = max(enclosing_modules, key=lambda candidate: len(candidate.parts), default=None)
        return owner_path is None or owner_path == module.path.resolve()
    dependencies_by_source: dict[str, set[str]] = {}
    for dependency in module_dependencies or []:
        dependencies_by_source.setdefault(dependency.source, set()).add(dependency.target)

    def reachable_modules(service: str) -> set[str]:
        reachable = {service}
        pending = [service]
        while pending:
            source = pending.pop()
            for target in dependencies_by_source.get(source, set()):
                if target not in reachable:
                    reachable.add(target)
                    pending.append(target)
        return reachable

    persistence_candidates_by_collection: dict[
        str, list[tuple[str, DiscoveredModule, MongoPersistenceClass]]
    ] = {}
    for identity, candidate_module in module_by_identity.items():
        for item in candidate_module.mongo_persistence_classes:
            persistence_candidates_by_collection.setdefault(item.collection, []).append(
                (identity, candidate_module, item)
            )
    mongo_persistence_classes: list[dict[str, object]] = []
    for service, collections in sorted((collections_by_service or {}).items()):
        reachable = reachable_modules(service)
        for collection in sorted(set(collections)):
            candidates = persistence_candidates_by_collection.get(collection, [])
            scoped = [candidate for candidate in candidates if candidate[0] in reachable]
            # A unique workspace-wide candidate is safe when dependency metadata
            # is absent (common in small Gradle builds and federated snapshots).
            selected = scoped or (candidates if len(candidates) == 1 else [])
            for identity, candidate_module, item in selected:
                mongo_persistence_classes.append({
                    "id": f"{service}:{identity}:{item.qualified_name}",
                    "service": service,
                    "module": identity,
                    "collection": item.collection,
                    "name": item.name,
                    "qualified_name": item.qualified_name,
                    "source": item.path,
                    "line": item.line,
                    "root": item.root,
                    "vscode_uri": _vscode_file_uri(
                        candidate_module.path / item.path, root_path, source_roots
                    ),
                    "fields": [
                        {
                            "name": field.name,
                            "type": field.type,
                            "references": [
                                f"{service}:{identity}:{reference}"
                                for reference in field.references
                            ],
                        }
                        for field in item.fields
                    ],
                })
    # This JSON is deliberately assembled as a mutable object graph before it
    # is serialized.  ``Any`` is used at this boundary because the browser
    # contract contains heterogeneous node shapes; keeping that boundary
    # explicit prevents mypy from treating every nested value as an opaque
    # ``object`` while the rest of the renderer is being built.
    nodes: list[dict[str, Any]] = []
    runtime_namespaces = sorted({
        workload.namespace
        for module in all_modules
        for workload in module.kubernetes_workloads
        if workload.namespace
    } | {
        str(namespace)
        for fact in graph_facts or []
        for namespace in _fact_runtime_namespaces(fact)
        if namespace
    })
    fact_namespaces = sorted({fact.namespace for fact in graph_facts or [] if fact.namespace})
    method_by_endpoint_id = {
        endpoint_id: method.qualified_method
        for method in integration_methods or []
        for endpoint_id in (*method.input_endpoint_ids, *method.output_endpoint_ids)
    }
    resolved_http_target_by_call_id = {
        edge.from_endpoint.id: (edge.to_service, edge.to_endpoint)
        for edge in edges
        if edge.kind == "rest" and edge.to_endpoint is not None
    }
    port_label_by_endpoint_id: dict[str, str] = {}
    service_order = {service: index for index, service in enumerate(ordered_services)}
    service_successors: dict[str, set[str]] = {service: set() for service in ordered_services}
    service_indegree = {service: 0 for service in ordered_services}
    for edge in edges:
        if edge.from_service not in service_successors or edge.to_service not in service_successors:
            continue
        if edge.to_service in service_successors[edge.from_service]:
            continue
        service_successors[edge.from_service].add(edge.to_service)
        service_indegree[edge.to_service] += 1
    ranked_services: list[str] = []
    ready_services = sorted(
        (service for service, degree in service_indegree.items() if degree == 0),
        key=service_order.__getitem__,
    )
    while ready_services:
        service = ready_services.pop(0)
        ranked_services.append(service)
        for successor in sorted(service_successors[service], key=service_order.__getitem__):
            service_indegree[successor] -= 1
            if service_indegree[successor] == 0:
                ready_services.append(successor)
                ready_services.sort(key=service_order.__getitem__)
    # A cyclic service component has no topological root.  Preserve every
    # endpoint and give that component a deterministic order instead of
    # inventing a direction through the cycle.
    ranked_services.extend(
        service for service in ordered_services if service not in ranked_services
    )
    ordered_ports = [
        (service, endpoint)
        for service in ranked_services
        for endpoint in sorted(
            endpoints_by_service.get(service, []),
            key=lambda item: (item.path, item.start_line, item.id),
        )
        if endpoint.system in {"rest", "kafka"}
    ]
    input_ports = [
        endpoint for _, endpoint in ordered_ports
        if endpoint.role in {"serve", "consume"}
    ]
    output_ports = [
        endpoint for _, endpoint in ordered_ports
        if endpoint.role in {"call", "produce"}
    ]
    # Port identifiers span the complete export rather than resetting per
    # service.  This lets a consumer point to the exact producer/caller that
    # statically reaches it, even when the two endpoints belong to different
    # microservices.
    port_label_by_endpoint_id.update({
        endpoint.id: f"I{number}"
        for number, endpoint in enumerate(input_ports, start=1)
    })
    port_label_by_endpoint_id.update({
        endpoint.id: f"O{number}"
        for number, endpoint in enumerate(output_ports, start=1)
    })
    endpoint_service_by_id = {
        endpoint.id: service
        for service, endpoints in endpoints_by_service.items()
        for endpoint in endpoints
    }
    endpoint_by_id = {
        endpoint.id: endpoint
        for endpoints in endpoints_by_service.values()
        for endpoint in endpoints
    }
    input_port_ids = {endpoint.id for endpoint in input_ports}
    output_port_ids = {endpoint.id for endpoint in output_ports}
    local_output_id_sets_by_input_id: dict[str, set[str]] = {}
    for flow in code_flows or []:
        flow_input_ids = {
            step.endpoint_id for step in flow.steps
            if step.endpoint_id in input_port_ids
        }
        flow_output_ids = {
            step.endpoint_id for step in flow.steps
            if step.endpoint_id in output_port_ids
        }
        for input_id in flow_input_ids:
            input_service = endpoint_service_by_id.get(input_id)
            if input_service is None:
                continue
            for output_id in flow_output_ids:
                if endpoint_service_by_id.get(output_id) == input_service:
                    local_output_id_sets_by_input_id.setdefault(input_id, set()).add(output_id)
    local_output_ids_by_input_id = {
        endpoint_id: sorted(
            output_ids,
            key=lambda output_id: int(port_label_by_endpoint_id[output_id][1:]),
        )
        for endpoint_id, output_ids in local_output_id_sets_by_input_id.items()
    }
    local_output_labels_by_input_id = {
        endpoint_id: [port_label_by_endpoint_id[output_id] for output_id in output_ids]
        for endpoint_id, output_ids in local_output_ids_by_input_id.items()
    }
    for endpoint in input_ports:
        local_outputs = local_output_labels_by_input_id.get(endpoint.id, [])
        if local_outputs:
            port_label_by_endpoint_id[endpoint.id] = (
                f"{port_label_by_endpoint_id[endpoint.id]} → {', '.join(local_outputs)}"
            )
    def port_method_label(endpoint: MessageEndpoint) -> str:
        qualified = method_by_endpoint_id.get(endpoint.id)
        if qualified is None:
            return endpoint.qualified_name or "<unknown>"
        owner, separator, method = qualified.rpartition(".")
        return f"{owner}::{method}" if separator else qualified

    def port_type_label(endpoint: MessageEndpoint) -> str:
        return {
            ("rest", "serve"): "HTTP receive",
            ("rest", "call"): "HTTP call",
            ("kafka", "consume"): "Kafka receive",
            ("kafka", "produce"): "Kafka publish",
        }.get((endpoint.system, endpoint.role), f"{endpoint.system} {endpoint.role}")

    def resolved_port_target(endpoint: MessageEndpoint) -> dict[str, str] | None:
        resolved = resolved_http_target_by_call_id.get(endpoint.id)
        if resolved is None:
            return None
        service, target = resolved
        return {
            "service": service,
            "label": port_label_by_endpoint_id.get(target.id, "?"),
            "type": "HTTP receive",
            "method": port_method_label(target),
            "name": target.topic,
    }
    for name in ordered_services:
        endpoints = endpoints_by_service.get(name, [])
        ports: list[dict[str, object]] = []
        for endpoint in sorted(endpoints, key=lambda item: (item.path, item.start_line, item.id)):
            if endpoint.system not in {"rest", "kafka"}:
                continue
            direction = "in" if endpoint.role in {"serve", "consume"} else "out"
            # A port label identifies an architecture vertex.  It must not use
            # the position of one CodeQL route: the same port can be incident
            # to several independently evidenced call-graph arcs.
            ports.append({
                "label": port_label_by_endpoint_id[endpoint.id],
                "direction": direction,
                "type": port_type_label(endpoint),
                "system": endpoint.system,
                "role": endpoint.role,
                "method": port_method_label(endpoint),
                "name": endpoint.topic,
                "path": endpoint.path,
                "line": endpoint.start_line,
                "endpoint_id": endpoint.id,
                **({"message_type": endpoint.message_type} if endpoint.message_type else {}),
                **(
                    {"message_type_status": "unknown", "message_type_warning": "Type Java non déterminé"}
                    if endpoint.system == "kafka" and not endpoint.message_type else {}
                ),
                **(
                    {"local_output_labels": local_output_labels_by_input_id[endpoint.id]}
                    if endpoint.id in local_output_labels_by_input_id else {}
                ),
                **(
                    {"local_outputs": [
                        {
                            "endpoint_id": output_id,
                            "label": port_label_by_endpoint_id[output_id],
                            "type": port_type_label(endpoint_by_id[output_id]),
                            "name": endpoint_by_id[output_id].topic,
                            "method": port_method_label(endpoint_by_id[output_id]),
                            **(
                                {"message_type": endpoint_by_id[output_id].message_type}
                                if endpoint_by_id[output_id].message_type else {}
                            ),
                        }
                        for output_id in local_output_ids_by_input_id[endpoint.id]
                    ]}
                    if endpoint.id in local_output_ids_by_input_id else {}
                ),
                **({"target": resolved_port_target(endpoint)} if resolved_port_target(endpoint) else {}),
            })
        resources = _rest_resources_served(endpoints)
        contract_resources: dict[str, set[str]] = {}
        contract_owner_identity: dict[str, str] = {}
        module = module_details.get(name)
        module_layer = software_layer(module, strategy1=strategy1, root_path=root_path) if module else "external"
        module_namespaces = sorted({
            workload.namespace
            for workload in (module.kubernetes_workloads if module else ())
            if workload.namespace
        })
        for endpoint in endpoints:
            if (
                endpoint.system == "rest"
                and endpoint.role == "serve"
                and endpoint.framework == "openapi"
            ):
                contract_path = _openapi_contract_evidence_path(endpoint)
                owner_module, module_path = _resolve_openapi_contract_owner(
                    contract_path, all_modules
                )
                if owner_module is None:
                    # No known module encloses this evidence (for example a
                    # federated inventory without the referenced module):
                    # keep the previous best-effort attribution to the
                    # serving module itself, using the raw evidence path.
                    owner_module = module
                    accepted = module is None or module_owns_openapi_file(module, module_path)
                elif owner_module is module:
                    accepted = module_owns_openapi_file(module, module_path)
                else:
                    # A Strategy1 declaration published by ``module`` whose
                    # contract physically lives in a different module (for
                    # example a shared ``model-*`` module). This is a
                    # legitimate cross-module reference, not a duplicate.
                    accepted = True
                if accepted:
                    contract_resources.setdefault(module_path, set()).add(endpoint.topic)
                    if owner_module is not None:
                        contract_owner_identity[module_path] = module_identity(owner_module)
        openapi_files = sorted(
            {
                path
                for path in (module.openapi_files if module else ())
                if module is None or module_owns_openapi_file(module, path)
            }
            | set(contract_resources)
        )
        owner_identity_by_path: dict[str, str] = dict(contract_owner_identity)
        if module is not None:
            for path in module.openapi_files:
                owner_identity_by_path.setdefault(path, module_identity(module))
        nodes.append(
            {
                "id": f"microservice:{name}",
                "kind": "microservice",
                "name": name,
                "layer": module_layer,
                "layer_label": module_layer.replace("_", " ").title(),
                "color": {
                    "application": "#2563eb",
                    "external": "#64748b",
                    "domain": "#7c3aed",
                    "api": "#0891b2",
                    "orchestration": "#9333ea",
                    "infrastructure": "#d97706",
                    "persistence": "#0f766e",
                    "unknown": "#94a3b8",
                }.get(module_layer, "#94a3b8"),
                "runtime_namespaces": module_namespaces,
                "fact_namespaces": sorted(fact_namespaces_by_service.get(name, set())),
                **({"architecture_namespace": module_namespaces[0]} if module_namespaces else {}),
                **({"project_namespace": project_namespace(module, root_path)} if module else {}),
                **({"project_namespace_path": project_namespace_path(module, root_path)} if module else {}),
                **({"cluster_path": project_namespace_path(module, root_path)} if module else {}),
                "architecture_layer": module_layer,
                "ports": ports,
                "kafka_endpoints": [
                    {
                        "role": endpoint.role,
                        "topic": endpoint.topic,
                        "message_type": endpoint.message_type,
                        "location": f"{endpoint.path}:{endpoint.start_line}",
                        "vscode_uri": _endpoint_vscode_uri(endpoint, all_modules, source_roots, root_path),
                    }
                    for endpoint in endpoints
                    if endpoint.system == "kafka"
                ],
                **(
                    {
                        "build_system": module.build_system,
                        "vscode_uri": _vscode_file_uri(module.path, root_path, source_roots),
                    }
                    if module
                    else {}
                ),
                "resources": resources,
                "kubernetes_workloads": [workload.__dict__ for workload in module.kubernetes_workloads] if module else [],
                "openapi_files": openapi_files,
                "openapi_contracts": [
                    {
                        "path": path,
                        "resources": sorted(contract_resources.get(path, set())),
                        **({"spec": openapi_specs[(owner_identity_by_path[path], path)]}
                           if path in owner_identity_by_path
                           and (owner_identity_by_path[path], path) in openapi_specs else {}),
                        **({"vscode_uri": _vscode_file_uri(
                                (module_by_identity.get(owner_identity_by_path.get(path, ""), module)).path / path,
                                root_path, source_roots,
                            )} if module else {}),
                    }
                    for path in openapi_files
                ],
                "asyncapi_contracts": [
                    {
                        "path": path,
                        "spec": spec,
                        "vscode_uri": _vscode_file_uri(module.path / path, root_path, source_roots),
                    }
                    for (owner, path), spec in asyncapi_specs.items()
                    if module is not None and owner == module_identity(module)
                ],
                **(
                    {
                        "findings": [
                            {
                                "severity": finding.severity,
                                "rule_id": finding.rule_id,
                                "message": finding.message,
                                "path": finding.path,
                                "start_line": finding.start_line,
                                "vscode_uri": _vscode_uri(finding, module, source_roots, root_path),
                            }
                            for finding in findings_by_service.get(name, [])
                        ]
                    }
                    if findings_by_service is not None
                    else {}
                ),
                "label": name,
                "width": 190,
                "height": 42,
                **(
                    {"external": True, "shape": "triangle"}
                    if name in external_services
                    else {}
                ),
            }
        )
    nodes += [
        {
            "id": f"kafka_topic:{name}",
            "kind": "kafka_topic",
            "name": name,
            "label": name,
            "published_message_types": sorted(topic_message_types[name]["produce"]),
            "consumed_message_types": sorted(topic_message_types[name]["consume"]),
            "message_type_status": (
                "unknown"
                if not any(endpoint.message_type for endpoint in kafka_endpoints if endpoint.topic == name)
                else "partial"
                if any(not endpoint.message_type for endpoint in kafka_endpoints if endpoint.topic == name)
                else "mixed"
                if len({endpoint.message_type for endpoint in kafka_endpoints if endpoint.topic == name}) > 1
                else "known"
            ),
            "width": 190,
            "height": 42,
        }
        for name in kafka_topics
    ]
    # A dynamic topic expression cannot safely be shared with another dynamic
    # expression. Give each one its own evidence node so the graph exposes the
    # integration without inventing a concrete Kafka dependency.
    dynamic_kafka_nodes: dict[str, str] = {}
    for service, endpoint in sorted(
        ((service, endpoint) for service, endpoints in endpoints_by_service.items() for endpoint in endpoints
         if endpoint.system == "kafka" and endpoint.topic_dynamic),
        key=lambda item: (item[0], item[1].path, item[1].start_line, item[1].id),
    ):
        node_id = f"kafka_topic_unresolved:{endpoint.id}"
        dynamic_kafka_nodes[endpoint.id] = node_id
        nodes.append({
            "id": node_id,
            "kind": "kafka_topic",
            "name": f"Topic dynamique · {service}",
            "label": f"? {endpoint.topic} · {service}",
            "topic_expression": endpoint.topic,
            "unresolved": True,
            "message_type_status": "unknown" if not endpoint.message_type else "known",
            "width": 190,
            "height": 42,
        })
    nodes += [
        {
            "id": f"mongodb_collection:{identity}",
            "kind": "mongodb_collection",
            "name": collection,
            "owner": service,
            "label": collection,
            "persistence_classes": [
                item for item in mongo_persistence_classes
                if item["service"] == service and item["collection"] == collection
                and item["root"]
            ],
            "width": 190,
            "height": 42,
        }
        for service, collection, identity in _mongodb_collection_nodes(collections_by_service)
    ]
    known_node_ids = {str(node["id"]) for node in nodes}
    for fact in graph_facts or []:
        if fact.fact_type == "node" and fact.name is not None:
            fact_visual_kind = (
                "microservice"
                if fact.kind == "service"
                else _canonical_resource_kind(fact.kind)
            )
            node_id = f"{fact_visual_kind}:{fact.name}"
            if node_id not in known_node_ids:
                node = {
                    "id": node_id, "kind": fact_visual_kind, "name": fact.name,
                    "label": fact.name, "width": 190, "height": 42,
                    "status": fact.status,
                }
                known_node_ids.add(node_id)
                nodes.append(node)
            node = next(item for item in nodes if item["id"] == node_id)
            if fact.technology:
                node["technology"] = fact.technology
            if fact.metadata:
                node["metadata"] = fact.metadata
                if fact_visual_kind == "microservice":
                    layer = fact.metadata.get("layer")
                    if isinstance(layer, str) and layer:
                        node["layer"] = layer
                        node["layer_label"] = layer.replace("_", " ").title()
                        node["color"] = {
                    "application": "#2563eb",
                    "external": "#64748b",
                            "domain": "#7c3aed",
                    "api": "#0891b2",
                    "orchestration": "#9333ea",
                            "infrastructure": "#d97706",
                            "persistence": "#0f766e",
                        }.get(layer, "#94a3b8")
                    metadata: dict[str, Any] = fact.metadata
                    namespaces = metadata.get("namespaces")
                    if not isinstance(namespaces, list):
                        namespaces = [metadata.get("namespace")]
                    node["runtime_namespaces"] = sorted({
                        str(namespace) for namespace in namespaces if namespace
                    })
                    runtime_values = node["runtime_namespaces"]
                    if isinstance(runtime_values, list) and runtime_values:
                        node["architecture_namespace"] = runtime_values[0]
                    if node.get("layer"):
                        node["architecture_layer"] = node["layer"]
                    if node.get("project_namespace_path"):
                        node["cluster_path"] = node["project_namespace_path"]
            if fact_visual_kind == "microservice":
                fact_values = node.setdefault("fact_namespaces", [])
                if not isinstance(fact_values, list):
                    fact_values = []
                    node["fact_namespaces"] = fact_values
                if fact.namespace not in fact_values:
                    fact_values.append(fact.namespace)

    # A resource belongs visually to the boundary of the service that writes
    # or publishes it.  The namespace on a data/topic fact often describes
    # the broker or database infrastructure, which is useful as provenance
    # but is not the architectural ownership boundary used by this view.
    service_architecture: dict[str, dict[str, Any]] = {
        str(node["name"]): {
            "layer": node.get("layer", "unknown"),
            "namespace": node.get("project_namespace"),
            "namespace_path": node.get("project_namespace_path"),
        }
        for node in nodes
        if node.get("kind") == "microservice"
    }
    resource_owner_candidates: dict[tuple[str, str], set[str]] = {}

    def add_resource_owner(key: tuple[str, str], owner: str) -> None:
        resource_owner_candidates.setdefault(key, set()).add(owner)

    for fact in graph_facts or []:
        if fact.fact_type != "edge" or fact.relation not in {"publishes", "writes"}:
            continue
        source_kind = "microservice" if fact.source_kind == "service" else fact.source_kind
        if source_kind != "microservice" or not fact.source_name or not fact.target_name:
            continue
        target_kind = _canonical_resource_kind(fact.target_kind)
        add_resource_owner((target_kind, fact.target_name), fact.source_name)
    # Prefer the canonical persisted relation projection when the caller has
    # one.  The graph edges remain a rendering adapter, while ownership is
    # resolved from the same architecture model used by query APIs.
    for relation in architecture_relations or []:
        if relation.relation not in {"publishes", "writes"}:
            continue
        source_kind = "microservice" if relation.source_kind == "service" else relation.source_kind
        if source_kind == "microservice":
            add_resource_owner(
                (_canonical_resource_kind(relation.target_kind), relation.target_name),
                relation.source_name,
            )
    # Mongo methods are persisted as architecture relations, but older
    # callers of this renderer may not provide those relations as graph
    # facts.  Reuse the same source evidence here so ownership does not depend
    # on which export entry point was used.
    for module in all_modules:
        service = module_identity(module)
        for method in module.mongo_methods:
            if method.collection and method.operation in {
                "bulkOps", "findAndModify", "findAndReplace", "insert", "remove",
                "save", "updateFirst", "updateMulti", "upsert",
            }:
                add_resource_owner(("mongodb_collection", method.collection), service)
    for edge in edges:
        if edge.kind == "kafka" and edge.from_endpoint.topic:
            add_resource_owner(("kafka_topic", edge.from_endpoint.topic), edge.from_service)
    layer_order = ["api", "application", "orchestration", "infrastructure", "domain", "persistence", "external"]
    layer_rank = {layer: index for index, layer in enumerate(layer_order)}
    resource_owners = {
        key: sorted(
            owners,
            key=lambda owner: (-layer_rank.get(service_architecture.get(owner, {}).get("layer", "unknown"), -1), owner),
        )[0]
        for key, owners in resource_owner_candidates.items()
    }
    for node in nodes:
        owner = resource_owners.get((_canonical_resource_kind(str(node.get("kind"))), str(node.get("name"))))
        if owner is None and node.get("kind") == "mongodb_collection":
            owner = str(node.get("owner") or "") or None
        if owner is None:
            continue
        owner_data = service_architecture.get(owner)
        if not owner_data:
            continue
        node["owner_service"] = owner
        node["architecture_layer"] = owner_data["layer"]
        if owner_data["namespace"]:
            node["architecture_namespace"] = owner_data["namespace"]
        if owner_data["namespace_path"]:
            node["architecture_namespace_path"] = owner_data["namespace_path"]
        node["namespace_source"] = "writer"
    links: list[dict[str, object]] = []

    def visual_rest_label(edge: GraphEdge) -> str:
        """Return the REST label used by the visual-edge projection."""
        label = graph_edge_rest_resource(edge)
        if edge.from_endpoint.framework == "spring-cloud-gateway":
            match = re.search(r"Path=([^;]+)", edge.from_endpoint.snippet)
            if match is not None:
                label = f"ANY {match.group(1)}"
        return label.replace("<br/>", "\\n")

    for source_kind, source_name, target_kind, target_name, label, kind in _visual_graph_edges(edges):
        confidence, provenance = _visual_link_evidence(
            kind, source_kind, source_name, target_kind, target_name, edges
        )
        link: dict[str, object] = {
            "source": f"{source_kind}:{source_name}",
            "target": f"{target_kind}:{target_name}",
            "kind": kind,
            "direction": "outgoing" if kind == "rest" or source_kind == "microservice" else "incoming",
            "label": label.replace("<br/>", "\\n"),
            "confidence": confidence,
            "provenance": provenance,
        }
        # Keep the endpoint evidence on the visual relation.  A visual link
        # can intentionally coalesce several identical architectural facts,
        # but the browser still needs their endpoint identities to reconcile a
        # persisted code flow without falling back to a route-label heuristic.
        if kind == "rest":
            link["endpoint_ids"] = sorted({
                edge.from_endpoint.id
                for edge in edges
                if (
                    edge.kind == "rest"
                    and edge.from_service == source_name
                    and edge.to_service == target_name
                    and visual_rest_label(edge) == link["label"]
                )
            })
        elif source_kind == "microservice" and target_kind == "kafka_topic":
            link["endpoint_ids"] = sorted({
                edge.from_endpoint.id
                for edge in edges
                if (
                    edge.kind == "kafka"
                    and edge.from_service == source_name
                    and edge.from_endpoint.topic == target_name
                )
            })
        elif source_kind == "kafka_topic" and target_kind == "microservice":
            link["endpoint_ids"] = sorted({
                edge.to_endpoint.id
                for edge in edges
                if (
                    edge.kind == "kafka"
                    and edge.to_service == target_name
                    and edge.from_endpoint.topic == source_name
                    and edge.to_endpoint is not None
                )
            })
        if kind == "kafka" and source_kind == "microservice" and target_kind == "kafka_topic":
            link["published_message_types"] = sorted(
                published_message_types_by_relation.get((source_name, target_name), set())
            )
        if kind == "kafka" and source_kind == "microservice" and target_kind == "microservice":
            link["published_message_types"] = sorted({
                edge.from_endpoint.message_type
                for edge in edges
                if edge.kind == "kafka"
                and edge.from_service == source_name
                and edge.to_service == target_name
                and edge.from_endpoint.message_type
            })
            link["consumed_message_types"] = sorted({
                edge.to_endpoint.message_type
                for edge in edges
                if edge.kind == "kafka"
                and edge.from_service == source_name
                and edge.to_service == target_name
                and edge.to_endpoint is not None
                and edge.to_endpoint.message_type
            })
        if kind == "kafka" and source_kind == "kafka_topic" and target_kind == "microservice":
            link["consumed_message_types"] = sorted(
                consumed_message_types_by_relation.get((target_name, source_name), set())
            )
        if kind == "kafka":
            kafka_candidates = [
                edge for edge in edges
                if edge.kind == "kafka"
                and (
                    (source_kind == "microservice"
                     and target_kind == "microservice"
                     and edge.from_service == source_name
                     and edge.to_service == target_name)
                    or
                    (source_kind == "microservice"
                     and edge.from_service == source_name
                     and edge.from_endpoint.topic == target_name)
                    or (target_kind == "microservice"
                        and edge.to_service == target_name
                        and edge.from_endpoint.topic == source_name)
                )
            ]
            statuses = {
                _kafka_message_type_status(edge.from_endpoint, edge.to_endpoint)[0]
                for edge in kafka_candidates
            }
            status = next(
                (candidate for candidate in ("mismatch", "partial", "unknown", "consistent")
                 if candidate in statuses),
                "unknown",
            )
            link["message_type_status"] = status
            if status != "consistent":
                link["message_type_warning"] = (
                    "Type Java absent ou divergent ; relation conservée sur le topic."
                )
        links.append(link)

    # Preserve unmatched endpoint evidence in the topology. These links stop
    # at a topic node and deliberately never connect one service to another.
    for service, endpoint in sorted(
        ((service, endpoint) for service, endpoints in endpoints_by_service.items() for endpoint in endpoints
         if endpoint.system == "kafka" and endpoint.id not in kafka_endpoint_ids_in_edges),
        key=lambda item: (item[0], item[1].path, item[1].start_line, item[1].id),
    ):
        topic_node = (
            dynamic_kafka_nodes.get(endpoint.id)
            if endpoint.topic_dynamic
            else f"kafka_topic:{endpoint.topic}"
        )
        if topic_node is None:
            continue
        produces = endpoint.role == "produce"
        source = f"microservice:{service}" if produces else topic_node
        target = topic_node if produces else f"microservice:{service}"
        orphan_link: dict[str, object] = {
            "source": source,
            "target": target,
            "kind": "kafka",
            "direction": "outgoing" if produces else "incoming",
            "label": endpoint.topic,
            "confidence": "inferred" if endpoint.source == "code" else "proved",
            "provenance": endpoint.source,
            "endpoint_ids": [endpoint.id],
            "unresolved": True,
            "message_type_status": "known" if endpoint.message_type else "unknown",
            "message_type_warning": (
                "Type Java non déterminé ; relation conservée comme preuve partielle."
                if not endpoint.message_type else
                "Aucun endpoint opposé rapproché ; relation conservée comme preuve partielle."
            ),
        }
        if produces:
            orphan_link["published_message_types"] = [endpoint.message_type] if endpoint.message_type else []
        else:
            orphan_link["consumed_message_types"] = [endpoint.message_type] if endpoint.message_type else []
        links.append(orphan_link)
    link_keys = {
        (str(link["source"]), str(link["target"]), str(link["kind"]), str(link["label"]))
        for link in links
    }
    for fact in graph_facts or []:
        if fact.fact_type != "edge":
            continue
        if not all((fact.source_kind, fact.source_name, fact.target_kind, fact.target_name)):
            continue
        source_kind = (
            "microservice"
            if fact.source_kind == "service"
            else _canonical_resource_kind(fact.source_kind)
        )
        target_kind = (
            "microservice"
            if fact.target_kind == "service"
            else _canonical_resource_kind(fact.target_kind)
        )
        source_id = f"{source_kind}:{fact.source_name}"
        target_id = f"{target_kind}:{fact.target_name}"
        for node_id, node_kind, node_name in (
            (source_id, source_kind, fact.source_name),
            (target_id, target_kind, fact.target_name),
        ):
            if node_id not in known_node_ids:
                nodes.append({
                    "id": node_id, "kind": node_kind, "name": node_name,
                    "label": node_name, "width": 190, "height": 42,
                })
                known_node_ids.add(node_id)
        link = {
            "source": source_id, "target": target_id,
            "kind": f"mcp_{fact.kind}",
            "direction": "outgoing",
            "label": fact.relation or fact.kind,
            "confidence": fact.confidence,
            "status": fact.status,
            "provenance": "MCP graph enrichment",
            **({"technology": fact.technology} if fact.technology else {}),
            **({"metadata": fact.metadata} if fact.metadata else {}),
        }
        link_key = (source_id, target_id, str(link["kind"]), str(link["label"]))
        if link_key not in link_keys:
            links.append(link)
            link_keys.add(link_key)
    persisted_mongodb_relations = [
        relation
        for relation in architecture_relations or []
        if relation.source_kind in {"microservice", "service"}
        and relation.target_kind == "collection"
        and relation.relation in {"reads", "writes"}
    ]
    if persisted_mongodb_relations:
        links += [
            {
                "source": f"microservice:{relation.source_name}",
                "target": f"mongodb_collection:{relation.source_name}:{relation.target_name}",
                "kind": "mongodb",
                "direction": "data_access",
                "label": "lit" if relation.relation == "reads" else "écrit",
                "confidence": "proved" if relation.confidence == "high" else "inferred",
                "provenance": relation.origin,
            }
            for relation in persisted_mongodb_relations
        ]
    else:
        # Compatibility fallback for callers that only provide the older module
        # inventory and therefore have no source-level MongoDB relation proof.
        links += [
            {
                "source": f"{source_kind}:{source_name}",
                "target": f"{target_kind}:{target_name}",
                "kind": kind,
                "direction": "data_access",
                "label": label,
                "confidence": "inferred",
                "provenance": "module inventory",
            }
            for source_kind, source_name, target_kind, target_name, label, kind in _mongodb_visual_graph_edges(
                collections_by_service
            )
        ]
    # Complexity is derived from the links exported to the browser, rather
    # than from a parallel graph projection. A microservice score is exactly:
    # inbound HTTP clients + outbound HTTP targets + Kafka relations + MongoDB
    # relations. Routes between the same directed pair are deduplicated.
    complexity_relations = {
        (str(link["source"]), str(link["target"]), str(link["kind"]))
        for link in links
        if link["kind"] in {"rest", "kafka", "mongodb"}
        and not (
            link["kind"] == "kafka"
            and str(link["source"]).startswith("microservice:")
            and str(link["target"]).startswith("microservice:")
        )
    }
    relation_counts: dict[str, int] = {str(node["id"]): 0 for node in nodes}
    relation_breakdowns: dict[str, dict[str, int]] = {
        str(node["id"]): {"http": 0, "kafka": 0, "mongodb": 0}
        for node in nodes
    }
    for source, target, kind in complexity_relations:
        relation_counts[source] += 1
        relation_counts[target] += 1
        bucket = "http" if kind == "rest" else kind
        relation_breakdowns[source][bucket] += 1
        relation_breakdowns[target][bucket] += 1
    complexity_rankings_by_kind = {
        kind: _complexity_ranking({
            str(node["id"]): relation_counts[str(node["id"])]
            for node in nodes
            if node["kind"] == kind
        })
        for kind in ("microservice", "kafka_topic", "mongodb_collection")
    }
    for node in nodes:
        # Geometry is intentionally uniform: complexity is encoded by color,
        # not by node size, so shape and visual weight remain comparable.
        # The card must leave enough room for a centered name and its corner
        # technology marker. This value is shared by every node kind.
        base_size = 50
        if node["kind"] not in complexity_rankings_by_kind:
            node["color"] = "#64748b"
            node["size"] = base_size
            continue
        node_id = str(node["id"])
        score = relation_counts[node_id]
        ranking = complexity_rankings_by_kind[str(node["kind"])][node_id]
        level = ranking["level"]
        node["complexity"] = {
            "score": score,
            "level": level,
            "relations": relation_counts[node_id],
            "breakdown": relation_breakdowns[node_id],
            "rank": ranking["rank"],
            "population": ranking["population"],
            "tier_start": ranking["tier_start"],
            "tier_end": ranking["tier_end"],
        }
        # Keep labels to the resource name. Connectivity remains encoded by
        # the outline and available in the detail panel, without making the
        # node label carry a dependency count.
        node["label"] = str(node["name"])
        node["color"] = {"low": "#2563eb", "medium": "#d97706", "high": "#dc2626"}[level]
        node["size"] = base_size
    # A workspace may contain structural directories such as ``services`` or
    # ``libs`` whose children are the actual projects. Keep these containers
    # separate from architecture relations: they are visual ownership/grouping
    # hints, not inferred runtime dependencies.
    service_node_ids = {f"microservice:{name}" for name in ordered_services}
    modules_by_parent: dict[Path, list[tuple[str, DiscoveredModule]]] = {}
    for module in all_modules:
        identity = module_identity(module)
        node_id = f"microservice:{identity}"
        if node_id not in service_node_ids:
            continue
        modules_by_parent.setdefault(module.path.resolve().parent, []).append((node_id, module))
    project_groups = [
        {
            "name": parent.name,
            "namespace": project_namespace_path(children[0][1], root_path),
            "children": sorted(node_id for node_id, _module in children),
        }
        for parent, children in sorted(modules_by_parent.items(), key=lambda item: str(item[0]))
        if len(children) >= 2
    ]
    if kafka_dto_definitions is None:
        kafka_dtos, project_dto_definitions = kafka_dto_views(endpoints_by_service)
    else:
        for definition in kafka_dto_definitions:
            module = module_by_identity.get(str(definition.get("module") or ""))
            dto_source = definition.get("source")
            if module is not None and isinstance(dto_source, str):
                definition["vscode_uri"] = _vscode_file_uri(
                    module.path / dto_source, root_path, source_roots
                )
        kafka_dtos = [item for item in kafka_dto_definitions if item.get("root", True)]
        project_dto_definitions = [
            item for item in kafka_dto_definitions if not item.get("root", True)
        ]
    return {
            "nodes": nodes,
            "links": links,
            "port_links": _deduplicated_call_port_links(edges),
            "internal_port_links": [
                {
                    "input_endpoint_id": input_id,
                    "output_endpoint_id": output_id,
                }
                for input_id in sorted(
                    local_output_ids_by_input_id,
                    key=lambda endpoint_id: int(
                        port_label_by_endpoint_id[endpoint_id].split(" ", 1)[0][1:]
                    ),
                )
                for output_id in sorted(
                    local_output_ids_by_input_id[input_id],
                    key=lambda endpoint_id: int(port_label_by_endpoint_id[endpoint_id][1:]),
                )
            ],
            "software_layers": sorted({
                str(node.get("layer"))
                for node in nodes
                if node.get("kind") == "microservice"
            }),
            "runtime_namespaces": runtime_namespaces,
            "fact_namespaces": fact_namespaces,
            "groups": project_groups,
            "kafka_dtos": kafka_dtos,
            "project_dto_definitions": project_dto_definitions,
            "mongo_persistence_classes": mongo_persistence_classes,
            "asyncapi_contracts": asyncapi_contracts or [],
            "indexing_issues": _indexing_issues(
                endpoints_by_service,
                edges,
                indexing_warnings,
                all_modules,
                source_roots,
                root_path,
                diagnostics,
            ),
    }
