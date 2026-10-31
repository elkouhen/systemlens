"""Optional Elastic APM metric collection through the Elasticsearch search API.

Only pre-aggregated APM metric data streams and error-group aggregations are
queried, through paginated composite aggregations computed by Elasticsearch.
Trace and error documents are never downloaded, so the transfer depends on
cardinality rather than traffic.
"""

import json
import re
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

from systemlens.domain.runtime import (
    RuntimeCollection,
    RuntimeCollectionResult,
    RuntimeErrorGroup,
    RuntimeObservation,
)

SearchFunction = Callable[[str, dict[str, object]], dict[str, object]]

SOURCE = "elastic_apm"
ERROR_INDEX = "logs-apm.error-*"
INTERVALS = ("1m", "10m", "60m")
# Coarsest rollup first: it scans the fewest metric documents.
_AUTO_INTERVAL_ORDER = ("60m", "10m", "1m")
_PAGE_SIZE = 1000
_MAX_PAGES = 100
_TIMEOUT_SECONDS = 60
# Elasticsearch names the overflow bucket of a terms aggregation ``_other``.
OVERFLOW_KEY = "_other"
_PERCENTS = (50.0, 95.0, 99.0)


class ElasticApmError(RuntimeError):
    """Elasticsearch could not provide a trustworthy APM metric aggregate."""


def parse_window(value: str) -> timedelta:
    """Parse a positive window such as ``24h`` or ``7d``."""
    match = re.fullmatch(r"([1-9]\d*)([hd])", value.strip())
    if match is None:
        raise ElasticApmError(f"Fenêtre invalide {value!r} : utilisez par exemple 24h ou 7d.")
    amount = int(match.group(1))
    return timedelta(hours=amount) if match.group(2) == "h" else timedelta(days=amount)


