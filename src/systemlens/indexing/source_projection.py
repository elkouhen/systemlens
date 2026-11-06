"""Project the indexed Java sources into a source-only tree for an engine.

An external engine reads a copy of the indexed Java files, never the
repository itself: build descriptors and build outputs stay out, and
paths inside the copy are the repository-relative paths the index
persists. Every engine that builds its own database uses this projection,
so the engines analyze the same files.
"""

import hashlib
import shutil
from collections.abc import Sequence
from pathlib import Path

from systemlens.indexing.file_inventory import is_build_output


def project_java_sources(
    repo_root: Path, destination: Path, *, include_target_java_sources: bool = False,
    allowed_paths: Sequence[str] | None = None,
) -> int:
    """Copy application and generated Java sources without build descriptors.

    Maven projects may keep generated Java sources below ``target``. They are
    needed to resolve calls whose signatures use generated DTOs, while the rest
    of ``target`` remains an untrusted build artifact and is excluded unless
    source generation was explicitly requested for this index.
    """
    excluded_directories = {".git", ".systemlens"}
    allowed = set(allowed_paths or ())
    copied = 0
    for source in repo_root.rglob("*.java"):
        if not source.is_file():
            continue
        relative = source.relative_to(repo_root)
        if allowed and relative.as_posix() not in allowed:
            continue
        parts = relative.parts
        # Build directories are rooted at a module, never below ``src``: a
        # Java package named ``target`` or ``out`` is application source.
        build_directories = [
            index for index, part in enumerate(parts)
            if part in {"target", "out"} and "src" not in parts[:index]
        ]
        generated_target = any(
            parts[index:index + 2] == ("target", "generated-sources")
            for index in build_directories
        )
        target_java_source = generated_target
        if include_target_java_sources and any(parts[index] == "target" for index in build_directories):
            target_java_source = True
        if (
            any(part in excluded_directories for part in parts)
            or build_directories
            or is_build_output(relative.as_posix())
        ) and not target_java_source:
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    return copied


def executable_identity(executable: str) -> str:
    """Name an analyzer executable by its resolved path, size and mtime."""
    resolved = shutil.which(executable) or executable
    try:
        path = Path(resolved).resolve()
        stat = path.stat()
    except OSError:
        return resolved
    return f"{path}|{stat.st_size}|{stat.st_mtime_ns}"


def projection_fingerprint(source_root: Path, analyzer: str) -> str:
    """Hash a projected tree (paths and contents) with the analyzer's identity.

    Two runs with the same fingerprint read the same files with the same
    analyzer, so a database built by the first stays valid for the second.
    """
    digest = hashlib.sha256(analyzer.encode())
    for path in sorted(source_root.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(source_root).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
