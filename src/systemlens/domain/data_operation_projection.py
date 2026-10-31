"""Project module inventory facts of older indexes into data operations."""

from pathlib import Path
from typing import Sequence

from systemlens.domain.data_operations import (
    DataOperation,
    action_facets,
    compute_data_operation_id,
    crud_action,
    literal_key_identity,
    sort_data_operations,
)
from systemlens.domain.module_inventory import (
    DiscoveredModule,
    JpaMethod,
    MongoMethod,
    module_identity,
)
from systemlens.domain.mongo_operations import mongo_action


def _repository_path(repo_root: Path | None, module: DiscoveredModule, path: str) -> str:
    """Return the repository-relative path, or the module path without a root.

    A caller without the repository root (an in-memory join of engine rows)
    keeps module-relative paths; the materializer then matches them as path
    suffixes of the persisted methods.
    """
    if repo_root is None:
        return path
    module_root = module.path.resolve().relative_to(repo_root.resolve())
    return (module_root / path).as_posix()


def mongo_data_operation(
    method: MongoMethod, module: str, path: str,
) -> DataOperation | None:
    """Project a legacy Mongo inventory fact; builders are not operations."""
    action = mongo_action(method.operation)
    if action is None or not method.owner_method:
        return None
    reads, writes = action_facets(action)
    identity = f"mongo:{module}:{method.collection}" if method.collection else None
    return DataOperation(
        id=compute_data_operation_id(
            module, path, method.owner_method, method.line, "mongodb", method.operation,
        ),
        module=module, path=path, owner_method=method.owner_method, line=method.line,
        end_line=method.evidence.end_line if method.evidence else method.line,
        technology="mongodb", operation=method.operation, action=action,
        reads=reads, writes=writes,
        resource=method.collection, resource_identity=identity,
        key_expression=method.key_expression or None,
        key_identity=literal_key_identity(identity, None, method.key_expression or None),
        confidence="medium" if identity else "low",
        unresolved_reason=None if identity else "collection expression is dynamic",
        receiver=method.receiver,
    )


def jpa_data_operation(method: JpaMethod, module: str, path: str) -> DataOperation:
    """Project a legacy JPA inventory fact with its CRUD classification."""
    action = crud_action(method.operation) or (
        "delete" if method.operation.startswith("delete") else "create_or_update"
    )
    reads, writes = action_facets(action)
    identity = f"jpa:{module}:{method.entity}"
    return DataOperation(
        id=compute_data_operation_id(
            module, path, method.owner_method, method.line, "jpa", method.operation,
        ),
        module=module, path=path, owner_method=method.owner_method, line=method.line,
        end_line=method.line, technology="jpa", operation=method.operation,
        action=action, reads=reads, writes=writes,
        resource=method.entity, resource_identity=identity,
        key_expression=method.key_expression or None,
        key_identity=literal_key_identity(identity, None, method.key_expression or None),
        receiver=method.receiver,
    )


def data_operations_from_modules(
    repo_root: Path | None, modules: Sequence[DiscoveredModule],
) -> list[DataOperation]:
    """Project the module inventory JSON of an older index into operations.

    Indexes created before the ``data_operations`` table persist operations
    only inside the module inventory. This projection keeps them readable
    until the project is reindexed; it never reopens a source file.
    """
    operations: list[DataOperation] = []
    for module in modules:
        if module.data_operations:
            operations.extend(module.data_operations)
            continue
        identity = module_identity(module)
        for mongo in module.mongo_methods:
            operation = mongo_data_operation(
                mongo, identity, _repository_path(repo_root, module, mongo.path),
            )
            if operation is not None:
                operations.append(operation)
        for jpa in module.jpa_methods:
            operations.append(jpa_data_operation(
                jpa, identity, _repository_path(repo_root, module, jpa.path),
            ))
    return sort_data_operations(operations)


