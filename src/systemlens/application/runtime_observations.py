"""Collect aggregated runtime traffic and reconcile it with the static index.

Observations are persisted apart from source and enrichment facts (ADR-60).
Reconciliation is read-only: it reports agreement and gaps between both views
without creating, confirming or removing any architecture fact. Every runtime
source implements :class:`RuntimeSource`; its rows share one vocabulary, so
reconciliation, hotspots, and the export overlay never depend on the backend.
"""

import os
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol, cast

from systemlens.discovery import elastic_apm, prometheus_otel
from systemlens.discovery.elastic_apm import (
    SOURCE,
    ElasticApmError,
    SearchFunction,
    cluster_origin,
    http_search,
    parse_window,
)
from systemlens.discovery.prometheus_otel import (
    PrometheusError,
    QueryFunction,
    http_query,
    prometheus_origin,
)
from systemlens.domain.code_flows import CodeFlow
from systemlens.domain.graph import GraphEdge
from systemlens.domain.models import MessageEndpoint
from systemlens.domain.runtime import (
    RuntimeCollection,
    RuntimeCollectionResult,
    RuntimeErrorGroup,
    RuntimeObservation,
)
from systemlens.infrastructure.config import config_path
from systemlens.storage.sqlite import Store

DEFAULT_API_KEY_ENV = "SYSTEMLENS_ELASTIC_API_KEY"
DEFAULT_BEARER_ENV = "SYSTEMLENS_PROMETHEUS_TOKEN"
HOTSPOT_METRICS = ("p95", "p99", "failures", "errors")
_SOURCE_LABELS = {SOURCE: "Elastic APM", prometheus_otel.SOURCE: "Prometheus (OpenTelemetry)"}
_HTTP_METHODS = "GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS"
_REQUEST_NAME = re.compile(rf"^({_HTTP_METHODS})\s+(/\S*)$")
_MESSAGING_NAMES = (
    re.compile(r"^(?P<topic>\S+)\s+(?:process|receive)$"),
    re.compile(r"^Kafka record from (?P<topic>\S+)$"),
)
_EndpointIndex = dict[str, dict[str, list[MessageEndpoint]]]
# OTel's Spring scheduling instrumentation names a run ``SimpleClass.method``.
_SCHEDULED_NAME = re.compile(r"^[A-Za-z_$][\w$]*\.[A-Za-z_$][\w$]*$")
SCHEDULED_KEY_PREFIX = "cron:"
_HTTP_TARGET_TYPES = {"http", "https", "grpc"}
_DATA_TARGETS = {
    "cassandra", "db", "dynamodb", "elasticsearch", "memcached", "mongodb",
    "mysql", "oracle", "postgresql", "redis", "sqlserver",
}


class RuntimeSource(Protocol):
    """One backend that returns observations for a window.

    A source folds its own metric model into ``RuntimeObservation`` rows,
    ``RuntimeErrorGroup`` rows when the backend groups exceptions, and one
    ``RuntimeCollection`` stating how complete the aggregation was. It never
    reads source facts and never persists anything itself.
    """

    @property
    def name(self) -> str: ...

    def collect(self, window: timedelta, now: datetime | None) -> RuntimeCollectionResult: ...


@dataclass(frozen=True)
class ElasticApmSource:
    """Elastic APM metric data streams through the Elasticsearch search API."""

    search: SearchFunction
    origin: str
    interval: str = "auto"
    name: str = SOURCE

    def collect(self, window: timedelta, now: datetime | None) -> RuntimeCollectionResult:
        return elastic_apm.collect(
            self.search, origin=self.origin, window=window, interval=self.interval, now=now,
            with_errors=True,
        )


@dataclass(frozen=True)
class PrometheusSource:
    """OpenTelemetry HTTP and messaging metrics through the Prometheus query API."""

    query: QueryFunction
    origin: str
    name: str = prometheus_otel.SOURCE

    def collect(self, window: timedelta, now: datetime | None) -> RuntimeCollectionResult:
        return prometheus_otel.collect(self.query, origin=self.origin, window=window, now=now)


