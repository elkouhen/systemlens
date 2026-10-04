import json

import pytest

from systemlens.application.ai_graph import AiGraphError, load_ai_graph, load_fact_manifest


def test_load_ai_graph_projects_events_and_keeps_unresolved_claims(tmp_path):
    manifest = {
        "format": "systemlens-ai-graph-v1",
        "project": "demo",
        "nodes": [
            {"id": "a", "kind": "service", "name": "acquisition"},
            {"id": "b", "kind": "service", "name": "conformite"},
            {"id": "t", "kind": "topic", "name": "invoice.created"},
        ],
        "edges": [
            {"id": "e1", "source": "a", "target": "b", "kind": "event", "channel": "invoice.created", "confidence": "high"},
            {"id": "e2", "source": "a", "target": "b", "kind": "http", "status": "ambiguous", "reason": "multiple configured clients"},
        ],
    }
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    services, edges, collections, issues = load_ai_graph(path)

    assert sorted(services) == ["acquisition", "conformite"]
    assert len(edges) == 1
    assert edges[0].kind == "kafka"
    assert collections == {}
    assert issues == ["e2: multiple configured clients"]


def test_load_ai_graph_rejects_absolute_evidence_paths(tmp_path):
    path = tmp_path / "graph.json"
    path.write_text(json.dumps({
        "format": "systemlens-ai-graph-v1",
        "nodes": [{"id": "a", "kind": "service", "name": "a", "evidence": [{"path": "/tmp/source.java"}]}],
        "edges": [],
    }), encoding="utf-8")

    with pytest.raises(AiGraphError, match="doit être relatif"):
        load_ai_graph(path)


def test_load_fact_manifest_preserves_top_level_event_channel_in_metadata(tmp_path):
    path = tmp_path / "facts.json"
    path.write_text(json.dumps({
        "format": "systemlens-ai-graph-v1",
            "generated_by": {"namespace": "ai-messaging"},
        "mode": "complete",
        "nodes": [
            {"id": "orders", "kind": "service", "name": "orders", "module": "orders-api"},
            {"id": "billing", "kind": "service", "name": "billing"},
        ],
        "edges": [{
            "id": "orders-billing",
            "source": "orders",
            "target": "billing",
            "kind": "event",
            "channel": "orders.created",
            "confidence": "high",
            "module": "orders-api",
        }],
    }), encoding="utf-8")

    facts, _namespace, _complete = load_fact_manifest(path)

    edge = next(fact for fact in facts if fact.fact_type == "edge")
    assert edge.metadata == {"channel": "orders.created"}
    assert edge.module == "orders-api"
    assert next(fact for fact in facts if fact.fact_type == "node").module == "orders-api"


def test_load_fact_manifest_rejects_duplicate_storage_ids(tmp_path):
    path = tmp_path / "facts.json"
    path.write_text(json.dumps({
        "format": "systemlens-ai-graph-v1",
        "generated_by": {"namespace": "ai-architecture"},
        "mode": "partial",
        "nodes": [
            {"id": "orders", "storage_id": "same", "kind": "service", "name": "orders"},
            {"id": "billing", "storage_id": "same", "kind": "service", "name": "billing"},
        ],
        "edges": [],
    }), encoding="utf-8")

    with pytest.raises(AiGraphError, match="identifiant de stockage dupliqué"):
        load_fact_manifest(path)
