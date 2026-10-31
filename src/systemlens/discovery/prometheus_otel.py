"""Optional runtime collection from OpenTelemetry metrics through Prometheus.

Only the HTTP query API is used, with instant queries whose range vectors
are aggregated server side. No series is downloaded: each query returns one
value per service and target over the window. The OpenTelemetry HTTP and
messaging semantic conventions are mapped to the same observation kinds as
the Elastic APM collector, so reconciliation applies unchanged.
"""

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from systemlens.discovery.elastic_apm import collection_id, observation_id
from systemlens.domain.runtime import (
    RuntimeCollection,
    RuntimeCollectionResult,
    RuntimeObservation,
)

# ``query(expression, time)`` returns the parsed ``/api/v1/query`` payload.
QueryFunction = Callable[[str, str], dict[str, object]]

SOURCE = "prometheus"
_TIMEOUT_SECONDS = 60
_QUANTILES = (0.5, 0.95, 0.99)
# OTel metric names as the Prometheus exporter writes them, with and without
# the unit suffix (``--no-add-metric-suffixes``), oldest convention last.
_SERVER_METRICS = ("http_server_request_duration_seconds", "http_server_request_duration")
_CLIENT_METRICS = ("http_client_request_duration_seconds", "http_client_request_duration")
_MESSAGING_METRICS = (
    "messaging_client_operation_duration_seconds", "messaging_client_operation_duration",
)
_LEGACY_PUBLISH_METRICS = ("messaging_publish_duration_seconds", "messaging_publish_duration")
_LEGACY_PROCESS_METRICS = ("messaging_process_duration_seconds", "messaging_process_duration")
_SERVICE_LABELS = ("service_name", "job")
_SERVER_LABELS = ("http_request_method", "http_route", "http_response_status_code", "error_type")
_CLIENT_LABELS = ("server_address", "server_port", "http_response_status_code", "error_type")
_MESSAGING_LABELS = (
    "messaging_destination_name", "messaging_operation_type", "messaging_operation_name",
    "messaging_system", "error_type",
)


class PrometheusError(RuntimeError):
    """Prometheus could not provide a trustworthy aggregate."""


