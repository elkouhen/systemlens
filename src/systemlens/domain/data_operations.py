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


# --- Classifiers shared by the Java extractors ----------------------------

_ID_CRUD = frozenset({
    "findById", "existsById", "deleteById", "getById", "getOne", "getReferenceById",
})
_DERIVED_PREFIX = re.compile(
    r"^(?P<prefix>find|read|get|query|search|stream|count|exists|delete|remove)"
    r"(?P<subject>[A-Z]\w*?)??(?:By(?P<predicate>[A-Z]\w*))?$"
)
_PREDICATE_SPLIT = re.compile(r"(?<=[a-z0-9])(?:And|Or)(?=[A-Z])")
_ORDER_BY = re.compile(r"OrderBy[A-Z]\w*$")
_EQUALITY_SUFFIXES = ("Is", "Equals")
_NON_EQUALITY_SUFFIXES = (
    "IgnoreCase", "Between", "LessThan", "LessThanEqual", "GreaterThan",
    "GreaterThanEqual", "After", "Before", "IsNull", "Null", "IsNotNull", "NotNull",
    "Like", "NotLike", "StartingWith", "StartsWith", "EndingWith", "EndsWith",
    "Containing", "Contains", "In", "NotIn", "True", "False", "Not", "Exists",
    "Near", "Within", "Regex", "Matches", "IsEmpty", "Empty", "IsNotEmpty", "NotEmpty",
)


@dataclass(frozen=True)
class RepositoryMethodClassification:
    """How a Spring Data repository method name classifies."""

    action: str
    # ``keyed`` means the call targets one record through its only argument.
    keyed: bool = False
    # Predicate property for a derived equality; ``None`` means the entity ID.
    key_property: str | None = None
    reason: str | None = None


def _lower_first(name: str) -> str:
    return name[:1].lower() + name[1:]


def classify_repository_method(name: str) -> RepositoryMethodClassification:
    """Classify a CRUD or derived-query method by the Spring Data grammar.

    Reads are ``find``, ``read``, ``get``, ``query``, ``search``, ``stream``,
    ``count`` and ``exists``; ``delete`` and ``remove`` are deletes. A key is
    recorded only for a single ``By<Property>`` equality. Any other name stays
    unknown with a reason: Spring Data would reject it or route it to a
    custom implementation the source does not show.
    """
    crud = crud_action(name)
    if crud is not None:
        return RepositoryMethodClassification(crud, keyed=name in _ID_CRUD)
    match = _DERIVED_PREFIX.match(name)
    if match is None:
        return RepositoryMethodClassification(
            "unknown", reason="method name outside the Spring Data derived-query grammar",
        )
    action = "delete" if match.group("prefix") in {"delete", "remove"} else "read"
    predicate = match.group("predicate")
    if predicate is None:
        return RepositoryMethodClassification(action)
    predicate = _ORDER_BY.sub("", predicate)
    parts = _PREDICATE_SPLIT.split(predicate)
    if len(parts) != 1 or not parts[0]:
        return RepositoryMethodClassification(action)
    part = parts[0]
    for suffix in _EQUALITY_SUFFIXES:
        if part.endswith(suffix) and len(part) > len(suffix):
            part = part[: -len(suffix)]
            break
    if any(part.endswith(suffix) and len(part) > len(suffix) for suffix in _NON_EQUALITY_SUFFIXES):
        return RepositoryMethodClassification(action)
    key_property = _lower_first(part)
    return RepositoryMethodClassification(
        action, keyed=True, key_property=None if key_property == "id" else key_property,
    )


@dataclass(frozen=True)
class QueryClassification:
    """Classification of a literal JPQL or SQL statement."""

    action: str
    # JPQL: the root entity name as written. SQL: the single table name.
    target: str | None = None
    key_column: str | None = None
    # Position of the key placeholder among the ``?`` placeholders.
    key_placeholder: int | None = None
    where_columns: tuple[str, ...] = ()
    reason: str | None = None


_SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_IDENTIFIER = r"[`\"\[]?[A-Za-z_][\w.$]*[`\"\]]?"


def _clean_identifier(name: str) -> str:
    return name.strip("`\"[]")


