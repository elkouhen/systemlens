"""Graphe d'interactions entre services dérivé à la requête à partir
d'endpoints déjà indexés — aucune table de graphe en base (ADR-27)."""

from dataclasses import dataclass, replace
import os
import re
import sys
import time
from collections.abc import Callable
from typing import Literal

from systemlens.domain.models import BROKER_SYSTEMS, ArchitectureRelation, GraphFact, MessageEndpoint
from systemlens.domain.message_types import message_types_conflict


@dataclass(frozen=True)
class RestTargetPolicy:
    """Injected policy for resolving REST target conventions.

    The domain graph only knows how to apply a resolution policy. Repository
    conventions such as Strategy1 are supplied by an outer adapter.
    """

    target_hint: Callable[[MessageEndpoint], str | None] = lambda _endpoint: None
    external_service: Callable[[MessageEndpoint], str | None] = lambda _endpoint: None


_EXPLICIT_EXTERNAL_SERVICE_RE = re.compile(
    r"systemlens-external-microservice:([^\s]+)", re.IGNORECASE
)


def _explicit_external_service(endpoint: MessageEndpoint) -> str | None:
    """Read the stable external-service evidence emitted by the scanner."""
    match = _EXPLICIT_EXTERNAL_SERVICE_RE.search(endpoint.snippet)
    return match.group(1).casefold() if match else None


DEFAULT_REST_TARGET_POLICY = RestTargetPolicy(external_service=_explicit_external_service)


@dataclass(frozen=True)
class GraphEdge:
    kind: str  # Literal["rest", "kafka", "rabbitmq", "jms"]
    from_service: str
    to_service: str
    from_endpoint: MessageEndpoint  # site d'appel (call) ou de production (produce)
    to_endpoint: MessageEndpoint | None  # site exposé (serve) ou de consommation (consume)


@dataclass(frozen=True)
class OutboundCallInConsumer:
    consumer: MessageEndpoint
    call: MessageEndpoint


@dataclass(frozen=True)
class RestTargetResolution:
    """Conservative result of resolving one REST call to a service identity."""

    status: Literal["resolved", "ambiguous", "external", "unresolved"]
    hint: str | None = None
    service: str | None = None


def qualified_rest_resource(service_name: str, resource: str) -> str:
    """Return the stable identity of a REST resource published by a service.

    ``MessageEndpoint.topic`` deliberately stays as ``METHOD /path`` because
    route matching operates on that syntax.  Graphs and dependency views use
    this qualified form instead, so identical routes exposed by two services
    remain distinct facts.
    """
    return f"{service_name}: {resource}"


def graph_edge_rest_resource(edge: GraphEdge) -> str:
    """Retourne la ressource REST d'une arête, ou l'API logique du service.

    Une dépendance issue de ``Rest*Config*`` peut désigner un microservice sans
    qu'une ressource ``serve`` ait été détectée sur celui-ci. Dans ce cas,
    ``to_endpoint`` est absent mais l'arête A → B reste un fait utile.
    """
    resource = edge.to_endpoint.topic if edge.to_endpoint is not None else "API"
    return qualified_rest_resource(edge.to_service, resource)


def _trace(stage: str, **fields: object) -> None:
    if os.environ.get("SYSTEMLENS_TRACE") != "1":
        return
    details = " ".join(f"{name}={value}" for name, value in fields.items())
    print(
        f"SYSTEMLENS_TRACE ts={time.monotonic():.6f} stage={stage} {details}".rstrip(),
        file=sys.stderr,
        flush=True,
    )


def group_endpoints_by_module(
    endpoints: list[MessageEndpoint],
) -> dict[str, list[MessageEndpoint]]:
    """Regroupe des endpoints par module Maven (`endpoint.module`).

    La forme du dictionnaire est la même que celle produite par
    `workspace.load_federation`, et peut donc alimenter directement
    `build_graph`. Un endpoint sans module (`None` — repo non-Maven, ou fichier
    hors arborescence Maven) est ignoré : sans nom stable, il ne peut jamais
    former une arête inter-service fiable.
    """

    grouped: dict[str, list[MessageEndpoint]] = {}
    for endpoint in endpoints:
        if endpoint.module is None:
            continue
        grouped.setdefault(endpoint.module, []).append(endpoint)
    return grouped


def _split_path(path: str) -> list[str]:
    return [segment for segment in path.partition("?")[0].split("/") if segment]


