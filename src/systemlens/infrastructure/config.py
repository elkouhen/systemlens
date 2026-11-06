from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from systemlens.callgraph.engine import EngineSettings
from systemlens.callgraph.registry import (
    DEFAULT_ENGINE,
    NO_ENGINE,
    declaration,
    declarations,
    default_excludes,
    engine_names,
    init_profiles,
)
from systemlens.infrastructure.paths import config_path, state_dir

DEFAULT_INCLUDE = ["**/*"]
DEFAULT_EXCLUDE = [
    ".git/**", ".venv/**", "node_modules/**", ".systemlens/**", *default_excludes(),
]
DEFAULT_MIN_SEVERITY = "INFO"
VALID_SEVERITIES = ("INFO", "WARNING", "ERROR")
VALID_TOPIC_STRATEGIES = ("default", "strategy1")
VALID_EDGE_CONFIDENCES = ("exact", "possible")
DEFAULT_ENGINE_TIMEOUT_SECONDS = 600
DEFAULT_ENGINE_MAX_HOPS = 12
# Keys accepted under ``analysis``; any other key is an error, so a
# setting that no longer exists cannot be silently ignored.
ANALYSIS_KEYS = (
    "strategy",
    "topic_strategy",
    "call_graph_engine",
    "engine_timeout_seconds",
    "engine_threads",
    "engine_ram_mb",
    "engine_max_hops",
    "engine_edge_confidence",
    "engines",
    "disabled_extractors",
)


class ConfigError(Exception):
    pass


@dataclass
class Config:
    include: list[str] = field(default_factory=lambda: list(DEFAULT_INCLUDE))
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    min_severity: str = DEFAULT_MIN_SEVERITY
    strategy: str = "default"
    # The tree-sitter source engine needs no external tool (ADR-66); any
    # other declared engine is opt-in.
    call_graph_engine: str = DEFAULT_ENGINE
    # Generic bounds of every engine and of the call-graph join (ADR-85).
    engine_timeout_seconds: int = DEFAULT_ENGINE_TIMEOUT_SECONDS
    # Zero means one thread per available core.
    engine_threads: int = 0
    engine_ram_mb: int | None = None
    engine_max_hops: int = DEFAULT_ENGINE_MAX_HOPS
    engine_edge_confidence: str = "possible"
    # ``analysis.engines.<name>``: validated engine-owned options.
    engine_options: dict[str, dict[str, object]] = field(default_factory=dict)
    disabled_extractors: list[str] = field(default_factory=list)
    root_path: str | None = None
    # Explicit APM service name to indexed service name (ADR-60).
    runtime_service_map: dict[str, str] = field(default_factory=dict)
    # APM service names left out of reconciliation, such as OTel collectors.
    runtime_ignore_services: list[str] = field(default_factory=list)

    def engine_settings(
        self,
        name: str | None = None,
        *,
        repo_root: Path | None = None,
        overrides: Mapping[str, object] | None = None,
    ) -> EngineSettings:
        """Build the settings of engine ``name`` (default: the selected one).

        ``overrides`` replaces engine-owned options for one run, as the CLI
        does. A relative ``path`` option is resolved against ``repo_root``.
        """
        engine = name or self.call_graph_engine
        if engine == NO_ENGINE:
            return EngineSettings(threads=self.engine_threads, ram_mb=self.engine_ram_mb)
        declared = declaration(engine)
        options = {
            **declared.defaults(),
            **self.engine_options.get(engine, {}),
            **parse_engine_options(engine, overrides or {}, where="--engine-option "),
        }
        for option in declared.options:
            value = options.get(option.name)
            if option.kind == "path" and value is not None and repo_root is not None:
                path = Path(str(value)).expanduser()
                options[option.name] = path if path.is_absolute() else repo_root / path
        return EngineSettings(
            threads=self.engine_threads, ram_mb=self.engine_ram_mb, options=options,
        )


