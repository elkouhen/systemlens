from dataclasses import dataclass, field
from pathlib import Path

import yaml

from systemlens.infrastructure.paths import config_path, state_dir

DEFAULT_INCLUDE = ["**/*"]
DEFAULT_EXCLUDE = [".git/**", ".venv/**", "node_modules/**", ".systemlens/**"]
DEFAULT_MIN_SEVERITY = "INFO"
VALID_SEVERITIES = ("INFO", "WARNING", "ERROR")
VALID_TOPIC_STRATEGIES = ("default", "strategy1")
VALID_CALL_GRAPH_ENGINES = ("codeql", "none")
VALID_CODEQL_EDGE_CONFIDENCES = ("exact", "possible")
VALID_CODEQL_VERBOSITIES = ("errors", "progress", "progress+", "progress++", "progress+++")


class ConfigError(Exception):
    pass


@dataclass
class Config:
    include: list[str] = field(default_factory=lambda: list(DEFAULT_INCLUDE))
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    min_severity: str = DEFAULT_MIN_SEVERITY
    strategy: str = "default"
    codeql_enabled: bool = True
    call_graph_engine: str = "codeql"
    codeql_timeout_seconds: int = 600
    # CodeQL interprets zero as one thread per available core.
    codeql_threads: int = 0
    codeql_ram_mb: int | None = None
    codeql_verbosity: str | None = None
    codeql_max_hops: int = 12
    codeql_edge_confidence: str = "possible"
    disabled_extractors: list[str] = field(default_factory=list)
    root_path: str | None = None


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
    if "strategy" in analysis and "topic_strategy" in analysis:
        raise ConfigError(
            "analysis.strategy et analysis.topic_strategy ne peuvent pas être utilisés ensemble."
        )
    strategy_key = "strategy" if "strategy" in analysis else "topic_strategy"
    strategy = analysis.get(strategy_key, "default")
    if strategy not in VALID_TOPIC_STRATEGIES:
        raise ConfigError(f"analysis.{strategy_key} invalide : {strategy!r}.")
    codeql_enabled = analysis.get("codeql", True)
    if not isinstance(codeql_enabled, bool):
        raise ConfigError("analysis.codeql doit être un booléen.")
    call_graph_engine = analysis.get(
        "call_graph_engine", "codeql" if codeql_enabled else "none"
    )
    if call_graph_engine not in VALID_CALL_GRAPH_ENGINES:
        raise ConfigError(
            f"analysis.call_graph_engine invalide : {call_graph_engine!r}. "
            f"Valeurs autorisées : {', '.join(VALID_CALL_GRAPH_ENGINES)}."
        )
    codeql_timeout_seconds = analysis.get("codeql_timeout_seconds", 600)
    codeql_threads = analysis.get("codeql_threads", 0)
    codeql_ram_mb = analysis.get("codeql_ram_mb")
    codeql_verbosity = analysis.get("codeql_verbosity")
    codeql_max_hops = analysis.get("codeql_max_hops", 12)
    codeql_edge_confidence = analysis.get("codeql_edge_confidence", "possible")
    if not isinstance(codeql_max_hops, int) or codeql_max_hops < 1:
        raise ConfigError("analysis.codeql_max_hops doit être un entier positif.")
    if codeql_edge_confidence not in VALID_CODEQL_EDGE_CONFIDENCES:
        raise ConfigError(
            "analysis.codeql_edge_confidence invalide : "
            f"{codeql_edge_confidence!r}. Valeurs : {', '.join(VALID_CODEQL_EDGE_CONFIDENCES)}."
        )
    if not isinstance(codeql_timeout_seconds, int) or codeql_timeout_seconds < 1:
        raise ConfigError("analysis.codeql_timeout_seconds doit être un entier positif.")
    if isinstance(codeql_threads, bool) or not isinstance(codeql_threads, int) or codeql_threads < 0:
        raise ConfigError("analysis.codeql_threads doit être un entier supérieur ou égal à zéro.")
    if codeql_ram_mb is not None and (
        isinstance(codeql_ram_mb, bool) or not isinstance(codeql_ram_mb, int) or codeql_ram_mb < 1
    ):
        raise ConfigError("analysis.codeql_ram_mb doit être un entier positif ou null.")
    if codeql_verbosity is not None and codeql_verbosity not in VALID_CODEQL_VERBOSITIES:
        raise ConfigError(
            "analysis.codeql_verbosity invalide : "
            f"{codeql_verbosity!r}. Valeurs : {', '.join(VALID_CODEQL_VERBOSITIES)}."
        )

    return Config(
        include=list(raw.get("include", DEFAULT_INCLUDE)),
        exclude=list(raw.get("exclude", DEFAULT_EXCLUDE)),
        min_severity=min_severity,
        strategy=strategy,
        codeql_enabled=codeql_enabled,
        call_graph_engine=call_graph_engine,
        codeql_timeout_seconds=codeql_timeout_seconds,
        codeql_threads=codeql_threads,
        codeql_ram_mb=codeql_ram_mb,
        codeql_verbosity=codeql_verbosity,
        codeql_max_hops=codeql_max_hops,
        codeql_edge_confidence=codeql_edge_confidence,
        disabled_extractors=list(analysis.get("disabled_extractors", [])),
        root_path=raw.get("root_path"),
    )


def init_config(repo_root: Path) -> Path:
    path = config_path(repo_root)
    if path.exists():
        raise ConfigError(f"Une configuration existe déjà : {path}.")

    state_dir(repo_root).mkdir(parents=True, exist_ok=True)
    content = {
        "include": DEFAULT_INCLUDE,
        "exclude": DEFAULT_EXCLUDE,
        "min_severity": DEFAULT_MIN_SEVERITY,
        "root_path": ".",
        "analysis": {
            "strategy": "default", "codeql": True, "call_graph_engine": "codeql",
            "codeql_timeout_seconds": 600,
            "codeql_threads": 0, "codeql_ram_mb": None,
            "codeql_verbosity": None,
            "codeql_max_hops": 12,
            "codeql_edge_confidence": "possible",
            "disabled_extractors": [],
        },
    }
    path.write_text(yaml.dump(content, sort_keys=False))
    return path
