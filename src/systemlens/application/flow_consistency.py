"""Conservative consistency diagnostics for persisted code-flow evidence."""

from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from itertools import chain
from typing import Iterable, Mapping

from systemlens.domain.code_flows import CodeFlow, CodeFlowStep


@dataclass(frozen=True)
class FlowConsistencyDiagnostic:
    id: str
    flow_id: str
    classification: str
    severity: str
    resource: str | None
    key: str | None
    write_order: int
    read_order: int
    write_step: int
    read_step: int
    causal_evidence: str
    completion: str
    confidence: str
    limitation: str | None = None
    variant_count: int = 1
    write_flow_id: str = ""
    read_flow_id: str = ""
    write_endpoint_id: str | None = None
    read_endpoint_id: str | None = None
    write_name: str = ""
    read_name: str = ""
    write_path_endpoint_ids: tuple[str, ...] = ()
    read_path_endpoint_ids: tuple[str, ...] = ()
    event_type: str = "async_write_sync_read"
    write_action: str | None = None
    read_action: str | None = None
    write_phase: str | None = None
    read_phase: str | None = None
    write_transaction_id: str | None = None
    read_transaction_id: str | None = None
    write_fields: tuple[str, ...] = ()
    read_fields: tuple[str, ...] = ()
    guard: str | None = None
    guard_scope: str | None = None
    order_evidence: str | None = None
    write_dependency_step: int | None = None
    read_dependency_step: int | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _is_async(step: CodeFlowStep) -> bool:
    return (step.branch or "").lower() in {"async", "asynchronous", "event", "callback"}


def _is_sync(step: CodeFlowStep) -> bool:
    return (step.branch or "").lower() in {"sync", "synchronous", "blocking"}


def _resource(step: CodeFlowStep) -> str | None:
    return step.resource or step.name or None


_CONCURRENCY_EVIDENCE = {"parallel", "independent_handlers", "reentrant"}
_CONFLICT_GUARDS = {"version", "compare_and_set", "lock", "serializable"}


def _may_overlap(left: CodeFlowStep, right: CodeFlowStep) -> bool:
    """Require explicit concurrency evidence; async branch labels are insufficient."""
    if left.concurrency == "sequential" or right.concurrency == "sequential":
        return False
    return left.concurrency in _CONCURRENCY_EVIDENCE or right.concurrency in _CONCURRENCY_EVIDENCE


def _guard_applies(left: CodeFlowStep, right: CodeFlowStep, identity: str) -> bool:
    return bool(
        left.guard in _CONFLICT_GUARDS
        and right.guard == left.guard
        and left.guard_scope == right.guard_scope == identity
    )


def _event_id(flow: CodeFlow, step: CodeFlowStep) -> str:
    return f"{flow.id}:{step.order}"


def _overlap(write: CodeFlowStep, read: CodeFlowStep) -> tuple[bool, str | None]:
    write_resource, read_resource = _resource(write), _resource(read)
    if not write_resource or not read_resource:
        return False, "resource non identifié"
    if write_resource != read_resource:
        return False, None
    if write.key and read.key:
        if write.key == read.key:
            return True, None
        return False, "correspondance entre les expressions de clé non établie"
    if write.query_shape and read.query_shape:
        if write.query_shape in {"point", "upsert"} and read.query_shape == "point":
            return False, "clé de lecture différente ou non prouvée"
        return True, "recouvrement de requête approximatif"
    return False, "clé ou recouvrement de requête non prouvé"


def _causal(flow: CodeFlow, write: CodeFlowStep, read: CodeFlowStep) -> tuple[bool, str]:
    if write.causal_id and read.causal_id and write.causal_id == read.causal_id:
        return True, "identifiant causal partagé"
    trigger = next((step for step in flow.steps if (
        step.kind in {"http_entry", "message_entry", "cron_entry"}
        and step.order < write.order and step.order < read.order
    )), None)
    if trigger is not None and not write.causal_id and not read.causal_id:
        return True, "déclencheur commun du flux"
    return False, "lien causal non prouvé"


