"""One build layout per index: which Maven and Gradle projects exist, and
which project owns each file.

Module inventory, file ownership, the excluded source perimeter and module
identities all read this layout, so a module that owns a file is always a
module of the inventory, and the reverse (ADR-80).

Rules:

- A directory with a ``pom.xml`` is a Maven project; a directory with a
  ``build.gradle(.kts)`` is a Gradle project; a ``settings.gradle(.kts)``
  makes its directory the root project of a Gradle build. When one directory has both,
  Maven wins.
- An aggregator POM with ``<modules>`` declares its build: below its
  directory, only the POMs it declares, recursively, are projects. A
  ``settings.gradle(.kts)`` with literal ``include`` calls declares its
  build in the same way, ``projectDir`` remaps included. A declaration that
  cannot be read statically (an unresolved module, a computed include)
  declares nothing, and discovery keeps every build file below it.
- ``buildSrc`` and ``includeBuild`` directories are never projects.
- Test, mock and archetype modules, ``buildSrc`` and included builds are
  excluded from indexing with everything below them. An undeclared build
  directory is excluded unless it contains a project.
- A file belongs to the deepest project that contains it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from systemlens.discovery.build.gradle import (
    GradleSettings,
    gradle_project_name,
    parse_gradle_settings,
)
from systemlens.discovery.build.maven import declared_modules, parse_pom

_GRADLE_BUILD_FILES = ("build.gradle", "build.gradle.kts")
_GRADLE_SETTINGS_FILES = ("settings.gradle", "settings.gradle.kts")
# Directories that never hold a build project of the indexed repository.
# Pruning them keeps the walk proportional to the source tree.
_PRUNED_DIRECTORIES = frozenset({"node_modules", "__pycache__", "bower_components"})
_BUILD_OUTPUT_DIRECTORIES = frozenset({"target", "build"})


@dataclass(frozen=True)
class BuildProject:
    """One Maven or Gradle project of the indexed repository."""

    path: Path
    build_system: str
    name: str
    identity: str
    gradle_build: Path | None = None
    gradle_path: str | None = None


@dataclass(frozen=True)
class BuildLayout:
    """The projects of one repository and the build directories excluded."""

    root: Path
    projects: tuple[BuildProject, ...]
    excluded_paths: tuple[Path, ...]
    build_directories: frozenset[Path]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "_by_path", {project.path: project for project in self.projects}
        )
        object.__setattr__(self, "_excluded", frozenset(self.excluded_paths))

    def owner(self, path: Path) -> BuildProject | None:
        """Return the deepest project that contains ``path``.

        ``None`` when no project contains it, or when an excluded build
        directory contains it.
        """
        by_path: dict[Path, BuildProject] = getattr(self, "_by_path")
        excluded: frozenset[Path] = getattr(self, "_excluded")
        for candidate in (path, *path.parents):
            project = by_path.get(candidate)
            if project is not None:
                return project
            if candidate in excluded:
                return None
            if candidate == self.root:
                break
        return None


def _is_build_output(parts: tuple[str, ...]) -> bool:
    return any(
        part in _BUILD_OUTPUT_DIRECTORIES and "src" not in parts[:index]
        for index, part in enumerate(parts)
    )


def _walk_build_files(root: Path) -> dict[Path, frozenset[str]]:
    """Return every directory holding a build descriptor, at any depth.

    Hidden directories, dependency caches and build outputs are not walked.
    """
    descriptors = {"pom.xml", *_GRADLE_BUILD_FILES, *_GRADLE_SETTINGS_FILES}
    found: dict[Path, frozenset[str]] = {}
    for directory, subdirectories, files in os.walk(root):
        current = Path(directory)
        relative_parts = current.relative_to(root).parts
        subdirectories[:] = [
            name for name in subdirectories
            if not name.startswith(".")
            and name not in _PRUNED_DIRECTORIES
            and not _is_build_output((*relative_parts, name))
        ]
        present = descriptors.intersection(files)
        if present:
            found[current] = frozenset(present)
    return found


def outermost_build_directories(root: Path) -> tuple[Path, ...]:
    """Return the build directories that no other build directory contains."""
    root = root.resolve()
    directories = set(_walk_build_files(root))
    return tuple(
        directory for directory in sorted(directories)
        if not any(parent in directories for parent in directory.parents)
    )


def _within(path: Path, ancestor: Path) -> bool:
    return path == ancestor or ancestor in path.parents


def _resolve_inside(root: Path, base: Path, relative: str) -> Path | None:
    candidate = Path(os.path.normpath(base / relative))
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _maven_projects(root: Path, pom_directories: set[Path]) -> tuple[set[Path], set[Path]]:
    """Return ``(projects, undeclared)`` among the directories with a POM."""
    kept: set[Path] = set()
    governors: list[Path] = []
    declared: set[Path] = set()
    changed = True
    while changed:
        changed = False
        for directory in sorted(pom_directories, key=lambda path: (len(path.parts), path)):
            if directory in kept:
                continue
            governed = any(
                directory != governor and governor in directory.parents for governor in governors
            )
            if governed and directory not in declared:
                continue
            kept.add(directory)
            changed = True
            entries = declared_modules(directory / "pom.xml")
            if not entries:
                continue
            resolved: list[Path] = []
            complete = True
            for entry in entries:
                target = _resolve_inside(root, directory, entry)
                if target is None:
                    continue
                if target.name == "pom.xml":
                    target = target.parent
                if target not in pom_directories:
                    complete = False
                    continue
                resolved.append(target)
            declared.update(resolved)
            if complete:
                governors.append(directory)
    return kept, pom_directories - kept


def _gradle_projects(
    root: Path, found: dict[Path, frozenset[str]],
) -> tuple[dict[Path, tuple[Path | None, str | None]], set[Path], set[Path]]:
    """Return Gradle projects, undeclared build directories and included builds.

    Projects map to their declaring settings directory and project path when a
    complete settings file declares them.
    """
    settings_directories = sorted(
        (directory for directory, files in found.items() if files & set(_GRADLE_SETTINGS_FILES)),
        key=lambda path: (len(path.parts), path),
    )
    build_directories = {
        directory for directory, files in found.items() if files & set(_GRADLE_BUILD_FILES)
    }
    projects: dict[Path, tuple[Path | None, str | None]] = {}
    included_builds: set[Path] = set()
    governed: list[Path] = []
    for settings_directory in settings_directories:
        if any(_within(settings_directory, other) for other in governed):
            continue
        settings_file = next(
            settings_directory / name for name in _GRADLE_SETTINGS_FILES
            if name in found[settings_directory]
        )
        settings: GradleSettings = parse_gradle_settings(settings_file)
        for included in settings.included_builds:
            target = _resolve_inside(root, settings_directory, included)
            if target is not None:
                included_builds.add(target)
        if not settings.complete:
            # The settings directory is the root project of its own build.
            projects.setdefault(settings_directory, (None, None))
            continue
        governed.append(settings_directory)
        projects[settings_directory] = (settings_directory, ":")
        for project_path, relative in settings.projects.items():
            target = _resolve_inside(root, settings_directory, relative)
            if target is None or not target.is_dir():
                continue
            if project_path in settings.explicit or target in build_directories:
                projects.setdefault(target, (settings_directory, project_path))
    for directory in build_directories:
        if any(_within(directory, settings_directory) for settings_directory in governed):
            continue
        projects.setdefault(directory, (None, None))
    undeclared = {
        directory for directory, files in found.items()
        if files & {*_GRADLE_BUILD_FILES, *_GRADLE_SETTINGS_FILES}
    } - set(projects)
    return projects, undeclared, included_builds


def _is_excluded_module(name: str, module_dir: Path, root: Path) -> bool:
    """Whether a non-production build module must not be indexed.

    Test and mock projects do not describe the production architecture.  Their
    artifact name is not always explicit: a module in ``contract-tests`` may
    still publish an artifact named ``contract-api``. Inspect both the build
    name and every component of its path relative to the workspace, without
    considering the workspace path itself.
    """
    try:
        path_parts = module_dir.resolve().relative_to(root.resolve()).parts
    except ValueError:
        path_parts = (module_dir.name,)
    # A substring match made ordinary artifact names such as ``test-service``
    # (and pytest's temporary workspace names) disappear from discovery.
    # Keep explicit test directories excluded, while retaining production
    # modules whose names merely contain that word.
    test_or_mock = any(
        value.casefold() in {"test", "tests"} or "mock" in value.casefold()
        for value in path_parts
    ) or "mock" in name.casefold() or name.casefold().endswith("-test")
    # Build archetypes are templates rather than runtime applications. This
    # applies to both Maven and Gradle module names.
    return test_or_mock or "archetype" in name.casefold()


def _is_excluded_maven_module(name: str, module_dir: Path, root: Path) -> bool:
    """Exclude Maven modules whose identity is test-only or templated."""
    identities = (name.casefold(), module_dir.name.casefold())
    # The workspace root is also a valid Maven aggregator. Its directory name
    # may be an implementation detail such as ``pytest-123`` and must not
    # make the root POM disappear from discovery. ``test`` must be a whole
    # name token: ``orders-test-support`` is excluded, ``attestation-service``
    # and ``contest-service`` remain production modules.
    module_name_is_test = module_dir.resolve() != root.resolve() and any(
        {"test", "tests"} & set(re.split(r"[-_.]", identity)) for identity in identities
    )
    return _is_excluded_module(name, module_dir, root) or module_name_is_test


def _qualified_identity(name: str, path: Path, root: Path, collisions: dict[str, int]) -> str:
    if collisions[name] <= 1:
        return name
    relative = path.relative_to(root).as_posix()
    return f"{name}@{'racine' if relative == '.' else relative}"


def build_layout(root: Path) -> BuildLayout:
    """Discover the build layout of ``root`` from its build files."""
    root = root.resolve()
    found = _walk_build_files(root)
    pom_directories = {directory for directory, files in found.items() if "pom.xml" in files}
    maven_kept, maven_undeclared = _maven_projects(root, pom_directories)
    gradle_kept, gradle_undeclared, included_builds = _gradle_projects(root, found)

    candidates: list[tuple[Path, str, str, Path | None, str | None]] = []
    # Test, mock and archetype modules, buildSrc and included builds are
    # excluded with everything below them.
    rejected: set[Path] = {path for path in included_builds if path.is_dir()}
    for directory in sorted(maven_kept):
        artifact_id, _, _ = parse_pom(directory / "pom.xml")
        name = artifact_id or directory.name
        if _is_excluded_maven_module(name, directory, root):
            rejected.add(directory)
            continue
        candidates.append((directory, "maven", name, None, None))
    for directory, (build, project_path) in sorted(gradle_kept.items()):
        if directory in maven_kept:
            continue
        if "buildSrc" in directory.relative_to(root).parts:
            rejected.add(directory)
            continue
        fallback = (
            project_path.rsplit(":", 1)[-1]
            if project_path not in (None, ":") else directory.name
        )
        name = gradle_project_name(directory, fallback)
        if _is_excluded_module(name, directory, root):
            rejected.add(directory)
            continue
        candidates.append((directory, "gradle", name, build, project_path))
    candidates = [
        candidate for candidate in candidates
        if not any(_within(candidate[0], path) for path in rejected)
    ]
    project_paths = {candidate[0] for candidate in candidates}
    # An undeclared build directory that holds a project below it stays in
    # the perimeter; its own files then belong to the nearest project above.
    undeclared = {
        path for path in (maven_undeclared | gradle_undeclared) - project_paths
        if not any(path in project.parents for project in project_paths)
    }
    excluded = rejected | undeclared
    collisions: dict[str, int] = {}
    for _path, _system, name, _build, _project_path in candidates:
        collisions[name] = collisions.get(name, 0) + 1
    projects = tuple(
        BuildProject(
            path=path,
            build_system=system,
            name=name,
            identity=_qualified_identity(name, path, root, collisions),
            gradle_build=build,
            gradle_path=project_path,
        )
        for path, system, name, build, project_path in sorted(candidates)
    )
    return BuildLayout(
        root=root,
        projects=projects,
        excluded_paths=tuple(sorted(excluded)),
        build_directories=frozenset(found),
    )


_LAYOUTS: dict[Path, BuildLayout] = {}


def cached_build_layout(root: Path) -> BuildLayout:
    """Return the layout of ``root`` for the current indexing pass."""
    resolved = root.resolve()
    layout = _LAYOUTS.get(resolved)
    if layout is None:
        layout = build_layout(resolved)
        _LAYOUTS[resolved] = layout
    return layout


def refresh_build_layout(root: Path) -> BuildLayout:
    """Rediscover the layout of ``root`` and make it the cached one."""
    resolved = root.resolve()
    layout = build_layout(resolved)
    _LAYOUTS[resolved] = layout
    return layout


def clear_caches() -> None:
    """Forget every layout before a new indexing pass."""
    _LAYOUTS.clear()


def module_for_path(repo_root: Path, rel_path: str) -> str | None:
    """Return the identity of the deepest project that owns ``rel_path``."""
    layout = cached_build_layout(repo_root)
    project = layout.owner(Path(os.path.normpath(layout.root / rel_path)))
    return project.identity if project is not None else None
