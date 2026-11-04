"""Compare call-graph engines on one persisted index, without persisting.

Each engine rebuilds interprocedural flows from the same stored inputs as
indexing and ``flows calculate``: endpoints, integration methods, modules,
data operations and module dependencies. The comparison reports the flows
and call edges that both engines find, and those only one of them finds, so
the cost of choosing one engine over another is measured on the user's own
code.

Call edges are compared inside the universe both engines share, the indexed
integration methods. An edge that touches a method only one engine knows,
such as a CodeQL helper method, is counted apart instead of being reported
as a difference.
"""

import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypeVar

from systemlens.application.flow_calculation import load_persisted_flow_inputs
from systemlens.callgraph.engine import CallGraphEngine, EngineRequest
from systemlens.domain.code_flows import CallGraphEdge, CodeFlow
from systemlens.indexing.code_flows import CallGraph, materialize_call_graph_flows
from systemlens.storage.sqlite import Store

_EXAMPLES = 20
# (caller, callee, caller path, call line), with method labels for display.
_EdgeKey = tuple[str, str, str, int]
_K = TypeVar("_K")


@dataclass(frozen=True)
class _Run:
    seconds: float
    calls: int
    flow_count: int
    flow_confidence: dict[str, int]
    flows: dict[str, str]
    edge_count: int
    edges: dict[_EdgeKey, str]
    outside_edge_count: int
    outside_edges: dict[_EdgeKey, str]


def _flow_key(flow: CodeFlow) -> str:
    """A flow's full identity: its entry method and every step."""
    steps = " → ".join(
        f"{step.kind} {step.name}"
        + (f" [{step.operation}]" if step.operation else "")
        + f" ({step.path}:{step.start_line})"
        for step in flow.steps
    )
    return f"{flow.module} · {flow.method} · {flow.status} · {steps}"


def _edge_label(edge: CallGraphEdge) -> str:
    return edge.dispatch_confidence + (" inferred" if edge.inferred else "")


def _merge(labels: Mapping[_K, list[str]]) -> dict[_K, str]:
    """One value per key; several facts on one key stay visible together."""
    return {key: " + ".join(sorted(values)) for key, values in labels.items()}


def _engine_run(
    engine: CallGraphEngine,
    store: Store,
    *,
    timeout_seconds: int,
    max_hops: int,
    edge_confidence: str,
) -> _Run:
    repo_root = store.repo_root
    inputs = load_persisted_flow_inputs(store)
    methods = inputs.methods
    paths = sorted(store.get_file_hashes())
    graphs: list[CallGraph] = []
    started = time.perf_counter()
    with engine.open(EngineRequest(
        repo_root=repo_root, allowed_paths=paths, timeout_seconds=timeout_seconds,
        deadline=time.monotonic() + timeout_seconds,
    )) as session:
        calls = session.calls()
        reachability = session.reachability(methods)
    flows = materialize_call_graph_flows(
        methods, inputs.flow_endpoints, calls, repo_root=repo_root, modules=inputs.modules,
        operations=inputs.operations,
        source_paths=paths, max_hops=max_hops, codeql_edge_confidence=edge_confidence,
        reachability=reachability, call_graph_sink=graphs.append,
        closed_world=not engine.resolves_types,
        module_dependencies=store.all_module_dependencies(),
    )
    flows = [flow for flow in flows if any(step.kind == "method_call" for step in flow.steps)]
    # A method is named by its declaration line too, so overloads differ.
    shared = {method.id: f"{method.qualified_method}:{method.start_line}" for method in methods}
    names = dict(shared)
    edges: dict[_EdgeKey, list[str]] = defaultdict(list)
    outside: dict[_EdgeKey, list[str]] = defaultdict(list)
    graph_edges = graphs[-1].edges() if graphs else ()
    if graphs:
        names.update({
            method.id: f"{method.qualified_method}:{method.start_line}"
            for method in graphs[-1].methods if method.id not in shared
        })
    for edge in graph_edges:
        key = (names.get(edge.caller_id, edge.caller_id), names.get(edge.callee_id, edge.callee_id),
               edge.path, edge.line)
        in_shared = edge.caller_id in shared and edge.callee_id in shared
        (edges if in_shared else outside)[key].append(_edge_label(edge))
    flow_labels: dict[str, list[str]] = defaultdict(list)
    for flow in flows:
        flow_labels[_flow_key(flow)].append(flow.confidence)
    return _Run(
        seconds=round(time.perf_counter() - started, 2),
        calls=len(calls),
        flow_count=len(flows),
        flow_confidence=dict(Counter(flow.confidence for flow in flows)),
        flows=_merge(flow_labels),
        edge_count=sum(len(values) for values in edges.values()),
        edges=_merge(edges),
        outside_edge_count=sum(len(values) for values in outside.values()),
        outside_edges=_merge(outside),
    )