def collect_runtime_source(
    repo_root: Path, source: RuntimeSource, *, since: str = "7d", now: datetime | None = None,
) -> RuntimeCollectionResult:
    """Collect one window from a source and persist it as a new collection."""
    if not config_path(repo_root).is_file():
        raise RuntimeError("Configuration absente. Lancez d'abord systemlens init.")
    try:
        window = parse_window(since)
        result = source.collect(window, now)
    except (ElasticApmError, PrometheusError) as exc:
        raise RuntimeError(str(exc)) from exc
    with Store(repo_root) as store:
        with store.transaction():
            store.add_runtime_collection(result.collection, result.observations, result.errors)
    return result


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
) -> tuple[list[RuntimeObservation], list[RuntimeErrorGroup]]:
    """Collect one Elastic APM window and persist it with its error groups."""
    try:
        origin = cluster_origin(url)
    except ElasticApmError as exc:
        raise RuntimeError(str(exc)) from exc
    searcher = search or http_search(
        url, api_key=os.environ.get(api_key_env) or None, ca_cert=ca_cert,
    )
    result = collect_runtime_source(
        repo_root, ElasticApmSource(searcher, origin, interval), since=since, now=now,
    )
    return result.observations, result.errors


def collect_prometheus_observations(
    repo_root: Path,
    url: str,
    *,
    since: str = "7d",
    bearer_env: str = DEFAULT_BEARER_ENV,
    ca_cert: Path | None = None,
    query: QueryFunction | None = None,
    now: datetime | None = None,
) -> RuntimeCollectionResult:
    """Collect one window of OpenTelemetry metrics from a Prometheus server.

    The bearer token is read from ``bearer_env`` only; it is sent as an
    ``Authorization`` header and never logged or persisted.
    """
    try:
        origin = prometheus_origin(url)
    except PrometheusError as exc:
        raise RuntimeError(str(exc)) from exc
    querier = query or http_query(url, bearer=os.environ.get(bearer_env) or None, ca_cert=ca_cert)
    return collect_runtime_source(repo_root, PrometheusSource(querier, origin), since=since, now=now)


def source_label(source: str) -> str:
    return _SOURCE_LABELS.get(source, source)


def completeness_line(collection: RuntimeCollection) -> str:
    """One sentence on the window, interval, source, and completeness."""
    parts = [
        f"{source_label(collection.source)} {collection.origin}",
        f"du {collection.window_start} au {collection.window_end}",
        f"intervalle {collection.interval}" if collection.interval else "intervalle inconnu",
    ]
    if collection.step:
        parts.append(f"pas de collecte {collection.step}")
    if collection.overflow_services:
        parts.append(
            "débordement d'agrégation pour "
            + ", ".join(collection.overflow_services)
            + " (compteurs minimaux)"
        )
    if collection.truncated:
        parts.append("pagination tronquée")
    if not collection.overflow_services and not collection.truncated:
        parts.append("ni débordement ni troncature")
    return " · ".join(parts) + "."


def completeness_dict(collection: RuntimeCollection) -> dict[str, object]:
    return {
        "collection_id": collection.id,
        "source": collection.source,
        "source_label": source_label(collection.source),
        "origin": collection.origin,
        "start": collection.window_start,
        "end": collection.window_end,
        "collected_at": collection.collected_at,
        "interval": collection.interval,
        "step": collection.step,
        "page_counts": dict(collection.page_counts),
        "truncated": collection.truncated,
        "overflow_services": list(collection.overflow_services),
        "notes": list(collection.notes),
        "line": completeness_line(collection),
    }


def scheduled_entries(
    flows: Iterable[CodeFlow], services: Collection[str],
) -> dict[str, list[tuple[str, str, int]]]:
    """Index flows triggered by ``@Scheduled`` methods per indexed service.

    Each entry is ``(qualified method, path, line)``. Scheduler triggers have
    no endpoint, so the export keys their traffic by ``cron:path:line``.
    """
    entries: dict[str, set[tuple[str, str, int]]] = defaultdict(set)
    for flow in flows:
        if flow.steps and flow.steps[0].kind == "cron_entry" and flow.module in services:
            trigger = flow.steps[0]
            entries[flow.module].add((flow.method, trigger.path, trigger.start_line))
    return {service: sorted(items) for service, items in entries.items()}


