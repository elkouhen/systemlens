"""Résolution de configuration Spring (properties/YAML/`@Value`) (BACKLOG-16).

Découvre et aplatit les fichiers `application*.yml/properties` (y compris
profils et config-server local), résout une clé de propriété Spring
(``${...}``) via ces fichiers ou une valeur par défaut, et résout la valeur
d'un champ Java annoté ``@Value`` en remontant vers la même résolution de
propriétés. Utilisé par l'inférence REST (chemins) comme par l'inférence
Kafka (topics)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import fnmatch
import re

import yaml

from systemlens.discovery.java import parser as java_parser

_SPRING_BASE_FILENAMES = (
    "kafka.yml",
    "kafka.yaml",
    "application.yml",
    "application.yaml",
    "application.properties",
    "bootstrap.yml",
    "bootstrap.yaml",
    "bootstrap.properties",
)
_SPRING_PROFILE_PATTERNS = (
    "application-*.yml",
    "application-*.yaml",
    "application-*.properties",
    "bootstrap-*.yml",
    "bootstrap-*.yaml",
    "bootstrap-*.properties",
)
_SPRING_CLOUD_CONFIG_DIR_PATTERNS = (
    "src/main/resources/configurations",
    "configurations",
)
_STRATEGY1_KAFKA_PREFIX_RE = re.compile(r"^\$\{[^}]+\}\.")


def normalize_strategy1_kafka_key(value: str) -> str:
    """Normalize a Strategy1 logical Kafka key for code/config matching."""
    separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", separated)
    separated = re.sub(r"([A-Za-z])([0-9])", r"\1_\2", separated)
    return re.sub(r"-+", "-", re.sub(r"[-_.\s]+", "-", separated)).strip("-").casefold()


def _flatten_properties(data: object, prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            full_key = f"{prefix}.{key}" if prefix else str(key)
            flat.update(_flatten_properties(value, full_key))
    elif isinstance(data, (str, int, float, bool)):
        flat[prefix] = str(data)
    return flat
def _parse_dotted_properties_file(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("!"):
            continue
        sep_index = min(
            (i for i in (stripped.find("="), stripped.find(":")) if i != -1), default=-1
        )
        if sep_index == -1:
            continue
        key, value = stripped[:sep_index], stripped[sep_index + 1 :]
        result[key.strip()] = value.strip()
    return result
def _candidate_spring_roots(repo_root: Path, source_path: str | None) -> list[Path]:
    # The source path is resolved below; the root must be too, or a relative
    # or symlinked root never matches the resolved ancestors.
    repo_root = repo_root.resolve()
    if source_path is None:
        return [repo_root]
    source_abs = (repo_root / source_path).resolve()
    roots: list[Path] = []
    for candidate in [source_abs.parent, *source_abs.parents]:
        if candidate == repo_root or repo_root in candidate.parents:
            roots.append(candidate)
        if candidate == repo_root:
            break
    if repo_root not in roots:
        roots.append(repo_root)
    return roots
def _discover_spring_property_files(
    repo_root_str: str, source_path: str | None
) -> tuple[str, ...]:
    repo_root = Path(repo_root_str)
    discovered: list[str] = []
    seen: set[Path] = set()

    for root in _candidate_spring_roots(repo_root, source_path):
        for config_dir in (root / "src" / "main" / "resources", root):
            for filename in _SPRING_BASE_FILENAMES:
                candidate = config_dir / filename
                if candidate.is_file() and candidate not in seen:
                    seen.add(candidate)
                    discovered.append(str(candidate))
            for pattern in _SPRING_PROFILE_PATTERNS:
                for candidate in sorted(config_dir.glob(pattern)):
                    if candidate.is_file() and candidate not in seen:
                        seen.add(candidate)
                        discovered.append(str(candidate))
    for candidate in _discover_spring_cloud_config_files(repo_root, source_path):
        if candidate not in seen:
            seen.add(candidate)
            discovered.append(str(candidate))
    return tuple(discovered)
def _local_spring_application_names(repo_root: Path, source_path: str | None) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for root in _candidate_spring_roots(repo_root, source_path):
        for config_dir in (root / "src" / "main" / "resources", root):
            for filename in _SPRING_BASE_FILENAMES:
                candidate = config_dir / filename
                if not candidate.is_file():
                    continue
                name = _load_flat_spring_properties(str(candidate)).get("spring.application.name")
                if name and name not in seen:
                    seen.add(name)
                    names.append(name)
            for pattern in _SPRING_PROFILE_PATTERNS:
                for candidate in sorted(config_dir.glob(pattern)):
                    if not candidate.is_file():
                        continue
                    name = _load_flat_spring_properties(str(candidate)).get("spring.application.name")
                    if name and name not in seen:
                        seen.add(name)
                        names.append(name)
    return tuple(names)
def _discover_spring_cloud_config_files(
    repo_root: Path, source_path: str | None
) -> tuple[Path, ...]:
    discovered: list[Path] = []
    seen: set[Path] = set()
    for app_name in _local_spring_application_names(repo_root, source_path):
        for config_dir_pattern in _SPRING_CLOUD_CONFIG_DIR_PATTERNS:
            for suffix in (".yml", ".yaml", ".properties"):
                for candidate in sorted(
                    repo_root.glob(f"**/{config_dir_pattern}/{app_name}{suffix}")
                ):
                    if candidate.is_file() and candidate not in seen:
                        seen.add(candidate)
                        discovered.append(candidate)
    return tuple(discovered)


def _document_profile(data: object) -> str | None:
    """Profile a YAML document is activated by, or ``None`` for a base one.

    Spring Boot reads ``spring.config.activate.on-profile``, and before 2.4
    ``spring.profiles``.
    """
    if not isinstance(data, dict):
        return None
    flat = _flatten_properties(data)
    profile = flat.get("spring.config.activate.on-profile") or flat.get("spring.profiles")
    return profile or None


@lru_cache(maxsize=512)
def _load_spring_property_documents(
    path_str: str,
) -> tuple[dict[str, str], tuple[dict[str, str], ...]]:
    """Base and profile-specific declarations of one property file.

    A multi-document YAML file holds a base document and documents activated
    by a profile (BC-31). Indexing has no active profile, so the two are kept
    apart: the caller applies the "all profiles agree" rule to the latter.
    A ``.properties`` file is one base document.
    """
    path = Path(path_str)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}, ()

    if path.suffix == ".properties":
        return _parse_dotted_properties_file(text), ()
    try:
        # ``safe_load_all`` returns a lazy generator: materialize it here so
        # parser failures from later YAML documents (notably Helm templates)
        # remain inside the protected boundary.
        documents = list(yaml.safe_load_all(text))
    except (yaml.YAMLError, ValueError, RecursionError):
        # PyYAML raises a plain ``ValueError`` for an impossible timestamp
        # such as ``2023-02-30`` and may exhaust the stack on deep nesting.
        return {}, ()
    base: dict[str, str] = {}
    profiles: list[dict[str, str]] = []
    for data in documents:
        flat = _flatten_properties(data or {})
        if _document_profile(data) is None:
            for key, value in flat.items():
                base.setdefault(key, value)
        else:
            profiles.append(flat)
    return base, tuple(profiles)


def _declared_values(path_str: str, key: str) -> tuple[str | None, tuple[str, ...]]:
    """Base value and distinct profile values of ``key`` in one file.

    A key under ``kafka.`` also matches its unprefixed spelling.
    """
    base, profiles = _load_spring_property_documents(path_str)
    keys = (key, key[6:]) if key.casefold().startswith("kafka.") else (key,)
    base_value = next((base[k] for k in keys if k in base), None)
    profile_values: list[str] = []
    for document in profiles:
        value = next((document[k] for k in keys if k in document), None)
        if value is not None and value not in profile_values:
            profile_values.append(value)
    return base_value, tuple(profile_values)


@lru_cache(maxsize=512)
def _load_flat_spring_properties(path_str: str) -> dict[str, str]:
    """One file's properties, without choosing a profile.

    A base document wins. A key declared only by profile documents is kept
    when they all agree; conflicting profile values are left out, since no
    active profile is known (BC-31).
    """
    base, profiles = _load_spring_property_documents(path_str)
    flat = dict(base)
    profile_values: dict[str, set[str]] = {}
    for document in profiles:
        for key, value in document.items():
            if key not in base:
                profile_values.setdefault(key, set()).add(value)
    for key, values in profile_values.items():
        if len(values) == 1:
            flat[key] = next(iter(values))
    return flat
_NESTED_PLACEHOLDER_RE = re.compile(r"\$\{([^${}]+)\}")
_MAX_PLACEHOLDER_DEPTH = 5


def _is_profile_property_file(path_str: str) -> bool:
    name = Path(path_str).name
    return any(fnmatch.fnmatch(name, pattern) for pattern in _SPRING_PROFILE_PATTERNS)


def _lookup_spring_property(
    repo_root: Path, key: str, source_path: str | None
) -> tuple[str | None, bool]:
    """Return the declared value of ``key`` and whether it is ambiguous.

    Files are visited from the owning module outwards. A base declaration
    answers at once. Profile declarations (an ``application-<profile>``
    file, or a YAML document activated by ``on-profile``) answer only when
    every profile that declares the key agrees: indexing has no active
    profile. Conflicting declarations are ambiguous (BC-31): the caller
    keeps the value dynamic and never falls back to a placeholder default.
    """
    profile_values: list[str] = []
    for path_str in _discover_spring_property_files(str(repo_root), source_path):
        base_value, file_profile_values = _declared_values(path_str, key)
        if _is_profile_property_file(path_str):
            file_profile_values = (
                ((base_value,) if base_value is not None else ()) + file_profile_values
            )
            base_value = None
        if base_value is not None:
            if not profile_values:
                return base_value, False
            break
        for value in file_profile_values:
            if value not in profile_values:
                profile_values.append(value)
    if len(profile_values) == 1:
        return profile_values[0], False
    return None, len(profile_values) > 1


def resolve_spring_property(
    repo_root: Path, property_key: str, source_path: str | None = None,
    *, allow_empty: bool = False, _depth: int = 0,
) -> str | None:
    """Cherche `property_key` (ex. `app.kafka.topics.orders`, ou
    `prop:default` — syntaxe de valeur par défaut Spring) dans les fichiers
    de configuration Spring Boot conventionnels du repo. La recherche est
    best-effort mais orientée microservice : on essaie d'abord les configs du
    module contenant `source_path`, puis celles du repo parent ; les fichiers
    sont parsés une seule fois par process via cache.

    A value containing ``${...}`` placeholders is expanded recursively, with
    a bounded depth. A placeholder that cannot be resolved stays in the
    returned text: callers treat a value still containing ``${`` as a
    configuration key, not as a static value.

    An empty default (``${key:}``) is the empty string only with
    ``allow_empty``: a route prefix may be empty, a topic may not (BC-33).
    """
    key, separator, default = property_key.partition(":")
    value, ambiguous = _lookup_spring_property(repo_root, key.strip(), source_path)
    if ambiguous:
        # Profiles disagree: the default is not the value in any of them.
        return None
    if value is None:
        value = default if separator and (default or allow_empty) else None
    if value is None or "${" not in value or _depth >= _MAX_PLACEHOLDER_DEPTH:
        return value
    expanded, _complete = expand_placeholders(
        value, repo_root, source_path, allow_empty=allow_empty, _depth=_depth + 1,
    )
    return expanded


def expand_placeholders(
    text: str, repo_root: Path, source_path: str | None, *,
    allow_empty: bool = False, unresolved: str | None = None, _depth: int = 0,
) -> tuple[str, bool]:
    """Substitute ``${...}`` placeholders, innermost first, until stable.

    A nested default such as ``${a:${b}}`` resolves ``${b}`` first and then
    ``${a:<value>}`` (BC-33). An unresolved placeholder is replaced by
    ``unresolved`` when given, else kept, which also blocks its enclosing
    placeholder. Returns the text and whether every placeholder resolved.
    """
    complete = True
    for _ in range(_MAX_PLACEHOLDER_DEPTH):
        changed = False

        def substitute(match: re.Match[str]) -> str:
            nonlocal changed, complete
            resolved = resolve_spring_property(
                repo_root, match.group(1), source_path,
                allow_empty=allow_empty, _depth=_depth,
            )
            if resolved is None or "${" in resolved:
                complete = False
                if unresolved is None:
                    return match.group(0)
                changed = True
                return unresolved
            changed = True
            return resolved

        text = _NESTED_PLACEHOLDER_RE.sub(substitute, text)
        if not changed or "${" not in text:
            break
    return text, complete and "${" not in text


def resolve_strategy1_kafka_topic(
    repo_root: Path, logical_key: str, source_path: str | None = None
) -> str | None:
    """Resolve a Strategy1 logical topic key from a declared Kafka YAML value.

    ``kafka.yml`` files expose a nested topic object whose ``nom`` leaf
    contains the broker topic. Matching is limited to a unique topic-shaped key so an
    unrelated YAML setting cannot become a Kafka endpoint by coincidence.
    """
    catalog = strategy1_kafka_topics(repo_root, source_path)
    if catalog is None:
        return None
    return catalog.get(normalize_strategy1_kafka_key(logical_key))


def strategy1_kafka_topics(
    repo_root: Path, source_path: str | None = None
) -> dict[str, str] | None:
    """Load the unique logical-to-broker topic declarations from kafka YAML."""
    values_by_key: dict[str, set[str]] = {}
    found_file = False
    if source_path is None:
        property_files = tuple(
            path
            for path in repo_root.rglob("*")
            if path.is_file()
            and path.name.casefold() in {"kafka.yml", "kafka.yaml"}
            and "target" not in path.parts
            and "build" not in path.parts
            and not any(
                path.parts[index:index + 2] == ("src", "test")
                for index in range(len(path.parts) - 1)
            )
        )
    else:
        property_files = tuple(
            Path(path)
            for path in _discover_spring_property_files(str(repo_root), source_path)
            if Path(path).name.casefold() in {"kafka.yml", "kafka.yaml"}
        )
    for path in property_files:
        found_file = True
        flat = _load_flat_spring_properties(str(path))
        for property_key, value in flat.items():
            parts = property_key.split(".")
            if "topics" not in {part.casefold() for part in parts}:
                continue
            if parts[-1].casefold() != "nom" or len(parts) < 2:
                continue
            logical_key = normalize_strategy1_kafka_key(parts[-2])
            declared_value = remove_strategy1_kafka_prefix(value)
            if declared_value:
                values_by_key.setdefault(logical_key, set()).add(declared_value)
    if not found_file:
        return None
    return {
        logical_key: next(iter(values))
        for logical_key, values in values_by_key.items()
        if len(values) == 1
    }


def remove_strategy1_kafka_prefix(value: str) -> str:
    """Remove the variable Kafka topic prefix from a declared topic value."""
    return _STRATEGY1_KAFKA_PREFIX_RE.sub("", value.strip(), count=1)


@lru_cache(maxsize=512)
def _load_value_annotated_fields(path_str: str) -> dict[str, str]:
    """Champs `@Value("${clé}")` d'un fichier source Java — variable ->
    clé de propriété (avec éventuel `:défaut`, laissé tel quel pour
    `resolve_spring_property`). Extrait via l'AST tree-sitter : pour chaque
    `field_declaration` annotée `@Value("${...}")`, on associe le nom du
    champ à la clé de propriété (sans les `${ }`)."""
    path = Path(path_str)
    try:
        source = path.read_bytes()
    except OSError:
        return {}
    root = java_parser.java_parser("value_fields").parse(source).root_node
    if root.has_error:
        return {}
    fields: dict[str, str] = {}
    for node in java_parser.walk(root):
        if node.type != "field_declaration":
            continue
        value_ann = next(
            (
                ann
                for ann in java_parser.annotations_of(node)
                if java_parser.annotation_name(ann, source) == "Value"
            ),
            None,
        )
        if value_ann is None:
            continue
        raw = java_parser.first_string_argument(value_ann, source)
        if raw is None or not (raw.startswith("${") and raw.endswith("}")):
            continue
        property_key = raw[2:-1]
        for declarator in node.children:
            if declarator.type != "variable_declarator":
                continue
            name_node = declarator.child_by_field_name("name")
            if name_node is None:
                continue
            fields[java_parser.node_text(source, name_node)] = property_key
    return fields


@lru_cache(maxsize=512)
def _declared_field_names(path_str: str) -> frozenset[str]:
    """Names of every field a Java source file declares, annotated or not."""
    try:
        source = Path(path_str).read_bytes()
    except OSError:
        return frozenset()
    root = java_parser.java_parser("value_fields").parse(source).root_node
    names: set[str] = set()
    for node in java_parser.walk(root):
        if node.type not in {"field_declaration", "constant_declaration"}:
            continue
        for declarator in node.children:
            if declarator.type != "variable_declarator":
                continue
            name_node = declarator.child_by_field_name("name")
            if name_node is not None:
                names.add(java_parser.node_text(source, name_node))
    return frozenset(names)


def _is_test_source(parts: tuple[str, ...]) -> bool:
    return any(parts[index:index + 2] == ("src", "test") for index in range(len(parts) - 1))


@lru_cache(maxsize=64)
def _load_unique_value_annotated_fields(repo_root_str: str) -> dict[str, str | None]:
    """Index globally unique ``@Value`` field names for cross-file lookup.

    A field reference may be used from a class different from the one that
    declares the injected value, especially when a WebClient is configured in
    one bean and consumed in another.  A name is usable only when every source
    declaration found for it resolves to the same property key.  Conflicting
    declarations remain unresolved rather than selecting one by filename.
    Test sources are left out: a test's ``@Value`` never configures the
    application (BC-32).
    """
    repo_root = Path(repo_root_str)
    candidates: dict[str, set[str]] = {}
    for path in repo_root.rglob("*.java"):
        try:
            relative = path.relative_to(repo_root)
        except ValueError:
            continue
        if {"target", "build"}.intersection(relative.parts) or _is_test_source(relative.parts):
            continue
        for name, property_key in _load_value_annotated_fields(str(path)).items():
            candidates.setdefault(name, set()).add(property_key)
    return {
        name: next(iter(keys)) if len(keys) == 1 else None
        for name, keys in candidates.items()
    }


def _resolve_value_annotated_variable(
    repo_root: Path, source_path: str, var_name: str
) -> str | None:
    """Resolve ``var_name`` as an ``@Value`` field of ``source_path``.

    A field the file declares is resolved from that file only: a plain
    field, or a constant, never borrows another class's ``@Value`` of the
    same name (BC-32). Only a name the file does not declare, such as a
    field of a configuration bean, may use the repository-wide table.
    """
    path_str = str(repo_root / source_path)
    fields = _load_value_annotated_fields(path_str)
    property_key = fields.get(var_name)
    if property_key is None and var_name not in _declared_field_names(path_str):
        property_key = _load_unique_value_annotated_fields(str(repo_root.resolve())).get(var_name)
    if property_key is None:
        return None
    return resolve_spring_property(repo_root, property_key, source_path)


# Certains microservices ne portent pas l'URL de destination au site d'appel :
# le client HTTP est injecté et construit dans une configuration `Rest*Config*`.
# Dans cette
# convention, un `@Bean` délègue à un helper auquel est passé le domaine qui
# publie l'API. On conserve ce domaine dans l'évidence de l'endpoint pour que
# le graphe puisse restreindre la cible, sans prétendre résoudre une URL.
