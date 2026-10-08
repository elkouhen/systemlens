#!/usr/bin/env python3
"""Validate SystemLens against its companion skill and Java laboratory."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
LOCAL_LINK = re.compile(r"\[[^]]+\]\((?!https?://|#)([^)]+)\)")
JSON_BLOCK = re.compile(r"```json\n(.*?)\n```", re.DOTALL)


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int = 900,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=True,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        rendered = " ".join(command)
        raise TimeoutError(
            f"Command timed out after {timeout_seconds}s: {rendered}"
        ) from error


def _validate_skill(skill_root: Path) -> None:
    skill_document = skill_root / "SKILL.md"
    content = skill_document.read_text(encoding="utf-8")
    frontmatter = re.match(r"\A---\n(.*?)\n---\n", content, re.DOTALL)
    if frontmatter is None:
        raise ValueError("SKILL.md must start with YAML frontmatter")
    metadata = yaml.safe_load(frontmatter.group(1))
    if not isinstance(metadata, dict) or not metadata.get("name") or not metadata.get("description"):
        raise ValueError("SKILL.md frontmatter must define name and description")

    broken_links: list[str] = []
    invalid_json: list[str] = []
    for document in sorted(skill_root.rglob("*.md")):
        document_content = document.read_text(encoding="utf-8")
        for target in LOCAL_LINK.findall(document_content):
            path_text = target.split("#", 1)[0]
            if not path_text or path_text.startswith("../"):
                continue
            if not (document.parent / path_text).resolve().exists():
                broken_links.append(f"{document.relative_to(skill_root)} -> {target}")
        for block_number, block in enumerate(JSON_BLOCK.findall(document_content), start=1):
            try:
                json.loads(block)
            except json.JSONDecodeError as error:
                invalid_json.append(
                    f"{document.relative_to(skill_root)} block {block_number}: {error}"
                )
    if broken_links:
        raise ValueError("Broken skill links:\n" + "\n".join(broken_links))
    if invalid_json:
        raise ValueError("Invalid skill JSON examples:\n" + "\n".join(invalid_json))


def _copy_lab_application(lab_root: Path, destination: Path) -> None:
    source = lab_root / "apps" / "supermarket-demo"
    if not source.is_dir():
        raise FileNotFoundError(f"Java laboratory application not found: {source}")

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = {"target", ".git", ".systemlens"}.intersection(names)
        if Path(directory).name == ".systemlens":
            ignored.update({"findings.db", "findings.db-shm", "findings.db-wal"}.intersection(names))
        return ignored

    shutil.copytree(source, destination, ignore=ignore)


def _validate_lab(systemlens: Path, lab_root: Path, skill_root: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="systemlens-companion-contract-") as temporary:
        application = Path(temporary) / "supermarket-demo"
        _copy_lab_application(lab_root, application)
        manifest = skill_root / "examples" / "supermarket-direct-analysis.json"
        shutil.copy2(manifest, application / manifest.name)
        _run([str(systemlens), "version"], cwd=application)
        _run([str(systemlens), "init"], cwd=application)
        _run([
            str(systemlens), "import-facts", manifest.name,
            "--namespace", "direct-analysis", "--complete",
        ], cwd=application)
        output = Path(temporary) / "architecture.html"
        _run([str(systemlens), "export", "microservices", "--html", str(output)], cwd=application)
        document = output.read_text(encoding="utf-8")
        if '<script id="graph-data" type="application/json">' not in document:
            raise ValueError("The laboratory HTML export does not contain graph data")
        with sqlite3.connect(application / ".systemlens" / "findings.db") as connection:
            counts = dict(connection.execute(
                "SELECT 'indexed_endpoints', COUNT(*) FROM endpoints WHERE source != 'manifest' "
                "UNION ALL SELECT 'modules', COUNT(*) FROM modules "
                "UNION ALL SELECT 'graph_facts', COUNT(*) FROM graph_facts "
                "UNION ALL SELECT 'code_flows', COUNT(*) FROM code_flows"
            ).fetchall())
        if counts["indexed_endpoints"] != 0 or counts["modules"] != 0:
            raise ValueError("The direct-analysis workflow unexpectedly indexed source facts")
        if counts["graph_facts"] == 0 or counts["code_flows"] == 0:
            raise ValueError("The direct-analysis workflow imported no graph facts or flows")
            raise ValueError("The direct-analysis workflow imported no graph facts")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-root", type=Path, default=ROOT.parent / "systemlens-skill")
    parser.add_argument(
        "--lab-root",
        type=Path,
        default=ROOT.parent / "systemlens-observability-lab",
    )
    parser.add_argument(
        "--systemlens",
        type=Path,
        default=ROOT / ".venv" / "bin" / "systemlens",
    )
    arguments = parser.parse_args()

    _validate_skill(arguments.skill_root.resolve())
    _validate_lab(
        arguments.systemlens.resolve(),
        arguments.lab_root.resolve(),
        arguments.skill_root.resolve(),
    )
    print(
        "Companion contracts validated: skill metadata/links/JSON and direct "
        "fact import/HTML export on the Java laboratory."
    )


if __name__ == "__main__":
    main()
