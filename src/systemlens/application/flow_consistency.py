"""Conservative consistency diagnostics for persisted code-flow evidence.

The detector deliberately reports a potential risk rather than claiming a
runtime race.  It needs an asynchronous write, a synchronous read, evidence
that both belong to the same causal flow, and overlapping resource/key
evidence.  Missing evidence is surfaced as ``insufficient_evidence``.
"""

from dataclasses import asdict, dataclass

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

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _is_async(step: CodeFlowStep) -> bool:
    return (step.branch or "").lower() in {"async", "asynchronous", "event", "callback"}


def _is_sync(step: CodeFlowStep) -> bool:
    branch = (step.branch or "sync").lower()
    return branch in {"sync", "synchronous", "blocking", ""}


def _resource(step: CodeFlowStep) -> str | None:
    return step.resource or step.name or None


def _overlap(write: CodeFlowStep, read: CodeFlowStep) -> tuple[bool, str | None]:
    write_resource, read_resource = _resource(write), _resource(read)
    if not write_resource or not read_resource:
        return False, "resource non identifié"
    if write_resource != read_resource:
        return False, None
    if write.key and read.key:
        return (write.key == read.key), (None if write.key == read.key else None)
    if write.query_shape and read.query_shape:
        if write.query_shape in {"point", "upsert"} and read.query_shape == "point":
            return False, "clé de lecture différente ou non prouvée"
        return True, "recouvrement de requête approximatif"
    return False, "clé ou recouvrement de requête non prouvé"


def _causal(flow: CodeFlow, write: CodeFlowStep, read: CodeFlowStep) -> tuple[bool, str]:
    if write.causal_id and read.causal_id and write.causal_id == read.causal_id:
        return True, "identifiant causal partagé"
    trigger = next((step for step in flow.steps if step.order < write.order and step.order < read.order), None)
    if trigger is not None and not write.causal_id and not read.causal_id:
        return True, "déclencheur commun du flux"
    return False, "lien causal non prouvé"


def diagnose_flow_consistency(flow: CodeFlow) -> list[FlowConsistencyDiagnostic]:
    """Return one diagnostic per distinct async-write/sync-read pair."""
    writes = [step for step in flow.steps if step.kind == "data_write" and _is_async(step)]
    reads = [step for step in flow.steps if step.kind == "data_read" and _is_sync(step)]
    diagnostics: list[FlowConsistencyDiagnostic] = []
    for write in writes:
        for read in reads:
            overlaps, limitation = _overlap(write, read)
            if limitation is None and not overlaps:
                continue
            causal, causal_evidence = _causal(flow, write, read)
            if not causal:
                classification, severity, confidence = "insufficient_evidence", "P1", "low"
                limitation = "lien causal non prouvé"
            elif limitation is not None:
                classification, severity, confidence = "insufficient_evidence", "P1", "low"
            elif write.completion in {"visible", "committed", "awaited", "transactional"} and (
                not write.expected_version or write.expected_version == read.expected_version
            ):
                classification, severity, confidence = "guarantee_identified", "info", "high"
            else:
                classification, severity, confidence = "potential_risk", "P1", "medium"
                limitation = write.completion or "aucune garantie de visibilité attendue"
            diagnostics.append(FlowConsistencyDiagnostic(
                id=f"{flow.id}:consistency:{write.order}:{read.order}",
                flow_id=flow.id,
                classification=classification,
                severity=severity,
                resource=_resource(write),
                key=write.key or read.key,
                write_order=write.order,
                read_order=read.order,
                write_step=write.order,
                read_step=read.order,
                causal_evidence=causal_evidence,
                completion=write.completion or "unknown",
                confidence=confidence,
                limitation=limitation,
            ))
    return diagnostics


def diagnose_flow_consistency_snapshot(flows: list[CodeFlow]) -> list[FlowConsistencyDiagnostic]:
    """Analyse persisted flows without reparsing source files."""
    diagnostics = [diagnostic for flow in flows for diagnostic in diagnose_flow_consistency(flow)]
    return sorted(diagnostics, key=lambda item: (item.flow_id, item.write_order, item.read_order))
