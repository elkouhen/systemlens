"""Measure a local SystemLens index without persisting benchmark output."""

from __future__ import annotations

import argparse
import json
import time
import tracemalloc
from pathlib import Path

from systemlens.infrastructure.config import load_config
from systemlens.indexing.service import index_repo
from systemlens.storage.sqlite import Store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="indexed repository root")
    parser.add_argument("--json", action="store_true", help="emit machine-readable results")
    args = parser.parse_args()
    root = args.root.resolve()
    config = load_config(root)
    tracemalloc.start()
    started = time.perf_counter()
    with Store(root) as store:
        index_repo(root, config, store)
        snapshot = {
            "endpoints": len(store.all_endpoints()),
            "relations": len(store.all_architecture_relations()),
            "flows": len(store.all_code_flows()),
        }
    elapsed = time.perf_counter() - started
    incremental_started = time.perf_counter()
    with Store(root) as store:
        index_repo(root, config, store)
    incremental_elapsed = time.perf_counter() - incremental_started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result = {
        "root": str(root),
        "elapsed_seconds": round(elapsed, 3),
        "incremental_elapsed_seconds": round(incremental_elapsed, 3),
        "peak_memory_bytes": peak,
        "database_bytes": (root / ".systemlens" / "findings.db").stat().st_size,
        **snapshot,
    }
    print(json.dumps(result, indent=2) if args.json else _text_result(result))
    return 0


def _text_result(result: dict[str, object]) -> str:
    return (
        f"Indexed {result['root']} in {result['elapsed_seconds']}s; "
        f"peak memory {result['peak_memory_bytes']} bytes; "
        f"database {result['database_bytes']} bytes; "
        f"{result['endpoints']} endpoints, {result['relations']} relations, "
        f"{result['flows']} flows."
    )


if __name__ == "__main__":
    raise SystemExit(main())