def _matches_wildcard_path(pattern_segments: list[str], concrete_segments: list[str]) -> bool:
    if not pattern_segments or pattern_segments[-1] != "**":
        return False
    prefix = pattern_segments[:-1]
    if len(concrete_segments) <= len(prefix):
        return False
    return all(
        _segment_matches(pattern_seg, concrete_seg)
        for pattern_seg, concrete_seg in zip(prefix, concrete_segments)
    )


def _is_template_segment(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


def _template_constraint(segment: str) -> re.Pattern[str] | None:
    """Return the regular expression of a ``{name:regex}`` segment.

    An unconstrained template, or a constraint that Python cannot compile,
    returns ``None``: the segment then accepts any literal (ADR-77).
    """
    _name, separator, pattern = segment[1:-1].partition(":")
    if not separator or not pattern:
        return None
    try:
        return re.compile(pattern)
    except re.error:
        return None


def _literal_fits_template(literal: str, template: str) -> bool:
    constraint = _template_constraint(template)
    return constraint is None or constraint.fullmatch(literal) is not None


def _segment_matches(call_segment: str, serve_segment: str) -> bool:
    """Whether one call segment may reach one served segment (ADR-77).

    Two templates are compatible: both values are unknown. A literal reaches a
    template only when it satisfies the template's ``{name:regex}`` constraint.
    """
    call_is_template = _is_template_segment(call_segment)
    serve_is_template = _is_template_segment(serve_segment)
    if call_is_template and serve_is_template:
        return True
    if serve_is_template:
        return _literal_fits_template(call_segment, serve_segment)
    if call_is_template:
        return _literal_fits_template(serve_segment, call_segment)
    return call_segment == serve_segment


# An optional ``userinfo@`` precedes the host: ``https://admin@inventory``
# designates ``inventory``, not ``admin``.
_URL_HOST_RE = re.compile(
    r"https?://(?:[^/\s@?#\"'`]*@)?([^/\s:?#\"'`@]+)", re.IGNORECASE
)
_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", re.IGNORECASE)
_IPV4_HOST_RE = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
_CLUSTER_LOCAL_SUFFIXES = (".svc", ".svc.cluster.local")


def service_hint_from_host(host: str) -> str | None:
    """Return the service identity an HTTP host may designate, or ``None``.

    Only a host shaped like an internal service name yields a service name:
    a single DNS label (``inventory-service``) or a Kubernetes cluster-local
    name (``inventory.shop.svc.cluster.local`` yields ``inventory``). An IP
    address or a malformed host yields ``None``. Any other multi-label host
    (``auth.okta.com``) is returned whole, so it can only match an exact
    configured service alias and otherwise stays unresolved; its first DNS
    label never names a service.
    """
    normalized = host.strip().casefold().rstrip(".")
    if not normalized or _IPV4_HOST_RE.fullmatch(normalized):
        return None
    labels = normalized.split(".")
    if not all(_DNS_LABEL_RE.fullmatch(label) for label in labels):
        return None
    if len(labels) == 1:
        return normalized
    if normalized.endswith(_CLUSTER_LOCAL_SUFFIXES):
        return labels[0]
    return normalized


def url_service_hint(url: str) -> str | None:
    """Service name of the host of a URL that starts with ``http(s)://``.

    Unlike :func:`service_hint_from_host`, a multi-label public host yields
    ``None``: the scanner stamps only names that can designate an indexed
    service without an alias table.
    """
    match = _URL_HOST_RE.match(url.strip())
    if match is None:
        return None
    hint = service_hint_from_host(match.group(1))
    return hint if hint is not None and "." not in hint else None


_LOAD_BALANCED_URI_RE = re.compile(r"lb://([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)\b", re.IGNORECASE)
_CONFIGURED_API_DOMAIN_RE = re.compile(r"\bsystemlens-api-domain:([a-z0-9][a-z0-9-]*)\b", re.IGNORECASE)
_OPENAPI_CONTRACT_RE = re.compile(r"systemlens-openapi-contract:([^\s]+)", re.IGNORECASE)


def configured_api_client_domain(endpoint: MessageEndpoint) -> str | None:
    """Domaine ``systemlens-api-domain:`` tamponné sur un endpoint, normalisé en minuscules.

    Preuve injectée par le scanner pour les clients créés via
    ``createInternalClientApi`` : la route HTTP n'est pas au site d'appel, mais
    le domaine logique y figure et correspond, par convention, au nom du
    microservice hôte. Consommé par la fédération et par le graphe de
    dépendances pour relier l'appelant au microservice hôte.
    """
    match = _CONFIGURED_API_DOMAIN_RE.search(endpoint.snippet)
    return match.group(1).lower() if match is not None else None


def openapi_contract_evidence(endpoint: MessageEndpoint) -> str | None:
    """Return an exact indexed OpenAPI contract path carried by an endpoint."""
    match = _OPENAPI_CONTRACT_RE.search(endpoint.snippet)
    return match.group(1) if match is not None else None


def external_microservice_name(
    endpoint: MessageEndpoint,
    *,
    rest_policy: RestTargetPolicy = DEFAULT_REST_TARGET_POLICY,
) -> str | None:
    """Return the external service explicitly identified by the injected policy."""
    return rest_policy.external_service(endpoint)


def external_microservice_names(edges: list[GraphEdge]) -> set[str]:
    """Return graph targets that Strategy1 explicitly marks as external."""
    return {
        edge.to_service
        for edge in edges
        if edge.kind == "rest" and external_microservice_name(edge.from_endpoint) is not None
    }


def _rest_target_service_hint(
    call: MessageEndpoint,
    *,
    rest_policy: RestTargetPolicy = DEFAULT_REST_TARGET_POLICY,
) -> str | None:
    if hint := rest_policy.target_hint(call):
        return hint
    host_match = _URL_HOST_RE.search(call.snippet)
    load_balanced_match = _LOAD_BALANCED_URI_RE.search(call.snippet)
    if load_balanced_match is not None and (
        host_match is None or load_balanced_match.start() < host_match.start()
    ):
        # The first target URI of the evidence wins; an ``http://`` later in
        # the snippet does not override an earlier ``lb://`` (finding H2).
        return load_balanced_match.group(1).lower()
    if host_match is not None:
        # The explicit URL is the target evidence, even when its host is
        # external or not service-shaped: never fall back to another marker.
        return service_hint_from_host(host_match.group(1))
    return configured_api_client_domain(call)


# Clients whose call site declares its own route. The scanner stamps their
# resolved HTTP host as ``systemlens-api-domain:`` evidence, but an unresolved
# route never makes them configured clients (finding H1).
_ROUTE_DECLARING_HTTP_CLIENTS = frozenset(
    {"resttemplate", "webclient", "restclient", "http-interface", "feign"}
)


def _is_configured_api_client_call(call: MessageEndpoint) -> bool:
    """Le site est un client créé par `createInternalClientApi`.

    Son code d'appel ne porte pas nécessairement le chemin HTTP. Le domaine
    injecté par le scanner permet alors de le résoudre contre les ressources
    exposées par le microservice hôte pendant la fédération. Un appel
    RestTemplate, WebClient, RestClient, HTTP interface ou Feign porte le même
    marqueur pour son hôte, mais déclare sa propre route : il n'est jamais
    projeté sur les routes de l'hôte.
    """
    return (
        call.framework not in _ROUTE_DECLARING_HTTP_CLIENTS
        and configured_api_client_domain(call) is not None
    )


def _http_methods_match(call_topic: str, serve_topic: str) -> bool:
    call_method, _, _ = call_topic.partition(" ")
    serve_method, _, _ = serve_topic.partition(" ")
    return call_method == serve_method or call_method == "ANY" or serve_method == "ANY"


def _resolved_configured_api_call(
    call: MessageEndpoint, serve: MessageEndpoint
) -> MessageEndpoint | None:
    """Projette un client d'API typé sur une ressource de son hôte.

    Cette résolution est délibérément réservée aux appels portant l'évidence
    `systemlens-api-domain:` : un appel HTTP dynamique ordinaire ne doit jamais être
    relié aveuglément à toutes les routes d'un service.
    """
    if (
        not call.topic_dynamic
        or not _is_configured_api_client_call(call)
        or not _http_methods_match(call.topic, serve.topic)
    ):
        return None
    return replace(call, topic=serve.topic, topic_dynamic=False)


def _normalized_service_alias(value: str) -> str:
    """Normalize one explicit service identity for exact matching only.

    A host from ``http://`` or ``lb://`` has already been parsed by
    :func:`_rest_target_service_hint`; this helper only normalizes case and a
    harmless trailing DNS dot.  It deliberately does not accept prefixes,
    suffixes or substrings: route similarity is not evidence that two services
    are the same dependency target.
    """
    return value.strip().casefold().rstrip(".")


def _services_matching_hint(
    service_names: list[str], hint: str | None,
    service_aliases: dict[str, tuple[str, ...]] | None = None,
) -> list[str]:
    """Return the uniquely evidenced candidates for an explicit REST target.

    Calls without a target hint are intentionally unresolved.  If a workspace
    contains two names that normalize to the same explicit alias, the target is
    ambiguous and is likewise left unresolved rather than choosing one.
    """
    if hint is None:
        return []
    normalized_hint = _normalized_service_alias(hint)
    matches = [
        service_name
        for service_name in service_names
        if _normalized_service_alias(service_name) == normalized_hint
    ]
    matches.extend(
        service_name
        for service_name, aliases in (service_aliases or {}).items()
        if service_name in service_names
        if any(_normalized_service_alias(alias) == normalized_hint for alias in aliases)
    )
    return sorted(set(matches))


def resolve_rest_target_service(
    call: MessageEndpoint,
    service_names: list[str],
    *,
    rest_policy: RestTargetPolicy = DEFAULT_REST_TARGET_POLICY,
    service_aliases: dict[str, tuple[str, ...]] | None = None,
) -> RestTargetResolution:
    """Resolve an explicit REST target without using route similarity.

    Only an exact normalized target alias may resolve an indexed internal
    service. A call explicitly marked as an external microservice is retained
    as such; a missing target or several equivalent aliases stays unresolved.
    """
    if external_service := external_microservice_name(call, rest_policy=rest_policy):
        return RestTargetResolution("external", service=external_service)
    hint = _rest_target_service_hint(call, rest_policy=rest_policy)
    matches = _services_matching_hint(service_names, hint, service_aliases)
    if len(matches) == 1:
        return RestTargetResolution("resolved", hint=hint, service=matches[0])
    if len(matches) > 1:
        return RestTargetResolution("ambiguous", hint=hint)
    return RestTargetResolution("unresolved", hint=hint)


def paths_match(call_topic: str, serve_topic: str) -> bool:
    """Best-effort : même méthode HTTP, et les segments de chemin du call
    (littéral ↔ template `{param}`) préfixent ou couvrent ceux de la route
    exposée. Un call sans aucun littéral exploitable (`<dynamic>`) ne matche
    jamais — mieux vaut une arête absente qu'une fausse arête."""

    call_method, _, call_path = call_topic.partition(" ")
    serve_method, _, serve_path = serve_topic.partition(" ")
    if (
        call_path == "<dynamic>"
        or (call_method != serve_method and call_method != "ANY" and serve_method != "ANY")
    ):
        return False

    call_segments = _split_path(call_path)
    serve_segments = _split_path(serve_path)
    if not call_segments:
        return False
    if _matches_wildcard_path(call_segments, serve_segments):
        return True
    if _matches_wildcard_path(serve_segments, call_segments):
        return True
    if len(call_segments) == len(serve_segments):
        return all(
            _segment_matches(call_seg, serve_seg)
            for call_seg, serve_seg in zip(call_segments, serve_segments)
        )
    return False


def _route_rank(call_segments: list[str], serve_segments: list[str]) -> tuple[int, int, int]:
    """Specificity rank of a served route that matches a call (ADR-77).

    Lower is better: a route that needs no guess about a call template comes
    first, then a route without ``**``, then the route binding the fewest call
    literals to templates (a literal segment wins over a template segment).
    """
    if serve_segments and serve_segments[-1] == "**":
        return (0, 1, len(serve_segments))
    guessed = sum(
        1
        for call_seg, serve_seg in zip(call_segments, serve_segments)
        if _is_template_segment(call_seg) and not _is_template_segment(serve_seg)
    )
    bound = sum(
        1
        for call_seg, serve_seg in zip(call_segments, serve_segments)
        if not _is_template_segment(call_seg) and _is_template_segment(serve_seg)
    )
    return (1 if guessed else 0, 0, bound)


def select_matching_routes(
    call_topic: str, serves: list[MessageEndpoint],
) -> list[MessageEndpoint]:
    """Served routes of one target service that a call reaches (ADR-77).

    Spring MVC's rule applies among the routes :func:`paths_match` accepts:
    the most specific route wins, so ``GET /items/count`` reaches the literal
    route and not its sibling ``/items/{id}``. A templated call keeps the route
    whose templates align with its own; when every candidate would need a
    guess about the call's unknown value, no single best match exists and all
    candidates are kept. A call ending in ``**`` (a gateway proxy) keeps every
    compatible route. Among routes of equal path rank, an explicit HTTP method
    beats ``ANY``; a call with ``ANY`` is ranked per served method.
    """
    candidates = [serve for serve in serves if paths_match(call_topic, serve.topic)]
    call_method, _, call_path = call_topic.partition(" ")
    call_segments = _split_path(call_path)
    if len(candidates) < 2 or (call_segments and call_segments[-1] == "**"):
        return candidates
    groups: dict[str, list[tuple[tuple[int, int, int, int], MessageEndpoint]]] = {}
    for serve in candidates:
        serve_method, _, serve_path = serve.topic.partition(" ")
        rank = (
            *_route_rank(call_segments, _split_path(serve_path)),
            1 if serve_method == "ANY" and call_method != "ANY" else 0,
        )
        group = serve_method if call_method == "ANY" else ""
        groups.setdefault(group, []).append((rank, serve))
    selected: set[str] = set()
    for ranked in groups.values():
        best = min(rank for rank, _serve in ranked)
        if best[0]:
            # Every candidate guesses the value of a call template.
            selected.update(serve.id for _rank, serve in ranked)
            continue
        selected.update(serve.id for rank, serve in ranked if rank == best)
    return [serve for serve in candidates if serve.id in selected]


def build_graph(
    endpoints_by_service: dict[str, list[MessageEndpoint]], *,
    rest_policy: RestTargetPolicy = DEFAULT_REST_TARGET_POLICY,
    service_aliases: dict[str, tuple[str, ...]] | None = None,
) -> list[GraphEdge]:
    """Construit les arêtes REST et Kafka entre services distincts.

    Une arête REST n'est créée que si le site d'appel désigne exactement un
    unique service interne (URL de service, ``lb://`` ou convention Strategy1).
    La méthode et la route ne font alors que confirmer la compatibilité au sein
    de ce service. Les conventions de résolution sont fournies par
    ``rest_policy``. Strategy1 is supplied by an outer adapter and is not a
    domain flag.
    Pas d'auto-arête : un service qui s'appelle lui-même n'entre pas dans le
    graphe inter-services.

    Les entrées Kafka provenant d'un manifeste sont autoritatives
    pour le service décrit : elles remplacent les détections de code de ce
    service, qui peuvent être incomplètes ou produire des faux positifs. Les
    services absents du manifeste conservent leurs endpoints détectés."""

    all_endpoints = [
        (service, endpoint)
        for service, endpoints in endpoints_by_service.items()
        for endpoint in endpoints
    ]
    calls = [(s, e) for s, e in all_endpoints if e.system == "rest" and e.role == "call"]
    serves_by_service: dict[str, list[MessageEndpoint]] = {}
    for service, endpoint in all_endpoints:
        if endpoint.system == "rest" and endpoint.role == "serve":
            serves_by_service.setdefault(service, []).append(endpoint)
    serves_by_contract: dict[str, list[tuple[str, MessageEndpoint]]] = {}
    for service, endpoint in all_endpoints:
        if endpoint.system == "rest" and endpoint.role == "serve" and endpoint.framework == "openapi":
            # A Strategy1 ``.rest`` declaration serves the contract named by
            # its evidence, not the declaration file itself.
            declared = openapi_contract_evidence(endpoint)
            for served_contract in {endpoint.path, *([declared] if declared else [])}:
                serves_by_contract.setdefault(served_contract, []).append((service, endpoint))
    manifest_services = {
        service
        for service, endpoint in all_endpoints
        if endpoint.system == "kafka" and endpoint.source == "manifest"
    }
    kafka_endpoints = [
        (service, endpoint)
        for service, endpoint in all_endpoints
        if endpoint.system == "kafka"
        and (service not in manifest_services or endpoint.source == "manifest")
    ]
    produces = [(s, e) for s, e in kafka_endpoints if e.role == "produce"]
    consumes_by_topic: dict[str, list[tuple[str, MessageEndpoint]]] = {}
    for service, endpoint in kafka_endpoints:
        if endpoint.role == "consume" and not endpoint.topic_dynamic:
            consumes_by_topic.setdefault(endpoint.topic, []).append((service, endpoint))

    edges: list[GraphEdge] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    gateway_proxy_targets: set[tuple[str, str]] = set()
    service_names = sorted(endpoints_by_service)
    for call_service, call in calls:
        contract = openapi_contract_evidence(call)
        if contract is not None:
            contract_targets = [
                (service, serve)
                for service, serve in serves_by_contract.get(contract, ())
                if service != call_service and paths_match(call.topic, serve.topic)
            ]
            if len({service for service, _serve in contract_targets}) == 1:
                target_service = contract_targets[0][0]
                serve = next(
                    serve for service, serve in contract_targets if service == target_service
                )
                key = ("rest", call_service, target_service, call.id, serve.id)
                if key not in seen:
                    seen.add(key)
                    edges.append(GraphEdge("rest", call_service, target_service, call, serve))
                continue
        resolution = resolve_rest_target_service(
            call, service_names, rest_policy=rest_policy, service_aliases=service_aliases
        )
        if resolution.status != "resolved" or resolution.service is None:
            continue
        target_service = resolution.service
        if call_service == target_service:
            continue
        target_serves = serves_by_service.get(target_service, [])
        selected_ids = {
            serve.id for serve in select_matching_routes(call.topic, target_serves)
        }
        for serve in target_serves:
            serve_service = target_service
            effective_call = call
            if serve.id not in selected_ids:
                if paths_match(call.topic, serve.topic):
                    # Compatible, but a more specific route wins (ADR-77).
                    continue
                resolved_call = _resolved_configured_api_call(call, serve)
                if resolved_call is None:
                    continue
                effective_call = resolved_call
                _trace(
                    "rest_client.graph.resource_resolved",
                    caller=call_service,
                    host=serve_service,
                    source_topic=call.topic,
                    resource=serve.topic,
                )
            if effective_call is not None:
                proxy_target = (call.id, serve_service)
                if call.framework == "spring-cloud-gateway" and call.topic.startswith("ANY "):
                    if proxy_target in gateway_proxy_targets:
                        continue
                    gateway_proxy_targets.add(proxy_target)
                key = ("rest", call_service, serve_service, call.id, serve.id)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(GraphEdge("rest", call_service, serve_service, effective_call, serve))

    # Une configuration Rest*Config* établit une dépendance A → B même si B
    # n'expose aucune ressource REST détectée. Une arête spécifique à une
    # ressource, déjà ajoutée ci-dessus, reste plus précise et suffit alors.
    resolved_configured_calls = {
        (edge.from_endpoint.id, edge.to_service)
        for edge in edges
        if edge.kind == "rest" and configured_api_client_domain(edge.from_endpoint) is not None
    }
    for call_service, call in calls:
        domain = configured_api_client_domain(call)
        if domain is None or domain not in endpoints_by_service or call_service == domain:
            continue
        if (call.id, domain) in resolved_configured_calls:
            continue
        key = ("rest", call_service, domain, "configured-api", "")
        if key in seen:
            continue
        seen.add(key)
        edges.append(GraphEdge("rest", call_service, domain, call, None))

    # `getRest().get("partner")` names a microservice
    # outside the indexed workspace.  Keep it as a microservice relation (not
    # an untyped external API) so the topology can label the target external.
    for call_service, call in calls:
        external_service = external_microservice_name(call, rest_policy=rest_policy)
        if external_service is None or call_service == external_service:
            continue
        key = ("rest", call_service, external_service, "configured-external", "")
        if key in seen:
            continue
        seen.add(key)
        edges.append(GraphEdge("rest", call_service, external_service, call, None))

    # A concrete shared topic establishes a producer/consumer relation. Missing
    # payload types lower confidence but do not prevent the relation. Two known
    # and different types remain incompatible because the evidence conflicts.
    for produce_service, produce in produces:
        if produce.topic_dynamic:
            continue
        for consume_service, consume in consumes_by_topic.get(produce.topic, ()):
            if produce_service == consume_service:
                continue
            if not message_types_conflict(produce.message_type, consume.message_type):
                key = ("kafka", produce_service, consume_service, produce.id, consume.id)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(GraphEdge("kafka", produce_service, consume_service, produce, consume))

    # A RabbitMQ or JMS channel pairs a publication and a consumer of the
    # same system and the same concrete name (PDR-16). Neither side has a
    # payload type, and a dynamic name never pairs.
    broker_consumers: dict[tuple[str, str], list[tuple[str, MessageEndpoint]]] = {}
    for service, endpoint in all_endpoints:
        if endpoint.system in BROKER_SYSTEMS and endpoint.role == "consume" and not endpoint.topic_dynamic:
            broker_consumers.setdefault((endpoint.system, endpoint.topic), []).append((service, endpoint))
    for produce_service, produce in all_endpoints:
        if produce.system not in BROKER_SYSTEMS or produce.role != "produce" or produce.topic_dynamic:
            continue
        for consume_service, consume in broker_consumers.get((produce.system, produce.topic), ()):
            if produce_service == consume_service:
                continue
            key = (produce.system, produce_service, consume_service, produce.id, consume.id)
            if key in seen:
                continue
            seen.add(key)
            edges.append(GraphEdge(produce.system, produce_service, consume_service, produce, consume))

    return edges


def graph_edges_from_facts(
    facts: list[GraphFact],
    endpoints_by_service: dict[str, list[MessageEndpoint]],
) -> list[GraphEdge]:
    """Project accepted AI messaging facts onto indexed endpoint pairs.

    Facts remain independent persisted evidence. This adapter only creates a
    transient topology view for flow reconstruction; it never writes facts
    into source-derived tables. Only confirmed or proposed event facts with a
    concrete channel are eligible. Ambiguous and unresolved facts stay out of
    the executable flow graph.
    """
    service_names = {
        service
        for service, endpoints in endpoints_by_service.items()
        if endpoints
    }
    published: dict[str, set[str]] = {}
    consumed: dict[str, set[str]] = {}
    direct: list[tuple[str, str, str]] = []

    def service_name(kind: str | None, name: str | None) -> str | None:
        if name is None or kind not in {"service", "microservice"}:
            return None
        return name if name in service_names else None

    def channel(fact: GraphFact) -> str | None:
        metadata = fact.metadata or {}
        value = metadata.get("channel") or metadata.get("topic")
        if value is None:
            if fact.target_kind in {"topic", "message_channel"}:
                value = fact.target_name
            elif fact.source_kind in {"topic", "message_channel"}:
                value = fact.source_name
        return value.strip() if isinstance(value, str) and value.strip() else None

    for fact in facts:
        if (
            fact.fact_type != "edge"
            or fact.status not in {"confirmed", "proposed"}
            or fact.kind != "event"
        ):
            continue
        source_service = service_name(fact.source_kind, fact.source_name)
        target_service = service_name(fact.target_kind, fact.target_name)
        fact_channel = channel(fact)
        if fact_channel is None:
            continue
        if source_service and target_service:
            direct.append((source_service, target_service, fact_channel))
            continue
        source_channel = fact.source_name if fact.source_kind in {"topic", "message_channel"} else None
        target_channel = fact.target_name if fact.target_kind in {"topic", "message_channel"} else None
        if source_service and target_channel:
            published.setdefault(target_channel, set()).add(source_service)
        elif source_channel and target_service:
            consumed.setdefault(source_channel, set()).add(target_service)

    for fact_channel, source_services in published.items():
        for source_service in source_services:
            for target_service in consumed.get(fact_channel, set()):
                direct.append((source_service, target_service, fact_channel))

    kafka_endpoint_index: dict[tuple[str, str, str], list[MessageEndpoint]] = {}
    for service, service_endpoints in endpoints_by_service.items():
        for endpoint in service_endpoints:
            if endpoint.system == "kafka" and endpoint.role in {"produce", "consume"}:
                kafka_endpoint_index.setdefault(
                    (service, endpoint.topic, endpoint.role), []
                ).append(endpoint)
    edges: list[GraphEdge] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for source_service, target_service, fact_channel in direct:
        producers = kafka_endpoint_index.get((source_service, fact_channel, "produce"), ())
        consumers = kafka_endpoint_index.get((target_service, fact_channel, "consume"), ())
        for producer in producers:
            for consumer in consumers:
                if message_types_conflict(producer.message_type, consumer.message_type):
                    continue
                key = ("kafka", source_service, target_service, producer.id, consumer.id)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(GraphEdge(
                    "kafka", source_service, target_service, producer, consumer
                ))
    return edges


def graph_edges_from_relations(
    relations: list[ArchitectureRelation] | tuple[ArchitectureRelation, ...],
    endpoints_by_service: dict[str, list[MessageEndpoint]],
) -> list[GraphEdge]:
    """Adapt persisted topology relations to the graph rendering view.

    ``architecture_relations`` decides which services are connected. Endpoints
    are consulted only to attach route/topic labels and source evidence to an
    already materialized relation; this function never resolves a target from
    endpoint similarity. It is deliberately separate from :func:`build_graph`,
    which remains the index-time materializer and a fixture convenience.
    """
    endpoints = [
        endpoint
        for service_endpoints in endpoints_by_service.values()
        for endpoint in service_endpoints
    ]
    source_index: dict[tuple[str, str | None, int | None, str], list[MessageEndpoint]] = {}
    consumers_index: dict[tuple[str, str, str], list[MessageEndpoint]] = {}
    serves_index: dict[str, list[MessageEndpoint]] = {}
    for endpoint in endpoints:
        if endpoint.module is None:
            continue
        source_index.setdefault(
            (endpoint.module, endpoint.path, endpoint.start_line, endpoint.role), []
        ).append(endpoint)
        if endpoint.system in {"kafka", *BROKER_SYSTEMS} and endpoint.role == "consume":
            consumers_index.setdefault(
                (endpoint.system, endpoint.module, endpoint.topic), [],
            ).append(endpoint)
        elif endpoint.system == "rest" and endpoint.role == "serve":
            serves_index.setdefault(endpoint.module, []).append(endpoint)

    def target_endpoints(
        kind: str, target_service: str, source_endpoint: MessageEndpoint,
    ) -> list[MessageEndpoint]:
        """Served sites of the asserted target that the source site reaches.

        The relation already names the target service. Inside it, the same
        route and payload rules as :func:`build_graph` select the served
        site, so an export sees the site the index reconciled against.
        """
        if kind == "kafka":
            consumers = consumers_index.get(("kafka", target_service, source_endpoint.topic), [])
            return [
                consumer for consumer in consumers
                if not message_types_conflict(source_endpoint.message_type, consumer.message_type)
            ]
        if kind in BROKER_SYSTEMS:
            return consumers_index.get((kind, target_service, source_endpoint.topic), [])
        return select_matching_routes(
            source_endpoint.topic, serves_index.get(target_service, [])
        )

    edges: list[GraphEdge] = []
    seen: set[tuple[str, str, str, str, str | None]] = set()
    for relation in sorted(relations, key=lambda item: (item.id, item.source_name, item.target_name)):
        if (
            relation.source_kind != "microservice"
            or relation.target_kind != "microservice"
            or relation.relation not in {"calls_service", "publishes_to"}
        ):
            continue
        role = "call" if relation.relation == "calls_service" else "produce"
        candidates = source_index.get(
            (relation.source_name, relation.path, relation.start_line, role), []
        )
        if not candidates:
            # A relation without its endpoint evidence is incomplete. Do not
            # fabricate a visual edge; coverage still exposes the persisted
            # relation and the next index will restore an atomic snapshot.
            continue
        for source_endpoint in candidates:
            # A publication's relation is a Kafka or a broker channel (PDR-16).
            kind = (
                "rest" if role == "call"
                else source_endpoint.system if source_endpoint.system in BROKER_SYSTEMS
                else "kafka"
            )
            targets: list[MessageEndpoint | None] = list(
                target_endpoints(kind, relation.target_name, source_endpoint)
            ) or [None]
            for target_endpoint in targets:
                key = (
                    kind, relation.source_name, relation.target_name, source_endpoint.id,
                    target_endpoint.id if target_endpoint is not None else None,
                )
                if key in seen:
                    continue
                seen.add(key)
                edges.append(
                    GraphEdge(
                        kind,
                        relation.source_name,
                        relation.target_name,
                        source_endpoint,
                        target_endpoint,
                    )
                )
    return edges


def find_outbound_calls_in_consumers(
    endpoints: list[MessageEndpoint],
) -> list[OutboundCallInConsumer]:
    """Un appel REST (`call`) dont le site tombe dans la plage de lignes
    d'un handler de consommation Kafka (`consume`) du même fichier. `endpoints`
    doit venir d'un seul service : fichier et lignes ne sont comparables qu'au
    sein d'un même repo."""

    consumers = [e for e in endpoints if e.system == "kafka" and e.role == "consume"]
    calls = [e for e in endpoints if e.system == "rest" and e.role == "call"]
    calls_by_path: dict[str, list[MessageEndpoint]] = {}
    for call in calls:
        calls_by_path.setdefault(call.path, []).append(call)

    results: list[OutboundCallInConsumer] = []
    for consumer in consumers:
        for call in calls_by_path.get(consumer.path, ()):
            if consumer.start_line <= call.start_line <= consumer.end_line:
                results.append(OutboundCallInConsumer(consumer, call))
    return results
