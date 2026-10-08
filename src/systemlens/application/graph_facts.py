"""Import and reconcile AI-produced architecture facts through the CLI."""

from pathlib import Path

from systemlens.application.ai_graph import (
    AiGraphError,
    load_direct_flow_manifest,
    load_fact_manifest,
)
from systemlens.infrastructure.config import config_path
from systemlens.storage.sqlite import Store


def import_graph_facts(
    repo_root: Path,
    manifest_path: Path,
    *,
    namespace: str | None = None,
    complete: bool = False,
) -> dict[str, object]:
    """Validate and atomically import a fact manifest into the local index."""
    if not config_path(repo_root).is_file():
        raise RuntimeError("Configuration absente. Lancez d'abord systemlens init.")
    if manifest_path.is_absolute() or ".." in manifest_path.parts:
        raise ValueError("manifest_path doit être relatif au projet, sans '..'.")
    path = (repo_root / manifest_path).resolve()
    if repo_root not in path.parents and path != repo_root:
        raise ValueError("manifest_path doit rester dans le projet.")
    try:
        facts, resolved_namespace, manifest_complete = load_fact_manifest(
            path, namespace=namespace
        )
        endpoints, flows = load_direct_flow_manifest(path)
    except AiGraphError as exc:
        raise ValueError(str(exc)) from exc

    replace_stale = manifest_complete if complete is None else complete
    inserted = updated = 0
    with Store(repo_root) as store:
        with store.transaction():
            if (endpoints or flows) and (store.all_endpoints() or store.all_modules()):
                raise ValueError(
                    "Les flux directs ne peuvent être importés que dans un dépôt "
                    "sans index source."
                )
            existing = {
                fact.id for fact in store.graph_facts_by_namespace(resolved_namespace)
            }
            for fact in facts:
                if fact.id in existing:
                    updated += 1
                else:
                    inserted += 1
                store.upsert_graph_fact(fact)
            removed = (
                store.delete_graph_facts_not_in(
                    resolved_namespace, {fact.id for fact in facts}
                )
                if replace_stale
                else 0
            )
            if endpoints:
                store.replace_endpoints_for_files([], endpoints)
            if flows:
                store.replace_code_flows(flows)
                store.set_meta("code_flow_snapshot_status", "complete")
    return {
        "namespace": resolved_namespace,
        "inserted": inserted,
        "updated": updated,
        "removed": removed,
        "facts": len(facts),
        "endpoints": len(endpoints),
        "flows": len(flows),
        "complete": replace_stale,
    }
