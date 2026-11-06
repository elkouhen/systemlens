"""Decode the Joern export (``export.sc``) into engine-neutral facts.

The export lists every internal method and every non-operator call with
the methods Joern's call graph links it to. This module keeps only
source-to-source calls and never guesses a target (ADR-61, ADR-85):

- a call Joern could not resolve (``<unresolvedNamespace>``,
  ``<unresolvedSignature>``, ``ANY``) is dropped;
- a callee without a source file, a constructor or initializer, a lambda
  or a method of an anonymous class is dropped, as the CodeQL query
  reports only named source methods;
- one linked body is ``exact``; an abstract declaration alone is a
  ``possible`` target, like a Spring Data query method;
- several linked methods follow ADR-70: a unique concrete body is
  ``exact`` and the abstract declaration the call names stays a
  ``possible`` witness; several concrete bodies are all ``possible``. An
  abstract method the call does not name, such as the interface method a
  concrete target overrides, is no target;
- a concrete body Joern links but the adapter cannot report, in an
  anonymous class, a lambda or a library, still counts as a candidate:
  every source target of that call stays ``possible`` (ADR-70, ADR-81).

Joern names a method of a local class after the enclosing method and its
signature (``pkg.App.create:void().Local.go:void()``); the adapter drops
every signature and the enclosing method name, which gives the index's
spelling ``pkg.App.Local.go``.

A call made inside a lambda or an anonymous class is reported with an
``<anonymous`` caller, which the join attributes to the enclosing indexed
method by the call line (ADR-62).
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from systemlens.callgraph.model import CallGraphError, MethodCall, SourceMethod

SCHEMA = "systemlens-joern-v1"
_UNRESOLVED = ("<unresolvedNamespace>", "<unresolvedSignature>", "<unknownFullName>")
_ANONYMOUS_TYPE = re.compile(r"\$\d+$")
_SIGNATURE = re.compile(r":[^:()]*\([^()]*\)")
_INITIALIZERS = ("<init>", "<clinit>")


class JoernError(CallGraphError):
    """Joern is installed but failed, or its export is malformed."""


@dataclass(frozen=True)
class _Method:
    full_name: str
    path: str
    line: int
    abstract: bool
    external: bool

    @property
    def qualified(self) -> str:
        """``pkg.Type.method`` without Joern's ``:signature`` suffixes."""
        return _qualified(self.full_name)

    @property
    def simple_name(self) -> str:
        return self.qualified.rsplit(".", 1)[-1]

    @property
    def anonymous(self) -> bool:
        """A lambda, or a method of an anonymous class (``Runnable$0``)."""
        owner = self.qualified.rsplit(".", 1)[0]
        return self.simple_name.startswith("<lambda>") or any(
            _ANONYMOUS_TYPE.search(part) for part in owner.split(".")
        )

    @property
    def initializer(self) -> bool:
        return self.simple_name in _INITIALIZERS

    @property
    def parameter_types(self) -> tuple[str, ...] | None:
        return _parameter_types(self.full_name)


def _qualified(full_name: str) -> str:
    """The index's name of a Joern method full name.

    ``pkg.T.m:void()`` is ``pkg.T.m``. A local class declared in ``m`` is
    spelled ``pkg.T.m:void().Local.go:void()`` by Joern and
    ``pkg.T.Local.go`` by the index: each signature but the last closes an
    enclosing method, whose name is dropped with it.
    """
    pieces = _SIGNATURE.split(full_name)
    if len(pieces) == 1:
        return full_name.split(":", 1)[0]
    enclosing = [piece.rsplit(".", 1)[0] for piece in pieces[:-2]]
    return "".join([*enclosing, pieces[-2], pieces[-1]])


def _parameter_types(full_name: str) -> tuple[str, ...] | None:
    """Split ``pkg.T.m:ret(a,b)`` into ``("a", "b")``; ``None`` when unknown."""
    _, separator, signature = full_name.rpartition(":")
    if not separator or any(marker in signature for marker in _UNRESOLVED):
        return None
    open_index = signature.find("(")
    if open_index < 0 or not signature.endswith(")"):
        return None
    inner = signature[open_index + 1:-1]
    if not inner:
        return ()
    types: list[str] = []
    depth = 0
    current: list[str] = []
    for character in inner:
        if character == "<":
            depth += 1
        elif character == ">":
            depth -= 1
        if character == "," and depth == 0:
            types.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    types.append("".join(current).strip())
    return tuple(types)


def _path(value: object) -> str:
    path = str(value or "").replace("\\", "/").removeprefix("./")
    return "" if path in {"", "<empty>", "<unknown>"} else path


