"""Gradle build files: project names, versions and settings declarations."""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_GRADLE_ARTIFACT_RE = re.compile(
    r"(?:archiveBaseName|archivesBaseName|archivesName)\s*(?:\.set\s*\()?\s*=\s*['\"]([^'\"]+)['\"]"
)
_GRADLE_ARTIFACT_SET_RE = re.compile(
    r"(?:archiveBaseName|archivesBaseName|archivesName)\s*\.set\s*\(\s*['\"]([^'\"]+)['\"]\s*\)"
)
_ROOT_PROJECT_NAME_RE = re.compile(r"rootProject\.name\s*=\s*['\"]([^'\"]+)['\"]")
_GRADLE_VERSION_RE = re.compile(r"\bversion\s*(?:=|\.set\()\s*['\"]([^'\"]+)['\"]")
_GRADLE_BUILD_FILENAMES = ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")


def _first_gradle_match(paths: list[Path], patterns: tuple[re.Pattern[str], ...]) -> str | None:
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in patterns:
            match = pattern.search(text)
            if match is not None:
                return match.group(1)
    return None


@lru_cache(maxsize=128)
def _gradle_artifact_name(service_dir_str: str, fallback: str) -> str:
    """Nom d'artefact Gradle, ou nom de projet si Gradle utilise son défaut."""
    service_dir = Path(service_dir_str)
    build_files = [service_dir / "build.gradle", service_dir / "build.gradle.kts"]
    artifact_name = _first_gradle_match(
        build_files, (_GRADLE_ARTIFACT_RE, _GRADLE_ARTIFACT_SET_RE)
    )
    if artifact_name is not None:
        return artifact_name

    settings_files = [service_dir / "settings.gradle", service_dir / "settings.gradle.kts"]
    project_name = _first_gradle_match(settings_files, (_ROOT_PROJECT_NAME_RE,))
    return project_name or fallback


def clear_caches() -> None:
    """Vide les caches de détection Gradle avant une nouvelle indexation."""
    _gradle_artifact_name.cache_clear()


def gradle_version(module_dir: Path) -> str | None:
    """Return the version literal declared by a project's own build file."""
    return _first_gradle_match(
        [module_dir / "build.gradle", module_dir / "build.gradle.kts"], (_GRADLE_VERSION_RE,)
    )


def gradle_project_name(module_dir: Path, fallback: str) -> str:
    """Return the archive name, the root project name, or ``fallback``."""
    return _gradle_artifact_name(str(module_dir), fallback)


@dataclass(frozen=True)
class GradleSettings:
    """Projects declared by one ``settings.gradle(.kts)`` file.

    ``projects`` maps each declared project path (``:a:b``) to its directory,
    relative to the settings directory, including implicit parents of nested
    paths. ``complete`` is false when the file computes its includes or
    project directories at runtime: the declaration is then not used to
    decide which build files belong to the build. ``included_builds`` lists
    the literal ``includeBuild`` directories, which are never projects of
    this build.
    """

    projects: dict[str, str]
    explicit: frozenset[str]
    included_builds: tuple[str, ...]
    complete: bool


_INCLUDE_CALL_RE = re.compile(r"\b(include|includeFlat|includeBuild)\b(?!\s*[.=])")
_STRING_LITERAL_RE = re.compile(r"\s*(['\"])([^'\"\n]*)\1")
_PROJECT_DIR_ASSIGNMENT_RE = re.compile(r"\bprojectDir\s*=(?!=)")
_PROJECT_DIR_REMAP_RE = re.compile(
    r"\b(?:project|findProject)\s*\(\s*(['\"])(:?[^'\"$]+)\1\s*\)\s*!?\??\.projectDir\s*=\s*"
    r"(?:file\s*\(\s*(['\"])([^'\"$]+)\3\s*\)"
    r"|(?:new\s+)?(?:java\.io\.)?File\s*\(\s*(?:settingsDir|rootDir|rootProject\.projectDir)\s*,"
    r"\s*(['\"])([^'\"$]+)\5\s*\))"
)
# A settings script that computes its project list cannot be read statically.
_DYNAMIC_SETTINGS_MARKERS = (
    "apply from", "apply(from", "eachDir", "listFiles", ".each", "forEach", "for (", "for(",
    "fileTree", "walkTopDown",
)


def _strip_gradle_comments(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments outside string literals."""
    result: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(text):
        char = text[index]
        if quote is not None:
            result.append(char)
            if char == "\\" and index + 1 < len(text):
                result.append(text[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            result.append(char)
            index += 1
            continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            index = len(text) if end < 0 else end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
            result.append(" ")
            continue
        result.append(char)
        index += 1
    return "".join(result)


def _literal_arguments(text: str, start: int) -> list[str] | None:
    """Read ``'a', 'b'`` or ``('a', "b")`` after a call name, else ``None``."""
    position = start
    while position < len(text) and text[position] in " \t":
        position += 1
    parenthesized = position < len(text) and text[position] == "("
    if parenthesized:
        position += 1
    values: list[str] = []
    while True:
        match = _STRING_LITERAL_RE.match(text, position)
        if match is None or "$" in match.group(2):
            return None
        values.append(match.group(2))
        position = match.end()
        separator = re.match(r"\s*,", text[position:])
        if separator is None:
            break
        position += separator.end()
        if parenthesized and re.match(r"\s*\)", text[position:]) is not None:
            break
    if parenthesized and re.match(r"\s*\)", text[position:]) is None:
        return None
    return values


def _normalized_project_path(value: str) -> str:
    segments = [segment for segment in value.strip().split(":") if segment]
    return ":" + ":".join(segments) if segments else ":"


def parse_gradle_settings(settings_path: Path) -> GradleSettings:
    """Read the literal project declarations of one settings script."""
    try:
        text = _strip_gradle_comments(
            settings_path.read_text(encoding="utf-8", errors="replace")
        )
    except OSError:
        return GradleSettings({}, frozenset(), (), False)
    complete = not any(marker in text for marker in _DYNAMIC_SETTINGS_MARKERS)
    declared: list[str] = []
    included_builds: list[str] = []
    for match in _INCLUDE_CALL_RE.finditer(text):
        arguments = _literal_arguments(text, match.end())
        kind = match.group(1)
        if arguments is None:
            if kind != "includeBuild":
                complete = False
            continue
        if kind == "include":
            declared.extend(_normalized_project_path(argument) for argument in arguments)
        elif kind == "includeBuild":
            included_builds.extend(arguments)
        # ``includeFlat`` projects live beside the settings directory, outside
        # the indexed tree; they are never projects of this repository.
    remaps: dict[str, str] = {}
    for match in _PROJECT_DIR_REMAP_RE.finditer(text):
        remaps[_normalized_project_path(match.group(2))] = match.group(4) or match.group(6)
    if len(remaps) != len(_PROJECT_DIR_ASSIGNMENT_RE.findall(text)):
        complete = False
    every_path: set[str] = set()
    for path in declared:
        segments = path.strip(":").split(":")
        for depth in range(1, len(segments) + 1):
            every_path.add(":" + ":".join(segments[:depth]))
    directories: dict[str, str] = {}
    for path in sorted(every_path, key=lambda value: value.count(":")):
        parent, _, name = path.rpartition(":")
        if path in remaps:
            directories[path] = remaps[path]
        else:
            parent_dir = directories.get(parent, "") if parent else ""
            directories[path] = f"{parent_dir}/{name}" if parent_dir else name
    if not declared:
        complete = False
    return GradleSettings(
        projects=directories,
        explicit=frozenset(declared),
        included_builds=tuple(included_builds),
        complete=complete,
    )
