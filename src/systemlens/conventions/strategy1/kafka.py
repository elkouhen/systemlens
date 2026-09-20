"""Kafka conventions supplied by the optional Strategy1 pack."""

from systemlens.domain.models import MessageEndpoint


def infer_kafka_endpoints(repo_root, files: list[str] | None = None) -> list[MessageEndpoint]:
    """Infer Strategy1 Kafka endpoints without enabling generic fallbacks."""
    # The scanner entry point remains a compatibility façade for third-party
    # callers.  Keeping the implementation behind this pack prevents the core
    # indexing service from importing repository conventions directly.
    from systemlens.scanner.kafka_conventions import infer_kafka_topic_strategy1_endpoints

    return infer_kafka_topic_strategy1_endpoints(repo_root, files)


def apply_kafka_endpoints(
    endpoints: list[MessageEndpoint], strategy_endpoints: list[MessageEndpoint]
) -> list[MessageEndpoint]:
    """Replace generic Kafka facts at source sites covered by Strategy1."""
    from systemlens.scanner.kafka_conventions import apply_kafka_topic_strategy1

    return apply_kafka_topic_strategy1(endpoints, strategy_endpoints)