def prometheus_origin(url: str) -> str:
    """Return the persisted server origin, rejecting embedded credentials."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise PrometheusError("L'URL Prometheus doit commencer par http:// ou https://.")
    if parts.username or parts.password:
        raise PrometheusError(
            "L'URL Prometheus ne doit pas contenir d'identifiants ; "
            "utilisez un jeton dans une variable d'environnement."
        )
    if parts.query or parts.fragment:
        raise PrometheusError("L'URL Prometheus ne doit contenir ni requête ni fragment.")
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


def http_query(url: str, *, bearer: str | None, ca_cert: Path | None = None) -> QueryFunction:
    """Build an instant-query function bound to one server and its token."""
    base = url.rstrip("/")
    context = ssl.create_default_context(cafile=str(ca_cert) if ca_cert else None)

    def query(expression: str, time: str) -> dict[str, object]:
        parameters = urllib.parse.urlencode({"query": expression, "time": time})
        request = urllib.request.Request(f"{base}/api/v1/query?{parameters}", method="GET")
        if bearer:
            request.add_header("Authorization", f"Bearer {bearer}")
        try:
            with urllib.request.urlopen(  # nosec B310 - scheme validated by prometheus_origin
                request, timeout=_TIMEOUT_SECONDS, context=context,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise PrometheusError(f"Prometheus a refusé la requête (HTTP {exc.code}).") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PrometheusError(f"Prometheus est injoignable : {exc}") from exc
        except json.JSONDecodeError as exc:
            raise PrometheusError("Prometheus a renvoyé une réponse non JSON.") from exc
        if not isinstance(payload, dict):
            raise PrometheusError("Prometheus a renvoyé une réponse inattendue.")
        return payload

    return query


def promql_range(window: timedelta) -> str:
    """Render a window as a PromQL duration (``7d`` or ``36h``)."""
    hours = int(window.total_seconds() // 3600)
    return f"{hours // 24}d" if hours % 24 == 0 and hours else f"{hours}h"


def _window_bounds(window: timedelta, now: datetime | None) -> tuple[str, str]:
    end_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    start = (end_time - window).isoformat().replace("+00:00", "Z")
    return start, end_time.isoformat().replace("+00:00", "Z")


def _results(query: QueryFunction, expression: str, time: str) -> list[tuple[dict[str, str], float]]:
    payload = query(expression, time)
    if payload.get("status") != "success":
        error = payload.get("error")
        raise PrometheusError(
            f"Prometheus a renvoyé une erreur : {error}" if error
            else "Prometheus a renvoyé une réponse sans statut success."
        )
    data = payload.get("data")
    result = data.get("result") if isinstance(data, dict) else None
    rows: list[tuple[dict[str, str], float]] = []
    for item in result if isinstance(result, list) else []:
        if not isinstance(item, dict):
            continue
        metric = item.get("metric")
        value = item.get("value")
        if not isinstance(metric, dict) or not isinstance(value, list) or len(value) != 2:
            continue
        try:
            number = float(value[1])
        except (TypeError, ValueError):
            continue
        if number != number:  # NaN: an empty histogram quantile
            continue
        rows.append(({str(k): str(v) for k, v in metric.items()}, number))
    return rows


def _service(labels: dict[str, str]) -> str | None:
    for name in _SERVICE_LABELS:
        value = labels.get(name)
        if value:
            return value
    return None


def _failed(labels: dict[str, str], *, server: bool) -> bool:
    status = labels.get("http_response_status_code", "")
    if labels.get("error_type"):
        return True
    if not status.isdigit():
        return False
    return int(status) >= (500 if server else 400)


class _Folder:
    """Fold instant-query rows into observations keyed like APM rows."""

    def __init__(self) -> None:
        self.totals: dict[tuple[str, str, str, str | None], list[float]] = {}
        self.percentiles: dict[tuple[str, str, str, str | None], dict[float, float]] = {}
        self.hints: dict[tuple[str, str, str, str | None], str] = {}

    def add(self, key: tuple[str, str, str, str | None], calls: float, failures: float,
            duration_us: float | None = None) -> None:
        total = self.totals.setdefault(key, [0.0, 0.0, 0.0])
        total[0] += calls
        total[1] += failures
        total[2] += duration_us or 0.0

    def quantile(self, key: tuple[str, str, str, str | None], quantile: float, seconds: float) -> None:
        self.percentiles.setdefault(key, {})[quantile] = seconds * 1_000_000


def _first_metric_with_data(
    query: QueryFunction, names: tuple[str, ...], suffix: str, by: str, range_: str, time: str,
    page_counts: dict[str, int],
) -> tuple[str | None, list[tuple[dict[str, str], float]]]:
    for name in names:
        expression = f"sum by ({by}) (increase({name}_{suffix}[{range_}]))"
        rows = _results(query, expression, time)
        page_counts[f"{name}_{suffix}"] = 1
        if rows:
            return name, rows
    return None, []


def collect(
    query: QueryFunction,
    *,
    origin: str,
    window: timedelta,
    now: datetime | None = None,
) -> RuntimeCollectionResult:
    """Collect one window of OpenTelemetry HTTP and messaging metrics.

    Request transactions come from ``http.server.request.duration`` folded
    per service, method, and route; a series without a route is kept under
    its method only, which reconciliation reports as not attributable.
    REST destinations come from ``http.client.request.duration`` per server
    address and port. Messaging operations map publishes to Kafka
    destinations and processing to messaging transactions. Percentiles are
    ``histogram_quantile`` approximations computed by the server from the
    exported buckets. Prometheus exports no exception grouping, so the
    collection has no error group.
    """
    start, end = _window_bounds(window, now)
    range_ = promql_range(window)
    collection = collection_id(SOURCE, origin, start, end, range_)
    page_counts: dict[str, int] = {}
    notes: list[str] = []
    folder = _Folder()
    service_by = ", ".join(_SERVICE_LABELS)

    server_metric, server_rows = _first_metric_with_data(
        query, _SERVER_METRICS, "count", f"{service_by}, {', '.join(_SERVER_LABELS)}",
        range_, end, page_counts,
    )
    unrouted = 0
    for labels, count in server_rows:
        service = _service(labels)
        if service is None or count <= 0:
            continue
        method = labels.get("http_request_method", "").upper()
        route = labels.get("http_route", "")
        if not route:
            unrouted += 1
        target = f"{method} {route}".strip() or "HTTP"
        folder.add(("transaction", service, target, "request"), count,
                   count if _failed(labels, server=True) else 0.0)
    if server_metric is not None:
        quantile_by = f"le, {service_by}, http_request_method, http_route"
        for quantile in _QUANTILES:
            expression = (
                f"histogram_quantile({quantile}, sum by ({quantile_by}) "
                f"(increase({server_metric}_bucket[{range_}])))"
            )
            page_counts[f"{server_metric}_bucket"] = 1
            for labels, seconds in _results(query, expression, end):
                service = _service(labels)
                if service is None:
                    continue
                method = labels.get("http_request_method", "").upper()
                route = labels.get("http_route", "")
                target = f"{method} {route}".strip() or "HTTP"
                folder.quantile(("transaction", service, target, "request"), quantile, seconds)
    else:
        notes.append("aucune métrique http.server.request.duration sur la fenêtre")
    if unrouted:
        notes.append(
            f"{unrouted} série(s) HTTP serveur sans étiquette http_route : "
            "trafic non attribuable à une route"
        )

    client_metric, client_rows = _first_metric_with_data(
        query, _CLIENT_METRICS, "count", f"{service_by}, {', '.join(_CLIENT_LABELS)}",
        range_, end, page_counts,
    )
    if client_metric is not None:
        sums = {
            tuple(labels.get(name, "") for name in (*_SERVICE_LABELS, *_CLIENT_LABELS)): seconds
            for labels, seconds in _results(
                query,
                f"sum by ({service_by}, {', '.join(_CLIENT_LABELS)}) "
                f"(increase({client_metric}_sum[{range_}]))",
                end,
            )
        }
        page_counts[f"{client_metric}_sum"] = 1
        for labels, count in client_rows:
            service = _service(labels)
            host = labels.get("server_address", "")
            if service is None or not host or count <= 0:
                continue
            port = labels.get("server_port", "")
            target = f"{host}:{port}" if port else host
            client_seconds: float | None = sums.get(
                tuple(labels.get(name, "") for name in (*_SERVICE_LABELS, *_CLIENT_LABELS))
            )
            folder.add(("destination", service, target, "http"), count,
                       count if _failed(labels, server=False) else 0.0,
                       client_seconds * 1_000_000 if client_seconds is not None else None)

    messaging_metric, messaging_rows = _first_metric_with_data(
        query, _MESSAGING_METRICS, "count", f"{service_by}, {', '.join(_MESSAGING_LABELS)}",
        range_, end, page_counts,
    )
    legacy: list[tuple[str, list[tuple[dict[str, str], float]]]] = []
    if messaging_metric is None:
        for names, operation in ((_LEGACY_PUBLISH_METRICS, "publish"), (_LEGACY_PROCESS_METRICS, "process")):
            metric, rows = _first_metric_with_data(
                query, names, "count", f"{service_by}, {', '.join(_MESSAGING_LABELS)}",
                range_, end, page_counts,
            )
            if metric is not None:
                legacy.append((operation, rows))
    for operation, rows in ([("", messaging_rows)] if messaging_metric else legacy):
        for labels, count in rows:
            service = _service(labels)
            topic = labels.get("messaging_destination_name", "")
            if service is None or not topic or count <= 0:
                continue
            system = labels.get("messaging_system", "kafka").lower() or "kafka"
            kind = operation or labels.get("messaging_operation_type") or labels.get(
                "messaging_operation_name", ""
            )
            failed = count if labels.get("error_type") else 0.0
            if kind in {"publish", "create", "send"}:
                folder.add(("destination", service, f"{system}/{topic}", system), count, failed)
            elif kind in {"process", "receive", "deliver"}:
                folder.add(("transaction", service, f"{topic} process", "messaging"), count, failed)
            else:
                folder.add(("transaction", service, f"{topic} {kind or 'unknown'}", "messaging"),
                           count, failed)

    step: str | None = None
    for labels, seconds in _results(
        query, 'max(prometheus_target_interval_length_seconds{quantile="0.5"})', end,
    ):
        step = f"{seconds:g}s"
    page_counts["prometheus_target_interval_length_seconds"] = 1
    if step is None:
        notes.append("intervalle de collecte Prometheus inconnu (métrique interne absente)")
    notes.append("aucun groupe d'erreurs : les métriques ne portent pas d'exception")

    observations = []
    for key, (calls, failures, duration) in sorted(
        folder.totals.items(), key=lambda item: tuple(part or "" for part in item[0]),
    ):
        kind, service, target, target_type = key
        quantiles = folder.percentiles.get(key, {})
        observations.append(RuntimeObservation(
            id=observation_id(SOURCE, collection, kind, service, target, target_type),
            source=SOURCE, origin=origin, kind=kind, service_name=service, target=target,
            target_type=target_type, calls=int(round(calls)), failures=int(round(failures)),
            duration_sum_us=duration if kind == "destination" and duration else None,
            window_start=start, window_end=end, collected_at=end,
            duration_p50_us=quantiles.get(0.5), duration_p95_us=quantiles.get(0.95),
            duration_p99_us=quantiles.get(0.99), collection_id=collection,
        ))
    return RuntimeCollectionResult(
        collection=RuntimeCollection(
            id=collection, source=SOURCE, origin=origin, window_start=start, window_end=end,
            interval=range_, step=step, collected_at=end,
            page_counts=dict(sorted(page_counts.items())), truncated=False,
            overflow_services=(), notes=tuple(notes),
        ),
        observations=observations,
        errors=[],
    )

