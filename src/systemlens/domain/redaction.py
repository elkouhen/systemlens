"""Removal of credentials from URL text kept as evidence (invariant 5).

An endpoint snippet quotes the source line or YAML value it comes from. A
URI there may carry ``user:password@`` userinfo or a secret query value
such as ``?token=...``. :func:`redact_url_secrets` strips the userinfo and
masks those values, so no persisted column or export repeats them.
"""

import re

REDACTED = "<redacted>"

# ``scheme://userinfo@``: the userinfo cannot hold a delimiter of the
# authority, a quote, or whitespace.
_USERINFO_RE = re.compile(
    r"(\b[a-z][a-z0-9+.-]*://)[^/\s@?#\"'`<>]+@", re.IGNORECASE
)
# A query or matrix parameter whose name holds a sensitive word. A value
# that is a placeholder (``${...}``) or a URI template (``{...}``) names no
# secret and is kept, so property keys stay readable.
_SECRET_PARAMETER_RE = re.compile(
    r"([?&;][^=&\s\"'`#?;]*?"
    r"(?:token|key|secret|password|passwd|pwd|credential|signature|sig|auth)"
    r"[^=&\s\"'`#?;]*=)(?![$]?\{)[^&\s\"'`#;)]+",
    re.IGNORECASE,
)


def redact_url_secrets(text: str) -> str:
    """Return ``text`` without URL userinfo and secret query values."""
    if "://" not in text and "=" not in text:
        return text
    text = _USERINFO_RE.sub(r"\1", text)
    return _SECRET_PARAMETER_RE.sub(lambda match: match.group(1) + REDACTED, text)
