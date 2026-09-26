"""Shared helpers for the Typer delivery surface."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import click
import typer

from systemlens.application.architecture import render_text as render_architecture_text
from systemlens.indexing.freshness import endpoint_inventory_warning
from systemlens.infrastructure.paths import db_path
from systemlens.storage.sqlite import Store


def current_repo_endpoint_warning(store: Store) -> str | None:
    return endpoint_inventory_warning(
        store.get_meta("endpoint_inventory_signature"),
        scope="ce projet",
        inventory_indexed=store.get_meta("endpoint_inventory_indexed") == "1",
    )


def echo_index_progress(message: str) -> None:
    typer.echo(message)


def trace_index(stage: str, **fields: object) -> None:
    if os.environ.get("SYSTEMLENS_TRACE") != "1":
        return
    details = " ".join(f"{name}={value}" for name, value in fields.items())
    print(
        f"SYSTEMLENS_TRACE ts={time.monotonic():.6f} stage={stage} {details}".rstrip(),
        file=sys.stderr,
        flush=True,
    )


def manifest_rel_paths(repo_root: Path, paths: list[Path]) -> list[str]:
    manifests: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = raw_path.expanduser()
        if not path.is_absolute():
            path = repo_root / path
        try:
            rel_path = path.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError as exc:
            raise typer.BadParameter(
                f"Le manifeste doit être dans le dépôt indexé : {raw_path}"
            ) from exc
        if not path.is_file():
            raise typer.BadParameter(f"Manifeste introuvable : {raw_path}")
        if path.suffix.lower() not in {".md", ".json"}:
            raise typer.BadParameter(
                "Le manifeste doit être un fichier Markdown (.md) ou un flux de Topics JSON (.json) : "
                f"{raw_path}"
            )
        if rel_path not in seen:
            seen.add(rel_path)
            manifests.append(rel_path)
    return manifests


def emit_architecture(result: object, json_output: bool) -> None:
    typer.echo(json.dumps(result) if json_output else render_architecture_text(result))


def option_root(root: Path | None) -> Path:
    """Resolve --root from a command or its parent Typer group."""
    if root is not None:
        return root.resolve()
    context = click.get_current_context(silent=True)
    parent_root = (
        context.parent.params.get("root") if context and context.parent else None
    )
    return (parent_root or Path.cwd()).resolve()


def option_json(json_output: bool) -> bool:
    """Resolve --json from a command or its parent Typer group."""
    if json_output:
        return True
    context = click.get_current_context(silent=True)
    return bool(context and context.parent and context.parent.params.get("json_output"))


def require_index(repo_root: Path) -> None:
    if not db_path(repo_root).is_file():
        typer.echo("Index absent. Lancez d'abord: systemlens index", err=True)
        raise typer.Exit(code=2)
