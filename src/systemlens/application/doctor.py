"""Preflight checks for a reproducible architecture audit."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from systemlens.callgraph.registry import NO_ENGINE, declaration
from systemlens.infrastructure.config import ConfigError, load_config
from systemlens.discovery.java import parser as java_parser
from systemlens.indexing.freshness import endpoint_inventory_warning
from systemlens.infrastructure.paths import db_path
from systemlens.storage.sqlite import Store, StoreError


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # ok | warning | error
    detail: str


def run_doctor(repo_root: Path) -> list[Check]:
    """Report local indexing readiness without mutating the repository."""
    checks = [
        Check("systemlens", "ok", "CLI disponible."),
    ]
    try:
        config = load_config(repo_root)
    except ConfigError as exc:
        checks.append(Check("configuration", "error", str(exc)))
        return checks

    checks.append(Check("configuration", "ok", "Configuration .systemlens/config.yml chargée."))
    checks.append(_engine_check(config.call_graph_engine))
    try:
        java_parser.java_parser("doctor")
    except Exception as exc:
        checks.append(Check("analyse AST", "error", f"Tree-sitter Java indisponible : {exc}"))
    else:
        checks.append(Check("analyse AST", "ok", "Extracteurs Tree-sitter Java disponibles."))

    if not db_path(repo_root).is_file():
        checks.append(Check("index", "warning", "Index absent : lancez `systemlens index` après le préflight."))
        return checks
    try:
        with Store(repo_root, readonly=True) as store:
            warning = endpoint_inventory_warning(
                store.get_meta("endpoint_inventory_signature"),
                scope="ce projet",
                inventory_indexed=store.get_meta("endpoint_inventory_indexed") == "1",
            )
            diagnostics = store.all_extraction_diagnostics()
    except StoreError as exc:
        checks.append(Check("index", "error", str(exc)))
        return checks
    checks.append(Check("index", "warning" if warning else "ok", warning or "Index et schéma compatibles."))
    checks.append(Check(
        "diagnostics d'extraction",
        "warning" if diagnostics else "ok",
        f"{len(diagnostics)} diagnostic(s) d'extraction indexé(s)." if diagnostics else "Aucun diagnostic d'extraction.",
    ))
    return checks


def _engine_check(name: str) -> Check:
    """Whether the configured call-graph engine can run (ADR-61, ADR-85).

    An unavailable engine is a warning: indexing announces it and uses
    ``source`` instead.
    """
    if name == NO_ENGINE:
        return Check("moteur de graphe d'appel", "ok", "Moteur `none` : aucun graphe d'appel.")
    reason = declaration(name).unavailable_reason()
    if reason is not None:
        return Check(
            "moteur de graphe d'appel", "warning",
            f"Moteur `{name}` indisponible : {reason} L'indexation utilisera `source`.",
        )
    return Check("moteur de graphe d'appel", "ok", f"Moteur `{name}` disponible.")


def has_errors(checks: list[Check]) -> bool:
    return any(check.status == "error" for check in checks)