def _diagnose_pair(
    write_flow: CodeFlow,
    write: CodeFlowStep,
    read_flow: CodeFlow,
    read: CodeFlowStep,
    display_flow_id: str,
    causal_evidence: str,
    causal_limitation: str | None = None,
    write_path_endpoint_ids: tuple[str, ...] = (),
    read_path_endpoint_ids: tuple[str, ...] = (),
) -> FlowConsistencyDiagnostic:
    overlaps, limitation = _overlap(write, read)
    if not overlaps and limitation is None:
        limitation = "ressources différentes"
    limitation = causal_limitation or limitation
    if limitation is not None:
        classification, severity, confidence = "insufficient_evidence", "P1", "low"
    elif write.completion == "visible" and write.expected_version and (
        write.expected_version == read.expected_version
    ):
        classification, severity, confidence = "guarantee_identified", "info", "high"
    else:
        classification, severity, confidence = "potential_risk", "P1", "medium"
        limitation = write.completion or "aucune garantie de visibilité attendue"
    return FlowConsistencyDiagnostic(
        id=(f"{display_flow_id}:consistency:{write_flow.id}:{write.order}:"
            f"{read_flow.id}:{read.order}"),
        flow_id=display_flow_id,
        classification=classification,
        severity=severity,
        resource=_resource(write),
        key=write.key if write.key == read.key else None,
        write_order=write.order,
        read_order=read.order,
        write_step=write.order,
        read_step=read.order,
        causal_evidence=causal_evidence,
        completion=write.completion or "unknown",
        confidence=confidence,
        limitation=limitation,
        write_flow_id=write_flow.id,
        read_flow_id=read_flow.id,
        write_endpoint_id=write.endpoint_id,
        read_endpoint_id=read.endpoint_id,
        write_name=write.name,
        read_name=read.name,
        write_path_endpoint_ids=write_path_endpoint_ids,
        read_path_endpoint_ids=read_path_endpoint_ids,
        write_action=write.action,
        read_action=read.action,
        write_phase=write.phase,
        read_phase=read.phase,
        write_transaction_id=write.transaction_id,
        read_transaction_id=read.transaction_id,
        write_fields=write.fields,
        read_fields=read.fields,
        guard=write.guard if write.guard == read.guard else None,
        guard_scope=write.guard_scope if write.guard_scope == read.guard_scope else None,
        order_evidence=causal_evidence,
    )


def diagnose_flow_consistency(flow: CodeFlow) -> list[FlowConsistencyDiagnostic]:
    """Return pair diagnostics for accesses recorded in one persisted flow."""
    return diagnose_flow_consistency_snapshot([flow])