def parse_engine_options(
    engine: str, values: Mapping[str, object], *, where: str,
) -> dict[str, object]:
    """Validate engine-owned option values against the engine's declaration."""
    try:
        declared = declaration(engine)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    parsed: dict[str, object] = {}
    for name, value in values.items():
        try:
            parsed[str(name)] = declared.option(str(name)).parse(value)
        except ValueError as exc:
            raise ConfigError(f"{where}{engine}.{name} invalide : {exc}.") from exc
    return parsed


def _positive_int(analysis: Mapping[str, object], key: str, default: int) -> int:
    value = analysis.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"analysis.{key} doit être un entier positif.")
    return value


def _engine_options(analysis: Mapping[str, object]) -> dict[str, dict[str, object]]:
    section = analysis.get("engines") or {}
    if not isinstance(section, dict):
        raise ConfigError("analysis.engines doit être un objet YAML.")
    known = [item.name for item in declarations()]
    result: dict[str, dict[str, object]] = {}
    for engine, values in section.items():
        if engine not in known:
            raise ConfigError(
                f"analysis.engines.{engine} : moteur inconnu. Valeurs : {', '.join(known)}."
            )
        if values is None:
            values = {}
        if not isinstance(values, dict):
            raise ConfigError(f"analysis.engines.{engine} doit être un objet YAML.")
        result[engine] = parse_engine_options(engine, values, where="analysis.engines.")
    return result


def load_config(repo_root: Path) -> Config:
    path = config_path(repo_root)
    if not path.is_file():
        raise ConfigError(
            f"Fichier de configuration introuvable : {path}. "
            "Lancez d'abord: systemlens init"
        )

    raw = yaml.safe_load(path.read_text()) or {}

    min_severity = raw.get("min_severity", DEFAULT_MIN_SEVERITY)
    if min_severity not in VALID_SEVERITIES:
        raise ConfigError(
            f"min_severity invalide : {min_severity!r}. "
            f"Valeurs autorisées : {VALID_SEVERITIES}."
        )
    analysis = raw.get("analysis", {})
    if not isinstance(analysis, dict):
        raise ConfigError("analysis doit être un objet YAML.")
    unknown_keys = sorted(str(key) for key in analysis if key not in ANALYSIS_KEYS)
    if unknown_keys:
        raise ConfigError(
            f"Clé(s) inconnue(s) dans analysis : {', '.join(unknown_keys)}. "
            f"Clés autorisées : {', '.join(ANALYSIS_KEYS)}."
        )
    if "strategy" in analysis and "topic_strategy" in analysis:
        raise ConfigError(
            "analysis.strategy et analysis.topic_strategy ne peuvent pas être utilisés ensemble."
        )
    strategy_key = "strategy" if "strategy" in analysis else "topic_strategy"
    strategy = analysis.get(strategy_key, "default")
    if strategy not in VALID_TOPIC_STRATEGIES:
        raise ConfigError(f"analysis.{strategy_key} invalide : {strategy!r}.")
    call_graph_engine = analysis.get("call_graph_engine", DEFAULT_ENGINE)
    if call_graph_engine not in engine_names():
        raise ConfigError(
            f"analysis.call_graph_engine invalide : {call_graph_engine!r}. "
            f"Valeurs autorisées : {', '.join(engine_names())}."
        )
    engine_timeout_seconds = _positive_int(
        analysis, "engine_timeout_seconds", DEFAULT_ENGINE_TIMEOUT_SECONDS,
    )
    engine_max_hops = _positive_int(analysis, "engine_max_hops", DEFAULT_ENGINE_MAX_HOPS)
    engine_threads = analysis.get("engine_threads", 0)
    if isinstance(engine_threads, bool) or not isinstance(engine_threads, int) or engine_threads < 0:
        raise ConfigError("analysis.engine_threads doit être un entier supérieur ou égal à zéro.")
    engine_ram_mb = analysis.get("engine_ram_mb")
    if engine_ram_mb is not None and (
        isinstance(engine_ram_mb, bool) or not isinstance(engine_ram_mb, int) or engine_ram_mb < 1
    ):
        raise ConfigError("analysis.engine_ram_mb doit être un entier positif ou null.")
    engine_edge_confidence = analysis.get("engine_edge_confidence", "possible")
    if engine_edge_confidence not in VALID_EDGE_CONFIDENCES:
        raise ConfigError(
            "analysis.engine_edge_confidence invalide : "
            f"{engine_edge_confidence!r}. Valeurs : {', '.join(VALID_EDGE_CONFIDENCES)}."
        )
    engine_options = _engine_options(analysis)
    runtime = raw.get("runtime") or {}
    if not isinstance(runtime, dict):
        raise ConfigError("runtime doit être un objet YAML.")
    runtime_service_map = runtime.get("service_map") or {}
    if not isinstance(runtime_service_map, dict) or not all(
        isinstance(name, str) and name.strip() and isinstance(service, str) and service.strip()
        for name, service in runtime_service_map.items()
    ):
        raise ConfigError(
            "runtime.service_map doit associer des noms de service APM à des services indexés."
        )
    runtime_ignore_services = runtime.get("ignore_services") or []
    if not isinstance(runtime_ignore_services, list) or not all(
        isinstance(name, str) and name.strip() for name in runtime_ignore_services
    ):
        raise ConfigError("runtime.ignore_services doit être une liste de noms de service APM.")

    return Config(
        include=list(raw.get("include", DEFAULT_INCLUDE)),
        exclude=list(raw.get("exclude", DEFAULT_EXCLUDE)),
        min_severity=min_severity,
        strategy=strategy,
        call_graph_engine=call_graph_engine,
        engine_timeout_seconds=engine_timeout_seconds,
        engine_threads=engine_threads,
        engine_ram_mb=engine_ram_mb,
        engine_max_hops=engine_max_hops,
        engine_edge_confidence=engine_edge_confidence,
        engine_options=engine_options,
        disabled_extractors=list(analysis.get("disabled_extractors", [])),
        root_path=raw.get("root_path"),
        runtime_service_map={
            name.strip(): service.strip() for name, service in runtime_service_map.items()
        },
        runtime_ignore_services=[name.strip() for name in runtime_ignore_services],
    )


