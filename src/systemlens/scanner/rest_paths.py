"""Pure REST route normalization helpers."""

from __future__ import annotations

import re
from urllib.parse import urlsplit


_MULTI_SLASH_RE = re.compile(r"/{2,}")


def with_query_params(route: str, params: list[str]) -> str:
    if not params or route == "<dynamic>":
        return route
    return f"{route}?{'&'.join(params)}"


def join_paths(prefix: str, suffix: str) -> str:
    """Join normalized paths without treating a double slash as a URL host."""
    segments = [s for s in (prefix.strip("/"), suffix.strip("/")) if s]
    return "/" + "/".join(segments) if segments else "/"


def normalize_path(literal: str) -> str:
    normalized = literal.strip()
    if not normalized:
        return "/"
    if normalized.startswith("//"):
        normalized = urlsplit(f"http:{normalized}").path or "/"
    elif "://" in normalized:
        normalized = urlsplit(normalized).path or "/"
    normalized = normalized.split("?", 1)[0].split("#", 1)[0]
    normalized = _MULTI_SLASH_RE.sub("/", normalized)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized or "/"
