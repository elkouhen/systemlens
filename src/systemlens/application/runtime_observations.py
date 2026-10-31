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
    interval: str = "60m",
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


def parse_service_map(entries: list[str]) -> dict[str, str]:
    """Parse repeated ``APM=SERVICE`` options."""
    mapping: dict[str, str] = {}
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
        raise ValueError(f"--service-map vise des services non indexés : {', '.join(unknown)}.")
    resolve = _service_resolver(services, explicit)

    produced: dict[str, dict[str, MessageEndpoint]] = defaultdict(dict)
    consumed: dict[str, dict[str, MessageEndpoint]] = defaultdict(dict)
    served: dict[str, dict[str, MessageEndpoint]] = defaultdict(dict)
    dynamic_outputs: set[str] = set()
    for service, endpoints in endpoints_by_service.items():
        for endpoint in endpoints:
            dynamic = endpoint.topic_dynamic or endpoint.topic.endswith("<dynamic>")
            if endpoint.role in {"produce", "call"} and dynamic:
                dynamic_outputs.add(service)
            if dynamic:
                continue
            if endpoint.system == "kafka" and endpoint.role == "produce":
                produced[service].setdefault(endpoint.topic, endpoint)
            elif endpoint.system == "kafka" and endpoint.role == "consume":
                consumed[service].setdefault(endpoint.topic, endpoint)
            elif endpoint.system == "rest" and endpoint.role == "serve":
                route = _static_route(endpoint)
                if route:
                    served[service].setdefault(route, endpoint)
    rest_edges = {
        (edge.from_service, edge.to_service) for edge in edges if edge.kind == "rest"
    }

    seen: set[tuple[str, str, str]] = set()
    observed_services: set[str] = set()
    unresolved: dict[str, dict[str, object]] = {}
    rows: list[dict[str, object]] = []
    for observation in observations:
        resolution, mapped_service, candidates = resolve(observation.service_name)
        status: str = "not_compared"
        hint: str | None = None
        if mapped_service is None:
            unresolved[observation.service_name] = {
                "name": observation.service_name, "status": resolution,
                "candidates": candidates,
            }
            hint = f"service APM {resolution}"
        else:
            observed_services.add(mapped_service)
            status, hint = _classify(
                observation, mapped_service, resolve, produced, consumed, served,
                rest_edges, seen,
            )
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
        })

    static_only: list[dict[str, object]] = []
    for service in sorted(observed_services):
        for integration, index in (
            ("kafka_produce", produced), ("kafka_consume", consumed), ("rest_serve", served),
        ):
            for name, endpoint in sorted(index.get(service, {}).items()):
                if (service, integration, name) in seen:
                    continue
                static_only.append({
                    "service": service, "integration": integration, "name": endpoint.topic,
                    "path": endpoint.path, "line": endpoint.start_line,
                })

    summary = Counter(str(row["status"]) for row in rows)
    summary["static_only"] = len(static_only)
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
            for status in ("matched", "observed_only", "external", "static_only", "not_compared")
        },
        "observations": rows,
        "static_only": static_only,
        "unresolved_services": [unresolved[name] for name in sorted(unresolved)],
        "unobserved_services": sorted(
            service for service, endpoints in endpoints_by_service.items()
            if endpoints and service not in observed_services
        ),
    }


def _classify(
    observation: RuntimeObservation,
    service: str,
    resolve: Callable[[str], tuple[str, str | None, list[str]]],
    produced: Mapping[str, Mapping[str, MessageEndpoint]],
    consumed: Mapping[str, Mapping[str, MessageEndpoint]],
    served: Mapping[str, Mapping[str, MessageEndpoint]],
    rest_edges: set[tuple[str, str]],
    seen: set[tuple[str, str, str]],
) -> tuple[str, str | None]:
    target, target_type = observation.target, (observation.target_type or "").lower()
    if observation.kind == "destination":
        prefix, slash, remainder = target.partition("/")
        if target_type == "kafka" or (slash and prefix.lower() == "kafka"):
            topic = remainder if slash else target
            if topic in produced.get(service, {}):
                seen.add((service, "kafka_produce", topic))
                return "matched", None
            return "observed_only", "producteur Kafka absent de l'index"
        if slash or target.lower() in _DATA_TARGETS or (
            target_type and target_type not in _HTTP_TARGET_TYPES
        ):
            return "not_compared", "destination hors périmètre (donnée, cache ou autre système)"
        host = target.rsplit(":", 1)[0] if target.count(":") == 1 else target
        resolution, target_service, _candidates = resolve(host)
        if target_service is None:
            return ("not_compared", "hôte ambigu") if resolution == "ambiguous" else (
                "external", None
            )
        if (service, target_service) in rest_edges:
            return "matched", None
        return "observed_only", f"aucune arête REST indexée vers {target_service}"
    if target_type == "request":
        match = _REQUEST_NAME.match(" ".join(target.split()))
        if match is None:
            return "not_compared", "nom de transaction sans route"
        route = _normalize_route(match.group(1), match.group(2))
        if route in served.get(service, {}):
            seen.add((service, "rest_serve", route))
            return "matched", None
        return "observed_only", "route servie absente de l'index"
    if target_type == "messaging":
        consumed_topic = _messaging_topic(target)
        if consumed_topic is None:
            return "not_compared", "topic non identifiable dans le nom de transaction"
        if consumed_topic in consumed.get(service, {}):
            seen.add((service, "kafka_consume", consumed_topic))
            return "matched", None
        return "observed_only", "consumer Kafka absent de l'index"
    return "not_compared", "type de transaction hors périmètre"


_STATUS_LABELS = {
    "matched": "rapprochées",
    "observed_only": "observées mais absentes de l'index",
    "external": "vers des cibles externes",
    "static_only": "indexées mais sans trafic observé",
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
