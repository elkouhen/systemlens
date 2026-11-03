"""Compare call-graph engines on one persisted index, without persisting.

Each engine rebuilds interprocedural flows from the same stored endpoints,
integration methods and modules. The comparison reports the flows and call
edges that both engines find, and those only one of them finds, so the cost
of choosing one engine over another is measured on the user's own code.
"""

import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypeVar

from systemlens.callgraph.engine import CallGraphEngine, EngineRequest
from systemlens.domain.code_flows import CodeFlow
from systemlens.indexing.code_flows import CallGraph, materialize_call_graph_flows
from systemlens.indexing.integration_methods import scheduled_trigger_endpoints
from systemlens.storage.sqlite import Store

_EXAMPLES = 20
_EdgeKey = tuple[str, str, int, str]
_K = TypeVar("_K")


@dataclass(frozen=True)
class _Run:
    seconds: float
    calls: int
    flows: dict[str, str]
    edges: dict[_EdgeKey, str]


def _flow_key(flow: CodeFlow) -> str:
    first, last = flow.steps[0], flow.steps[-1]
    return f"{flow.module} · {first.kind} {first.name} → {last.kind} {last.name}"


def _engine_run(
    engine: CallGraphEngine,
    store: Store,
    *,
    timeout_seconds: int,
    max_hops: int,
    edge_confidence: str,
) -> _Run:
    repo_root = store.repo_root
    endpoints = store.all_endpoints()
    methods = store.all_integration_methods()
    modules = store.all_modules()
    paths = sorted(store.get_file_hashes())
    endpoints = [*endpoints, *scheduled_trigger_endpoints(repo_root, paths, endpoints, modules)]
    graphs: list[CallGraph] = []
    started = time.perf_counter()
    with engine.open(EngineRequest(
        repo_root=repo_root, allowed_paths=paths, timeout_seconds=timeout_seconds,
        deadline=time.monotonic() + timeout_seconds,
    )) as session:
        calls = session.calls()
        reachability = session.reachability(methods)
    flows = materialize_call_graph_flows(
        methods, endpoints, calls, repo_root=repo_root, modules=modules,
        source_paths=paths, max_hops=max_hops, codeql_edge_confidence=edge_confidence,
        reachability=reachability, call_graph_sink=graphs.append,
        closed_world=not engine.resolves_types,
        module_dependencies=store.all_module_dependencies(),
    )
    flows = [flow for flow in flows if any(step.kind == "method_call" for step in flow.steps)]
    names = {method.id: method.qualified_method for method in methods}
    edges: dict[_EdgeKey, str] = {}
    if graphs:
        names.update({method.id: method.qualified_method for method in graphs[-1].methods})
        for edge in graphs[-1].edges():
            key = (names.get(edge.caller_id, edge.caller_id), edge.path, edge.line,
                   names.get(edge.callee_id, edge.callee_id))
            edges[key] = edge.dispatch_confidence
    return _Run(
        seconds=round(time.perf_counter() - started, 2),
        calls=len(calls),
        flows={_flow_key(flow): flow.confidence for flow in flows},
        edges=edges,
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
                "flows": len(run.flows),
                "edges": len(run.edges),
                "flow_confidence": dict(Counter(run.flows.values())),
            }
            for name, run in runs.items()
        },
        "flows": _diff(runs[left].flows, runs[right].flows, left, right),
        "edges": _diff(runs[left].edges, runs[right].edges, left, right),
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
    }


def _label(item: object) -> str:
    if isinstance(item, tuple):
        caller, path, line, callee = item
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
            f"  {name}: {run['flows']} flux interprocéduraux, {run['edges']} arêtes, "
            f"{run['calls']} appels moteur, {run['seconds']} s, confiance {run['flow_confidence']}"
        )
    for kind, title in (("flows", "Flux"), ("edges", "Arêtes d'appel")):
        section = result[kind]
        assert isinstance(section, dict)
        lines.append("")
        lines.append(
            f"{title} : {section['both']} communs, "
            f"{section[f'only_{left}_count']} seulement {left}, "
            f"{section[f'only_{right}_count']} seulement {right}."
        )
        for side in (left, right):
            for item in section[f"only_{side}"]:
                lines.append(f"  [{side}] {item}")
        for change in section["confidence_changes"]:
            lines.append(f"  [confiance {left} → {right}] {change}")
    return "\n".join(lines)
