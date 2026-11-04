"""Lecture minimale de `pom.xml` (artifactId, détection Spring Boot) partagée
entre `workspace.py` (fédération multi-services, BACKLOG-11 A2) et `scanner.py`
(attribution d'un module à chaque finding/endpoint indexé, BACKLOG-13 M1)."""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
# Element is used only for annotations; parsing is performed by defusedxml below.
from xml.etree.ElementTree import Element  # nosec B405

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as ET

from systemlens.discovery.java import parser as java_parser

_MAVEN_NS = "{http://maven.apache.org/POM/4.0.0}"
_MAVEN_PROPERTY_RE = re.compile(r"\$\{([^}]+)\}")


def _pom_child_text(root: Element, tag: str) -> str | None:
    element = root.find(f"{_MAVEN_NS}{tag}")
    if element is None:
        element = root.find(tag)  # pom sans espace de noms déclaré (rare)
    return element.text.strip() if element is not None and element.text else None


def _pom_findall(root: Element, path: str) -> list[Element]:
    namespaced = root.findall(path)
    plain = root.findall(path.replace(_MAVEN_NS, ""))
    seen: set[int] = set()
    merged: list[Element] = []
    for element in [*namespaced, *plain]:
        marker = id(element)
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(element)
    return merged


def parse_pom_root(pom_path: Path) -> Element | None:
    """Parse ``pom_path`` or return ``None`` when it cannot be read safely.

    A POM that defusedxml refuses (an internal DTD entity, an external
    reference) is treated like a malformed POM: the module is skipped and the
    discovery of the other modules goes on. The protection itself stays on.
    """
    try:
        return ET.fromstring(pom_path.read_text(encoding="utf-8", errors="replace"))
    except (ET.ParseError, DefusedXmlException, OSError):
        return None


def _resolve_maven_value(value: str, properties: dict[str, str]) -> str:
    resolved = value.strip()
    for _ in range(8):
        updated = _MAVEN_PROPERTY_RE.sub(
            lambda match: properties.get(match.group(1), match.group(0)),
            resolved,
        )
        if updated == resolved:
            break
        resolved = updated
    return resolved


def _pom_properties(root: Element, pom_path: Path) -> dict[str, str]:
    module_dir = pom_path.parent.resolve()
    properties = {
        "basedir": str(module_dir),
        "pom.basedir": str(module_dir),
        "project.basedir": str(module_dir),
        "project.parent.basedir": str(module_dir.parent),
        "project.build.directory": str(module_dir / "target"),
    }
    artifact_id = _pom_child_text(root, "artifactId")
    version = _pom_child_text(root, "version")
    if artifact_id:
        properties.update({
            "artifactId": artifact_id,
            "project.artifactId": artifact_id,
            "pom.artifactId": artifact_id,
        })
    if version:
        properties.update({
            "version": version,
            "project.version": version,
            "pom.version": version,
        })
    for props in _pom_findall(root, f"{_MAVEN_NS}properties"):
        for child in list(props):
            tag = child.tag.split("}", 1)[-1]
            if child.text and tag:
                properties[tag] = child.text.strip()
    return properties


def _plugin_config_value(plugin: Element, tag: str) -> str | None:
    config = plugin.find(f"{_MAVEN_NS}configuration")
    if config is None:
        config = plugin.find("configuration")
    return _pom_child_text(config, tag) if config is not None else None


def _execution_configurations(plugin: Element) -> list[Element]:
    executions = plugin.find(f"{_MAVEN_NS}executions")
    if executions is None:
        executions = plugin.find("executions")
    if executions is None:
        return []
    configurations: list[Element] = []
    for execution in list(executions):
        config = execution.find(f"{_MAVEN_NS}configuration")
        if config is None:
            config = execution.find("configuration")
        if config is not None:
            configurations.append(config)
    return configurations


def _iter_openapi_generator_plugins(root: Element) -> list[Element]:
    plugins: list[Element] = []
    for plugin in _pom_findall(root, f".//{_MAVEN_NS}plugin"):
        artifact_id = _pom_child_text(plugin, "artifactId")
        if artifact_id and artifact_id.strip().casefold() == "openapi-generator-maven-plugin":
            plugins.append(plugin)
    return plugins