def diagnose_flow_consistency_snapshot(
    flows: list[CodeFlow],
    causal_reachability: Mapping[str, set[str]] | None = None,
    causal_paths: Mapping[str, Mapping[str, tuple[str, ...]]] | None = None,
) -> list[FlowConsistencyDiagnostic]:
    """Pair access evidence across flows reachable from a persisted trigger."""
    writes = [
        (flow, step) for flow in flows for step in flow.steps
        if step.kind == "data_write" and _is_async(step)
    ]
    reads = [
        (flow, step) for flow in flows for step in flow.steps
        if step.kind == "data_read" and _is_sync(step)
    ]
    reads_by_resource: dict[str, list[tuple[CodeFlow, CodeFlowStep]]] = defaultdict(list)
    unknown_resource_reads: list[tuple[CodeFlow, CodeFlowStep]] = []
    for read_flow, read in reads:
        resource = _resource(read)
        if resource is None:
            unknown_resource_reads.append((read_flow, read))
        else:
            reads_by_resource[resource].append((read_flow, read))

    def candidate_reads(write: CodeFlowStep) -> Iterable[tuple[CodeFlow, CodeFlowStep]]:
        resource = _resource(write)
        if resource is None:
            return reads
        return chain(reads_by_resource.get(resource, ()), unknown_resource_reads)

    exact_matches: set[tuple[tuple[str, int], tuple[str, int]]] = set()
    for write_flow, write in writes:
        for read_flow, read in candidate_reads(write):
            overlaps, limitation = _overlap(write, read)
            if overlaps and limitation is None:
                exact_matches.add(((write_flow.id, write.order), (read_flow.id, read.order)))
    matched_writes = {write for write, _read in exact_matches}
    matched_reads = {read for _write, read in exact_matches}
    roots_by_flow: dict[str, set[str]] = defaultdict(set)
    for root, reachable in (causal_reachability or {}).items():
        for flow_id in reachable:
            roots_by_flow[flow_id].add(root)
    flow_by_id = {flow.id: flow for flow in flows}
    diagnostics: list[FlowConsistencyDiagnostic] = []
    for write_flow, write in writes:
        for read_flow, read in candidate_reads(write):
            overlaps, overlap_limitation = _overlap(write, read)
            if not overlaps and overlap_limitation is None:
                continue
            if overlap_limitation == "correspondance entre les expressions de clé non établie" and (
                (write_flow.id, write.order) in matched_writes
                or (read_flow.id, read.order) in matched_reads
            ):
                continue
            if write_flow.id == read_flow.id:
                causal, evidence = _causal(write_flow, write, read)
                diagnostics.append(_diagnose_pair(
                    write_flow, write, read_flow, read, write_flow.id, evidence,
                    "lecture située avant l'écriture dans le flux" if read.order < write.order
                    else None if causal else "lien causal non prouvé",
                ))
                continue
            roots = sorted(roots_by_flow[write_flow.id] & roots_by_flow[read_flow.id])
            for display_root in roots:
                if write.causal_id and read.causal_id and write.causal_id != read.causal_id:
                    evidence = "déclencheur commun, identifiants causaux différents"
                    causal_limitation = "identifiants causaux incompatibles"
                elif write.causal_id and read.causal_id:
                    evidence = "identifiant causal partagé sur deux branches atteignables"
                    causal_limitation = None
                else:
                    evidence = "déclencheur commun et chemins persistés"
                    causal_limitation = None
                root_flow = flow_by_id.get(display_root)
                write_path = (causal_paths or {}).get(display_root, {}).get(write_flow.id, ())
                if root_flow is not None and write_path and causal_limitation is None:
                    publication = next((step for step in root_flow.steps if (
                        step.endpoint_id == write_path[0]
                        and step.kind in {"message_publish", "http_call"}
                    )), None)
                    read_site = next((step for step in read_flow.steps[1:] if (
                        step.kind in {"method_call", "data_read"}
                    )), None)
                    if publication is not None and read_site is not None and (
                        read_site.path == publication.path and
                        read_site.start_line < publication.start_line
                    ):
                        causal_limitation = "lecture située avant la publication dans la méthode d'entrée"
                diagnostics.append(_diagnose_pair(
                    write_flow, write, read_flow, read, display_root, evidence, causal_limitation,
                    (causal_paths or {}).get(display_root, {}).get(write_flow.id, ()),
                    (causal_paths or {}).get(display_root, {}).get(read_flow.id, ()),
                ))

    # Advanced detectors require exact identity and explicit concurrency facts.
    accesses_by_identity: dict[
        tuple[str, str], dict[str, list[tuple[CodeFlow, CodeFlowStep]]]
    ] = defaultdict(lambda: {"data_read": [], "data_write": []})
    for flow in flows:
        if flow.status == "cycle":
            continue
        for step in flow.steps:
            resource = _resource(step)
            if step.kind in {"data_read", "data_write"} and resource and step.key:
                accesses_by_identity[(resource, step.key)][step.kind].append((flow, step))
    rmw_sequences_by_identity: dict[
        tuple[str, str], list[tuple[CodeFlow, CodeFlowStep, CodeFlowStep]]
    ] = {}
    for identity, grouped_accesses in accesses_by_identity.items():
        reads_for_key = grouped_accesses["data_read"]
        writes_for_key = grouped_accesses["data_write"]
        reads_by_flow: dict[str, list[CodeFlowStep]] = defaultdict(list)
        writes_by_flow: dict[str, list[CodeFlowStep]] = defaultdict(list)
        for candidate_flow, step in reads_for_key:
            reads_by_flow[candidate_flow.id].append(step)
        for candidate_flow, step in writes_for_key:
            writes_by_flow[candidate_flow.id].append(step)
        rmw_sequences: list[tuple[CodeFlow, CodeFlowStep, CodeFlowStep]] = []
        for flow_id in reads_by_flow.keys() & writes_by_flow.keys():
            flow = flow_by_id[flow_id]
            flow_reads = reads_by_flow[flow_id]
            flow_writes = writes_by_flow[flow_id]
            for read in flow_reads:
                for write in flow_writes:
                    if (
                        write.order > read.order
                        and read.transaction_id
                        and read.transaction_id == write.transaction_id
                        and _event_id(flow, read) in write.value_dependencies
                    ):
                        rmw_sequences.append((flow, read, write))
        rmw_sequences_by_identity[identity] = rmw_sequences

    lost_update_pairs: set[tuple[str, int, str, int]] = set()
    for (resource, key), rmw_sequences in rmw_sequences_by_identity.items():
        for index, (left_flow, left_read, left_write) in enumerate(rmw_sequences):
            if _resource(left_write) != resource or left_write.key != key:
                continue
            for right_flow, right_read, right_write in rmw_sequences[index + 1:]:
                if right_flow.id == left_flow.id or _resource(right_write) != resource or right_write.key != key:
                    continue
                if left_write.transaction_id == right_write.transaction_id:
                    continue
                if not _may_overlap(left_write, right_write):
                    continue
                if left_write.fields and right_write.fields and not set(left_write.fields).intersection(right_write.fields):
                    continue
                guard_identity = f"{resource}:{key}"
                if _guard_applies(left_write, right_write, guard_identity):
                    continue
                if left_write.causal_id and right_write.causal_id and left_write.causal_id != right_write.causal_id:
                    continue
                roots = sorted(roots_by_flow[left_flow.id] & roots_by_flow[right_flow.id])
                for display_root in roots:
                    diagnostic = _diagnose_pair(
                        left_flow, left_write, right_flow, right_write, display_root,
                        "deux écritures dépendent de lectures antérieures dans des transactions distinctes",
                        "l’interleaving d’exécution reste potentiel",
                        (causal_paths or {}).get(display_root, {}).get(left_flow.id, ()),
                        (causal_paths or {}).get(display_root, {}).get(right_flow.id, ()),
                    )
                    diagnostics.append(replace(
                        diagnostic,
                        id=f"{diagnostic.id}:potential_lost_update",
                        event_type="potential_lost_update",
                        classification="potential_lost_update",
                        severity="P1",
                        confidence="medium" if left_write.fields and right_write.fields else "low",
                        limitation=(None if left_write.fields and right_write.fields else
                                    "champs écrits non résolus; recouvrement non prouvé"),
                        causal_evidence=(
                            f"lecture {left_read.order} → écriture {left_write.order}; "
                            f"lecture {right_read.order} → écriture {right_write.order}"
                        ),
                        order_evidence="dépendances de valeur explicites; entrelacement possible selon les faits fournis",
                        write_dependency_step=left_read.order,
                        read_dependency_step=right_read.order,
                    ))
                    lost_update_pairs.add((left_flow.id, left_write.order, right_flow.id, right_write.order))

    for grouped_accesses in accesses_by_identity.values():
        for write_flow, write in grouped_accesses["data_write"]:
            for read_flow, read in grouped_accesses["data_read"]:
                if write_flow.id == read_flow.id:
                    continue
                if _is_async(write) and _is_sync(read):
                    continue
                if not _may_overlap(write, read):
                    continue
                if write.causal_id and read.causal_id and write.causal_id != read.causal_id:
                    continue
                roots = sorted(roots_by_flow[write_flow.id] & roots_by_flow[read_flow.id])
                for display_root in roots:
                    guard_identity = f"{_resource(write)}:{write.key}"
                    if read.isolation in {"read_uncommitted", "read-uncommitted"} and write.phase == "uncommitted":
                        event_type = "potential_dirty_read"
                        classification = "potential_dirty_read"
                        limitation = "la visibilité réelle dépend du moteur et de sa configuration"
                    elif write.freshness_required or read.freshness_required:
                        event_type = "potential_read_write_anomaly"
                        classification = "potential_read_write_anomaly"
                        limitation = "exigence de fraîcheur fournie; sémantique de stockage à confirmer"
                    elif _guard_applies(write, read, guard_identity):
                        event_type = "conflict_protection_identified"
                        classification = "conflict_protection_identified"
                        limitation = None
                    else:
                        event_type = "potential_concurrent_read_write"
                        classification = "potential_interaction"
                        limitation = "chevauchement possible; aucun défaut de fraîcheur établi"
                    diagnostic = _diagnose_pair(
                        write_flow, write, read_flow, read, display_root,
                        "concurrence explicitement documentée pour au moins un accès",
                        limitation,
                        (causal_paths or {}).get(display_root, {}).get(write_flow.id, ()),
                        (causal_paths or {}).get(display_root, {}).get(read_flow.id, ()),
                    )
                    if event_type == "potential_concurrent_read_write" and (
                        write_flow.id, write.order, read_flow.id, read.order
                    ) in lost_update_pairs:
                        continue
                    diagnostics.append(replace(
                        diagnostic,
                        id=f"{diagnostic.id}:{event_type}",
                        event_type=event_type,
                        classification=classification,
                        severity="P1" if event_type != "potential_concurrent_read_write" else "P2",
                        confidence="medium" if event_type in {
                            "potential_read_write_anomaly", "potential_dirty_read",
                        } else "low",
                        limitation=limitation,
                        guard=write.guard if write.guard == read.guard else None,
                        guard_scope=write.guard_scope if write.guard_scope == read.guard_scope else None,
                        order_evidence=diagnostic.causal_evidence,
                    ))

    # Concurrent writes are reported only with exact key identity and explicit
    # overlap evidence. Distinct source locations or async branches alone do
    # not establish a race.
    for (resource, key), grouped_accesses in accesses_by_identity.items():
        writes_for_key = grouped_accesses["data_write"]
        for index, (left_flow, left_write) in enumerate(writes_for_key):
            for right_flow, right_write in writes_for_key[index + 1:]:
                if left_flow.id == right_flow.id or not _may_overlap(left_write, right_write):
                    continue
                if (left_flow.id, left_write.order, right_flow.id, right_write.order) in lost_update_pairs:
                    continue
                if left_write.causal_id and right_write.causal_id and left_write.causal_id != right_write.causal_id:
                    continue
                roots = sorted(roots_by_flow[left_flow.id] & roots_by_flow[right_flow.id])
                for display_root in roots:
                    guard_identity = f"{resource}:{key}"
                    protected = _guard_applies(left_write, right_write, guard_identity)
                    fields_disjoint = bool(
                        left_write.fields and right_write.fields
                        and not set(left_write.fields).intersection(right_write.fields)
                    )
                    event_type = "conflict_protection_identified" if protected else "potential_concurrent_write"
                    diagnostic = _diagnose_pair(
                        left_flow, left_write, right_flow, right_write, display_root,
                        "concurrence explicitement documentée; ordre total absent",
                        None if protected else (
                            "champs disjoints; aucun lost update déduit" if fields_disjoint else
                            "intervalles de transaction et garanties du stockage non établis"
                        ),
                        (causal_paths or {}).get(display_root, {}).get(left_flow.id, ()),
                        (causal_paths or {}).get(display_root, {}).get(right_flow.id, ()),
                    )
                    diagnostics.append(replace(
                        diagnostic,
                        id=f"{diagnostic.id}:{event_type}:writes",
                        event_type=event_type,
                        classification=event_type,
                        severity="info" if protected else "P1",
                        confidence="high" if protected else "low",
                        key=key,
                        read_name=right_write.name,
                        read_action=right_write.action,
                        read_phase=right_write.phase,
                        read_transaction_id=right_write.transaction_id,
                        read_fields=right_write.fields,
                        guard=left_write.guard if protected else None,
                        guard_scope=left_write.guard_scope if protected else None,
                    ))
    unique = {diagnostic.id: diagnostic for diagnostic in diagnostics}
    return sorted(unique.values(), key=lambda item: (
        item.flow_id, item.write_flow_id, item.write_order, item.read_flow_id, item.read_order,
    ))
