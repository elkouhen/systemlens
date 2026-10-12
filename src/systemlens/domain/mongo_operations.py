"""Conservative source-level effects of supported Spring Data MongoDB calls."""

from typing import Literal


MongoAction = Literal["read", "insert", "update", "delete", "create_or_update", "read_update"]

_ACTIONS: dict[str, MongoAction] = {
    "aggregate": "read",
    "count": "read",
    "exists": "read",
    "existsById": "read",
    "find": "read",
    "findAll": "read",
    "findById": "read",
    "findOne": "read",
    "findAndModify": "read_update",
    "findAndReplace": "read_update",
    "insert": "insert",
    "insertAll": "insert",
    "remove": "delete",
    "delete": "delete",
    "deleteAll": "delete",
    "deleteAllById": "delete",
    "deleteById": "delete",
    "save": "create_or_update",
    "saveAll": "create_or_update",
    "updateFirst": "update",
    "updateMulti": "update",
    "upsert": "create_or_update",
}


def mongo_action(operation: str) -> MongoAction | None:
    """Classify a resolved Mongo API call; builders and unknown calls have no effect."""
    return _ACTIONS.get(operation)


def mongo_facets(operation: str) -> frozenset[str]:
    """Return broad read/write facets for existing architecture consumers."""
    action = mongo_action(operation)
    if action == "read":
        return frozenset({"read"})
    if action == "read_update":
        return frozenset({"read", "write"})
    if action is not None:
        return frozenset({"write"})
    return frozenset()
