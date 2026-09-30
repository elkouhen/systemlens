"""Repository file inventory and conservative incremental-refresh policy."""

import fnmatch
import hashlib
from pathlib import Path

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

    A CodeQL capture or a local Maven build can populate ``target/classes``
    with copied YAML files.  Treating those as source duplicates REST gateway
    ports and makes an index depend on whether someone compiled beforehand.
    """
    parts = rel_path.split("/")
    if "target" in parts:
        return True
    # A Gradle output directory is rooted at the module level. A package or
    # resource named ``build`` remains valid when it is below a source root.
    for index, part in enumerate(parts):
        if part == "build" and "src" not in parts[:index]:
            return True
    return False


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
    """Return outermost build roots when the input is a workspace container."""
    descriptors = (
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
    )
    if any((repo_root / descriptor).is_file() for descriptor in descriptors):
        return ()
    candidates = {
        path.parent
        for descriptor in descriptors
        for path in repo_root.rglob(descriptor)
        if not is_git_metadata(path.relative_to(repo_root).as_posix())
        if not is_build_output(path.relative_to(repo_root).as_posix())
        if len(path.parent.relative_to(repo_root).parts) <= 5
    }
    return tuple(
        candidate
        for candidate in sorted(candidates)
        if not any(parent != candidate and parent in candidates for parent in candidate.parents)
    )


def is_in_excluded_module(path: Path, excluded_module_paths: tuple[Path, ...]) -> bool:
    return any(module_path == path.parent or module_path in path.parents for module_path in excluded_module_paths)


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
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
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