def _resolve_openapi_spec_path(
    pom_path: Path, raw_value: str, properties: dict[str, str]
) -> str | None:
    """Resolve a plugin-configured ``inputSpec`` to a module-relative path.

    A multi-module Maven repository commonly keeps the OpenAPI contract in a
    shared ``model-*``/``api-*`` module rather than inside the implementing
    service's own directory (``inputSpec`` resolving to e.g.
    ``../model-common/src/main/resources/orders.yaml``). Rejecting anything
    outside the pom's own directory silently drops that contract instead of
    following it into the sibling module, so this only checks the file
    actually exists and returns the ``..``-relative path as-is; the caller
    resolves it against the repository root, wherever it physically lives.
    """
    resolved = _resolve_maven_value(raw_value, properties)
    if "${" in resolved or "://" in resolved and not resolved.startswith("file://"):
        return None
    if resolved.startswith("file://"):
        resolved = resolved[7:]
    candidate = Path(resolved)
    if not candidate.is_absolute():
        candidate = pom_path.parent / candidate
    if not candidate.is_file():
        return None
    if {"target", "build"}.intersection(candidate.parts):
        return None
    try:
        module_relative = Path(os.path.relpath(candidate.resolve(strict=False), pom_path.parent.resolve()))
    except ValueError:
        return None
    return module_relative.as_posix()


def _resolve_openapi_spec_root_directory(
    pom_path: Path, raw_value: str, properties: dict[str, str]
) -> tuple[str, ...]:
    """Return every local file configured through ``inputSpecRootDirectory``.

    Like ``_resolve_openapi_spec_path``, the configured directory may live
    outside the pom's own module (a shared contracts module); files are kept
    relative to the module directory, ``..``-segments included, rather than
    silently dropped.
    """
    resolved = _resolve_maven_value(raw_value, properties)
    if "${" in resolved or "://" in resolved and not resolved.startswith("file://"):
        return ()
    if resolved.startswith("file://"):
        resolved = resolved[7:]
    candidate = Path(resolved)
    if not candidate.is_absolute():
        candidate = pom_path.parent / candidate
    if not candidate.is_dir():
        return ()
    if {"target", "build"}.intersection(candidate.parts):
        return ()
    module_dir = pom_path.parent.resolve()
    try:
        return tuple(
            os.path.relpath(path.resolve(), module_dir).replace(os.sep, "/")
            for path in sorted(candidate.rglob("*"))
            if path.is_file() and not {"target", "build"}.intersection(path.parts)
        )
    except (OSError, ValueError):
        return ()


def _is_spring_boot_main_class(source: bytes) -> bool:
    return java_parser.has_spring_boot_main_class(source)


@lru_cache(maxsize=256)
def _module_has_spring_boot_main_class(module_dir_str: str) -> bool:
    module_dir = Path(module_dir_str)
    source_root = module_dir / "src" / "main" / "java"
    if not source_root.is_dir():
        return False
    for java_file in source_root.rglob("*.java"):
        try:
            source = java_file.read_bytes()
        except OSError:
            continue
        if _is_spring_boot_main_class(source):
            return True
    return False


def parse_pom(pom_path: Path) -> tuple[str | None, bool, str | None]:
    """Renvoie (artifactId, is_spring_boot_app, packaging). Un pom.xml illisible ou mal
    formé renvoie (None, False, None) plutôt que de faire échouer toute la
    découverte — un seul module cassé ne doit pas bloquer les autres."""
    root = parse_pom_root(pom_path)
    if root is None:
        return None, False, None
    artifact_id = _pom_child_text(root, "artifactId")
    packaging = _pom_child_text(root, "packaging")
    # A Spring Boot plugin can be inherited by a shared library. A deployable
    # microservice must have an actual Spring Boot entry point; otherwise a
    # `buildingblocks`-style module pollutes the architecture graph.
    is_spring_boot_app = _module_has_spring_boot_main_class(str(pom_path.parent))
    return artifact_id, is_spring_boot_app, packaging


