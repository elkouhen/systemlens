"""Repository file inventory and conservative incremental-refresh policy."""

import fnmatch
import hashlib
import os
from pathlib import Path

from systemlens.callgraph.registry import is_engine_artifact
from systemlens.discovery.build.projects import outermost_build_directories
from systemlens.infrastructure.config import Config
from systemlens.domain.module_inventory import DiscoveredModule
from systemlens.conventions.strategy1.indexing import (
    is_generated_openapi_source_path,
    is_openapi_declaration_path,
    requires_full_reindex,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _matches_any(rel_path: str, patterns: list[str]) -> bool:
    return any(pattern == "**/*" or fnmatch.fnmatch(rel_path, pattern) for pattern in patterns)


def is_maven_or_gradle_test_source_set(source_set: str) -> bool:
    """Return whether *source_set* follows the Maven/Gradle test convention."""
    return source_set == "test" or source_set.endswith("Test")


def is_test_source(rel_path: str) -> bool:
    """Exclude only explicit Maven/Gradle test source sets.

    Segment-based matching avoids treating a package such as ``testutils`` as
    a test source tree and keeps non-Java ``src/<package>`` layouts eligible.
    """
    segments = rel_path.split("/")
    return any(
        segment == "src"
        and index + 1 < len(segments)
        and is_maven_or_gradle_test_source_set(segments[index + 1])
        for index, segment in enumerate(segments)
    )


def _is_strategy1_openapi_declaration(rel_path: str) -> bool:
    """Compatibility façade for the moved Strategy1 convention."""
    return is_openapi_declaration_path(rel_path)


def strategy1_requires_full_reindex(
    changed_or_deleted: set[str],
    repo_root: Path,
    modules: list[DiscoveredModule],
) -> bool:
    """Compatibility façade for the moved Strategy1 convention."""
    return requires_full_reindex(changed_or_deleted, repo_root, modules)


def is_git_metadata(rel_path: str) -> bool:
    """Return whether a relative path enters Git's metadata directory."""
    return ".git" in rel_path.split("/")


def is_build_output(rel_path: str) -> bool:
    """Exclude standard Maven/Gradle output trees from source evidence.

    An engine capture or a local Maven build can populate ``target/classes``
    with copied YAML files.  Treating those as source duplicates REST gateway
    ports and makes an index depend on whether someone compiled beforehand.
    """
    parts = rel_path.split("/")
    # Maven ``target``, Gradle ``build`` and IDE ``out`` output directories
    # are rooted at the module level. A Java package or resource directory
    # with one of these names remains source when it is below a ``src``
    # directory. The engine projection skips the same directories, so a
    # file the index extracts is a file the engine reads (review P7).
    return any(
        part in {"target", "build", "out"} and "src" not in parts[:index]
        for index, part in enumerate(parts)
    )


def is_generated_java_source_path(rel_path: str) -> bool:
    """Recognize Java sources generated below Maven's source output tree."""
    parts = Path(rel_path).parts
    return Path(rel_path).suffix.casefold() == ".java" and any(
        parts[index:index + 2] == ("target", "generated-sources")
        for index in range(max(0, len(parts) - 1))
    )


def is_target_java_source_path(rel_path: str) -> bool:
    """Recognize any Java source below a Maven ``target`` directory."""
    parts = Path(rel_path).parts
    return Path(rel_path).suffix.casefold() == ".java" and "target" in parts


def _nested_build_roots(repo_root: Path) -> tuple[Path, ...]:
    """Return outermost build roots when the input is a workspace container.

    Build roots are found at any depth, with the walk of the build layout,
    so a deep project is inside the perimeter it is attributed to (ADR-80).
    """
    descriptors = (
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
    )
    if any((repo_root / descriptor).is_file() for descriptor in descriptors):
        return ()
    return outermost_build_directories(repo_root)


def is_in_excluded_module(path: Path, excluded_module_paths: tuple[Path, ...]) -> bool:
    return any(module_path == path.parent or module_path in path.parents for module_path in excluded_module_paths)


def _repository_files(repo_root: Path) -> list[Path]:
    """Return the repository files in path order, without engine artifacts.

    An analyzer database holds a copy of the sources and its own data,
    wherever it lives; walking into it would hash and inventory thousands
    of files. Each engine declares how to recognize its artifacts.
    """
    files: list[Path] = []
    for directory, subdirectories, names in os.walk(repo_root):
        current = Path(directory)
        subdirectories[:] = [
            name for name in subdirectories
            if not is_engine_artifact(current / name)
        ]
        files.extend(
            current / name for name in names if (current / name).is_file()
        )
    return sorted(files)


def list_repo_files(
    repo_root: Path,
    config: Config,
    *,
    excluded_module_paths: tuple[Path, ...] = (),
    include_strategy1_generated_sources: bool = False,
    include_generated_sources: bool = False,
) -> dict[str, str]:
    """Build the eligible relative-path to content-hash inventory."""
    repo_root = repo_root.resolve()
    hashes: dict[str, str] = {}
    nested_roots = _nested_build_roots(repo_root)
    for path in _repository_files(repo_root):
        if is_in_excluded_module(path, excluded_module_paths):
            continue
        if nested_roots and not any(root == path.parent or root in path.parents for root in nested_roots):
            continue
        rel_path = path.relative_to(repo_root).as_posix()
        if is_git_metadata(rel_path) or is_test_source(rel_path):
            continue
        generated_source = (
            include_generated_sources and is_target_java_source_path(rel_path)
        )
        strategy1_source = (
            include_strategy1_generated_sources and is_generated_openapi_source_path(rel_path)
        )
        if is_build_output(rel_path) and not (generated_source or strategy1_source):
            continue
        if config.exclude and _matches_any(rel_path, config.exclude):
            continue
        if config.include and not _matches_any(rel_path, config.include):
            continue
        hashes[rel_path] = sha256_file(path)
    return hashes


def analysis_inputs_signature(repo_root: Path) -> str:
    """Fingerprint local configuration that changes AST analysis facts."""
    digest = hashlib.sha256()
    config_file = repo_root / ".systemlens" / "config.yml"
    if config_file.is_file():
        digest.update(sha256_file(config_file).encode())
    return digest.hexdigest()


def changes_require_dependent_rescan(paths: set[str]) -> bool:
    """Return whether changed inputs can alter facts in unchanged sources."""
    build_files = {
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
    }
    return any(
        Path(path).name in build_files
        or Path(path).suffix.casefold() in {".properties", ".yml", ".yaml"}
        for path in paths
    )


def is_spring_cloud_config_file(rel_path: str) -> bool:
    """A Spring Cloud Config file: ``configurations/<application>.yml``.

    The scanners read it for the application of that name, wherever that
    application's module is (``scanner/_spring_properties.py``).
    """
    path = Path(rel_path)
    return (
        path.parent.name == "configurations"
        and path.suffix.casefold() in {".yml", ".yaml", ".properties"}
    )


def dependent_rescan_paths(
    changed_paths: set[str],
    current_paths: set[str],
    repo_root: Path,
    modules: list[DiscoveredModule],
) -> set[str] | None:
    """Return the module-local paths invalidated by configuration changes.

    ``None`` means that a safe module boundary could not be established and a
    global rescan remains required.  The deepest owning module is selected so
    nested build roots do not cause sibling modules to be rescanned.
    """
    invalidated = {
        path for path in changed_paths
        if Path(path).name in {
            "pom.xml", "build.gradle", "build.gradle.kts",
            "settings.gradle", "settings.gradle.kts",
        }
        or Path(path).suffix.casefold() in {".properties", ".yml", ".yaml"}
    }
    if not invalidated:
        return set()
    if any(is_spring_cloud_config_file(path) for path in invalidated):
        # A config-server file configures the application it names, in
        # whichever module that application lives: no module boundary
        # bounds its readers (review P8).
        return None

    module_roots = sorted(
        {module.path.resolve() for module in modules},
        key=lambda path: len(path.parts),
        reverse=True,
    )
    affected_roots: set[Path] = set()
    for relative_path in invalidated:
        absolute_path = (repo_root / relative_path).resolve()
        owner = next(
            (
                root for root in module_roots
                if root == absolute_path.parent or root in absolute_path.parents
            ),
            None,
        )
        if owner is None or owner == repo_root.resolve():
            return None
        affected_roots.add(owner)

    return {
        relative_path
        for relative_path in current_paths
        if any(
            root == (repo_root / relative_path).resolve().parent
            or root in (repo_root / relative_path).resolve().parents
            for root in affected_roots
        )
    }
