from dataclasses import dataclass, field
from pathlib import Path

import yaml

from systemlens.infrastructure.paths import config_path, state_dir

DEFAULT_INCLUDE = ["**/*"]
DEFAULT_EXCLUDE = [".git/**", ".venv/**", "node_modules/**", ".systemlens/**"]
DEFAULT_MIN_SEVERITY = "INFO"
VALID_SEVERITIES = ("INFO", "WARNING", "ERROR")
VALID_TOPIC_STRATEGIES = ("default", "strategy1")


class ConfigError(Exception):
    pass


@dataclass
class Config:
    include: list[str] = field(default_factory=lambda: list(DEFAULT_INCLUDE))
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    min_severity: str = DEFAULT_MIN_SEVERITY
    topic_strategy: str = "default"
    codeql_enabled: bool = True
    codeql_max_hops: int = 12
    codeql_max_paths: int = 10_000
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
    topic_strategy = analysis.get("topic_strategy", "default")
    if topic_strategy not in VALID_TOPIC_STRATEGIES:
        raise ConfigError(f"analysis.topic_strategy invalide : {topic_strategy!r}.")
    codeql_enabled = analysis.get("codeql", True)
    if not isinstance(codeql_enabled, bool):
        raise ConfigError("analysis.codeql doit être un booléen.")
    codeql_max_hops = analysis.get("codeql_max_hops", 12)
    codeql_max_paths = analysis.get("codeql_max_paths", 10_000)
    if not isinstance(codeql_max_hops, int) or codeql_max_hops < 1:
        raise ConfigError("analysis.codeql_max_hops doit être un entier positif.")
    if not isinstance(codeql_max_paths, int) or codeql_max_paths < 1:
        raise ConfigError("analysis.codeql_max_paths doit être un entier positif.")

    return Config(
        include=list(raw.get("include", DEFAULT_INCLUDE)),
        exclude=list(raw.get("exclude", DEFAULT_EXCLUDE)),
        min_severity=min_severity,
        topic_strategy=topic_strategy,
        codeql_enabled=codeql_enabled,
        codeql_max_hops=codeql_max_hops,
        codeql_max_paths=codeql_max_paths,
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
            "topic_strategy": "default", "codeql": True,
            "codeql_max_hops": 12, "codeql_max_paths": 10_000,
            "disabled_extractors": [],
        },
    }
    path.write_text(yaml.dump(content, sort_keys=False))
    return path