def pom_version(pom_path: Path) -> str | None:
    """Return the locally declared Maven version or its parent declaration."""
    root = parse_pom_root(pom_path)
    if root is None:
        return None
    version = _pom_child_text(root, "version")
    if version is not None:
        return version
    parent = root.find(f"{_MAVEN_NS}parent")
    if parent is None:
        parent = root.find("parent")
    return _pom_child_text(parent, "version") if parent is not None else None


def is_runtime_service(packaging: str | None, is_spring_boot_app: bool) -> bool:
    """Un parent/agrégateur Maven `packaging=pom` n'est jamais un service
    runtime, même s'il déclare le plugin Spring Boot pour ses enfants."""
    return packaging != "pom" and is_spring_boot_app


def _has_openapi_generator_plugin(pom_path: Path) -> bool:
    """Détecte la présence du plugin openapi-generator-maven-plugin dans le pom.xml."""
    root = parse_pom_root(pom_path)
    if root is None:
        return False
    return bool(_iter_openapi_generator_plugins(root))


def detect_openapi_generator_input_specs(pom_path: Path) -> tuple[str, ...]:
    """Liste les contrats OpenAPI/Swagger locaux référencés par le plugin Maven.

    Les implémentations serveur générées par ``openapi-generator-maven-plugin``
    publient l'API décrite par ``inputSpec`` ou ``inputSpecRootDirectory`` même quand les classes
    ``@RestController`` n'exposent aucune annotation de méthode locale.
    """
    root = parse_pom_root(pom_path)
    if root is None:
        return ()
    properties = _pom_properties(root, pom_path)
    specs: set[str] = set()
    for plugin in _iter_openapi_generator_plugins(root):
        plugin_level = _plugin_config_value(plugin, "inputSpec")
        if plugin_level:
            resolved = _resolve_openapi_spec_path(pom_path, plugin_level, properties)
            if resolved is not None:
                specs.add(resolved)
        plugin_root_directory = _plugin_config_value(plugin, "inputSpecRootDirectory")
        if plugin_root_directory:
            specs.update(
                _resolve_openapi_spec_root_directory(pom_path, plugin_root_directory, properties)
            )
        for configuration in _execution_configurations(plugin):
            execution_level = _pom_child_text(configuration, "inputSpec")
            if execution_level:
                resolved = _resolve_openapi_spec_path(pom_path, execution_level, properties)
                if resolved is not None:
                    specs.add(resolved)
            execution_root_directory = _pom_child_text(configuration, "inputSpecRootDirectory")
            if execution_root_directory:
                specs.update(
                    _resolve_openapi_spec_root_directory(
                        pom_path, execution_root_directory, properties
                    )
                )
    return tuple(sorted(specs))


def detect_openapi_generated_clients(pom_path: Path) -> tuple[str, ...]:
    """Détecte les clients OpenAPI générés par openapi-generator-maven-plugin.

    Retourne un tuple des chemins relatifs des fichiers Java générés.
    """
    # Build output is deliberately outside the SystemLens source perimeter.
    # Generated clients are derived artefacts, not indexed architecture facts.
    return ()


@lru_cache(maxsize=512)
def _cached_module_name(pom_path_str: str) -> str:
    pom_path = Path(pom_path_str)
    artifact_id, _, _ = parse_pom(pom_path)
    return artifact_id or pom_path.parent.name


def clear_caches() -> None:
    """BACKLOG-16 P2 : à appeler en tête de chaque indexation dans un
    processus long-vivant — `_cached_module_name` est caché par
    chemin de pom.xml pour toute la durée du process, un artifactId modifié
    entre deux `systemlens index` resterait sinon périmé."""
    _cached_module_name.cache_clear()
    _module_has_spring_boot_main_class.cache_clear()


def declared_modules(pom_path: Path) -> tuple[str, ...]:
    """Return the ``<module>`` entries of a POM, its profiles included."""
    root = parse_pom_root(pom_path)
    if root is None:
        return ()
    entries = [
        *_pom_findall(root, f"{_MAVEN_NS}modules/{_MAVEN_NS}module"),
        *_pom_findall(
            root,
            f"{_MAVEN_NS}profiles/{_MAVEN_NS}profile/{_MAVEN_NS}modules/{_MAVEN_NS}module",
        ),
    ]
    return tuple(
        dict.fromkeys(entry.text.strip() for entry in entries if entry.text and entry.text.strip())
    )


