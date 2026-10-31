"""Source-level data operation facts shared by extraction, storage, and flows.

A data operation is an attempt visible in the source: ``insert`` does not
prove a row was created, and a reachable operation does not prove execution.
Each fact keeps its raw API operation, a conservative action, the resolved
resource when the source establishes it, and an explicit reason otherwise.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Literal, Sequence

DataAction = Literal[
    "read", "insert", "update", "delete", "create_or_update", "read_update", "unknown",
]
ACTIONS: tuple[str, ...] = (
    "read", "insert", "update", "delete", "create_or_update", "read_update", "unknown",
)
TECHNOLOGIES: tuple[str, ...] = ("mongodb", "jpa", "jdbc")
# Conflict-control mechanisms recorded only on explicit source evidence.
GUARDS: tuple[str, ...] = (
    "optimistic_version", "pessimistic_lock", "atomic_update", "conditional_update",
)
_READ_ACTIONS = frozenset({"read", "read_update"})
_WRITE_ACTIONS = frozenset({"insert", "update", "delete", "create_or_update", "read_update"})


def compute_data_operation_id(
    module: str, path: str, owner_method: str, line: int, technology: str, operation: str,
) -> str:
    """Build a stable identity from the invocation coordinate, not its text."""
    coordinate = f"{module}|{path}|{owner_method}|{line}|{technology}|{operation}"
    return hashlib.sha256(coordinate.encode("utf-8")).hexdigest()[:16]


def action_facets(action: str) -> tuple[bool, bool]:
    """Return the broad ``(reads, writes)`` facets of an action."""
    return action in _READ_ACTIONS, action in _WRITE_ACTIONS


@dataclass(frozen=True)
class DataOperation:
    """One persisted data operation attempt at a source invocation."""

    id: str
    module: str
    path: str
    owner_method: str
    line: int
    end_line: int
    technology: str
    operation: str
    action: str
    reads: bool
    writes: bool
    resource: str | None
    resource_identity: str | None
    key_expression: str | None = None
    key_property: str | None = None
    key_identity: str | None = None
    confidence: str = "medium"
    unresolved_reason: str | None = None
    receiver: str | None = None
    # Transaction scope evidence (never a guard by itself).
    transaction_id: str | None = None
    propagation: str | None = None
    isolation: str | None = None
    read_only: bool | None = None
    # Conflict-control evidence; ``None`` means no evidence, not "unprotected".
    guard: str | None = None
    guard_scope: str | None = None
    # IDs of read operations whose unmodified result feeds this write.
    value_dependencies: tuple[str, ...] = ()

    @property
    def coverage(self) -> str:
        """Classify how much of the operation the source established."""
        if self.action == "unknown":
            return "unsupported" if self.resource_identity is None else "partial"
        return "supported" if self.resource_identity is not None else "partial"


def crud_action(operation: str) -> str | None:
    """Classify a Spring Data ``CrudRepository``/``JpaRepository`` method name."""
    if operation in {
        "findById", "findAll", "findAllById", "existsById", "count", "getById",
        "getOne", "getReferenceById",
    }:
        return "read"
    if operation in {"save", "saveAll", "saveAndFlush", "saveAllAndFlush"}:
        return "create_or_update"
    if operation in {
        "delete", "deleteById", "deleteAll", "deleteAllById", "deleteAllInBatch",
        "deleteAllByIdInBatch", "deleteInBatch",
    }:
        return "delete"
    return None


JAVA_LITERAL = re.compile(
    r'(?:"(?:[^"\\\r\n]|\\.)*"|\'(?:[^\'\\\r\n]|\\.)\'|'
    r'(?:0[xX][0-9a-fA-F](?:_?[0-9a-fA-F])*|'
    r'0[bB][01](?:_?[01])*|0(?:_?[0-7])*|[1-9](?:_?[0-9])*)(?:[lL])?|'
    r'true|false)'
)


def key_identity_base(resource_identity: str | None, key_property: str | None) -> str | None:
    """Scope a key identity to the predicate property when it is not the ID."""
    if resource_identity is None:
        return None
    return resource_identity if key_property is None else f"{resource_identity}#{key_property}"


def literal_key_identity(
    resource_identity: str | None, key_property: str | None, expression: str | None,
) -> str | None:
    """Identify only an exact Java literal in a known canonical resource."""
    base = key_identity_base(resource_identity, key_property)
    if base is None or expression is None:
        return None
    literal = expression.strip()
    if JAVA_LITERAL.fullmatch(literal) is None:
        return None
    return f"{base}:literal:{literal}"


def sort_data_operations(operations: Sequence[DataOperation]) -> list[DataOperation]:
    return sorted(
        operations,
        key=lambda item: (item.module, item.path, item.line, item.technology, item.operation, item.id),
    )