def _method(row: Mapping[str, object]) -> _Method:
    return _Method(
        full_name=str(row["full_name"]),
        path=_path(row.get("file")),
        line=int(str(row.get("line", -1))),
        abstract=bool(row.get("abstract", False)),
        external=bool(row.get("external", False)),
    )


def _is_source(method: _Method, indexed: frozenset[str]) -> bool:
    return (
        not method.external
        and bool(method.path)
        and method.line > 0
        and (not indexed or method.path in indexed)
    )


def _resolved(target: str) -> bool:
    return bool(target) and target != "ANY" and not any(
        marker in target for marker in _UNRESOLVED
    )


def _check_schema(document: Mapping[str, object]) -> None:
    if document.get("schema") != SCHEMA:
        raise JoernError(
            f"Joern export has schema {document.get('schema')!r}, expected {SCHEMA!r}."
        )


def parse_methods(
    document: Mapping[str, object], indexed_paths: Iterable[str] = (), source_prefix: str = "",
) -> list[SourceMethod]:
    """Named source methods of the export, without constructors or lambdas."""
    _check_schema(document)
    indexed = frozenset(indexed_paths)
    prefix = source_prefix.strip("/")
    result: set[SourceMethod] = set()
    try:
        for row in _rows(document, "methods"):
            method = _method(row)
            if (
                not _is_source(method, indexed) or method.anonymous or method.initializer
                or (prefix and not method.path.startswith(prefix + "/"))
            ):
                continue
            result.add(SourceMethod(method.qualified, method.path, method.line))
    except (KeyError, TypeError, ValueError) as exc:
        raise JoernError("Joern returned an unexpected method schema.") from exc
    return sorted(result, key=lambda item: (item.path, item.start_line, item.qualified_method))


def parse_calls(
    document: Mapping[str, object], indexed_paths: Iterable[str] = (),
) -> list[MethodCall]:
    """Source-to-source calls of the export, with ADR-61/70 confidences."""
    _check_schema(document)
    indexed = frozenset(indexed_paths)
    calls: set[MethodCall] = set()
    try:
        for row in _rows(document, "calls"):
            calls.update(_calls_of(row, indexed))
    except (KeyError, TypeError, ValueError) as exc:
        raise JoernError("Joern returned an unexpected call schema.") from exc
    return sorted(calls, key=lambda call: (
        call.caller_path, call.call_line, call.caller, call.callee, call.dispatch_confidence,
    ))


def _rows(document: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    rows = document.get(key)
    if not isinstance(rows, list):
        raise JoernError(f"Joern export has no {key!r} list.")
    return rows


def _calls_of(row: Mapping[str, object], indexed: frozenset[str]) -> list[MethodCall]:
    call_line = int(str(row["line"]))
    if not _resolved(str(row["target"])) or call_line <= 0:
        return []
    caller = _Method(
        full_name=str(row["caller"]),
        path=_path(row.get("caller_file")),
        line=int(str(row.get("caller_line", -1))),
        abstract=False,
        external=False,
    )
    if not _is_source(caller, indexed):
        return []
    target = str(row["target"])
    linked = [_method(item) for item in _callee_rows(row)]
    callees = [
        method for method in linked
        if _is_source(method, indexed) and not method.anonymous and not method.initializer
        # An abstract declaration is a witness only when the call names it:
        # Joern also links a call on a concrete receiver to the interface
        # method its target overrides (ADR-70).
        and (not method.abstract or method.full_name == target)
    ]
    if not callees:
        return []
    concrete = [method for method in callees if not method.abstract]
    # A body the output cannot carry (anonymous class, lambda, library)
    # still receives the call at run time: no source target is then the
    # only body, so none is exact (ADR-70, ADR-81).
    unreported = any(
        not method.abstract and not method.initializer
        and (method.external or method.anonymous)
        for method in linked
    )
    caller_name = "<anonymous>" if caller.anonymous else caller.qualified
    caller_types = None if caller.anonymous else caller.parameter_types

    def confidence(method: _Method) -> str:
        if unreported:
            return "possible"
        if len(callees) == 1:
            return "possible" if method.abstract else "exact"
        if len(concrete) == 1 and method is concrete[0]:
            return "exact"
        return "possible"

    return [
        MethodCall(
            caller=caller_name,
            caller_path=caller.path,
            caller_line=caller.line,
            callee=method.qualified,
            callee_path=method.path,
            callee_line=method.line,
            call_line=call_line,
            dispatch_confidence=confidence(method),
            caller_parameter_types=caller_types,
            callee_parameter_types=method.parameter_types,
        )
        for method in callees
    ]


def _callee_rows(row: Mapping[str, object]) -> list[Mapping[str, object]]:
    callees = row.get("callees", [])
    if not isinstance(callees, list):
        raise TypeError("callees")
    return callees