@dataclass(frozen=True)
class MavenDependency:
    """One ``<dependency>`` of a POM, with its groupId resolved when possible.

    ``group_id`` is ``None`` when the dependency declares none; it keeps an
    unresolved ``${...}`` placeholder when no local POM defines the property.
    """

    group_id: str | None
    artifact_id: str
    scope: str | None


def _local_parent_pom(pom_path: Path, root: Element) -> tuple[Path, Element] | None:
    parent = root.find(f"{_MAVEN_NS}parent")
    if parent is None:
        parent = root.find("parent")
    if parent is None:
        return None
    relative = _pom_child_text(parent, "relativePath")
    candidate = pom_path.parent / (relative if relative is not None else "../pom.xml")
    if candidate.is_dir():
        candidate = candidate / "pom.xml"
    if not candidate.is_file():
        return None
    parent_root = parse_pom_root(candidate)
    if parent_root is None:
        return None
    if _pom_child_text(parent_root, "artifactId") != _pom_child_text(parent, "artifactId"):
        return None
    return candidate, parent_root


def _declared_group_id(root: Element) -> str | None:
    group_id = _pom_child_text(root, "groupId")
    if group_id is not None:
        return group_id
    parent = root.find(f"{_MAVEN_NS}parent")
    if parent is None:
        parent = root.find("parent")
    return _pom_child_text(parent, "groupId") if parent is not None else None


def _inherited_properties(pom_path: Path, root: Element) -> dict[str, str]:
    """Return the POM's properties merged over those of its local parents."""
    chain: list[tuple[Path, Element]] = [(pom_path, root)]
    seen = {pom_path.resolve()}
    while len(chain) < 16:
        parent_pom = _local_parent_pom(*chain[-1])
        if parent_pom is None or parent_pom[0].resolve() in seen:
            break
        seen.add(parent_pom[0].resolve())
        chain.append(parent_pom)
    properties: dict[str, str] = {}
    for path, element in reversed(chain):
        properties.update(_pom_properties(element, path))
    group_id = _declared_group_id(root)
    if group_id:
        properties.update({"groupId": group_id, "project.groupId": group_id, "pom.groupId": group_id})
    parent = root.find(f"{_MAVEN_NS}parent")
    if parent is None:
        parent = root.find("parent")
    if parent is not None:
        for tag in ("groupId", "version"):
            value = _pom_child_text(parent, tag)
            if value:
                properties.setdefault(f"parent.{tag}", value)
                properties[f"project.parent.{tag}"] = value
    return properties


def maven_group_id(pom_path: Path) -> str | None:
    """Return the module groupId, declared or inherited from ``<parent>``."""
    root = parse_pom_root(pom_path)
    if root is None:
        return None
    group_id = _declared_group_id(root)
    if group_id is None:
        return None
    return _resolve_maven_value(group_id, _inherited_properties(pom_path, root))


def maven_dependencies(pom_path: Path) -> tuple[MavenDependency, ...]:
    """Return the direct ``<dependencies>`` of a POM (not its management)."""
    root = parse_pom_root(pom_path)
    if root is None:
        return ()
    dependencies = root.find(f"{_MAVEN_NS}dependencies")
    if dependencies is None:
        dependencies = root.find("dependencies")
    if dependencies is None:
        return ()
    properties: dict[str, str] | None = None
    result: list[MavenDependency] = []
    for dependency in list(dependencies):
        artifact = _pom_child_text(dependency, "artifactId")
        if not artifact:
            continue
        group_id = _pom_child_text(dependency, "groupId")
        scope = _pom_child_text(dependency, "scope")
        if (group_id and "${" in group_id) or (scope and "${" in scope) or "${" in artifact:
            if properties is None:
                properties = _inherited_properties(pom_path, root)
            group_id = _resolve_maven_value(group_id, properties) if group_id else group_id
            scope = _resolve_maven_value(scope, properties) if scope else scope
            artifact = _resolve_maven_value(artifact, properties)
        result.append(MavenDependency(group_id=group_id, artifact_id=artifact, scope=scope))
    return tuple(result)
