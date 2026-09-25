import hashlib
from dataclasses import dataclass, replace
from typing import Literal, TypeAlias


IntegrationSystem: TypeAlias = Literal["kafka", "rest"]
KafkaRole: TypeAlias = Literal["produce", "consume"]
RestRole: TypeAlias = Literal["serve", "call"]
ArchitectureRelationType: TypeAlias = Literal[
    "depends_on", "calls_service", "publishes_to", "reads", "writes"
]
Confidence: TypeAlias = Literal["low", "medium", "high"]


def compute_finding_id(
    rule_id: str,
    path: str,
    snippet: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    normalized_snippet = " ".join(snippet.split())
    location = "" if start_line is None else f"|{start_line}:{end_line or start_line}"
    digest = hashlib.sha256(
        f"{rule_id}|{path}{location}|{normalized_snippet}".encode()
    ).hexdigest()
    return digest[:16]


@dataclass(frozen=True)
class Finding:
    id: str
    rule_id: str
    severity: str
    message: str
    path: str
    start_line: int
    end_line: int
    snippet: str
    fix: str | None
    cwe: list[str]
    owasp: list[str]
    # BACKLOG-13 M1 : module Maven (artifactId du pom.xml le plus proche) et
    # nom qualifié Java (package + classe) du fichier — None si non
    # applicable (repo non-Maven, fichier non-Java). Permet de grouper par
    # module sans fédération multi-dépôts (voir graph.py).
    module: str | None = None
    qualified_name: str | None = None


def compute_endpoint_id(
    role: str,
    topic: str,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    location = "" if start_line is None else f"|{start_line}:{end_line or start_line}"
    digest = hashlib.sha256(f"{role}|{topic}|{path}{location}".encode()).hexdigest()
    return digest[:16]


def compute_architecture_relation_id(
    source_kind: str,
    source_name: str,
    relation: str,
    target_kind: str,
    target_name: str,
    path: str | None = None,
    start_line: int | None = None,
) -> str:
    """Return a stable identifier for one evidenced architecture fact."""
    location = f"|{path}:{start_line}" if path is not None and start_line is not None else ""
    digest = hashlib.sha256(
        f"{source_kind}|{source_name}|{relation}|{target_kind}|{target_name}{location}".encode()
    ).hexdigest()
    return digest[:16]


@dataclass(frozen=True)
class MessageEndpoint:
    """A static integration point backed by source evidence.

    ``topic`` is retained as the storage-compatible resource identity. Use
    :attr:`kafka_topic` for Kafka and :attr:`route` for HTTP so the ubiquitous
    language does not call an HTTP route a topic.
    """

    id: str
    role: str  # KafkaRole or RestRole
    system: str  # IntegrationSystem
    topic: str
    topic_dynamic: bool
    source: str  # code | manifest
    framework: str | None
    path: str
    start_line: int
    end_line: int
    snippet: str
    # BACKLOG-13 M1 : voir Finding.module/qualified_name — même principe.
    module: str | None = None
    qualified_name: str | None = None
    # Type Java du payload ou paramètre REST/Kafka lorsqu'il est déductible
    # statiquement. Les manifestes et appels sans signature exploitable restent
    # à `None`.
    message_type: str | None = None
    # Libellé source conservé pour l'affichage lorsque `topic` est normalisé
    # par une convention d'indexation, notamment Strategy1.
    topic_display: str | None = None

    @property
    def kafka_topic(self) -> str | None:
        """Return the Kafka topic when this point belongs to Kafka."""
        return self.topic if self.system == "kafka" else None

    @property
    def route(self) -> str | None:
        """Return the HTTP method and route when this point belongs to HTTP."""
        return self.topic if self.system == "rest" else None

    def validate_semantics(self) -> None:
        """Raise when a persisted integration point uses an invalid vocabulary."""
        valid_roles = {"produce", "consume"} if self.system == "kafka" else {"serve", "call"}
        if self.system not in {"kafka", "rest"} or self.role not in valid_roles:
            raise ValueError(
                f"Invalid integration point: system={self.system!r}, role={self.role!r}"
            )


@dataclass(frozen=True)
class ArchitectureRelation:
    """A typed, evidenced relation between two indexed architecture objects."""

    id: str
    source_kind: str
    source_name: str
    relation: str
    target_kind: str
    target_name: str
    origin: str  # code | manifest | derived
    confidence: str  # Confidence
    module: str | None = None
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    qualified_name: str | None = None


@dataclass(frozen=True)
class GraphFact:
    """A user/AI-supplied graph fact kept separately from code extraction."""

    id: str
    fact_type: str  # node | edge
    kind: str
    name: str | None
    source_kind: str | None
    source_name: str | None
    target_kind: str | None
    target_name: str | None
    relation: str | None
    origin: str
    confidence: str
    evidence_path: str | None = None
    evidence_line: int | None = None
    note: str | None = None
    technology: str | None = None
    metadata: dict[str, object] | None = None
    namespace: str = "manual"
    status: str = "confirmed"
    pass_id: str | None = None
    source_revision: str | None = None


_FACT_CONFIDENCE_RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
_FACT_STATUS_RANK = {"ambiguous": 0, "unresolved": 0, "proposed": 1, "confirmed": 2}


def merge_graph_facts(existing: GraphFact, incoming: GraphFact) -> GraphFact:
    """Merge one repeated enrichment fact without degrading its evidence.

    Equal-quality observations are treated as a refresh and the incoming
    value wins. A lower-quality observation can only fill fields that were
    absent; it cannot replace the stronger fact's semantic payload, status or
    provenance.
    """
    if existing.id != incoming.id:
        raise ValueError("Cannot merge graph facts with different identities.")
    existing_quality = (
        _FACT_CONFIDENCE_RANK.get(existing.confidence, -1),
        _FACT_STATUS_RANK.get(existing.status, -1),
    )
    incoming_quality = (
        _FACT_CONFIDENCE_RANK.get(incoming.confidence, -1),
        _FACT_STATUS_RANK.get(incoming.status, -1),
    )
    stronger = incoming if incoming_quality >= existing_quality else existing
    weaker = existing if stronger is incoming else incoming
    metadata = {**(weaker.metadata or {}), **(stronger.metadata or {})}
    return replace(
        stronger,
        evidence_path=stronger.evidence_path or weaker.evidence_path,
        evidence_line=stronger.evidence_line or weaker.evidence_line,
        note=stronger.note or weaker.note,
        technology=stronger.technology or weaker.technology,
        pass_id=stronger.pass_id or weaker.pass_id,
        source_revision=stronger.source_revision or weaker.source_revision,
        metadata=metadata,
    )


@dataclass(frozen=True)
class ExtractionDiagnostic:
    """A safe, indexed record of one extractor failure or limitation."""

    path: str
    extractor: str
    category: str
    severity: str
    detail: str
