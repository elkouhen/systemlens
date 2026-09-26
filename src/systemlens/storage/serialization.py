"""SQLite row and JSON serialization for persisted domain objects."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from systemlens.domain.models import Finding, MessageEndpoint
from systemlens.domain.module_inventory import (
    BlockingPoint,
    KafkaMethod,
    MongoField,
    MongoMethod,
    MongoPersistenceClass,
    SourceEvidence,
)
from systemlens.domain.runtime import KubernetesWorkload


@dataclass(frozen=True)
class CodeChunk:
    """Persisted source chunk returned by the storage search API."""

    id: str
    path: str
    start_line: int
    end_line: int
    language: str
    content: str


def method_to_json(item: object) -> dict[str, object]:
    data = dict(item.__dict__)
    evidence = data.get("evidence")
    if evidence is not None:
        data["evidence"] = evidence.__dict__
    return data


def evidence_from_json(data: dict[str, Any]) -> SourceEvidence | None:
    evidence = data.pop("evidence", None)
    return SourceEvidence(**evidence) if evidence else None


def mongo_method_from_json(data: dict[str, Any]) -> MongoMethod:
    data = dict(data)
    evidence = evidence_from_json(data)
    return MongoMethod(**data, evidence=evidence)


def mongo_persistence_class_from_json(data: dict[str, Any]) -> MongoPersistenceClass:
    data = dict(data)
    data["fields"] = tuple(
        MongoField(**{**field, "references": tuple(field.get("references", []))})
        for field in data.get("fields", [])
    )
    return MongoPersistenceClass(**data)


def kafka_method_from_json(data: dict[str, Any]) -> KafkaMethod:
    data = dict(data)
    evidence = evidence_from_json(data)
    return KafkaMethod(**data, evidence=evidence)


def blocking_point_from_json(data: dict[str, Any]) -> BlockingPoint:
    data = dict(data)
    evidence = evidence_from_json(data)
    return BlockingPoint(**data, evidence=evidence)


def kubernetes_workload_from_json(data: dict[str, Any]) -> KubernetesWorkload:
    return KubernetesWorkload(**data)


def row_to_finding(row: sqlite3.Row) -> Finding:
    return Finding(
        id=row["id"], rule_id=row["rule_id"], severity=row["severity"],
        message=row["message"], path=row["path"], start_line=row["start_line"],
        end_line=row["end_line"], snippet=row["snippet"], fix=row["fix"],
        cwe=json.loads(row["cwe"]) if row["cwe"] else [],
        owasp=json.loads(row["owasp"]) if row["owasp"] else [],
        module=row["module"], qualified_name=row["qualified_name"],
    )


def row_to_code_chunk(row: sqlite3.Row) -> CodeChunk:
    return CodeChunk(
        id=row["id"], path=row["path"], start_line=row["start_line"],
        end_line=row["end_line"], language=row["language"], content=row["content"],
    )


def row_to_endpoint(row: sqlite3.Row) -> MessageEndpoint:
    return MessageEndpoint(
        id=row["id"], role=row["role"], system=row["system"], topic=row["topic"],
        topic_dynamic=bool(row["topic_dynamic"]), source=row["source"],
        framework=row["framework"], path=row["path"], start_line=row["start_line"],
        end_line=row["end_line"], snippet=row["snippet"], module=row["module"],
        qualified_name=row["qualified_name"], message_type=row["message_type"],
        topic_display=row["topic_display"],
    )
