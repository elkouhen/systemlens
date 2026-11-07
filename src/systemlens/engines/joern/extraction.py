"""Decode the Joern export (``export.sc``) into engine-neutral facts.

The export lists every internal method and every non-operator call with
the methods Joern's call graph links it to. This module keeps only
source-to-source calls and never guesses a target (ADR-61, ADR-85):

- a call Joern could not resolve (``<unresolvedNamespace>``, ``ANY``) is
  dropped. A call whose argument types are unresolved
  (``Owner.name:<unresolvedSignature>(n)``) is kept only when ``Owner`` is
  a source type that declares exactly one method ``name`` with ``n``
  parameters, without a body; that method is a ``possible`` target;
- a callee without a source file, a static initializer, a lambda or a
  method of an anonymous class is dropped, as the CodeQL query reports only
  named source methods and constructors. A constructor is the node
  ``Owner.<init>`` (ADR-75): Joern inlines field initializers into each
  constructor that does not delegate to ``this(...)``, and puts static
  field initializers in ``Owner.<clinit>``; both callers are kept. Joern
  leaves initializer blocks out of its graph, so their calls are left to
  the symbol pass;
- one linked body is ``exact``; an abstract declaration alone is a
  ``possible`` target, like a Spring Data query method;
- several linked methods follow ADR-70: a unique concrete body is
  ``exact`` and the abstract declaration the call names stays a
  ``possible`` witness; several concrete bodies are all ``possible``. An
  abstract method the call does not name, such as the interface method a
  concrete target overrides, is no target;
- a concrete body Joern links but the adapter cannot report, in an
  anonymous class, a lambda or a library, still counts as a candidate:
  every source target of that call stays ``possible`` (ADR-70, ADR-81);
- a call whose named method belongs to a ``@FeignClient`` interface or a
  direct supertype, listed in ``proxied_types``, reaches a runtime proxy
  no source body stands for: every target, a fallback class included,
  stays ``possible`` (ADR-81 rule 3). Test sources are never indexed, so
  their bodies are never targets (rule 2);
- a dynamic call is ``exact`` only when every concrete source subtype of
  the type it names, listed in ``types``, inherits or declares a linked
  body and generates no member for the call. A record accessor, a Lombok
  member, or a body inherited from a library class or ``Object`` keeps
  every target ``possible`` (ADR-81 rule 1). Joern parses Lombok types
  only, so its call graph never links a generated member.

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

from systemlens.callgraph.model import CallGraphError, MethodCall, PayloadTypeEvidence, SourceMethod

SCHEMA = "systemlens-joern-v1"
_UNRESOLVED = ("<unresolvedNamespace>", "<unresolvedSignature>", "<unknownFullName>")
_ANONYMOUS_TYPE = re.compile(r"\$\d+$")
_SIGNATURE = re.compile(r":[^:()]*\([^()]*\)")
_UNRESOLVED_ARITY = re.compile(r":<unresolvedSignature>\((\d+)\)$")
_UNRESOLVED_SIGNATURE = re.compile(r"^(?P<method>[^:]+):<unresolvedSignature>\((?P<arity>\d+)\)$")
_INITIALIZERS = ("<init>", "<clinit>")
_STATIC_INITIALIZER = "<clinit>"


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
        declared = _declared_methods(document, indexed)
        # An export from an older script lists no proxied type.
        listed = document.get("proxied_types", [])
        proxied = frozenset(str(name) for name in listed) if isinstance(listed, list) else frozenset()
        hierarchy = _Hierarchy.of(document, indexed)
        for row in _rows(document, "calls"):
            calls.update(_calls_of(row, indexed, declared, proxied, hierarchy))
    except (KeyError, TypeError, ValueError) as exc:
        raise JoernError("Joern returned an unexpected call schema.") from exc
    return sorted(calls, key=lambda call: (
        call.caller_path, call.call_line, call.caller, call.callee, call.dispatch_confidence,
    ))


@dataclass(frozen=True)
class _Field:
    name: str
    type: str
    annotations: frozenset[str]


@dataclass(frozen=True)
class _Type:
    full_name: str
    concrete: bool
    record: bool
    inherits: tuple[str, ...]
    # Simple names of supertypes the generic signature lists and
    # ``inherits`` misses, such as the interfaces of a record.
    unresolved: tuple[str, ...]
    annotations: frozenset[str]
    fields: tuple[_Field, ...]


def _signature_supertypes(signature: str) -> list[str]:
    """Simple names of the top-level types of ``LRecord;LNamed<LT;>;``."""
    names: list[str] = []
    depth = 0
    current: list[str] = []
    for character in signature:
        if character == "<":
            depth += 1
        elif character == ">":
            depth -= 1
        elif depth == 0 and character == ";":
            written = "".join(current)
            if written.startswith("L"):
                names.append(re.split(r"[/.$]", written[1:])[-1])
            current = []
        elif depth == 0:
            current.append(character)
    return names


def _lombok_names(rows: object) -> frozenset[str]:
    """Simple names of the annotations that are, or may be, Lombok's.

    Joern resolves an imported Lombok annotation to ``lombok.Name``; an
    annotation it could not resolve keeps its written name.
    """
    names: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        name = str(row.get("name", ""))
        full_name = str(row.get("full_name", ""))
        if full_name == f"lombok.{name}" or "." not in full_name or "<unresolved" in full_name:
            names.add(name)
    return frozenset(names)


class _Hierarchy:
    """The internal types of the export, for ADR-81 rule 1."""

    def __init__(self, types: Mapping[str, _Type]) -> None:
        self.types = types
        self._by_simple_name: dict[str, list[str]] = {}
        for name in types:
            self._by_simple_name.setdefault(re.split(r"[.$]", name)[-1], []).append(name)
        self._ancestors: dict[str, frozenset[str]] = {}
        self._subtypes: dict[str, list[_Type]] = {}

    @classmethod
    def of(cls, document: Mapping[str, object], indexed: frozenset[str]) -> "_Hierarchy | None":
        """``None`` for an export from an older script, without ``types``."""
        rows = document.get("types")
        if not isinstance(rows, list):
            return None
        types: dict[str, _Type] = {}
        for row in rows:
            path = _path(row.get("file"))
            if not path or (indexed and path not in indexed):
                continue
            inherits = tuple(str(name) for name in row.get("inherits", []))
            named = {name.rsplit(".", 1)[-1] for name in inherits}
            unresolved = tuple(
                simple for simple in _signature_supertypes(str(row.get("signature", "")))
                if simple not in named
            )
            fields = row.get("fields", [])
            types[str(row["full_name"])] = _Type(
                full_name=str(row["full_name"]),
                concrete=not bool(row.get("abstract", False)),
                record="java.lang.Record" in inherits,
                inherits=inherits,
                unresolved=unresolved,
                annotations=_lombok_names(row.get("annotations")),
                fields=tuple(
                    _Field(
                        name=str(field["name"]), type=str(field.get("type", "")),
                        annotations=_lombok_names(field.get("annotations")),
                    )
                    for field in fields if isinstance(fields, list) and not field.get("static", False)
                ),
            )
        return cls(types)

    def ancestors(self, name: str) -> frozenset[str]:
        """``name`` and every type it extends or implements, through the
        internal types; an external supertype is named, not walked."""
        cached = self._ancestors.get(name)
        if cached is None:
            found: set[str] = set()
            pending = [name]
            while pending:
                current = pending.pop()
                if current in found:
                    continue
                found.add(current)
                declared = self.types.get(current)
                if declared is not None:
                    pending.extend(declared.inherits)
                    # A supertype known by its simple name only: every
                    # internal type of that name, which may widen, never
                    # narrow, the subtypes of a call's type.
                    for simple in declared.unresolved:
                        pending.extend(self._by_simple_name.get(simple, ()))
            cached = self._ancestors[name] = frozenset(found)
        return cached

    def concrete_subtypes(self, name: str) -> list[_Type]:
        cached = self._subtypes.get(name)
        if cached is None:
            cached = self._subtypes[name] = [
                declared for declared in self.types.values()
                if declared.concrete and name in self.ancestors(declared.full_name)
            ]
        return cached

    def incomplete(self, owner: str, name: str, arity: int, bodies: Iterable[str]) -> bool:
        """Whether some concrete subtype of ``owner`` has a body for
        ``name`` that no owner of ``bodies`` declares (ADR-81 rule 1). A
        member the type writes itself is never generated."""
        written = frozenset(bodies)
        return any(
            declared.full_name not in written and _generated_member(declared, name, arity)
            or not written & self.ancestors(declared.full_name)
            for declared in self.concrete_subtypes(owner)
        )


def _generated_member(declared: _Type, name: str, arity: int) -> bool:
    """Whether the compiler or Lombok writes ``name`` on ``declared``, as the
    ``source`` engine decides it (``JavaSymbols._generated_member``)."""
    if declared.record:
        return arity == 0 and any(field.name == name for field in declared.fields)
    if (name, arity) in {("equals", 1), ("hashCode", 0)}:
        return bool(declared.annotations & {"Data", "Value", "EqualsAndHashCode"})
    if (name, arity) == ("toString", 0):
        return bool(declared.annotations & {"Data", "Value", "ToString"})
    prefix = next((prefix for prefix in ("get", "is", "set") if name.startswith(prefix)), None)
    if prefix is None or arity != (1 if prefix == "set" else 0):
        return False
    accessors = {"Getter"} if prefix != "set" else {"Setter"}
    class_level = bool(declared.annotations & (accessors | ({"Data"} if prefix == "set" else {"Data", "Value"})))
    suffix = name[len(prefix):]
    return any(
        (class_level or field.annotations & accessors)
        and field.name[:1].upper() + field.name[1:] == suffix
        and (prefix != "is" or field.type == "boolean")
        for field in declared.fields
    )


def _owner(full_name: str) -> str:
    """``pkg.T`` of the Joern method full name ``pkg.T.m:ret(args)``."""
    return full_name[:full_name.rfind(".", 0, full_name.rfind(":"))]


def _rows(document: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    rows = document.get(key)
    if not isinstance(rows, list):
        raise JoernError(f"Joern export has no {key!r} list.")
    return rows


def _arity(method: _Method) -> int | None:
    types = method.parameter_types
    if types is not None:
        return len(types)
    match = _UNRESOLVED_ARITY.search(method.full_name)
    return int(match.group(1)) if match else None


def _declared_methods(
    document: Mapping[str, object], indexed: frozenset[str],
) -> dict[tuple[str, int], list[_Method]]:
    """Named source methods by ``(pkg.Owner.name, arity)``."""
    declared: dict[tuple[str, int], list[_Method]] = {}
    rows = document.get("methods", [])
    for row in rows if isinstance(rows, list) else _rows(document, "methods"):
        method = _method(row)
        if not _is_source(method, indexed) or method.anonymous or method.initializer:
            continue
        arity = _arity(method)
        if arity is not None:
            declared.setdefault((method.qualified, arity), []).append(method)
    return declared


def _unresolved_signature_call(
    row: Mapping[str, object], caller: _Method, call_line: int,
    declared: Mapping[tuple[str, int], list[_Method]],
) -> list[MethodCall]:
    """A call Joern typed by owner, name and arity only (review J3).

    Joern leaves the signature unresolved when an argument type is
    external, such as Spring's ``HttpHeaders``. When the owner is a source
    type with one method of that name and arity, and that method is an
    abstract declaration, it is the only declaration the call can name: a
    ``possible`` target, as with a resolved signature. A concrete body is
    left to the symbol pass; any other case stays dropped.
    """
    match = _UNRESOLVED_SIGNATURE.match(str(row["target"]))
    if match is None or "<unresolved" in match.group("method"):
        return []
    candidates = declared.get((match.group("method"), int(match.group("arity"))), [])
    if len(candidates) != 1 or not candidates[0].abstract:
        # A concrete body would be ``exact`` with a resolved signature; the
        # symbol pass, which types the arguments, decides it instead of a
        # ``possible`` row that would hide its answer.
        return []
    method = candidates[0]
    return [MethodCall(
        caller="<anonymous>" if caller.anonymous else caller.qualified,
        caller_path=caller.path,
        caller_line=caller.line,
        callee=method.qualified,
        callee_path=method.path,
        callee_line=method.line,
        call_line=call_line,
        dispatch_confidence="possible",
        caller_parameter_types=None if caller.anonymous else caller.parameter_types,
        callee_parameter_types=method.parameter_types,
    )]


def _calls_of(
    row: Mapping[str, object], indexed: frozenset[str],
    declared: Mapping[tuple[str, int], list[_Method]] | None = None,
    proxied: frozenset[str] = frozenset(),
    hierarchy: _Hierarchy | None = None,
) -> list[MethodCall]:
    call_line = int(str(row["line"]))
    if call_line <= 0:
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
    if not _resolved(str(row["target"])):
        return _unresolved_signature_call(row, caller, call_line, declared or {})
    target = str(row["target"])
    linked = [_method(item) for item in _callee_rows(row)]
    callees = [
        method for method in linked
        if _is_source(method, indexed) and not method.anonymous
        and method.simple_name != _STATIC_INITIALIZER
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
    ) or _qualified(target).rsplit(".", 1)[0] in proxied
    target_types = _parameter_types(target)
    if (
        not unreported and hierarchy is not None and concrete and target_types is not None
        and row.get("dispatch") == "DYNAMIC_DISPATCH"
    ):
        # A runtime type whose body is generated or inherited from a
        # library: no linked body is the only one (ADR-81 rule 1).
        unreported = hierarchy.incomplete(
            _owner(target), _qualified(target).rsplit(".", 1)[-1], len(target_types),
            (_owner(method.full_name) for method in concrete),
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


# Strategy1 payload evidence (ADR-85 amendment). The rules follow the
# CodeQL query ``_KAFKA_MESSAGE_TYPES_QUERY``; the stage decides which
# types are informative (ADR-76).
_WRAPPER_ARGUMENT = {"Message": 0, "GenericMessage": 0, "ProducerRecord": 1}
_NO_SOURCE_TYPE = {"Object", *_WRAPPER_ARGUMENT}
_DECLARATION_PREFIX = re.compile(r"^(?:@[\w.]+(?:\([^()]*\))?\s+|final\s+)*")


def _simple_name(full_name: str) -> str | None:
    """``com.example.Outer$Inner`` is ``Inner``; an unknown type is ``None``.

    Joern spells a type it could not resolve ``<unresolvedNamespace>.Name``:
    the written simple name is still the evidence CodeQL would report.
    """
    if not full_name or full_name == "ANY" or full_name.startswith("<unknown"):
        return None
    return re.split(r"[.$]", full_name)[-1] or None


def _written_simple(written: str) -> str:
    """``com.x.Box<com.x.Item>`` is ``Box<com.x.Item>``, as CodeQL names it."""
    base, separator, rest = written.partition("<")
    return base.strip().rsplit(".", 1)[-1] + separator + rest


def _written_type(declaration: str, name: str) -> str:
    """``final Message<OrderCreated> message`` is ``Message<OrderCreated>``."""
    text = _DECLARATION_PREFIX.sub("", declaration.strip())
    if name and text.endswith(name):
        text = text[: -len(name)]
    return text.strip()


def _type_arguments(written: str) -> list[str]:
    open_index = written.find("<")
    if open_index < 0 or not written.endswith(">"):
        return []
    arguments: list[str] = []
    depth = 0
    current: list[str] = []
    for character in written[open_index + 1:-1]:
        if character == "<":
            depth += 1
        elif character == ">":
            depth -= 1
        if character == "," and depth == 0:
            arguments.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    arguments.append("".join(current).strip())
    return arguments


def _assigned_types(
    variable: str, assignments: list[Mapping[str, object]],
) -> set[str]:
    """Types of the values assigned to ``variable``, through other variables.

    The local data flow of the CodeQL query: a value whose type names
    ``Object`` or a wrapper is no source, and a variable assigned from
    another variable takes that variable's sources.
    """
    found: set[str] = set()
    pending, seen = [variable], set()
    while pending:
        current = pending.pop()
        if not current or current in seen:
            continue
        seen.add(current)
        for assignment in assignments:
            if str(assignment.get("target", "")) != current:
                continue
            simple = _simple_name(str(assignment.get("type", "")))
            if simple is not None and simple not in _NO_SOURCE_TYPE:
                found.add(simple)
            pending.append(str(assignment.get("variable", "")))
    return found


def _send_types(send: Mapping[str, object]) -> set[str]:
    variable = str(send.get("variable", ""))
    written = _written_type(str(send.get("declaration", "")), variable)
    base = _written_simple(written).partition("<")[0] if written else ""
    erased = _simple_name(str(send.get("type", ""))) or ""
    if base in _WRAPPER_ARGUMENT or erased in _WRAPPER_ARGUMENT:
        # Joern erases type arguments: only the written declaration names
        # the payload a wrapper carries.
        arguments = _type_arguments(written)
        index = _WRAPPER_ARGUMENT.get(base, -1)
        if 0 <= index < len(arguments) and arguments[index] not in {"?", "Object"}:
            return {_written_simple(arguments[index])}
        return set()
    assignments = send.get("assignments", [])
    if not isinstance(assignments, list):
        raise TypeError("assignments")
    sources = _assigned_types(variable, assignments)
    if sources:
        return sources
    if written:
        return {_written_simple(written)}
    return {erased} if erased else set()


def parse_payload_types(
    document: Mapping[str, object], indexed_paths: Iterable[str] = (),
) -> list[PayloadTypeEvidence]:
    """Payload types of the export's Strategy1 Kafka sends.

    An export without the ``payload_sends`` list, from an older script,
    has no evidence.
    """
    _check_schema(document)
    indexed = frozenset(indexed_paths)
    sends = document.get("payload_sends", [])
    if not isinstance(sends, list):
        raise JoernError("Joern export has no 'payload_sends' list.")
    evidence: set[PayloadTypeEvidence] = set()
    try:
        for send in sends:
            path = _path(send.get("file"))
            line = int(str(send.get("line", -1)))
            if not path or line <= 0 or (indexed and path not in indexed):
                continue
            evidence.update(
                PayloadTypeEvidence(path=path, line=line, message_type=message_type)
                for message_type in _send_types(send)
            )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise JoernError("Joern returned an unexpected payload schema.") from exc
    return sorted(evidence, key=lambda item: (item.path, item.line, item.message_type))
