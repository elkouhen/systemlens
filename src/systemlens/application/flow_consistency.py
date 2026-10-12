"""Conservative consistency diagnostics for persisted code-flow evidence."""

from dataclasses import asdict, dataclass
from collections import defaultdict
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

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _is_async(step: CodeFlowStep) -> bool:
    return (step.branch or "").lower() in {"async", "asynchronous", "event", "callback"}


def _is_sync(step: CodeFlowStep) -> bool:
    return (step.branch or "").lower() in {"sync", "synchronous", "blocking"}


def _resource(step: CodeFlowStep) -> str | None:
    return step.resource or step.name or None


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
    unique = {diagnostic.id: diagnostic for diagnostic in diagnostics}
    return sorted(unique.values(), key=lambda item: (
        item.flow_id, item.write_flow_id, item.write_order, item.read_flow_id, item.read_order,
    ))