# ``init`` profiles: ``default`` needs no external tool (ADR-66); every
# engine that declares a profile can be selected for all indexes at once.
INIT_PROFILES = init_profiles()


def init_config(repo_root: Path, profile: str = "default") -> Path:
    if profile not in INIT_PROFILES:
        raise ConfigError(
            f"Profil d'initialisation invalide : {profile!r}. Valeurs : {', '.join(INIT_PROFILES)}."
        )
    path = config_path(repo_root)
    if path.exists():
        raise ConfigError(f"Une configuration existe déjà : {path}.")

    state_dir(repo_root).mkdir(parents=True, exist_ok=True)
    engine = DEFAULT_ENGINE if profile == "default" else profile
    engine_section = declaration(engine).defaults()
    analysis: dict[str, object] = {
        "strategy": "default",
        "call_graph_engine": engine,
        "engine_timeout_seconds": DEFAULT_ENGINE_TIMEOUT_SECONDS,
        "engine_threads": 0,
        "engine_ram_mb": None,
        "engine_max_hops": DEFAULT_ENGINE_MAX_HOPS,
        "engine_edge_confidence": "possible",
    }
    if engine_section:
        analysis["engines"] = {engine: engine_section}
    analysis["disabled_extractors"] = []
    content = {
        "include": DEFAULT_INCLUDE,
        "exclude": DEFAULT_EXCLUDE,
        "min_severity": DEFAULT_MIN_SEVERITY,
        "root_path": ".",
        "analysis": analysis,
    }
    path.write_text(yaml.dump(content, sort_keys=False))
    return path
