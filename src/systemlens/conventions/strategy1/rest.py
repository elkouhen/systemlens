"""REST target conventions supplied by Strategy1."""

import re

from systemlens.domain.models import MessageEndpoint


_SERVICE_URL_GETTER_RE = re.compile(r"\.get([A-Z][A-Za-z0-9]*)ServiceUrl\(")
_EXTERNAL_MICROSERVICE_RE = re.compile(r"systemlens-external-microservice:([^\s]+)")


def _camel_to_kebab(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", value).lower()


def rest_target_service_hint(endpoint: MessageEndpoint) -> str | None:
    """Resolve the Strategy1 ``getXxxServiceUrl()`` target convention."""
    match = _SERVICE_URL_GETTER_RE.search(endpoint.snippet)
    return f"{_camel_to_kebab(match.group(1))}-service" if match else None


def external_service_name(endpoint: MessageEndpoint) -> str | None:
    """Return an explicitly annotated external Strategy1 service name."""
    match = _EXTERNAL_MICROSERVICE_RE.search(endpoint.snippet)
    return match.group(1).lower() if match else None
