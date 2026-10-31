"""Collect aggregated runtime traffic and reconcile it with the static index.

Observations are persisted apart from source and enrichment facts (ADR-60).
Reconciliation is read-only: it reports agreement and gaps between both views
without creating, confirming or removing any architecture fact.
"""

import os
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import cast

from systemlens.discovery.elastic_apm import (
    SOURCE,
    ElasticApmError,
    SearchFunction,
    cluster_origin,
    collect_observations,
    http_search,
    parse_window,
)
from systemlens.domain.graph import GraphEdge
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.runtime import RuntimeObservation
from systemlens.infrastructure.config import config_path
from systemlens.storage.sqlite import Store

DEFAULT_API_KEY_ENV = "SYSTEMLENS_ELASTIC_API_KEY"
_HTTP_METHODS = "GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS"
_REQUEST_NAME = re.compile(rf"^({_HTTP_METHODS})\s+(/\S*)$")
_MESSAGING_NAMES = (
    re.compile(r"^(?P<topic>\S+)\s+(?:process|receive)$"),
    re.compile(r"^Kafka record from (?P<topic>\S+)$"),
)
_EndpointIndex = dict[str, dict[str, list[MessageEndpoint]]]
_HTTP_TARGET_TYPES = {"http", "https", "grpc"}
_DATA_TARGETS = {
    "cassandra", "db", "dynamodb", "elasticsearch", "memcached", "mongodb",
    "mysql", "oracle", "postgresql", "redis", "sqlserver",
}


def collect_runtime_observations(
    repo_root: Path,
    url: str,
    *,
    since: str = "7d",
    interval: str = "auto",
    api_key_env: str = DEFAULT_API_KEY_ENV,
    ca_cert: Path | None = None,
    search: SearchFunction | None = None,
    now: datetime | None = None,
) -> list[RuntimeObservation]:
    """Collect one Elastic APM window and replace the persisted observations."""
    if not config_path(repo_root).is_file():
        raise RuntimeError("Configuration absente. Lancez d'abord systemlens init.")
    try:
        origin = cluster_origin(url)
        window = parse_window(since)
        searcher = search or http_search(
            url, api_key=os.environ.get(api_key_env) or None, ca_cert=ca_cert,
        )
        observations = collect_observations(
            searcher, origin=origin, window=window, interval=interval, now=now,
        )
    except ElasticApmError as exc:
        raise RuntimeError(str(exc)) from exc
    with Store(repo_root) as store:
        with store.transaction():
            store.replace_runtime_observations(SOURCE, observations)
    return observations