def scheduled_key(path: str, line: int) -> str:
    return f"{SCHEDULED_KEY_PREFIX}{path}:{line}"


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
    ignored_services: Collection[str] = (),
    errors: Collection[RuntimeErrorGroup] = (),
    scheduled: Mapping[str, Sequence[tuple[str, str, int]]] | None = None,
    collections: Sequence[RuntimeCollection] = (),
) -> dict[str, object]:
    """Compare persisted observations with indexed integrations.

    Observations of ``ignored_services`` stay in the report as
    ``not_compared`` but never count as unresolved or as service evidence.
    Error groups attach to the indexed entry point of their transaction when
    its name identifies one; errors of ignored services are left out.
    ``scheduled`` lists ``@Scheduled`` entries per service (see
    :func:`scheduled_entries`); a transaction named ``Class.method`` matches
    the entry of that simple class and method. Unmatched names of that shape
    stay ``not_compared``: repository and helper spans share it.
    ``collections`` describe the completeness of each source's latest run;
    the window then names them, and a row of an overflowed service is
    marked ``incomplete``.
    """
    services = set(endpoints_by_service)
    overflowed = {
        (collection.source, service)
        for collection in collections for service in collection.overflow_services
    }
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
    scheduled_by_name: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for service, items in (scheduled or {}).items():
        for qualified_method, path, line in items:
            owner, _dot, method = qualified_method.rpartition(".")
            simple = f"{owner.rpartition('.')[2]}.{method}"
            scheduled_by_name[service][simple].append(scheduled_key(path, line))
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
    ignored = set(ignored_services)
    for observation in observations:
        resolution, mapped_service, candidates = resolve(observation.service_name)
        status: str = "not_compared"
        hint: str | None = None
        endpoint_ids: list[str] = []
        matched_edge: dict[str, str] | None = None
        if observation.service_name in ignored:
            mapped_service = None
            hint = "service APM ignoré (runtime.ignore_services)"
        elif mapped_service is None:
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
            scheduled_ids = scheduled_by_name.get(mapped_service, {}).get(observation.target)
            if (
                status == "not_compared" and observation.kind == "transaction"
                and scheduled_ids and _SCHEDULED_NAME.match(observation.target)
            ):
                status, hint, endpoint_ids = "matched", None, sorted(scheduled_ids)
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
            "source": observation.source,
            "p50_us": observation.duration_p50_us,
            "p95_us": observation.duration_p95_us,
            "p99_us": observation.duration_p99_us,
            "avg_us": (
                observation.duration_sum_us / observation.calls
                if observation.kind == "destination" and observation.duration_sum_us
                and observation.calls else None
            ),
            "completeness": (
                "incomplete" if (observation.source, observation.service_name) in overflowed
                else "complete"
            ),
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

    error_rows: list[dict[str, object]] = []
    for group in errors:
        if group.service_name in ignored:
            continue
        _resolution, error_service, _candidates = resolve(group.service_name)
        entry_ids = (
            _entry_endpoint_ids(group.transaction_name, served[error_service], consumed[error_service])
            if error_service and group.transaction_name else []
        )
        error_rows.append({
            "service_name": group.service_name,
            "service": error_service,
            "exception_type": group.exception_type,
            "culprit": group.culprit,
            "transaction": group.transaction_name,
            "occurrences": group.occurrences,
            "last_seen": group.last_seen,
            "endpoint_ids": entry_ids,
        })

    summary = Counter(str(row["status"]) for row in rows)
    summary["static_only"] = len(static_only)
    summary["unverified"] = len(unverified)
    window = None
    if observations:
        first = observations[0]
        window = {
            "origin": first.origin, "start": first.window_start,
            "end": first.window_end, "collected_at": first.collected_at,
            "sources": sorted({item.source for item in observations}),
            "collections": [completeness_dict(item) for item in collections],
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
        "errors": error_rows,
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


def _entry_endpoint_ids(
    transaction: str,
    served: Mapping[str, list[MessageEndpoint]],
    consumed: Mapping[str, list[MessageEndpoint]],
) -> list[str]:
    """Return the indexed entry points a transaction name designates."""
    match = _REQUEST_NAME.match(" ".join(transaction.split()))
    if match:
        return _ids(served.get(_normalize_route(match.group(1), match.group(2)), []))
    topic = _messaging_topic(transaction)
    return _ids(consumed.get(topic, [])) if topic else []


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
    errors = cast(list[dict[str, object]], result.get("errors", []))
    endpoint_errors: dict[str, list[int]] = defaultdict(list)
    for index, error in enumerate(errors):
        for endpoint_id in cast(list[str], error["endpoint_ids"]):
            endpoint_errors[endpoint_id].append(index)
    observed = {row["service"] for row in rows if row["service"]}
    data_destinations: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        target = str(row["target"]).lower()
        target_type = str(row["target_type"] or "").lower()
        if row["kind"] == "destination" and row["service"] and (
            target in _DATA_TARGETS or target_type in _DATA_TARGETS
        ):
            data_destinations[str(row["service"])].append({
                "target": row["target"], "calls": row["calls"], "failures": row["failures"],
            })
    hotspot_metric = "p95" if any(row.get("p95_us") is not None for row in rows) else "failures"
    return {
        "window": result["window"],
        "summary": result["summary"],
        "observations": rows,
        "hotspots": rank_hotspots(result, hotspot_metric, 10),
        "endpoint_observations": dict(sorted(endpoint_observations.items())),
        "silent_endpoint_ids": sorted({
            endpoint_id
            for item in static_only
            for endpoint_id in cast(list[str], item["endpoint_ids"])
        }),
        "silent": static_only,
        "unverified": result["unverified"],
        "errors": errors,
        "endpoint_errors": dict(sorted(endpoint_errors.items())),
        "data_destinations": dict(sorted(data_destinations.items())),
        "rest_call_covered": result["rest_call_covered"],
        "services": {
            service: "observed" if service in observed else "no_telemetry"
            for service in sorted(endpoints_by_service)
        },
        "unresolved_services": result["unresolved_services"],
    }


def rank_hotspots(result: Mapping[str, object], by: str, limit: int) -> dict[str, object]:
    """Rank entry points of the reconciliation by one runtime metric.

    ``by`` is ``p95``, ``p99``, ``failures``, or ``errors`` (occurrences of
    the error groups attached to the entry point). Ties break by service
    then target, so the list is deterministic. Entry points whose row is
    ``not_compared`` are listed apart, with the ``unverified`` indexed
    entries of the window, because their value cannot be attributed to an
    indexed entry point. Rows without the metric are counted, not ranked:
    a missing percentile is not zero latency.
    """
    if by not in HOTSPOT_METRICS:
        raise ValueError(f"--by attend {', '.join(HOTSPOT_METRICS)}, reçu {by!r}.")
    rows = cast(list[dict[str, object]], result.get("observations", []))
    errors = cast(list[dict[str, object]], result.get("errors", []))
    window = result.get("window")
    error_occurrences: dict[tuple[object, object], int] = defaultdict(int)
    for error in errors:
        error_occurrences[(error["service"], error["transaction"])] += int(
            cast(int, error["occurrences"])
        )

    def value(row: dict[str, object]) -> float | None:
        if by == "failures":
            return float(cast(int, row["failures"]))
        if by == "errors":
            occurrences = error_occurrences.get((row["service"], row["target"]))
            return float(occurrences) if occurrences is not None else 0.0
        raw = row.get(f"{by}_us")
        return float(cast(float, raw)) if raw is not None else None

    def item(row: dict[str, object], metric: float | None) -> dict[str, object]:
        return {
            "service": row["service"] or row["service_name"],
            "service_name": row["service_name"],
            "target": row["target"],
            "endpoint_ids": row["endpoint_ids"],
            "status": row["status"],
            "window": {k: window[k] for k in ("start", "end")} if isinstance(window, dict) else None,
            "calls": row["calls"],
            "failures": row["failures"],
            "by": by,
            "value": metric,
            "p95_us": row.get("p95_us"),
            "p99_us": row.get("p99_us"),
            "completeness": row.get("completeness", "complete"),
            "source": row.get("source"),
        }

    transactions = [row for row in rows if row["kind"] == "transaction"]
    ranked: list[tuple[float, str, str, dict[str, object]]] = []
    unranked: list[dict[str, object]] = []
    apart: list[dict[str, object]] = []
    for row in transactions:
        metric = value(row)
        entry = item(row, metric)
        if row["status"] == "not_compared":
            apart.append(entry)
        elif metric is None:
            unranked.append(entry)
        else:
            ranked.append((-metric, str(entry["service"]), str(row["target"]), entry))
    ranked.sort(key=lambda candidate: candidate[:3])
    unverified = cast(list[dict[str, object]], result.get("unverified", []))
    return {
        "by": by,
        "limit": limit,
        "rows": [entry for _value, _service, _target, entry in ranked[:limit]],
        "without_metric": len(unranked),
        "not_compared": apart[:limit],
        "unverified": [
            {"service": entry["service"], "name": entry["name"], "reason": entry["reason"]}
            for entry in unverified
        ],
        "note": (
            "Valeurs approximatives et minimales : percentiles calculés par le "
            "backend sur des histogrammes agrégés, compteurs dépendants de "
            "l'échantillonnage."
        ),
    }


def compare_with_previous(
    result: Mapping[str, object],
    collections: Sequence[RuntimeCollection],
    previous_rows: Mapping[str, Sequence[RuntimeObservation]],
) -> dict[str, object]:
    """Add the delta of calls, failure ratio, and p95 against the previous run.

    A comparison needs a previous collection of the same source and
    interval; ``previous_rows`` maps a collection ID to its observations.
    Otherwise the result states why no comparison is shown.
    """
    by_source: dict[str, list[RuntimeCollection]] = defaultdict(list)
    for collection in collections:
        by_source[collection.source].append(collection)
    comparisons: dict[str, object] = {}
    rows = cast(list[dict[str, object]], result.get("observations", []))
    for source, history in sorted(by_source.items()):
        ordered = sorted(history, key=lambda item: (item.collected_at, item.id), reverse=True)
        current = ordered[0]
        previous = next((item for item in ordered[1:] if item.interval == current.interval), None)
        if previous is None:
            comparisons[source] = {
                "previous": None,
                "reason": (
                    "aucune collecte antérieure de même source et de même intervalle"
                    if len(ordered) == 1 else
                    "les collectes antérieures ont un autre intervalle"
                ),
                "rows": [],
            }
            continue
        earlier = {
            (item.kind, item.service_name, item.target, item.target_type): item
            for item in previous_rows.get(previous.id, ())
        }
        deltas = []
        for row in rows:
            if row.get("source") != source:
                continue
            key = (
                str(row["kind"]), str(row["service_name"]), str(row["target"]),
                cast(str | None, row["target_type"]),
            )
            before = earlier.get(key)
            if before is None:
                continue
            calls = int(cast(int, row["calls"]))
            failures = int(cast(int, row["failures"]))
            ratio = failures / calls if calls else 0.0
            before_ratio = before.failures / before.calls if before.calls else 0.0
            p95 = cast(float | None, row.get("p95_us"))
            deltas.append({
                "kind": row["kind"], "service": row["service"], "service_name": row["service_name"],
                "target": row["target"], "calls_delta": calls - before.calls,
                "failure_ratio_delta": ratio - before_ratio,
                "p95_delta_us": (
                    p95 - before.duration_p95_us
                    if p95 is not None and before.duration_p95_us is not None else None
                ),
            })
        comparisons[source] = {
            "previous": completeness_dict(previous),
            "reason": None,
            "rows": deltas,
        }
    return comparisons


def _duration_label(micros: object) -> str:
    if not isinstance(micros, int | float):
        return "n/d"
    return f"{micros / 1000:.0f} ms" if micros >= 1000 else f"{micros:.0f} µs"


def render_hotspots_text(hotspots: Mapping[str, object]) -> list[str]:
    by = str(hotspots["by"])
    unit = {"p95": "p95", "p99": "p99", "failures": "échecs", "errors": "erreurs APM"}[by]
    lines = [f"Points chauds par {unit} (minimum échantillonné, percentiles approximatifs) :"]
    rows = cast(list[dict[str, object]], hotspots["rows"])
    if not rows:
        lines.append("  aucun point d'entrée rapproché ne porte cette mesure")
    for index, row in enumerate(rows, start=1):
        value = row["value"]
        rendered = _duration_label(value) if by in {"p95", "p99"} else f"{int(cast(float, value))}"
        incomplete = " · incomplet (débordement)" if row["completeness"] == "incomplete" else ""
        endpoints = ", ".join(cast(list[str], row["endpoint_ids"])) or "aucun endpoint indexé"
        lines.append(
            f"  {index:>2}. {row['service']} {row['target']} · {unit} {rendered} · "
            f"{row['calls']} appels · {row['status']}{incomplete} · {endpoints}"
        )
    without = int(cast(int, hotspots["without_metric"]))
    if without:
        lines.append(f"  {without} point(s) d'entrée sans cette mesure, non classé(s)")
    apart = cast(list[dict[str, object]], hotspots["not_compared"])
    if apart:
        lines.append("  Non comparables (hors index ou non attribuables) :")
        lines.extend(f"    {row['service']} {row['target']} ({row['calls']} appels)" for row in apart)
    unverified = cast(list[dict[str, object]], hotspots["unverified"])
    if unverified:
        lines.append("  Indexés mais non vérifiables :")
        lines.extend(f"    {row['service']} {row['name']} — {row['reason']}" for row in unverified)
    return lines


def render_comparison_text(comparisons: Mapping[str, object]) -> list[str]:
    lines = ["Comparaison avec la collecte précédente :"]
    for source, payload in comparisons.items():
        data = cast(dict[str, object], payload)
        if data["reason"]:
            lines.append(f"  {source_label(source)} : {data['reason']}")
            continue
        previous = cast(dict[str, object], data["previous"])
        lines.append(
            f"  {source_label(source)} : contre la collecte du {previous['end']} "
            f"(intervalle {previous['interval']})"
        )
        for row in cast(list[dict[str, object]], data["rows"]):
            p95 = row["p95_delta_us"]
            p95_text = f", p95 {'+' if cast(float, p95) >= 0 else ''}{_duration_label(abs(cast(float, p95)))}" if p95 is not None else ""
            ratio = cast(float, row["failure_ratio_delta"]) * 100
            lines.append(
                f"    {row['service'] or row['service_name']} {row['kind']} {row['target']} : "
                f"appels {cast(int, row['calls_delta']):+d}, échecs {ratio:+.1f} pt{p95_text}"
            )
    return lines


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
    for collection in cast(list[dict[str, object]], window.get("collections", [])):
        lines.append(f"Complétude : {collection['line']}")
        for note in cast(list[str], collection.get("notes", [])):
            lines.append(f"  note : {note}")
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
    errors = result.get("errors", [])
    assert isinstance(errors, list)
    lines.append("")
    if errors:
        lines.append("Erreurs APM (groupes d'exceptions, messages non conservés) :")
        for error in errors:
            where = f" sur {error['transaction']}" if error["transaction"] else ""
            culprit = f" — {error['culprit']}" if error["culprit"] else ""
            lines.append(
                f"  {error['service'] or error['service_name']} "
                f"{error['exception_type'] or 'exception inconnue'} "
                f"× {error['occurrences']}{where}{culprit}"
            )
    else:
        lines.append(
            "Aucune erreur APM sur la fenêtre (lecture de logs-apm.error-* requise pour les collecter)."
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
    hotspots = result.get("hotspots")
    if isinstance(hotspots, dict):
        lines.append("")
        lines.extend(render_hotspots_text(hotspots))
    comparison = result.get("comparison")
    if isinstance(comparison, dict):
        lines.append("")
        lines.extend(render_comparison_text(comparison))
    lines.append("")
    lines.append(
        "Les compteurs sont des minima qui dépendent de l'échantillonnage ; "
        "les percentiles sont des approximations du backend."
    )
    return "\n".join(lines)
