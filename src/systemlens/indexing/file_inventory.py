"""Repository file inventory and conservative incremental-refresh policy."""

import fnmatch
import hashlib
from pathlib import Path

from systemlens.infrastructure.config import Config
from systemlens.domain.module_inventory import DiscoveredModule


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
    path = Path(rel_path)
    parts = path.parts
    return path.suffix.casefold() == ".rest" and any(
        parts[index:index + 4] == ("src", "main", "resources", "openapi")
        for index in range(max(0, len(parts) - 3))
    )


def strategy1_requires_full_reindex(
    changed_or_deleted: set[str],
    repo_root: Path,
    modules: list[DiscoveredModule],
) -> bool:
    """Return whether a delta can change service-to-contract attribution."""
    model_roots = {
        module.path.resolve().relative_to(repo_root.resolve()).as_posix()
        for module in modules
        if module.name.casefold().startswith("model-")
        and module.path.resolve() != repo_root.resolve()
    }
    for rel_path in changed_or_deleted:
        if rel_path.endswith("pom.xml") or _is_strategy1_openapi_declaration(rel_path):
            return True
        if any(rel_path == root or rel_path.startswith(f"{root}/") for root in model_roots):
            return True
    return False


def is_git_metadata(rel_path: str) -> bool:
    """Return whether a relative path enters Git's metadata directory."""
    return ".git" in rel_path.split("/")


def is_build_output(rel_path: str) -> bool:
    """Exclude standard Maven/Gradle output trees from source evidence.

    A CodeQL capture or a local Maven build can populate ``target/classes``
    with copied YAML files.  Treating those as source duplicates REST gateway
    ports and makes an index depend on whether someone compiled beforehand.
    """
    return bool({"target", "build"}.intersection(rel_path.split("/")))


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
        if is_git_metadata(rel_path) or is_build_output(rel_path) or is_test_source(rel_path):
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
