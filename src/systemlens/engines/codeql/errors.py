"""Errors raised by the CodeQL engine, mapped onto the engine seam."""

import subprocess

from systemlens.callgraph.model import CallGraphError, CallGraphTimeout, MethodCall


class CodeQLError(CallGraphError):
    pass


class CodeQLTimeout(CallGraphTimeout, subprocess.TimeoutExpired):
    """A CodeQL deadline; ``calls`` stays empty, as no query result is partial."""

    def __init__(self, command: list[str], timeout: float, *, calls: list[MethodCall] | None = None):
        subprocess.TimeoutExpired.__init__(self, command, timeout)
        self.calls = calls or []