def cluster_origin(url: str) -> str:
    """Return the persisted cluster origin, rejecting embedded credentials."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ElasticApmError("L'URL Elasticsearch doit commencer par http:// ou https://.")
    if parts.username or parts.password:
        raise ElasticApmError(
            "L'URL Elasticsearch ne doit pas contenir d'identifiants ; "
            "utilisez une clé d'API dans une variable d'environnement."
        )
    if parts.query or parts.fragment:
        raise ElasticApmError("L'URL Elasticsearch ne doit contenir ni requête ni fragment.")
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


def http_search(
    url: str, *, api_key: str | None, ca_cert: Path | None = None,
) -> SearchFunction:
    """Build a search function bound to one cluster and its credentials."""
    base = url.rstrip("/")
    context = ssl.create_default_context(cafile=str(ca_cert) if ca_cert else None)

    def search(index: str, body: dict[str, object]) -> dict[str, object]:
        request = urllib.request.Request(
            f"{base}/{index}/_search",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if api_key:
            request.add_header("Authorization", f"ApiKey {api_key}")
        try:
            with urllib.request.urlopen(  # nosec B310 - scheme validated by cluster_origin
                request, timeout=_TIMEOUT_SECONDS, context=context,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ElasticApmError(
                f"Elasticsearch a refusé la requête sur {index} (HTTP {exc.code})."
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ElasticApmError(f"Elasticsearch est injoignable : {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ElasticApmError("Elasticsearch a renvoyé une réponse non JSON.") from exc
        if not isinstance(payload, dict):
            raise ElasticApmError("Elasticsearch a renvoyé une réponse inattendue.")
        return payload

    return search


def _composite_buckets(
    search: SearchFunction,
    index: str,
    sources: list[dict[str, object]],
    metrics: dict[str, object],
    start: str,
    end: str,
    page_counts: dict[str, int] | None = None,
) -> Iterator[dict[str, object]]:
    after: object = None
    for page in range(_MAX_PAGES):
        composite: dict[str, object] = {"size": _PAGE_SIZE, "sources": sources}
        if after is not None:
            composite["after"] = after
        groups_request: dict[str, object] = {"composite": composite}
        if metrics:
            groups_request["aggs"] = metrics
        response = search(index, {
            "size": 0,
            "track_total_hits": False,
            "query": {"range": {"@timestamp": {"gte": start, "lt": end}}},
            "aggs": {"groups": groups_request},
        })
        if page_counts is not None:
            page_counts[index] = page + 1
        if "error" in response:
            raise ElasticApmError(f"Elasticsearch a renvoyé une erreur sur {index}.")
        aggregations = response.get("aggregations")
        groups = aggregations.get("groups") if isinstance(aggregations, dict) else None
        if not isinstance(groups, dict):
            return
        buckets = groups.get("buckets") or []
        yield from buckets
        after = groups.get("after_key")
        if not buckets or after is None:
            return
    raise ElasticApmError(
        f"Plus de {_MAX_PAGES * _PAGE_SIZE} groupes sur {index} : "
        "réduisez la fenêtre ou vérifiez la cardinalité des noms."
    )


def detect_interval(search: SearchFunction, start: str, end: str) -> str:
    """Return the coarsest rollup interval holding transactions in the window.

    APM Server versions and configurations do not all write every rollup;
    Elastic 9.5 in the observability lab writes only ``1m``. A wildcard index
    without a match returns no hit instead of an error, so probe explicitly.
    """
    for interval in _AUTO_INTERVAL_ORDER:
        response = search(f"metrics-apm.transaction.{interval}-*", {
            "size": 0,
            "track_total_hits": 1,
            "query": {"range": {"@timestamp": {"gte": start, "lt": end}}},
        })
        hits = response.get("hits")
        total = hits.get("total") if isinstance(hits, dict) else None
        value = total.get("value") if isinstance(total, dict) else total
        if isinstance(value, int) and value > 0:
            return interval
    raise ElasticApmError(
        "Aucune métrique APM agrégée sur la fenêtre (intervalles 60m, 10m et 1m) : "
        "vérifiez l'URL, les droits de lecture sur metrics-apm* et la fenêtre."
    )


def _terms(name: str, field: str, *, optional: bool = False) -> dict[str, object]:
    terms: dict[str, object] = {"field": field}
    if optional:
        terms["missing_bucket"] = True
    return {name: {"terms": terms}}


def _number(bucket: dict[str, object], name: str) -> float:
    value = bucket.get(name)
    raw = value.get("value") if isinstance(value, dict) else None
    return float(raw) if isinstance(raw, int | float) else 0.0


def _doc_count(value: object) -> float:
    count = value.get("doc_count") if isinstance(value, dict) else None
    return float(count) if isinstance(count, int | float) else 0.0


def _percentile(bucket: dict[str, object], percent: float) -> float | None:
    """Read one keyed percentile; ``None`` when the field is absent or empty."""
    duration = bucket.get("duration")
    values = duration.get("values") if isinstance(duration, dict) else None
    if not isinstance(values, dict):
        return None
    raw = values.get(f"{percent:.1f}", values.get(str(percent)))
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    return float(raw)


def _window_bounds(window: timedelta, now: datetime | None) -> tuple[str, str]:
    end_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    start = (end_time - window).isoformat().replace("+00:00", "Z")
    return start, end_time.isoformat().replace("+00:00", "Z")


def _top_term(bucket: dict[str, object], name: str) -> str | None:
    value = bucket.get(name)
    buckets = value.get("buckets") if isinstance(value, dict) else None
    if not isinstance(buckets, list) or not buckets or not isinstance(buckets[0], dict):
        return None
    key = buckets[0].get("key")
    return key if isinstance(key, str) and key else None


def collection_id(source: str, origin: str, start: str, end: str, interval: str | None) -> str:
    key = "\x1f".join((source, origin, start, end, interval or ""))
    return sha256(key.encode("utf-8")).hexdigest()[:24]


def observation_id(
    source: str, collection: str, kind: str, service: str, target: str, target_type: str | None,
) -> str:
    """Identify one row of one collection; history keeps several per target."""
    key = "\x1f".join((source, collection, kind, service, target, target_type or ""))
    return sha256(key.encode("utf-8")).hexdigest()[:24]


def collect_error_groups(
    search: SearchFunction,
    *,
    origin: str,
    window: timedelta,
    now: datetime | None = None,
    collection: str | None = None,
    page_counts: dict[str, int] | None = None,
) -> list[RuntimeErrorGroup]:
    """Aggregate APM error groups per service, without reading any message.

    Each group keeps its most frequent exception type, culprit and
    transaction name. A wildcard index without a match, or one the API key
    may not read, yields no group rather than an error.
    """
    start, end = _window_bounds(window, now)
    collection = collection or collection_id(SOURCE, origin, start, end, None)
    groups: list[RuntimeErrorGroup] = []
    for bucket in _composite_buckets(
        search, ERROR_INDEX,
        [_terms("service", "service.name"), _terms("grouping_key", "error.grouping_key")],
        {
            "last_seen": {"max": {"field": "@timestamp"}},
            "exception_type": {"terms": {"field": "error.exception.type", "size": 1}},
            "culprit": {"terms": {"field": "error.culprit", "size": 1}},
            "transaction": {"terms": {"field": "transaction.name", "size": 1}},
        },
        start, end, page_counts,
    ):
        key = bucket.get("key") or {}
        assert isinstance(key, dict)
        service, grouping_key = key.get("service"), key.get("grouping_key")
        if not isinstance(service, str) or not isinstance(grouping_key, str):
            continue
        last = bucket.get("last_seen")
        last_seen = last.get("value_as_string") if isinstance(last, dict) else None
        count = bucket.get("doc_count")
        transaction = _top_term(bucket, "transaction")
        groups.append(RuntimeErrorGroup(
            id=observation_id(SOURCE, collection, "error", service, grouping_key, None),
            source=SOURCE, origin=origin, service_name=service, grouping_key=grouping_key,
            exception_type=_top_term(bucket, "exception_type"),
            culprit=_top_term(bucket, "culprit"),
            transaction_name=transaction.split("?", 1)[0] if transaction else None,
            occurrences=int(count) if isinstance(count, int | float) else 0,
            last_seen=last_seen if isinstance(last_seen, str) else None,
            window_start=start, window_end=end, collected_at=end,
            collection_id=collection,
        ))
    return sorted(groups, key=lambda group: (group.service_name, -group.occurrences, group.grouping_key))


def collect_observations(
    search: SearchFunction,
    *,
    origin: str,
    window: timedelta,
    interval: str = "60m",
    now: datetime | None = None,
) -> list[RuntimeObservation]:
    """Fold service-destination and transaction metrics into observations."""
    return collect(search, origin=origin, window=window, interval=interval, now=now).observations


def collect(
    search: SearchFunction,
    *,
    origin: str,
    window: timedelta,
    interval: str = "60m",
    now: datetime | None = None,
    with_errors: bool = False,
) -> RuntimeCollectionResult:
    """Collect one window and describe how complete the aggregation was.

    Transactions are folded per service, type, and name; failures come from
    a filter sub-aggregation and latency percentiles from the
    ``transaction.duration.histogram`` field. Destinations keep a sum and a
    count only, because the destination stream exposes no histogram. An
    ``_other`` key marks an aggregation overflow: the bucket is dropped and
    the service is recorded, since its counts are then lower bounds.
    """
    if interval != "auto" and interval not in INTERVALS:
        raise ElasticApmError(
            f"Intervalle invalide {interval!r} : auto, {', '.join(INTERVALS)}."
        )
    start, end = _window_bounds(window, now)
    if interval == "auto":
        interval = detect_interval(search, start, end)
    collection = collection_id(SOURCE, origin, start, end, interval)
    page_counts: dict[str, int] = {}
    overflow: set[str] = set()
    notes: list[str] = []
    totals: dict[tuple[str, str, str, str | None], list[float]] = {}
    percentiles: dict[tuple[str, str, str, str | None], tuple[float | None, ...]] = {}
    missing_percentiles = 0

    def add(kind: str, service: object, target: object, target_type: object,
            calls: float, failures: float, duration: float | None) -> bool:
        if not isinstance(service, str) or not isinstance(target, str) or not target:
            return False
        if OVERFLOW_KEY in (service, target):
            overflow.add(service)
            return False
        if kind == "transaction":
            target = target.split("?", 1)[0]
        key = (kind, service, target, target_type if isinstance(target_type, str) else None)
        total = totals.setdefault(key, [0.0, 0.0, 0.0])
        total[0] += calls
        total[1] += failures
        total[2] += duration or 0.0
        return True

    for bucket in _composite_buckets(
        search, f"metrics-apm.service_destination.{interval}-*",
        [
            _terms("service", "service.name"),
            _terms("target", "span.destination.service.resource"),
            _terms("target_type", "service.target.type", optional=True),
            _terms("outcome", "event.outcome", optional=True),
        ],
        {
            "calls": {"sum": {"field": "span.destination.service.response_time.count"}},
            "duration_us": {"sum": {"field": "span.destination.service.response_time.sum.us"}},
        },
        start, end, page_counts,
    ):
        key = bucket.get("key") or {}
        assert isinstance(key, dict)
        calls = _number(bucket, "calls")
        add("destination", key.get("service"), key.get("target"), key.get("target_type"),
            calls, calls if key.get("outcome") == "failure" else 0.0, _number(bucket, "duration_us"))

    for bucket in _composite_buckets(
        search, f"metrics-apm.transaction.{interval}-*",
        [
            _terms("service", "service.name"),
            _terms("type", "transaction.type", optional=True),
            _terms("name", "transaction.name"),
        ],
        {
            "failures": {"filter": {"term": {"event.outcome": "failure"}}},
            "duration": {"percentiles": {
                "field": "transaction.duration.histogram", "percents": list(_PERCENTS),
            }},
        },
        start, end, page_counts,
    ):
        key = bucket.get("key") or {}
        assert isinstance(key, dict)
        count = bucket.get("doc_count")
        calls = float(count) if isinstance(count, int | float) else 0.0
        service, name, kind = key.get("service"), key.get("name"), key.get("type")
        if not add("transaction", service, name, kind, calls, _doc_count(bucket.get("failures")), None):
            continue
        values = tuple(_percentile(bucket, percent) for percent in _PERCENTS)
        if any(value is None for value in values):
            missing_percentiles += 1
        assert isinstance(service, str) and isinstance(name, str)
        folded = (
            "transaction", service, name.split("?", 1)[0],
            kind if isinstance(kind, str) else None,
        )
        # Names that only differ by query string fold into one row; the
        # first histogram wins, so a percentile is never averaged.
        percentiles.setdefault(folded, values)

    if missing_percentiles:
        notes.append(
            f"percentiles indisponibles pour {missing_percentiles} transaction(s) : "
            "champ transaction.duration.histogram absent ou vide"
        )
    if overflow:
        notes.append(
            "débordement d'agrégation APM (_other) : compteurs minimaux pour "
            + ", ".join(sorted(overflow))
        )
    collected_at = end
    observations = []
    for (kind, service, target, target_type), (calls, failures, duration) in sorted(
        totals.items(), key=lambda item: tuple(part or "" for part in item[0]),
    ):
        p50, p95, p99 = percentiles.get((kind, service, target, target_type), (None, None, None))
        observations.append(RuntimeObservation(
            id=observation_id(SOURCE, collection, kind, service, target, target_type),
            source=SOURCE, origin=origin, kind=kind, service_name=service,
            target=target, target_type=target_type,
            calls=int(calls), failures=int(failures),
            duration_sum_us=duration if kind == "destination" else None,
            window_start=start, window_end=end, collected_at=collected_at,
            duration_p50_us=p50, duration_p95_us=p95, duration_p99_us=p99,
            collection_id=collection,
        ))
    errors = (
        collect_error_groups(
            search, origin=origin, window=window, now=now, collection=collection,
            page_counts=page_counts,
        )
        if with_errors else []
    )
    return RuntimeCollectionResult(
        collection=RuntimeCollection(
            id=collection, source=SOURCE, origin=origin, window_start=start, window_end=end,
            interval=interval, step=None, collected_at=collected_at,
            page_counts=dict(sorted(page_counts.items())), truncated=False,
            overflow_services=tuple(sorted(overflow)), notes=tuple(notes),
        ),
        observations=observations,
        errors=errors,
    )