def parse_service_map(
    entries: list[str], configured: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Merge ``runtime.service_map`` with repeated ``APM=SERVICE`` options.

    Command-line entries override configured entries with the same APM name.
    """
    mapping: dict[str, str] = dict(configured or {})
    for entry in entries:
        name, separator, service = entry.partition("=")
        if not separator or not name.strip() or not service.strip():
            raise ValueError(f"--service-map attend APM=SERVICE, reçu {entry!r}.")
        mapping[name.strip()] = service.strip()
    return mapping


def _normalize_route(method: str, path: str) -> str:
    path = re.sub(r"\{[^}]*\}", "{}", path.split("?", 1)[0])
    if len(path) > 1:
        path = path.rstrip("/")
    return f"{method.upper()} {path}"


def _static_route(endpoint: MessageEndpoint) -> str | None:
    match = _REQUEST_NAME.match(" ".join(endpoint.topic.split()))
    return _normalize_route(match.group(1), match.group(2)) if match else None


def _messaging_topic(name: str) -> str | None:
    for pattern in _MESSAGING_NAMES:
        match = pattern.match(name)
        if match:
            return match.group("topic")
    return None


def _service_resolver(
    services: set[str], explicit: Mapping[str, str],
) -> Callable[[str], tuple[str, str | None, list[str]]]:
    by_lower = {service.lower(): service for service in services}

    def resolve(name: str) -> tuple[str, str | None, list[str]]:
        if name in explicit:
            return "mapped", explicit[name], []
        exact = by_lower.get(name.lower())
        if exact is not None:
            return "mapped", exact, []
        lowered = name.lower()
        candidates = sorted(
            service for service in services
            if re.search(rf"(?:^|[-_.]){re.escape(service.lower())}(?:$|[-_.])", lowered)
        )
        if len(candidates) == 1:
            return "mapped", candidates[0], []
        return ("ambiguous" if candidates else "unmapped"), None, candidates

    return resolve


def reconcile_observations(
    observations: list[RuntimeObservation],
    endpoints_by_service: Mapping[str, list[MessageEndpoint]],
    edges: list[GraphEdge],
    service_map: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Compare persisted observations with indexed integrations."""
    services = set(endpoints_by_service)
    explicit = dict(service_map or {})
    unknown = sorted(set(explicit.values()) - services)
    if unknown:
        raise ValueError(
            f"La correspondance de services vise des services non indexés : {', '.join(unknown)}."
        )
    resolve = _service_resolver(services, explicit)

    produced: _EndpointIndex = defaultdict(lambda: defaultdict(list))
    consumed: _EndpointIndex = defaultdict(lambda: defaultdict(list))
    served: _EndpointIndex = defaultdict(lambda: defaultdict(list))
    dynamic_outputs: set[str] = set()
    for service, endpoints in endpoints_by_service.items():
        for endpoint in endpoints:
            dynamic = endpoint.topic_dynamic or endpoint.topic.endswith("<dynamic>")
            if endpoint.role in {"produce", "call"} and dynamic:
                dynamic_outputs.add(service)
            if dynamic:
                continue
            if endpoint.system == "kafka" and endpoint.role == "produce":
                produced[service][endpoint.topic].append(endpoint)
            elif endpoint.system == "kafka" and endpoint.role == "consume":
                consumed[service][endpoint.topic].append(endpoint)
            elif endpoint.system == "rest" and endpoint.role == "serve":
                route = _static_route(endpoint)
                if route:
                    served[service][route].append(endpoint)
    rest_edges: dict[tuple[str, str], list[str]] = defaultdict(list)
    for edge in edges:
        if edge.kind == "rest":
            rest_edges[(edge.from_service, edge.to_service)].append(edge.from_endpoint.id)

    seen: set[tuple[str, str, str]] = set()
    # Per service, what the telemetry proves it would have seen. Silence is
    # only asserted where instrumentation demonstrably names that traffic.
    evidence: dict[str, set[str]] = defaultdict(set)
    observed_services: set[str] = set()
    unresolved: dict[str, dict[str, object]] = {}
    rows: list[dict[str, object]] = []
    for observation in observations:
        resolution, mapped_service, candidates = resolve(observation.service_name)
        status: str = "not_compared"
        hint: str | None = None
        endpoint_ids: list[str] = []
        matched_edge: dict[str, str] | None = None
        if mapped_service is None:
            unresolved[observation.service_name] = {
                "name": observation.service_name, "status": resolution,
                "candidates": candidates,
            }
            hint = f"service APM {resolution}"
        else:
            observed_services.add(mapped_service)
            status, hint, endpoint_ids, matched_edge = _classify(
                observation, mapped_service, resolve, produced, consumed, served,
                rest_edges, seen,
            )
            evidence[mapped_service].update(_coverage(observation))
            if status == "observed_only" and mapped_service in dynamic_outputs \
                    and observation.kind == "destination":
                hint = "le service a une sortie dynamique qui peut expliquer cette cible"
        rows.append({
            "kind": observation.kind,
            "service_name": observation.service_name,
            "service": mapped_service,
            "target": observation.target,
            "target_type": observation.target_type,
            "calls": observation.calls,
            "failures": observation.failures,
            "status": status,
            "hint": hint,
            "endpoint_ids": endpoint_ids,
            **({"edge": matched_edge} if matched_edge else {}),
        })

    static_only: list[dict[str, object]] = []
    unverified: list[dict[str, object]] = []
    for service in sorted(observed_services):
        for integration, index in (
            ("kafka_produce", produced), ("kafka_consume", consumed), ("rest_serve", served),
        ):
            reason = _uncovered_reason(integration, evidence[service])
            for name, endpoints in sorted(index.get(service, {}).items()):
                if (service, integration, name) in seen:
                    continue
                endpoint = endpoints[0]
                item: dict[str, object] = {
                    "service": service, "integration": integration, "name": endpoint.topic,
                    "path": endpoint.path, "line": endpoint.start_line,
                    "endpoint_ids": sorted(item.id for item in endpoints),
                }
                if reason is None:
                    static_only.append(item)
                else:
                    unverified.append({**item, "reason": reason})

    summary = Counter(str(row["status"]) for row in rows)
    summary["static_only"] = len(static_only)
    summary["unverified"] = len(unverified)
    window = None
    if observations:
        first = observations[0]
        window = {
            "origin": first.origin, "start": first.window_start,
            "end": first.window_end, "collected_at": first.collected_at,
        }
    return {
        "window": window,
        "summary": {
            status: summary.get(status, 0)
            for status in (
                "matched", "observed_only", "external", "static_only", "unverified", "not_compared",
            )
        },
        "observations": rows,
        "static_only": static_only,
        "unverified": unverified,
        "rest_call_covered": sorted(
            service for service, kinds in evidence.items() if "http_destination" in kinds
        ),
        "unresolved_services": [unresolved[name] for name in sorted(unresolved)],
        "unobserved_services": sorted(
            service for service, endpoints in endpoints_by_service.items()
            if endpoints and service not in observed_services
        ),
    }


def _coverage(observation: RuntimeObservation) -> set[str]:
    """Name the traffic families that one observation proves are instrumented."""
    target, target_type = observation.target, (observation.target_type or "").lower()
    if observation.kind == "destination":
        prefix, slash, _remainder = target.partition("/")
        if target_type == "kafka" or (slash and prefix.lower() == "kafka"):
            return {"kafka_destination"}
        if not slash and target.lower() not in _DATA_TARGETS and (
            not target_type or target_type in _HTTP_TARGET_TYPES
        ):
            return {"http_destination"}
        return set()
    if target_type == "request":
        named = _REQUEST_NAME.match(" ".join(target.split())) is not None
        return {"routed_request" if named else "unrouted_request"}
    if target_type == "messaging":
        return {"named_message" if _messaging_topic(target) else "unnamed_message"}
    return set()


def _uncovered_reason(integration: str, kinds: set[str]) -> str | None:
    """Explain why silence cannot be asserted, or return None when it can."""
    if integration == "rest_serve":
        if "unrouted_request" in kinds:
            return "transactions HTTP sans route : trafic non attribuable"
        return None if "routed_request" in kinds else "aucune transaction HTTP observée"
    if integration == "kafka_consume":
        if "unnamed_message" in kinds:
            return "transactions de messaging sans topic : trafic non attribuable"
        return None if "named_message" in kinds else "aucune consommation Kafka observée"
    return None if "kafka_destination" in kinds else "aucune publication Kafka observée"


def _ids(endpoints: list[MessageEndpoint]) -> list[str]:
    return sorted({endpoint.id for endpoint in endpoints})


def _classify(
    observation: RuntimeObservation,
    service: str,
    resolve: Callable[[str], tuple[str, str | None, list[str]]],
    produced: _EndpointIndex,
    consumed: _EndpointIndex,
    served: _EndpointIndex,
    rest_edges: Mapping[tuple[str, str], list[str]],
    seen: set[tuple[str, str, str]],
) -> tuple[str, str | None, list[str], dict[str, str] | None]:
    """Return status, hint, matched endpoint IDs, and matched REST edge."""
    target, target_type = observation.target, (observation.target_type or "").lower()
    if observation.kind == "destination":
        prefix, slash, remainder = target.partition("/")
        if target_type == "kafka" or (slash and prefix.lower() == "kafka"):
            topic = remainder if slash else target
            producers = produced.get(service, {}).get(topic)
            if producers:
                seen.add((service, "kafka_produce", topic))
                return "matched", None, _ids(producers), None
            return "observed_only", "producteur Kafka absent de l'index", [], None
        if slash or target.lower() in _DATA_TARGETS or (
            target_type and target_type not in _HTTP_TARGET_TYPES
        ):
            return (
                "not_compared", "destination hors périmètre (donnée, cache ou autre système)",
                [], None,
            )
        host = target.rsplit(":", 1)[0] if target.count(":") == 1 else target
        resolution, target_service, _candidates = resolve(host)
        if target_service is None:
            if resolution == "ambiguous":
                return "not_compared", "hôte ambigu", [], None
            return "external", None, [], None
        callers = rest_edges.get((service, target_service))
        if callers:
            edge = {"source": service, "target": target_service}
            return "matched", None, sorted(set(callers)), edge
        return "observed_only", f"aucune arête REST indexée vers {target_service}", [], None
    if target_type == "request":
        match = _REQUEST_NAME.match(" ".join(target.split()))
        if match is None:
            return "not_compared", "nom de transaction sans route", [], None
        route = _normalize_route(match.group(1), match.group(2))
        servers = served.get(service, {}).get(route)
        if servers:
            seen.add((service, "rest_serve", route))
            return "matched", None, _ids(servers), None
        return "observed_only", "route servie absente de l'index", [], None
    if target_type == "messaging":
        consumed_topic = _messaging_topic(target)
        if consumed_topic is None:
            return "not_compared", "topic non identifiable dans le nom de transaction", [], None
        consumers = consumed.get(service, {}).get(consumed_topic)
        if consumers:
            seen.add((service, "kafka_consume", consumed_topic))
            return "matched", None, _ids(consumers), None
        return "observed_only", "consumer Kafka absent de l'index", [], None
    return "not_compared", "type de transaction hors périmètre", [], None


def runtime_overlay(
    result: Mapping[str, object],
    endpoints_by_service: Mapping[str, list[MessageEndpoint]],
) -> dict[str, object] | None:
    """Index a reconciliation result by endpoint ID for the HTML export.

    Several endpoints can share one observation: a REST destination is
    measured per service pair, not per call site. Consumers must therefore
    deduplicate observation indexes before summing counts.
    """
    if result.get("window") is None:
        return None
    rows = cast(list[dict[str, object]], result["observations"])
    static_only = cast(list[dict[str, object]], result["static_only"])
    endpoint_observations: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        for endpoint_id in cast(list[str], row["endpoint_ids"]):
            endpoint_observations[endpoint_id].append(index)
    observed = {row["service"] for row in rows if row["service"]}
    return {
        "window": result["window"],
        "summary": result["summary"],
        "observations": rows,
        "endpoint_observations": dict(sorted(endpoint_observations.items())),
        "silent_endpoint_ids": sorted({
            endpoint_id
            for item in static_only
            for endpoint_id in cast(list[str], item["endpoint_ids"])
        }),
        "silent": static_only,
        "unverified": result["unverified"],
        "rest_call_covered": result["rest_call_covered"],
        "services": {
            service: "observed" if service in observed else "no_telemetry"
            for service in sorted(endpoints_by_service)
        },
        "unresolved_services": result["unresolved_services"],
    }


_STATUS_LABELS = {
    "matched": "rapprochées",
    "observed_only": "observées mais absentes de l'index",
    "external": "vers des cibles externes",
    "static_only": "indexées mais sans trafic observé",
    "unverified": "indexées, trafic non vérifiable",
    "not_compared": "non comparées",
}


def render_runtime_report_text(result: Mapping[str, object]) -> str:
    """Render a reconciliation report for terminal reading."""
    lines: list[str] = []
    window = result.get("window")
    if not isinstance(window, dict):
        return "Aucune observation persistée. Lancez systemlens observe elastic."
    lines.append(
        f"Observations {window['origin']} du {window['start']} au {window['end']}."
    )
    summary = result["summary"]
    assert isinstance(summary, dict)
    for status, label in _STATUS_LABELS.items():
        lines.append(f"  {summary.get(status, 0):>5} {label}")
    rows = result["observations"]
    assert isinstance(rows, list)
    observed_only = [row for row in rows if row["status"] == "observed_only"]
    if observed_only:
        lines.append("")
        lines.append("Observées mais absentes de l'index :")
        for row in observed_only:
            hint = f" — {row['hint']}" if row["hint"] else ""
            lines.append(
                f"  {row['service']} {row['kind']} {row['target']} "
                f"({row['calls']} appels){hint}"
            )
    static_only = result["static_only"]
    assert isinstance(static_only, list)
    if static_only:
        lines.append("")
        lines.append("Indexées mais sans trafic observé sur la fenêtre :")
        for item in static_only:
            lines.append(
                f"  {item['service']} {item['integration']} {item['name']} "
                f"({item['path']}:{item['line']})"
            )
    unverified = result["unverified"]
    assert isinstance(unverified, list)
    if unverified:
        lines.append("")
        lines.append("Indexées mais non vérifiables (l'instrumentation ne permet pas de conclure) :")
        for item in unverified:
            lines.append(
                f"  {item['service']} {item['integration']} {item['name']} — {item['reason']}"
            )
    unresolved = result["unresolved_services"]
    assert isinstance(unresolved, list)
    if unresolved:
        lines.append("")
        lines.append("Services APM non rapprochés (utilisez --service-map APM=SERVICE) :")
        for item in unresolved:
            candidates = item["candidates"]
            suffix = f" : {', '.join(candidates)}" if candidates else ""
            lines.append(f"  {item['name']} ({item['status']}){suffix}")
    unobserved = result["unobserved_services"]
    assert isinstance(unobserved, list)
    if unobserved:
        lines.append("")
        lines.append(
            "Services indexés sans observation (télémétrie absente, pas forcément inactifs) : "
            + ", ".join(unobserved)
        )
    lines.append("")
    lines.append("Les compteurs sont des minima qui dépendent de l'échantillonnage APM.")
    return "\n".join(lines)