def compare_engines(
    store: Store,
    engines: Sequence[CallGraphEngine],
    *,
    timeout_seconds: int = 600,
    max_hops: int = 12,
    edge_confidence: str = "possible",
) -> dict[str, object]:
    """Run each available engine and diff its flows and call edges."""
    if len(engines) != 2 or engines[0].name == engines[1].name:
        raise ValueError("La comparaison porte sur exactement deux moteurs distincts.")
    runs: dict[str, _Run] = {}
    unavailable = [engine.name for engine in engines if not engine.available()]
    if unavailable:
        raise ValueError(f"Moteur indisponible : {', '.join(unavailable)}.")
    for engine in engines:
        runs[engine.name] = _engine_run(
            engine, store, timeout_seconds=timeout_seconds, max_hops=max_hops,
            edge_confidence=edge_confidence,
        )
    left, right = (engine.name for engine in engines)

    return {
        "engines": [left, right],
        "runs": {
            name: {
                "seconds": run.seconds,
                "calls": run.calls,
                "flows": run.flow_count,
                "edges": run.edge_count,
                "edges_outside_shared_methods": run.outside_edge_count,
                "flow_confidence": run.flow_confidence,
            }
            for name, run in runs.items()
        },
        "flows": _diff(runs[left].flows, runs[right].flows, left, right),
        "edges": _diff(runs[left].edges, runs[right].edges, left, right),
        # Edges touching a method only one engine models (CodeQL helpers):
        # outside the shared universe, so never a difference by themselves.
        "edges_outside_shared_methods": {
            name: {
                "count": run.outside_edge_count,
                "examples": [_label(item) for item in sorted(run.outside_edges, key=str)][:_EXAMPLES],
            }
            for name, run in runs.items()
        },
    }


def _diff(a: Mapping[_K, str], b: Mapping[_K, str], left: str, right: str) -> dict[str, object]:
    common = sorted(set(a) & set(b), key=str)
    return {
        "both": len(common),
        f"only_{left}": [_label(item) for item in sorted(set(a) - set(b), key=str)][:_EXAMPLES],
        f"only_{right}": [_label(item) for item in sorted(set(b) - set(a), key=str)][:_EXAMPLES],
        f"only_{left}_count": len(set(a) - set(b)),
        f"only_{right}_count": len(set(b) - set(a)),
        "confidence_changes": [
            f"{_label(item)} : {a[item]} → {b[item]}"
            for item in common if a[item] != b[item]
        ][:_EXAMPLES],
        "confidence_changes_count": sum(a[item] != b[item] for item in common),
    }


def _label(item: object) -> str:
    if isinstance(item, tuple):
        caller, callee, path, line = item
        return f"{caller} → {callee} ({path}:{line})"
    return str(item)


def render_engine_comparison_text(result: dict[str, object]) -> str:
    engines = result["engines"]
    assert isinstance(engines, list)
    left, right = str(engines[0]), str(engines[1])
    runs = result["runs"]
    assert isinstance(runs, dict)
    lines = ["[comparaison des moteurs de graphe d'appel]"]
    for name, run in runs.items():
        lines.append(
            f"  {name}: {run['flows']} flux interprocéduraux, {run['edges']} arêtes "
            f"entre méthodes indexées, {run['edges_outside_shared_methods']} arêtes hors de "
            f"ces méthodes, {run['calls']} appels moteur, {run['seconds']} s, "
            f"confiance {run['flow_confidence']}"
        )
    for kind, title in (("flows", "Flux"), ("edges", "Arêtes d'appel")):
        section = result[kind]
        assert isinstance(section, dict)
        lines.append("")
        lines.append(
            f"{title} : {section['both']} communs, "
            f"{section[f'only_{left}_count']} seulement {left}, "
            f"{section[f'only_{right}_count']} seulement {right}, "
            f"{section['confidence_changes_count']} changement(s) de confiance."
        )
        for side in (left, right):
            for item in section[f"only_{side}"]:
                lines.append(f"  [{side}] {item}")
        for change in section["confidence_changes"]:
            lines.append(f"  [confiance {left} → {right}] {change}")
    return "\n".join(lines)