def classify_jpql(query: str, *, modifying: bool) -> QueryClassification:
    """Classify a literal JPQL string; mutations require ``@Modifying``."""
    text = " ".join(query.split())
    lowered = text.lower()
    keyword = lowered.split(" ", 1)[0] if lowered else ""
    if keyword in {"select", "from"}:
        match = re.search(rf"\bfrom\s+({_IDENTIFIER})", lowered)
        entity = _clean_identifier(text[match.start(1):match.end(1)]) if match else None
        return QueryClassification(
            "read", entity, reason=None if entity else "entity of the JPQL query not established",
        )
    if keyword in {"update", "delete", "insert"}:
        if not modifying:
            return QueryClassification(
                "unknown", reason=f"{keyword} query without @Modifying",
            )
        pattern = {
            "update": rf"\bupdate\s+({_IDENTIFIER})",
            "delete": rf"\bdelete\s+(?:from\s+)?({_IDENTIFIER})",
            "insert": rf"\binsert\s+into\s+({_IDENTIFIER})",
        }[keyword]
        match = re.search(pattern, lowered)
        entity = _clean_identifier(text[match.start(1):match.end(1)]) if match else None
        return QueryClassification(
            keyword, entity, reason=None if entity else "entity of the JPQL query not established",
        )
    return QueryClassification("unknown", reason="unsupported JPQL statement")


def classify_sql(sql: str) -> QueryClassification:
    """Classify a literal single-statement SQL string conservatively.

    The table is recorded only when the statement names exactly one table:
    joins, subqueries, several ``FROM`` tables, stored procedures and
    multiple statements keep an unknown resource with a reason.
    """
    text = " ".join(_SQL_COMMENT.sub(" ", sql).split()).strip().rstrip(";").strip()
    if ";" in text:
        return QueryClassification("unknown", reason="multiple SQL statements")
    lowered = text.lower()
    keyword = lowered.split(" ", 1)[0] if lowered else ""
    if keyword in {"call", "exec", "execute", "begin", "declare"} or lowered.startswith("{call"):
        return QueryClassification("unknown", reason="stored procedure")
    actions = {
        "select": "read", "with": "read", "insert": "insert", "update": "update",
        "delete": "delete", "merge": "create_or_update", "upsert": "create_or_update",
    }
    action = actions.get(keyword)
    if action is None:
        return QueryClassification("unknown", reason="unsupported SQL statement")
    if re.search(r"\bjoin\b", lowered) or re.search(r"\(\s*select\b", lowered) or keyword == "with":
        return QueryClassification(action, reason="join or subquery: several tables involved")
    pattern = {
        "select": rf"\bfrom\s+({_IDENTIFIER})(\s*,\s*{_IDENTIFIER})?",
        "insert": rf"\binsert\s+(?:into\s+)?({_IDENTIFIER})",
        "update": rf"\bupdate\s+({_IDENTIFIER})",
        "delete": rf"\bdelete\s+(?:from\s+)?({_IDENTIFIER})",
        "merge": rf"\bmerge\s+into\s+({_IDENTIFIER})",
        "upsert": rf"\bupsert\s+into\s+({_IDENTIFIER})",
    }[keyword]
    match = re.search(pattern, lowered)
    if match is None:
        return QueryClassification(action, reason="table of the SQL statement not established")
    if keyword == "select" and match.lastindex and match.lastindex >= 2 and match.group(2):
        return QueryClassification(action, reason="several tables in FROM")
    table = _clean_identifier(text[match.start(1):match.end(1)])
    where = re.search(
        r"\bwhere\s+(.*?)(?:\s+(?:order\s+by|group\s+by|limit|returning|having)\b|$)", lowered,
    )
    where_columns: tuple[str, ...] = ()
    key_column: str | None = None
    key_placeholder: int | None = None
    if where:
        clause = where.group(1)
        where_columns = tuple(
            _clean_identifier(column).rsplit(".", 1)[-1]
            for column in re.findall(rf"({_IDENTIFIER})\s*(?:=|<>|!=|<|>|<=|>=|\blike\b|\bin\b)", clause)
        )
        single = re.fullmatch(rf"({_IDENTIFIER})\s*=\s*(\?|:\w+)", clause.strip())
        if single and not re.search(r"\b(and|or)\b", clause):
            key_column = _clean_identifier(single.group(1)).rsplit(".", 1)[-1]
            if single.group(2) == "?":
                key_placeholder = lowered[:where.start(1)].count("?")
    return QueryClassification(
        action, table, key_column=key_column, key_placeholder=key_placeholder,
        where_columns=where_columns,
    )


ENTITY_MANAGER_ACTIONS: dict[str, str] = {
    "persist": "insert",
    "merge": "create_or_update",
    "remove": "delete",
    "find": "read",
    "getReference": "read",
    "refresh": "read",
}
