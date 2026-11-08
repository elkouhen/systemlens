"""Transient, conservative Java symbols for missing call-graph edges.

This is not a Java compiler: unresolved types, generic substitutions the
sources do not write (BL-26) and ambiguous overloads remain unresolved.
Names alone never establish dispatch.
"""

from collections import defaultdict
from dataclasses import dataclass, field as dataclass_field, replace
from pathlib import Path
import re
from typing import Callable, NamedTuple, Sequence

from tree_sitter import Node

from systemlens.discovery.java import parser, patterns
from systemlens.domain.code_flows import IntegrationMethod
from systemlens.domain.module_inventory import ModuleDependency
from systemlens.indexing.file_inventory import is_test_source
from systemlens.indexing.integration_methods import (
    CONSTRUCTOR,
    CONSTRUCTOR_DECLARATIONS,
    INITIALIZER_NAMES,
    STATIC_INITIALIZER,
    declaration_parameters,
    declared_parameter_types,
    delegates_to_this,
    initializer_declarations,
    initializer_members,
)
from systemlens.callgraph.model import MethodCall


_TYPES = {"class_declaration", "interface_declaration", "record_declaration", "enum_declaration"}
# Nodes whose children are member or top-level types; a type declared
# anywhere else is local to its block.
_MEMBER_CONTAINERS = {"program", "class_body", "interface_body", "enum_body_declarations", "annotation_type_body"}
_GENERIC_DECLARATIONS = {
    "method_declaration", "constructor_declaration", "class_declaration", "interface_declaration",
    "record_declaration",
}
_PRIMITIVES = {"byte", "short", "int", "long", "float", "double", "char", "boolean", "void"}
_JAVA_LANG = {
    "String", "Object", "Boolean", "Byte", "Short", "Integer", "Long", "Float", "Double", "Character",
    "Class", "Number", "CharSequence", "StringBuilder", "StringBuffer", "Enum", "Void", "Iterable",
    "Comparable", "Runnable", "Thread", "Throwable", "Exception", "RuntimeException", "Error",
    "IllegalArgumentException", "IllegalStateException", "Record", "Math", "System",
}
_LITERAL_TYPES = {
    "string_literal": "java.lang.String",
    "text_block": "java.lang.String",
    "character_literal": "char",
    "true": "boolean",
    "false": "boolean",
}
_BOXED = {
    "int": "java.lang.Integer", "long": "java.lang.Long", "double": "java.lang.Double",
    "float": "java.lang.Float", "boolean": "java.lang.Boolean", "char": "java.lang.Character",
    "byte": "java.lang.Byte", "short": "java.lang.Short",
}
_INTEGER_LITERALS = {
    "decimal_integer_literal", "hex_integer_literal", "octal_integer_literal", "binary_integer_literal",
}
_FLOATING_LITERALS = {"decimal_floating_point_literal", "hex_floating_point_literal"}
# Widening primitive conversions (JLS 5.1.2).
_WIDENING = {
    "byte": {"short", "int", "long", "float", "double"},
    "short": {"int", "long", "float", "double"},
    "char": {"int", "long", "float", "double"},
    "int": {"long", "float", "double"},
    "long": {"float", "double"},
    "float": {"double"},
}
_UNBOXED = {boxed: primitive for primitive, boxed in _BOXED.items()}
_NUMERIC_BOXES = {"java.lang." + name for name in ("Byte", "Short", "Integer", "Long", "Float", "Double")}
# Supertypes of ``String`` and the boxes, fixed by the Java SE API.
_FIXED_SUPERTYPES: dict[str, frozenset[str]] = {
    name: frozenset({
        "java.lang.Object", "java.io.Serializable", "java.lang.Comparable",
        "java.lang.constant.Constable",
        *(("java.lang.Number",) if name in _NUMERIC_BOXES else ()),
        *(("java.lang.CharSequence", "java.lang.constant.ConstantDesc") if name == "java.lang.String" else ()),
        *(("java.lang.constant.ConstantDesc",) if name in _NUMERIC_BOXES - {
            "java.lang.Byte", "java.lang.Short"} else ()),
    })
    for name in ("java.lang.String", *_BOXED.values())
}


def _literal_type(kind: str, text: str) -> str | None:
    """The type of a numeric literal, from its suffix (JLS 3.10)."""
    text = text.replace("_", "")
    if kind in _INTEGER_LITERALS:
        return "long" if text[-1:] in {"l", "L"} else "int"
    if kind == "hex_floating_point_literal":
        # ``f`` is a hexadecimal digit: the suffix follows the exponent.
        return "float" if text[-1:] in {"f", "F"} and "p" in text.lower() else "double"
    if kind in _FLOATING_LITERALS:
        return "float" if text[-1:] in {"f", "F"} else "double"
    return None


# Members every type has: an unqualified call to one of them never reaches an
# outer type, and its return type is fixed by ``java.lang.Object``.
_OBJECT_METHODS = {
    "equals", "hashCode", "toString", "getClass", "notify", "notifyAll", "wait", "clone", "finalize",
}
_OBJECT_RETURNS = {("toString", 0): "java.lang.String", ("hashCode", 0): "int", ("equals", 1): "boolean"}
_ENUM_METHODS = {"name", "ordinal", "values", "valueOf", "compareTo", "getDeclaringClass", "describeConstable"}
# Method names of the few external supertypes whose members are fixed by the
# Java SE API or by a Spring type that declares only them (beyond the
# ``Object`` methods). Any other external supertype may declare any name, so
# an unqualified call in its anonymous or named subclass stays unresolved.
_KNOWN_EXTERNAL_MEMBERS: dict[str, frozenset[str]] = {
    "org.springframework.security.core.userdetails.UserDetails": frozenset({
        "getAuthorities", "getPassword", "getUsername", "isAccountNonExpired",
        "isAccountNonLocked", "isCredentialsNonExpired", "isEnabled",
    }),
    "org.springframework.security.core.GrantedAuthority": frozenset({"getAuthority"}),
    "org.springframework.boot.CommandLineRunner": frozenset({"run"}),
    "org.springframework.boot.ApplicationRunner": frozenset({"run"}),
    "org.springframework.beans.factory.InitializingBean": frozenset({"afterPropertiesSet"}),
    "org.springframework.beans.factory.DisposableBean": frozenset({"destroy"}),
    "java.lang.Runnable": frozenset({"run"}),
    "java.util.concurrent.Callable": frozenset({"call"}),
    "java.io.Serializable": frozenset(),
    "java.lang.Cloneable": frozenset(),
    "java.lang.Comparable": frozenset({"compareTo"}),
    "java.lang.AutoCloseable": frozenset({"close"}),
    "java.io.Closeable": frozenset({"close"}),
    # Classes an anonymous body often extends (BL-25): every public,
    # protected or static member name, removed or deprecated ones included.
    "java.lang.Object": frozenset(),
    "java.lang.Thread": frozenset({
        "activeCount", "checkAccess", "countStackFrames", "currentThread", "destroy", "dumpStack",
        "enumerate", "getAllStackTraces", "getContextClassLoader", "getDefaultUncaughtExceptionHandler",
        "getId", "getName", "getPriority", "getStackTrace", "getState", "getThreadGroup",
        "getUncaughtExceptionHandler", "holdsLock", "interrupt", "interrupted", "isAlive", "isDaemon",
        "isInterrupted", "isVirtual", "join", "ofPlatform", "ofVirtual", "onSpinWait", "resume", "run",
        "setContextClassLoader", "setDaemon", "setDefaultUncaughtExceptionHandler", "setName",
        "setPriority", "setUncaughtExceptionHandler", "sleep", "start", "startVirtualThread", "stop",
        "suspend", "threadId", "yield",
    }),
    "java.util.TimerTask": frozenset({"run", "cancel", "scheduledExecutionTime"}),
    "org.springframework.web.servlet.config.annotation.WebMvcConfigurerAdapter": frozenset({
        "configurePathMatch", "configureContentNegotiation", "configureAsyncSupport",
        "configureDefaultServletHandling", "addFormatters", "addInterceptors", "addResourceHandlers",
        "addCorsMappings", "addViewControllers", "configureViewResolvers", "addArgumentResolvers",
        "addReturnValueHandlers", "configureMessageConverters", "extendMessageConverters",
        "configureHandlerExceptionResolvers", "extendHandlerExceptionResolvers", "getValidator",
        "getMessageCodesResolver",
    }),
    "org.springframework.security.config.annotation.web.configuration.WebSecurityConfigurerAdapter": frozenset({
        "setApplicationContext", "setTrustResolver", "setContentNegotationStrategy",
        "setObjectPostProcessor", "setAuthenticationConfiguration", "configure", "getHttp",
        "authenticationManagerBean", "authenticationManager", "userDetailsServiceBean",
        "userDetailsService", "init", "getApplicationContext",
    }),
}
# Supertypes of external JDK types, fixed by the Java SE API.
_KNOWN_EXTERNAL_SUPERTYPES: dict[str, tuple[str, ...]] = {
    "java.lang.Thread": ("java.lang.Runnable",),
    "java.util.concurrent.ExecutorService": ("java.util.concurrent.Executor",),
    "java.util.concurrent.ScheduledExecutorService": (
        "java.util.concurrent.ExecutorService", "java.util.concurrent.Executor",
    ),
}
_LOMBOK_GETTERS = {"Data", "Getter", "Value"}
# Final JDK classes and every supertype they have: a type argument naming
# one is related only to these (BL-45, ADR-81 amendment).
_NUMBER_SUPERTYPES = frozenset({
    "java.lang.Object", "java.lang.Number", "java.io.Serializable", "java.lang.Comparable",
    "java.lang.constant.Constable", "java.lang.constant.ConstantDesc",
})
_FINAL_JDK_SUPERTYPES: dict[str, frozenset[str]] = {
    "java.lang.String": frozenset({
        "java.lang.Object", "java.io.Serializable", "java.lang.Comparable", "java.lang.CharSequence",
        "java.lang.constant.Constable", "java.lang.constant.ConstantDesc",
    }),
    **{f"java.lang.{name}": _NUMBER_SUPERTYPES for name in ("Integer", "Long", "Short", "Byte", "Double", "Float")},
    **{f"java.lang.{name}": frozenset({
        "java.lang.Object", "java.io.Serializable", "java.lang.Comparable", "java.lang.constant.Constable",
    }) for name in ("Boolean", "Character")},
}
# The supertypes every enum and every record has without writing them
# (JLS 8.9, 8.10): ``Enum<E>`` and its interfaces, and ``Record`` (BC-06).
_IMPLICIT_SUPERTYPES: dict[str, frozenset[str]] = {
    "enum_declaration": frozenset({
        "java.lang.Enum", "java.lang.Comparable", "java.io.Serializable", "java.lang.constant.Constable",
    }),
    "record_declaration": frozenset({"java.lang.Record"}),
}
# The supertypes of every array type (JLS 4.10.3).
_ARRAY_SUPERTYPES = frozenset({"java.lang.Object", "java.lang.Cloneable", "java.io.Serializable"})
# Functional JDK interfaces: a lambda or method reference can implement them
# anywhere, so dispatch on them is never exact.
_FUNCTIONAL_JDK = {"java.lang.Runnable", "java.util.concurrent.Callable"}
# JDK functional interfaces a source method may declare, by parameter count
# of their single abstract method: the arity a method reference passed there
# binds (BL-24).
_JDK_FUNCTIONAL_ARITIES: dict[str, int] = {
    "java.lang.Runnable": 0, "java.util.concurrent.Callable": 0,
    "java.util.function.Supplier": 0, "java.util.function.Consumer": 1,
    "java.util.function.Function": 1, "java.util.function.Predicate": 1,
    "java.util.function.UnaryOperator": 1, "java.util.function.BiConsumer": 2,
    "java.util.function.BiFunction": 2, "java.util.function.BiPredicate": 2,
    "java.util.function.BinaryOperator": 2, "java.util.Comparator": 2,
}
# The single abstract method of a JDK functional type (BL-37): the number
# of type arguments, then the type argument each parameter takes, and the
# one the result takes (``None`` for ``void`` or ``boolean``).
_JDK_FUNCTIONAL_SIGNATURES: dict[str, tuple[int, tuple[int, ...], int | None]] = {
    "java.util.function.Function": (2, (0,), 1),
    "java.util.function.BiFunction": (3, (0, 1), 2),
    "java.util.function.Predicate": (1, (0,), None),
    "java.util.function.BiPredicate": (2, (0, 1), None),
    "java.util.function.Consumer": (1, (0,), None),
    "java.util.function.BiConsumer": (2, (0, 1), None),
    "java.util.function.Supplier": (1, (), 0),
    "java.util.function.UnaryOperator": (1, (0,), 0),
    "java.util.function.BinaryOperator": (1, (0, 0), 0),
}
# Receivers of a method reference that are expressions, never a type name.
_EXPRESSION_RECEIVERS = {
    "this", "super", "method_invocation", "object_creation_expression", "parenthesized_expression",
    "array_access", "cast_expression",
}
# JDK generic types whose type arguments type an untyped lambda parameter
# (BL-14): the element of a collection, stream or optional, or the key and
# value of a map. Each maps to its kind and its number of type arguments.
_GENERIC_CONTAINERS: dict[str, tuple[str, int]] = {
    **{name: ("iterable", 1) for name in (
        "java.lang.Iterable", "java.util.Collection", "java.util.List", "java.util.ArrayList",
        "java.util.LinkedList", "java.util.Set", "java.util.HashSet", "java.util.LinkedHashSet",
        "java.util.SortedSet", "java.util.NavigableSet", "java.util.TreeSet", "java.util.Queue",
        "java.util.Deque", "java.util.ArrayDeque", "java.util.Vector", "java.util.PriorityQueue",
        "java.util.concurrent.CopyOnWriteArrayList", "java.util.concurrent.CopyOnWriteArraySet",
        "java.util.concurrent.ConcurrentLinkedQueue", "java.util.concurrent.BlockingQueue",
        "java.util.concurrent.LinkedBlockingQueue",
    )},
    "java.util.stream.Stream": ("stream", 1),
    "java.util.Optional": ("optional", 1),
    **{name: ("map", 2) for name in (
        "java.util.Map", "java.util.HashMap", "java.util.LinkedHashMap", "java.util.SortedMap",
        "java.util.NavigableMap", "java.util.TreeMap", "java.util.concurrent.ConcurrentMap",
        "java.util.concurrent.ConcurrentHashMap",
    )},
    "java.util.Iterator": ("iterator", 1),
    "java.util.ListIterator": ("iterator", 1),
    "java.util.Map.Entry": ("entry", 2),
    # BL-36: a future's value and the result of a JDK functional type.
    "java.util.concurrent.Future": ("future", 1),
    "java.util.concurrent.CompletableFuture": ("future", 1),
    "java.util.concurrent.Callable": ("callable", 1),
    "java.util.function.Supplier": ("supplier", 1),
    "java.util.function.Function": ("function", 2),
    "java.util.function.BiFunction": ("bifunction", 3),
    "java.util.function.UnaryOperator": ("unaryoperator", 1),
    "java.util.function.BinaryOperator": ("binaryoperator", 1),
    # BL-43, ADR-90: Spring types whose type argument types the value they
    # return, documented with their versions in indexing.md.
    "org.springframework.data.domain.Page": ("page", 1),
    "org.springframework.data.domain.Slice": ("page", 1),
    "org.springframework.http.ResponseEntity": ("entity", 1),
    "org.springframework.http.HttpEntity": ("entity", 1),
    # BL-44, ADR-90: ``X.class`` is a ``Class<X>``.
    "java.lang.Class": ("class", 1),
}
_ELEMENT_CALLBACKS = (
    "filter", "map", "flatMap", "forEach", "forEachOrdered", "peek", "anyMatch", "allMatch",
    "noneMatch", "takeWhile", "dropWhile", "mapToInt", "mapToLong", "mapToDouble",
)
# The functional parameter a JDK method declares, by receiver kind, method
# name, argument count, lambda position and lambda parameter count: the type
# argument of the receiver each lambda parameter takes, in order.
_LAMBDA_PARAMETERS: dict[tuple[str, str, int, int, int], tuple[int, ...]] = {
    ("iterable", "forEach", 1, 0, 1): (0,),
    ("iterable", "removeIf", 1, 0, 1): (0,),
    ("iterable", "replaceAll", 1, 0, 1): (0,),
    ("iterable", "sort", 1, 0, 2): (0, 0),
    **{("stream", name, 1, 0, 1): (0,) for name in _ELEMENT_CALLBACKS},
    ("stream", "sorted", 1, 0, 2): (0, 0),
    ("stream", "min", 1, 0, 2): (0, 0),
    ("stream", "max", 1, 0, 2): (0, 0),
    ("stream", "reduce", 1, 0, 2): (0, 0),
    ("stream", "reduce", 2, 1, 2): (0, 0),
    **{("optional", name, 1, 0, 1): (0,) for name in ("filter", "map", "flatMap", "ifPresent")},
    ("optional", "ifPresentOrElse", 2, 0, 1): (0,),
    ("map", "forEach", 1, 0, 2): (0, 1),
    ("map", "replaceAll", 1, 0, 2): (0, 1),
    ("map", "computeIfAbsent", 2, 1, 1): (0,),
    ("map", "computeIfPresent", 2, 1, 2): (0, 1),
    ("map", "compute", 2, 1, 2): (0, 1),
    ("map", "merge", 3, 2, 2): (1, 1),
    # BL-37: the value of a future, through ``Function``, ``Consumer``.
    **{("future", name, count, 0, 1): (0,) for name in (
        "thenApply", "thenAccept", "thenCompose", "thenApplyAsync", "thenAcceptAsync", "thenComposeAsync",
    ) for count in (1, 2) if count == 1 or name.endswith("Async")},
}
# Calls that keep a container's type arguments under another kind:
# (receiver kind, method name, argument count) -> (result kind, arguments).
_GENERIC_CALLS: dict[tuple[str, str, int], tuple[str, tuple[int, ...]]] = {
    ("iterable", "stream", 0): ("stream", (0,)),
    ("iterable", "parallelStream", 0): ("stream", (0,)),
    **{("stream", name, 1): ("stream", (0,)) for name in (
        "filter", "peek", "limit", "skip", "sorted", "takeWhile", "dropWhile",
    )},
    **{("stream", name, 0): ("stream", (0,)) for name in (
        "distinct", "sorted", "parallel", "sequential", "unordered",
    )},
    ("stream", "findFirst", 0): ("optional", (0,)),
    ("stream", "findAny", 0): ("optional", (0,)),
    ("stream", "min", 1): ("optional", (0,)),
    ("stream", "max", 1): ("optional", (0,)),
    ("optional", "filter", 1): ("optional", (0,)),
    ("map", "keySet", 0): ("iterable", (0,)),
    ("map", "values", 0): ("iterable", (1,)),
    ("iterable", "iterator", 0): ("iterator", (0,)),
    ("stream", "iterator", 0): ("iterator", (0,)),
    # BL-36, quoted with their Java SE signatures in indexing.md.
    ("stream", "toList", 0): ("iterable", (0,)),
    ("stream", "reduce", 1): ("optional", (0,)),
    # BL-43, ADR-90: ``List<T> getContent()`` of ``Page<T>`` and ``Slice<T>``.
    ("page", "getContent", 0): ("iterable", (0,)),
}
# Calls whose function argument gives the container's new element (BL-40):
# (receiver kind, method name) -> whether the function returns the element
# itself (``map``) or a container of the same kind holding it (``flatMap``).
_ELEMENT_TRANSFORMS: dict[tuple[str, str], bool] = {
    ("stream", "map"): False,
    ("stream", "flatMap"): True,
    ("optional", "map"): False,
    ("optional", "flatMap"): True,
    ("future", "thenApply"): False,
    ("future", "thenApplyAsync"): False,
    ("future", "thenCompose"): True,
    ("future", "thenComposeAsync"): True,
}
# ``Collectors`` methods that make ``stream.collect(..)`` a collection of
# the stream's element (BL-36).
_COLLECTION_COLLECTORS = {"toList", "toSet", "toUnmodifiableList", "toUnmodifiableSet"}
# Calls that return one type argument of their JDK container (BL-27):
# (receiver kind, method name, argument count) -> type argument index.
_ELEMENT_ACCESSORS: dict[tuple[str, str, int], int] = {
    ("iterable", "get", 1): 0,
    ("iterable", "getFirst", 0): 0,
    ("iterable", "getLast", 0): 0,
    ("optional", "get", 0): 0,
    ("optional", "orElse", 1): 0,
    ("optional", "orElseGet", 1): 0,
    ("optional", "orElseThrow", 0): 0,
    ("optional", "orElseThrow", 1): 0,
    ("map", "get", 1): 1,
    ("iterator", "next", 0): 0,
    ("entry", "getKey", 0): 0,
    ("entry", "getValue", 0): 1,
    # BL-36, quoted with their Java SE signatures in indexing.md.
    ("map", "getOrDefault", 2): 1,
    ("map", "computeIfAbsent", 2): 1,
    ("map", "computeIfPresent", 2): 1,
    ("map", "compute", 2): 1,
    ("map", "merge", 3): 1,
    ("map", "put", 2): 1,
    ("map", "putIfAbsent", 2): 1,
    ("map", "remove", 1): 1,
    ("map", "replace", 2): 1,
    ("stream", "reduce", 2): 0,
    ("future", "join", 0): 0,
    ("future", "get", 0): 0,
    ("future", "get", 2): 0,
    ("future", "getNow", 1): 0,
    ("callable", "call", 0): 0,
    ("supplier", "get", 0): 0,
    ("function", "apply", 1): 1,
    ("bifunction", "apply", 2): 2,
    ("unaryoperator", "apply", 1): 0,
    ("binaryoperator", "apply", 2): 0,
    # BL-43, ADR-90: ``T getBody()`` of ``HttpEntity<T>``, inherited by
    # ``ResponseEntity<T>``.
    ("entity", "getBody", 0): 0,
}
# Static JDK factories (BL-36): (owner, name) -> (result kind, rule). The
# rule reads the arguments: ``elements``, one known type shared by every
# argument; ``pairs``, one key type and one value type; ``copy``, the view
# of the one argument, of the result kind; ``array``, the element of an
# array argument; ``argument``, the first argument's own view.
_JDK_FACTORIES: dict[tuple[str, str], tuple[str, str]] = {
    ("java.util.List", "of"): ("iterable", "elements"),
    ("java.util.Set", "of"): ("iterable", "elements"),
    ("java.util.Arrays", "asList"): ("iterable", "elements"),
    ("java.util.Collections", "singletonList"): ("iterable", "elements"),
    ("java.util.Collections", "singleton"): ("iterable", "elements"),
    ("java.util.stream.Stream", "of"): ("stream", "elements"),
    ("java.util.Optional", "of"): ("optional", "elements"),
    ("java.util.Optional", "ofNullable"): ("optional", "elements"),
    ("java.util.concurrent.CompletableFuture", "completedFuture"): ("future", "elements"),
    ("java.util.Map", "of"): ("map", "pairs"),
    ("java.util.List", "copyOf"): ("iterable", "copy"),
    ("java.util.Set", "copyOf"): ("iterable", "copy"),
    ("java.util.Map", "copyOf"): ("map", "copy"),
    **{("java.util.Collections", name): ("iterable", "copy") for name in (
        "unmodifiableCollection", "unmodifiableList", "unmodifiableSet", "unmodifiableSortedSet",
        "unmodifiableNavigableSet", "synchronizedCollection", "synchronizedList", "synchronizedSet",
    )},
    **{("java.util.Collections", name): ("map", "copy") for name in (
        "unmodifiableMap", "unmodifiableSortedMap", "unmodifiableNavigableMap", "synchronizedMap",
    )},
    ("java.util.Arrays", "stream"): ("stream", "array"),
    ("java.util.Objects", "requireNonNull"): ("", "argument"),
    # BL-43, ADR-90: ``static <T> ResponseEntity<T> ok(T body)``.
    ("org.springframework.http.ResponseEntity", "ok"): ("entity", "elements"),
}
_ACCESSOR_CALLS = {(name, count) for _kind, name, count in _ELEMENT_ACCESSORS}


# Members a source type inherits from a Spring Data repository interface
# (BL-43, ADR-90), stable across Spring Data 2.x and 3.x and documented in
# indexing.md: (interface, name, arity) -> (returned type, the interface's
# type arguments it holds). ``None`` returns the type argument itself;
# ``_SAVED`` returns the argument's type, else the type argument
# (``<S extends T> S save(S entity)``). ``findOne`` (Spring Data 1.x) and
# the same-arity ``findAll`` overloads are left out.
_SAVED = "<S extends T> S"
_CRUD_REPOSITORY = "org.springframework.data.repository.CrudRepository"
_LIST_CRUD_REPOSITORY = "org.springframework.data.repository.ListCrudRepository"
_JPA_REPOSITORY = "org.springframework.data.jpa.repository.JpaRepository"
_REPOSITORY_RETURNS: dict[tuple[str, str, int], tuple[str | None, tuple[int, ...]]] = {
    **{(interface, member, arity): returned for interface in (
        _CRUD_REPOSITORY, _LIST_CRUD_REPOSITORY, _JPA_REPOSITORY,
    ) for member, arity, returned in (
        ("findById", 1, ("java.util.Optional", (0,))),
        ("save", 1, (_SAVED, (0,))),
    )},
    (_CRUD_REPOSITORY, "findAll", 0): ("java.lang.Iterable", (0,)),
    (_CRUD_REPOSITORY, "findAllById", 1): ("java.lang.Iterable", (0,)),
    **{(interface, member, arity): ("java.util.List", (0,)) for interface in (
        _LIST_CRUD_REPOSITORY, _JPA_REPOSITORY,
    ) for member, arity in (("findAll", 0), ("findAllById", 1))},
}
# Methods that return the type argument of a ``Class<T>`` argument (BL-44,
# ADR-90): (receiver type, name, arity) -> the ``Class<T>`` position.
# ``getBean(String name, Class<T> requiredType)`` also needs a ``String``
# first argument, which ``getBean(Class<T>, Object...)`` does not take.
_CLASS_TYPED: dict[tuple[str, str, int], int] = {
    (owner, "getBean", arity): arity - 1 for owner in (
        "org.springframework.beans.factory.BeanFactory", "org.springframework.context.ApplicationContext",
    ) for arity in (1, 2)
}
_REPOSITORY_ARITIES = {_CRUD_REPOSITORY: 2, _LIST_CRUD_REPOSITORY: 2, _JPA_REPOSITORY: 2}
# Declared returns of the table that are more specific than another one:
# a repository extending two kinds takes the most specific (ADR-90).
_RETURN_SUPERTYPES: dict[str, frozenset[str]] = {
    "java.util.List": frozenset({"java.lang.Iterable"}),
}


class _Nested(NamedTuple):
    """A JDK container written as a type argument, as ``List<Order>`` in
    ``Map<String, List<Order>>`` (BL-41): its type, its container kind and
    its own type arguments. One level deep: a deeper argument is flat."""

    type: str
    kind: str
    arguments: tuple[str | None, ...]


# A type argument of a container view: a type name, or a nested container.
_Arg = str | _Nested
# A JDK container view: its kind and its type arguments (BL-27).
_View = tuple[str, tuple[_Arg | None, ...]]


def _flat(argument: _Arg | None) -> str | None:
    """The type a view argument gives where a type name is read: a nested
    container gives its own type, without its arguments."""
    return argument.type if isinstance(argument, _Nested) else argument
# The type arguments of a source generic type, by type parameter name (BL-26).
# ``None`` for a parameter whose argument the sources do not prove.
_Env = dict[str, str | None]
# Methods of ``java.lang.Object``: on a receiver typed by a type variable
# they produce no edge (ADR-81 rule 4), so the bound never types it.
_OBJECT_DISPATCH = {("equals", 1), ("hashCode", 0), ("toString", 0)}
_TYPE_CLAUSES = {"superclass", "super_interfaces", "extends_interfaces"}


def _written_type(source: bytes, parameter: Node) -> str:
    """The erased simple name of a parameter type, as written."""
    parameter_type = parameter.child_by_field_name("type")
    if parameter_type is None or parameter.type != "formal_parameter":
        return ""
    text = re.sub(r"<.*>", "", parser.node_text(source, parameter_type)).strip()
    dimensions = parameter.child_by_field_name("dimensions")
    if dimensions is not None:
        text += "[]" * parser.node_text(source, dimensions).count("[")
    return text.rsplit(".", 1)[-1]


def _dimensions(source: bytes, node: Node | None) -> int:
    """The ``[]`` written after a declared name, as in ``Foo x[]``."""
    dimensions = node.child_by_field_name("dimensions") if node is not None else None
    return parser.node_text(source, dimensions).count("[") if dimensions is not None else 0


def _spread_type(parameter: Node) -> Node | None:
    """The element type node of a ``T...`` parameter."""
    for child in parameter.named_children:
        if child.type in {"modifiers", "annotation", "marker_annotation", "variable_declarator"}:
            continue
        return child
    return None


def _refers_to_anonymous_instance(source: bytes, receiver: Node | None) -> bool:
    """Whether a receiver inside an anonymous class body denotes that instance."""
    if receiver is None or receiver.type in {"this", "super"}:
        return True
    return receiver.type == "field_access" and parser.node_text(source, receiver).startswith("this.")


def _assigned(source: bytes, scope: Node, name: str) -> bool:
    """Whether ``scope`` assigns ``name`` or ``this.name`` after its
    declaration: ``=``, a compound assignment, ``++`` or ``--``."""
    for node in parser.walk(scope):
        if node.type == "assignment_expression":
            target = node.child_by_field_name("left")
        elif node.type == "update_expression":
            target = node.named_children[0] if node.named_children else None
        else:
            continue
        if target is not None and re.sub(r"\s+", "", parser.node_text(source, target)) in {name, f"this.{name}"}:
            return True
    return False


def _declared_members(source: bytes, body: Node | None) -> set[tuple[str, int]]:
    """The ``(name, arity)`` of each method a class body declares."""
    members: set[tuple[str, int]] = set()
    for member in body.named_children if body is not None else []:
        if member.type != "method_declaration":
            continue
        formals = member.child_by_field_name("parameters")
        members.add((parser.declaration_name(member, source) or "", len([
            formal for formal in (formals.named_children if formals is not None else [])
            if formal.type in {"formal_parameter", "spread_parameter"}
        ])))
    return members


def _implicit_value(value: Node | None) -> bool:
    """Whether ``value`` creates an instance no indexed method stands for:
    a lambda, a method reference or an anonymous class (ADR-62, ADR-72)."""
    while value is not None and value.type == "parenthesized_expression" and value.named_children:
        value = value.named_children[0]
    return value is not None and (
        value.type in {"lambda_expression", "method_reference"}
        or value.type == "object_creation_expression"
        and any(child.type == "class_body" for child in value.children)
    )


def _is_anonymous_body(node: Node) -> bool:
    return node.type == "class_body" and node.parent is not None and node.parent.type == "object_creation_expression"


def _is_class_scope_body(node: Node) -> bool:
    """An anonymous class body or an enum constant's body: an unnamed class
    whose own fields come before those of the type around it (BC-14)."""
    return node.type == "class_body" and node.parent is not None and node.parent.type in {
        "object_creation_expression", "enum_constant",
    }


_CALL_SITES = {"method_invocation", "method_reference"}
# In-process events (PDR-16): a listener annotation and a publisher type.
_EVENT_LISTENERS = frozenset({"EventListener", "TransactionalEventListener"})
_EVENT_PUBLISHERS = frozenset({
    "ApplicationEventPublisher", "ApplicationContext", "ConfigurableApplicationContext",
})
# Scopes that can introduce a pattern variable into one of their children.
_PATTERN_SCOPES = {
    "if_statement", "while_statement", "ternary_expression", "binary_expression", "switch_rule",
    "switch_block_statement_group", "block", "constructor_body",
}
_CONSTRUCTOR_SITES = {"object_creation_expression", "explicit_constructor_invocation"}
# Nodes a switch arm's value cannot cross to reach its switch expression.
_FUNCTIONAL_BOUNDARIES = {"lambda_expression", "method_declaration", "class_body", "constructor_declaration"}
_INITIALIZER_MEMBERS = {"field_declaration", "block", "static_initializer", "constant_declaration"}


def _call_context(node: Node) -> Node | None:
    """The code a call site belongs to (ADR-62, ADR-75).

    A named method or constructor, or an initializer member (a field
    initializer, an initializer block) of a named type. The members of an
    anonymous class and lambda bodies belong to the code creating them.
    """
    current = node.parent
    while current is not None:
        if current.type == "method_declaration" or current.type in CONSTRUCTOR_DECLARATIONS:
            if not parser.is_anonymous_member(current):
                return current
        elif (
            current.type in _INITIALIZER_MEMBERS and current.parent is not None
            and current.parent.type in {"class_body", "enum_body_declarations", "interface_body"}
            and not _is_anonymous_body(current.parent)
        ):
            return current
        current = current.parent
    return None


def _in_anonymous_body(node: Node, context: Node | None) -> bool:
    """Whether ``node`` lies in an anonymous class body below ``context``."""
    current = node.parent
    while current is not None and current != context:
        if _is_anonymous_body(current):
            return True
        current = current.parent
    return False


@dataclass
class _Unit:
    source: bytes
    package: str
    imports: dict[str, str]
    wildcards: list[str]
    # ``import static p.Util.helper;`` maps ``helper`` to ``p.Util``.
    static_imports: dict[str, list[str]] = dataclass_field(default_factory=dict)
    # ``import static p.Util.*;`` contributes ``p.Util``.
    static_wildcards: list[str] = dataclass_field(default_factory=list)


@dataclass
class _Method:
    method: IntegrationMethod
    module: str
    owner: str
    node: Node
    parameters: tuple[str | None, ...]
    concrete: bool
    # Erased simple type names as written, to match an override whose
    # parameter type comes from an external wildcard import.
    written: tuple[str, ...] = ()
    # A static or private method binds to its own declaration: no subtype
    # overrides it and it implements no ancestor's contract.
    binds_statically: bool = False
    # The last parameter is ``T...``: any arity from the fixed count up.
    varargs: bool = False
    # ``parameters`` with each type variable replaced by its erasure: the
    # signature a body overrides with (JLS 4.6, 8.4.2). A contract keeps the
    # unresolved ``parameters``, which match any argument of a subtype.
    erased: tuple[str | None, ...] = ()
    # The resolved element type ``T`` of a ``T...`` parameter, if known: the
    # last parameter is ``T[]`` in Java's first two phases and ``T`` for
    # each trailing argument in the third (BC-08).
    spread: str | None = None


@dataclass(frozen=True)
class _Declarator:
    """How a scope declares a variable: its written type, if any.

    ``value`` is the initializer of a ``var``; ``lambda_slot`` is
    ``(lambda, position, count)`` for an untyped lambda parameter;
    ``element_of`` is the iterable of a ``var`` loop variable;
    ``dimensions`` counts the ``[]`` of ``Foo x[]``.
    """

    type_node: Node | None = None
    value: Node | None = None
    lambda_slot: tuple[Node, int, int] | None = None
    element_of: Node | None = None
    # The ``[]`` a C-style declarator writes after its name (BC-11).
    dimensions: int = 0


class JavaSymbols:
    """Build once per join; index types by FQN and methods by name/arity."""

    def __init__(
        self, root: Path, methods: list[IntegrationMethod], source_paths: Sequence[str] = (),
        *, module_dependencies: Sequence[ModuleDependency] = (),
    ):
        self.units: dict[str, _Unit] = {}
        self.types: dict[str, list[tuple[str, Node]]] = defaultdict(list)
        self.path_modules: dict[str, str] = {}
        self.bases: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
        # The source superclass of a class, the type ``super`` names.
        self.superclasses: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
        self.methods: dict[str, _Method] = {}
        self.by_owner: dict[tuple[str, str, int, str], list[_Method]] = defaultdict(list)
        self.varargs_by_owner: dict[tuple[str, str, str], list[_Method]] = defaultdict(list)
        self.dispatch: dict[tuple[str, str, str, tuple[str | None, ...]], list[_Method]] = defaultdict(list)
        self._ancestors: dict[tuple[str, str], set[tuple[str, str]]] = {}
        self.inherited_dispatch: dict[tuple[str, str], set[str]] = defaultdict(set)
        # Supertypes outside the sources, ``None`` when the name is unresolved.
        self.external_bases: dict[tuple[str, str], set[str | None]] = defaultdict(set)
        # External interfaces and the source types that implement them directly.
        self.external_implementors: dict[str, set[tuple[str, str]]] = defaultdict(set)
        self.interface_witnesses: dict[MethodCall, MethodCall] = {}
        # Typing notes for the call-edge diagnostic (ADR-87): calls whose
        # overloads stayed unresolved, by caller method ID, and JDK accessor
        # calls whose chain broke, by (path, start byte).
        self.unresolved_overloads: dict[str, list[tuple[int, str, tuple[str, ...]]]] = defaultdict(list)
        self.broken_accessors: dict[tuple[str, int], tuple[int, str]] = {}
        # ``var`` initializers being typed, to break reference cycles.
        self._typing: set[tuple[str, int]] = set()
        # Whether a type variable without an argument takes its bound; off
        # while typing the receiver of an ``Object`` method (ADR-81 rule 4).
        self._bounds = True
        # Local classes: qualified name -> (path, declaration, enclosing block).
        self.local_types: dict[str, list[tuple[str, Node, Node]]] = defaultdict(list)
        self._levels: dict[tuple[str, str], list[list[tuple[str, str]]]] = {}
        # Types an anonymous class body extends or implements somewhere.
        self.anonymous_supertypes: set[str] = set()
        # The ``(name, arity)`` each anonymous class body declares, by the
        # type it extends or implements (BL-30, ADR-72).
        self.anonymous_members: dict[str, set[tuple[str, int]]] = defaultdict(set)
        # Lambdas and method references, typed on demand by their target.
        self._functional_values: list[tuple[str, Node]] = []
        self._implicit: tuple[dict[str, set[tuple[str, int]]], set[str]] | None = None
        self._context_methods: dict[tuple[str, int, int], _Method] | None = None
        # Narrowing by type flow (BL-46, ADR-89): the class a method's
        # returns create, and the class a private parameter's sites pass.
        self._creation_returns: dict[str, str | None] = {}
        self._context_types: dict[tuple[str, int], str | None] = {}
        # Strict type arguments an implementor gives an ancestor (BL-45).
        self._implemented: dict[tuple[str, str, str], _Env | None] = {}
        self._by_name_arity: dict[tuple[str, int], list[_Method]] | None = None
        self._varargs_by_name: dict[str, list[_Method]] = defaultdict(list)
        # External classes and the source classes that extend them directly
        # (BL-31, ADR-74).
        self.external_subclasses: dict[str, set[tuple[str, str]]] = defaultdict(set)
        # Build dependencies between module identities (ADR-71), and the
        # modules each caller module sees, computed on demand.
        self.module_dependencies: dict[str, set[str]] = defaultdict(set)
        for dependency in module_dependencies:
            self.module_dependencies[dependency.source].add(dependency.target)
        self._visible_modules: dict[str, frozenset[str]] = {}
        self._external_targets: dict[
            tuple[str, str, int, tuple[str | None, ...] | None, frozenset[str]],
            list[IntegrationMethod],
        ] = {}
        by_path: dict[str, list[IntegrationMethod]] = defaultdict(list)
        for method in methods:
            by_path[method.path].append(method)
            self.path_modules.setdefault(method.path, method.module)
        # Include empty intermediate classes too: they have no method facts.
        paths = set(by_path)
        paths.update(path for path in source_paths if path.endswith(".java"))
        declarations: dict[str, dict[tuple[str, str, int], list[Node]]] = {}
        for path in sorted(paths):
            parsed = parser.parse_java(str(root.resolve()), path)
            if parsed is None:
                continue
            source, tree = parsed
            package = ""
            imports: dict[str, str] = {}
            wildcards: list[str] = []
            static_imports: dict[str, list[str]] = defaultdict(list)
            static_wildcards: list[str] = []
            for child in tree.named_children:
                text = parser.node_text(source, child)
                if child.type == "package_declaration":
                    package = text.removeprefix("package").removesuffix(";").strip()
                elif child.type == "import_declaration":
                    imported = text.removeprefix("import").removesuffix(";").strip()
                    if imported.startswith("static "):
                        imported = imported.removeprefix("static").strip()
                        if imported.endswith(".*"):
                            static_wildcards.append(imported[:-2])
                        elif "." in imported:
                            owner_name, member = imported.rsplit(".", 1)
                            static_imports[member].append(owner_name)
                    elif imported.endswith(".*"):
                        wildcards.append(imported[:-2])
                    else:
                        imports[imported.rsplit(".", 1)[-1]] = imported
            self.units[path] = _Unit(
                source, package, imports, wildcards, dict(static_imports), static_wildcards,
            )
            self.path_modules.setdefault(path, self.path_modules.get(path, path.split("/", 1)[0] if "/" in path else ""))
            nodes = list(parser.walk(tree))
            for node in nodes:
                if node.type in _TYPES:
                    self.types[self.owner(path, node)].append((path, node))
                    if node.parent is not None and (
                        node.parent.type not in _MEMBER_CONTAINERS or _is_anonymous_body(node.parent)
                    ):
                        self.local_types[self.owner(path, node)].append((path, node, node.parent))
                elif node.type == "object_creation_expression" and parser.child_by_type(node, "class_body"):
                    type_node = node.child_by_field_name("type")
                    if type_node is not None:
                        anonymous = self.resolve(
                            path, parser.node_text(source, type_node), self.owner(path, node),
                            allow_ambiguous=True, at=type_node,
                        )
                        if anonymous is not None:
                            self.anonymous_supertypes.add(anonymous)
                            self.anonymous_members[anonymous].update(
                                _declared_members(source, parser.child_by_type(node, "class_body"))
                            )
                elif node.type in {"lambda_expression", "method_reference"}:
                    self._functional_values.append((path, node))
                elif node.type == "annotation_type_declaration":
                    # An annotation type names parameters (``NewSpan``) but
                    # declares no method or supertype of its own.
                    outer = self.owner(path, node)
                    name = parser.declaration_name(node, source) or ""
                    self.types[f"{outer}.{name}" if outer else name].append((path, node))
            declarations[path] = defaultdict(list)
            for node in nodes:
                # An anonymous class method is no indexed method: its body
                # belongs to the method creating the instance.
                if node.type == "method_declaration" and not parser.is_anonymous_member(node):
                    key = (self.owner(path, node), parser.declaration_name(node, source) or "", node.start_point.row + 1)
                    declarations[path][key].append(node)
            # Constructors and initializers (ADR-75); an implicit constructor
            # and ``<clinit>`` are keyed on their type declaration.
            for initializer in initializer_declarations(tree, source):
                declarations[path][(
                    self.owner(path, initializer.node), initializer.name, initializer.start_line,
                )].append(initializer.node)
        for owner, locations in self.types.items():
            for path, node in locations:
                for child in node.named_children:
                    if child.type not in {"superclass", "super_interfaces", "extends_interfaces"}:
                        continue
                    for type_node in child.named_children:
                        candidates = type_node.named_children if type_node.type == "type_list" else [type_node]
                        for candidate in candidates:
                            base = self.resolve(
                                path,
                                parser.node_text(self.units[path].source, candidate),
                                owner,
                                allow_ambiguous=True,
                                at=candidate,
                            )
                            if base is not None and base in self.types and base != owner:
                                base_locations = self._type_candidates(base, path)
                                for base_path, _base_node in base_locations:
                                    base_module = self.path_modules.get(base_path, "")
                                    self.bases[(owner, self.path_modules.get(path, ""))].add(
                                        (base, base_module)
                                    )
                                    if child.type == "superclass":
                                        self.superclasses[(owner, self.path_modules.get(path, ""))].add(
                                            (base, base_module)
                                        )
                            elif base != owner:
                                type_key = (owner, self.path_modules.get(path, ""))
                                self.external_bases[type_key].add(base)
                                if base is not None and child.type != "superclass":
                                    self.external_implementors[base].add(type_key)
                                elif base is not None and base != "java.lang.Object":
                                    self.external_subclasses[base].add(type_key)
                                for known in _KNOWN_EXTERNAL_SUPERTYPES.get(base or "", ()):
                                    self.external_implementors[known].add(type_key)
        for path, path_methods in by_path.items():
            if path not in self.units:
                continue
            source = self.units[path].source
            for method in path_methods:
                name = method.qualified_method.rsplit(".", 1)[-1]
                owner = method.qualified_method.rsplit(".", 1)[0]
                matching = declarations[path].get((owner, name, method.start_line), [])
                if len(matching) > 1:
                    # Several declarations on one line: the persisted end
                    # line and written signature identify the node (S6).
                    matching = [
                        node for node in matching
                        if node.end_point.row + 1 == method.end_line
                        and method.parameter_types is not None
                        and declared_parameter_types(node, source) == method.parameter_types
                    ]
                if len(matching) != 1:
                    continue
                node = matching[0]
                # An implicit constructor and ``<clinit>`` stand on their type.
                owner_node = node if node.type in _TYPES else parser.enclosing(node, *_TYPES)
                if owner_node is None:
                    continue
                owner = self.owner(path, owner_node)
                if len(self._type_candidates(owner, path)) != 1:
                    continue
                initializer_node = name in INITIALIZER_NAMES
                parameters = None if node.type in _TYPES else declaration_parameters(node)
                parameter_types = []
                erased: list[str | None] = []
                written: list[str] = []
                for parameter in parameters.named_children if parameters is not None else []:
                    if parameter.type not in {"formal_parameter", "spread_parameter"}:
                        continue
                    parameter_type = parameter.child_by_field_name("type")
                    # Varargs and unresolved generic parameters are intentionally
                    # not treated as an ordinary overload signature.
                    resolved_type = (
                        self.resolve(path, parser.node_text(source, parameter_type), owner, at=parameter_type)
                        if parameter_type is not None and parameter.type == "formal_parameter" else None
                    )
                    erased_type = resolved_type if resolved_type is not None or parameter_type is None or (
                        parameter.type != "formal_parameter"
                    ) else self._erasure(path, parameter_type, owner)
                    dimensions = parameter.child_by_field_name("dimensions")
                    if dimensions is not None:
                        suffix = "[]" * parser.node_text(source, dimensions).count("[")
                        resolved_type = resolved_type + suffix if resolved_type is not None else None
                        erased_type = erased_type + suffix if erased_type is not None else None
                    parameter_types.append(resolved_type)
                    erased.append(erased_type)
                    written.append(_written_type(source, parameter))
                spread: str | None = None
                last = parameters.named_children[-1] if parameters is not None and parameters.named_children else None
                if last is not None and last.type == "spread_parameter":
                    spread_type = _spread_type(last)
                    spread = self.resolve(
                        path, parser.node_text(source, spread_type), owner, at=spread_type,
                    ) if spread_type is not None else None
                modifiers = parser.modifiers_node(node)
                info = _Method(
                    method, method.module, owner, node, tuple(parameter_types),
                    node.child_by_field_name("body") is not None, tuple(written),
                    # A constructor is not inherited and is never overridden.
                    initializer_node or bool(modifiers is not None and {"static", "private"} & set(
                        parser.node_text(source, modifiers).split()
                    )),
                    varargs=bool(parameters is not None and parameters.named_children and (
                        parameters.named_children[-1].type == "spread_parameter"
                    )),
                    erased=tuple(erased),
                    spread=spread,
                )
                self.methods[method.id] = info
                self.by_owner[(owner, name, len(parameter_types), method.module)].append(info)
                if info.varargs:
                    self.varargs_by_owner[(owner, name, method.module)].append(info)
                # A body whose parameter is a type variable overrides by its
                # erased signature (BL-26).
                if info.concrete and all(value is not None for value in info.erased):
                    for base_owner, base_module in self._dispatch_owners(info):
                        self.dispatch[(base_owner, base_module, name, info.erased)].append(info)

        # Secondary indexes of ``by_owner``, so a call site reads only the
        # declarations of its receiver's types instead of scanning them all
        # (S13): by owner, name and arity across modules, in ``by_owner``
        # order, and the declared arities of a name per owner.
        self._by_owner_name: dict[tuple[str, str, int], list[_Method]] = defaultdict(list)
        self._by_owner_order: dict[tuple[str, str, int, str], int] = {}
        self._arities: dict[tuple[str, str], set[int]] = defaultdict(set)
        for order, ((owner_name, method_name, arity, module_name), values) in enumerate(self.by_owner.items()):
            self._by_owner_name[(owner_name, method_name, arity)].extend(values)
            self._by_owner_order[(owner_name, method_name, arity, module_name)] = order
            self._arities[(owner_name, method_name)].add(arity)

        self._complete_override_signatures()
        self._index_inherited_implementations()
        # ``dispatch`` is complete: index its signatures by name and arity so
        # a generic contract reads only the candidates it can match.
        self._dispatch_signatures: dict[
            tuple[str, str, str, int], list[tuple[str | None, ...]]
        ] = defaultdict(list)
        for dispatch_owner, dispatch_module, dispatch_name, signature in self.dispatch:
            self._dispatch_signatures[
                (dispatch_owner, dispatch_module, dispatch_name, len(signature))
            ].append(signature)
        # Concrete bodies whose signature stays unresolved: dispatch cannot
        # index them, yet they may override a contract (S9, ADR-81).
        self._unresolved_bodies: dict[tuple[str, int], list[_Method]] = defaultdict(list)
        for info in self.methods.values():
            if info.concrete and not info.binds_statically and None in info.erased:
                name = info.method.qualified_method.rsplit(".", 1)[-1]
                self._unresolved_bodies[(name, len(info.parameters))].append(info)
        # ``@FeignClient`` interfaces: Spring implements them with a runtime
        # proxy that no source body stands for (Q8, ADR-81 rule 3).
        self.proxied_interfaces: set[tuple[str, str]] = {
            (owner, self.path_modules.get(path, ""))
            for owner, locations in self.types.items()
            for path, node in locations
            if node.type == "interface_declaration" and self._feign_client(path, node)
        }
        self._subtypes: dict[tuple[str, str], list[tuple[str, str, str, Node]]] = {}
        # Last: it reads the lambda target types, which need every index.
        self._drop_unreachable_defaults()

    def _feign_client(self, path: str, declaration: Node) -> bool:
        unit = self.units[path]
        for annotation in parser.annotations_of(declaration):
            written = parser.node_text(unit.source, annotation.child_by_field_name("name") or annotation)
            if written.rsplit(".", 1)[-1] != "FeignClient":
                continue
            qualified = written if "." in written else unit.imports.get(written, "")
            if "feign" in qualified or any("feign" in package for package in unit.wildcards):
                return True
        return False

    def _concrete_subtypes(self, owner: str, module: str) -> list[tuple[str, str, str, Node]]:
        """Source classes, records and enums that can be the runtime type of
        a receiver typed ``owner``: ``(type, module, path, declaration)``."""
        key = (owner, module)
        found = self._subtypes.get(key)
        if found is None:
            found = []
            for candidate, locations in self.types.items():
                for path, node in locations:
                    if node.type not in {"class_declaration", "record_declaration", "enum_declaration"}:
                        continue
                    modifiers = parser.modifiers_node(node)
                    if node.type == "class_declaration" and modifiers is not None and (
                        "abstract" in parser.node_text(self.units[path].source, modifiers).split()
                    ):
                        continue
                    candidate_module = self.path_modules.get(path, "")
                    if key in self.ancestors(candidate, candidate_module):
                        found.append((candidate, candidate_module, path, node))
            self._subtypes[key] = found
        return found

    def _generated_member(self, path: str, declaration: Node, name: str, arity: int) -> bool:
        """Whether the compiler or Lombok writes ``name`` on this type.

        A record accessor and Lombok's getters, setters, ``equals``,
        ``hashCode`` and ``toString`` are implementors without an indexed
        body (ADR-81 rule 1).
        """
        source = self.units[path].source
        fields: list[tuple[str, Node | None]] = []
        if declaration.type == "record_declaration":
            parameters = declaration.child_by_field_name("parameters")
            for parameter in parameters.named_children if parameters is not None else []:
                ident = parameter.child_by_field_name("name")
                if ident is not None and arity == 0 and parser.node_text(source, ident) == name:
                    return True
            return False
        if declaration.type != "class_declaration":
            return False
        if (name, arity) in {("equals", 1), ("hashCode", 0)}:
            return self._lombok_annotated(path, declaration, {"Data", "Value", "EqualsAndHashCode"})
        if (name, arity) == ("toString", 0):
            return self._lombok_annotated(path, declaration, {"Data", "Value", "ToString"})
        prefix = next((prefix for prefix in ("get", "is", "set") if name.startswith(prefix)), None)
        if prefix is None or arity != (1 if prefix == "set" else 0):
            return False
        accessors = {"Getter"} if prefix != "set" else {"Setter"}
        class_level = self._lombok_annotated(
            path, declaration, accessors | ({"Data"} if prefix == "set" else {"Data", "Value"}),
        )
        body = declaration.child_by_field_name("body")
        for member in body.named_children if body is not None else []:
            if member.type != "field_declaration":
                continue
            modifiers = parser.modifiers_node(member)
            if modifiers is not None and "static" in parser.node_text(source, modifiers).split():
                continue
            if not class_level and not self._lombok_annotated(path, member, accessors):
                continue
            for child in member.named_children:
                ident = child.child_by_field_name("name")
                field_name = parser.node_text(source, ident) if ident is not None else ""
                fields.append((field_name, member.child_by_field_name("type")))
        suffix = name[len(prefix):]
        return any(
            field_name and field_name[:1].upper() + field_name[1:] == suffix
            and (prefix != "is" or (type_node is not None and parser.node_text(source, type_node) == "boolean"))
            for field_name, type_node in fields
        )

    def _incomplete_implementors(
        self, receiver_type: str, caller: _Method, name: str, arity: int, targets: list[IntegrationMethod],
        excluded: Callable[[str, str], bool] | None = None,
    ) -> bool:
        """Whether some runtime type of the receiver has a body no target is.

        Every concrete source subtype of the receiver, in the modules the
        caller sees, must inherit or declare one of ``targets`` and generate
        no member of its own for the call. Otherwise its body is generated
        (record, Lombok), inherited from an external class or ``Object``, or
        unresolved, and no target is exact (S10, ADR-81 rule 1). A
        ``@FeignClient`` proxy is such a body too (Q8, rule 3). A subtype
        ``excluded`` holds cannot be the runtime type (BL-45).
        """
        receiver_module = self._receiver_module(receiver_type, caller)
        if self._proxied(receiver_type, receiver_module):
            return True
        receiver_ancestors = self.ancestors(receiver_type, receiver_module)
        # A subtype body with an unresolved signature may not override the
        # contract: it proves nothing (S9). A type variable's erasure is a
        # resolved signature (BL-26).
        bodies = {
            (info.owner, info.module)
            for target in targets
            for info in [self.methods.get(target.id)]
            if info is not None and (None not in info.erased or (info.owner, info.module) in receiver_ancestors)
        }
        visible = self.visible_modules(caller.module) | {receiver_module}
        for owner, module, path, node in self._concrete_subtypes(receiver_type, receiver_module):
            if module not in visible or excluded is not None and excluded(owner, module):
                continue
            if (owner, module) not in bodies and self._generated_member(path, node, name, arity):
                return True
            if not bodies & self.ancestors(owner, module):
                return True
        return False

    def _proxied(self, receiver_type: str, receiver_module: str) -> bool:
        """Whether a ``@FeignClient`` proxy can be the receiver at run time."""
        key = (receiver_type, receiver_module)
        return any(key in self.ancestors(*client) for client in self.proxied_interfaces)

    def _complete_override_signatures(self) -> None:
        """Give an override the signature of the one contract it can match.

        A parameter type imported through an external wildcard (``import
        org.springframework.http.*``) cannot be resolved from source, while
        the interface may import it explicitly. Java overrides by erased
        signature, so a concrete method adopts an ancestor's resolved
        signature when exactly one ancestor declaration agrees on every
        resolved type and on the written simple name of every unresolved one.
        """
        for info in list(self.methods.values()):
            if (
                not info.concrete or info.binds_statically
                or all(value is not None for value in info.parameters)
            ):
                continue
            name = info.method.qualified_method.rsplit(".", 1)[-1]
            signatures = {
                contract.parameters
                for base_owner, base_module in self.ancestors(info.owner, info.module)
                if (base_owner, base_module) != (info.owner, info.module)
                for contract in self.by_owner.get((base_owner, name, len(info.parameters), base_module), [])
                if all(value is not None for value in contract.parameters)
                and all(
                    actual == expected if actual is not None
                    else expected is not None and expected.rsplit(".", 1)[-1] == written
                    for actual, expected, written in zip(
                        info.parameters, contract.parameters, info.written, strict=True,
                    )
                )
            }
            if len(signatures) != 1:
                continue
            info.parameters = info.erased = next(iter(signatures))
            for base_owner, base_module in self.ancestors(info.owner, info.module):
                self.dispatch[(base_owner, base_module, name, info.parameters)].append(info)

    def _dispatch_owners(self, info: _Method) -> set[tuple[str, str]]:
        """The types whose contract ``info`` can implement.

        A static method only hides an ancestor's homonym and a private one
        is not inherited: both answer only for their own type (S2, S4).
        """
        if info.binds_statically:
            return {(info.owner, info.module)}
        return self.ancestors(info.owner, info.module)

    def _index_inherited_implementations(self) -> None:
        """Associate inherited public bodies with contracts on source subtypes."""
        by_type: dict[tuple[str, str], list[_Method]] = defaultdict(list)
        for info in self.methods.values():
            by_type[(info.owner, info.module)].append(info)
        for owner, locations in self.types.items():
            for path, node in locations:
                if node.type != "class_declaration":
                    continue
                ancestors = self.ancestors(owner, self.path_modules.get(path, ""))
                signatures: dict[tuple[str, tuple[str | None, ...]], list[_Method]] = defaultdict(list)
                for ancestor in ancestors:
                    for info in by_type.get(ancestor, []):
                        name = info.method.qualified_method.rsplit(".", 1)[-1]
                        signatures[(name, info.parameters)].append(info)
                for (name, parameters), declarations in signatures.items():
                    if any(parameter is None for parameter in parameters):
                        continue
                    bodies = [info for info in declarations if info.concrete and not any(
                        other is not info
                        and (info.owner, info.module) != (other.owner, other.module)
                        and (info.owner, info.module) in self.ancestors(other.owner, other.module)
                        for other in declarations
                    )]
                    for info in bodies:
                        if info.binds_statically:
                            continue
                        modifiers = next((child for child in info.node.children if child.type == "modifiers"), None)
                        words = set(parser.node_text(self.units[info.method.path].source, modifiers).split()) if modifiers else set()
                        if "public" not in words or words & {"static", "private"}:
                            continue
                        for base_owner, base_module in ancestors:
                            key = (base_owner, base_module, name, parameters)
                            if info not in self.dispatch[key]:
                                self.dispatch[key].append(info)
                            self.inherited_dispatch[(base_owner, base_module)].add(info.method.id)

    def _drop_unreachable_defaults(self) -> None:
        """Remove interface default bodies that no source type inherits.

        Java's "class wins" rule: a method a class declares or inherits from
        its superclass chain, abstract or concrete, takes precedence over an
        interface default, and a more specific interface's declaration
        overrides a default of its superinterface (S12). A default body
        stays a target when some source class, record or enum inherits it,
        when an anonymous class may, when a lambda or a method reference
        implements the interface or a subinterface (BC-09), when no source
        type implements the interface at all, or when its signature is not
        fully resolved.
        """
        defaults = [
            info for info in self.methods.values()
            if info.concrete and not info.binds_statically
            and None not in info.parameters
            and self._owner_kind(info) == "interface_declaration"
        ]
        if not defaults:
            return
        classes = [
            (owner, self.path_modules.get(path, ""))
            for owner, locations in self.types.items()
            for path, node in locations
            if node.type in {"class_declaration", "record_declaration", "enum_declaration"}
        ]
        anonymous = {
            (supertype, module)
            for supertype in self.anonymous_supertypes
            for path, _node in self.types.get(supertype, [])
            for module in [self.path_modules.get(path, "")]
        }
        for info in defaults:
            key = (info.owner, info.module)
            if any(key in self.ancestors(*supertype) for supertype in anonymous):
                continue
            users = [user for user in classes if key in self.ancestors(*user)]
            if not users or any(self._inherits_default(user, info) for user in users):
                continue
            if info.owner in self._implicit_implementations()[1]:
                # A lambda or method reference implementing the interface
                # inherits the default body (ADR-72).
                continue
            name = info.method.qualified_method.rsplit(".", 1)[-1]
            for (_owner, _module, method_name, parameters), targets in self.dispatch.items():
                if method_name == name and parameters == info.parameters and info in targets:
                    targets.remove(info)
            for implemented in self.inherited_dispatch.values():
                implemented.discard(info.method.id)

    @staticmethod
    def _owner_kind(info: _Method) -> str | None:
        owner_node = parser.enclosing(info.node, *_TYPES)
        return owner_node.type if owner_node is not None else None

    def _inherits_default(self, user: tuple[str, str], default: _Method) -> bool:
        """Whether the class ``user`` gets ``default``'s body (JLS 8.4.8)."""
        name = default.method.qualified_method.rsplit(".", 1)[-1]
        arity = len(default.parameters)

        def declares(owner: tuple[str, str]) -> bool | None:
            """Whether ``owner`` declares the signature; ``None`` if unknown."""
            declarations = self.by_owner.get((owner[0], name, arity, owner[1]), [])
            if any(candidate.parameters == default.parameters for candidate in declarations):
                return True
            if any(None in candidate.parameters for candidate in declarations):
                return None
            return False

        current: tuple[str, str] | None = user
        seen: set[tuple[str, str]] = set()
        while current is not None and current not in seen:
            seen.add(current)
            verdict = declares(current)
            if verdict is None:
                return True
            if verdict:
                return False
            current = self._superclass(*current)
        defaulting = (default.owner, default.module)
        for interface in self.ancestors(*user):
            if interface == defaulting or defaulting not in self.ancestors(*interface):
                continue
            verdict = declares(interface)
            if verdict is None:
                return True
            if verdict:
                # A subinterface redeclares it: its declaration wins.
                return False
        return True

    def owner(self, path: str, node: Node) -> str:
        names: list[str] = []
        current: Node | None = node
        while current is not None:
            if current.type in _TYPES:
                names.append(parser.declaration_name(current, self.units[path].source) or "")
            current = current.parent
        return ".".join(filter(None, [self.units[path].package, *reversed(names)]))

    def _type_candidates(self, qualified_name: str, path: str) -> list[tuple[str, Node]]:
        """Return visible declarations, preferring the caller's build module.

        Java source-only analysis can see two modules that declare the same
        qualified type, even though a normal build would select one through
        its module classpath.  The source module is the strongest available
        discriminator for internal flow discovery.  If it still leaves more
        than one candidate, resolution remains conservative.
        """
        candidates = self.types.get(qualified_name, [])
        module = self.path_modules.get(path)
        if module is None:
            return candidates
        same_module = [candidate for candidate in candidates if self.path_modules.get(candidate[0]) == module]
        candidates = same_module or candidates
        if len(candidates) > 1 and not is_test_source(path):
            # Production code never sees its module's test source set.
            production = [candidate for candidate in candidates if not is_test_source(candidate[0])]
            candidates = production or candidates
        return candidates

    def resolve(
        self, path: str, text: str, owner: str, *, allow_ambiguous: bool = False,
        at: Node | None = None,
    ) -> str | None:
        """The qualified source or external type ``text`` names in ``path``.

        ``at`` is the node where the name is written. A type parameter of an
        enclosing method or class declared there hides every type of the
        same simple name, so the name has no type (S8). A local class is
        visible only from its declaration to the end of its block, or from
        inside itself (S7).
        """
        text = re.sub(r"<.*>", "", text).strip()
        if at is not None and text.split(".", 1)[0].split("[", 1)[0] in self._type_parameters(at):
            return None
        if text.endswith("[]"):
            element = self.resolve(path, text[:-2], owner, allow_ambiguous=allow_ambiguous, at=at)
            return element + "[]" if element else None
        if text in _PRIMITIVES:
            return text
        unit = self.units[path]
        if (
            text in self.types and ("." in text or not unit.package)
            and not self._hidden_local(text, path, owner, at)
        ):
            return text if allow_ambiguous or len(self._type_candidates(text, path)) == 1 else None
        scope = owner
        while scope and scope != unit.package:
            nested = f"{scope}.{text}"
            if nested in self.types and not self._hidden_local(nested, path, owner, at):
                return nested if allow_ambiguous or len(self._type_candidates(nested, path)) == 1 else None
            scope = scope.rpartition(".")[0]
        first, *rest = text.split(".")
        if first in unit.imports:
            imported = ".".join([unit.imports[first], *rest])
            return imported if allow_ambiguous or len(self._type_candidates(imported, path)) <= 1 else None
        local = f"{unit.package}.{text}" if unit.package else text
        if local in self.types and not self._hidden_local(local, path, owner, at):
            return local if allow_ambiguous or len(self._type_candidates(local, path)) == 1 else None
        candidates = {
            f"{package}.{text}"
            for package in unit.wildcards
            if f"{package}.{text}" in self.types
            and (allow_ambiguous or len(self._type_candidates(f"{package}.{text}", path)) == 1)
        }
        if text in _JAVA_LANG:
            candidates.add(f"java.lang.{text}")
        # ``import java.util.concurrent.*`` makes ``Callable`` a JDK type
        # whose members are known; any other external wildcard stays open.
        candidates.update(
            f"{package}.{text}" for package in unit.wildcards
            if f"{package}.{text}" in _KNOWN_EXTERNAL_MEMBERS or f"{package}.{text}" in _KNOWN_EXTERNAL_SUPERTYPES
        )
        if len(candidates) == 1:
            candidate = next(iter(candidates))
            return candidate if allow_ambiguous or len(self.types.get(candidate, [None])) == 1 else None
        if not candidates and re.fullmatch(r"[\w$]+(?:\.[\w$]+)+", text):
            return text
        return None

    def _type_parameters(self, at: Node) -> set[str]:
        """Type parameter names declared by the methods and types enclosing ``at``."""
        names: set[str] = set()
        current: Node | None = at
        while current is not None:
            if current.type in _GENERIC_DECLARATIONS:
                parameters = current.child_by_field_name("type_parameters")
                for parameter in parameters.named_children if parameters is not None else []:
                    ident = next((
                        child for child in parameter.named_children
                        if child.type in {"type_identifier", "identifier"}
                    ), None)
                    if parameter.type == "type_parameter" and ident is not None:
                        names.add(ident.text.decode("utf-8") if ident.text is not None else "")
            current = current.parent
        return names

    @staticmethod
    def _declared_type_parameters(declaration: Node) -> list[tuple[str, Node]]:
        """The type parameters a type or method declaration writes, in order."""
        parameters = declaration.child_by_field_name("type_parameters")
        found: list[tuple[str, Node]] = []
        for parameter in parameters.named_children if parameters is not None else []:
            ident = next((
                child for child in parameter.named_children
                if child.type in {"type_identifier", "identifier"}
            ), None)
            if parameter.type == "type_parameter" and ident is not None and ident.text is not None:
                found.append((ident.text.decode("utf-8"), parameter))
        return found

    def _type_variable(self, name: str, at: Node) -> Node | None:
        """The innermost type parameter ``name`` visible at ``at``, if any."""
        current: Node | None = at
        while current is not None:
            if current.type in _GENERIC_DECLARATIONS:
                for declared, parameter in self._declared_type_parameters(current):
                    if declared == name:
                        return parameter
            current = current.parent
        return None

    def _bound(self, path: str, parameter: Node, owner: str, *, erase: bool = False, depth: int = 0) -> str | None:
        """The first bound of a type parameter, as the sources resolve it.

        A parameter without a bound has none: it stays untyped, unless
        ``erase`` asks for its erasure, ``java.lang.Object`` (JLS 4.6). A
        bound that is itself a type variable takes that variable's bound.
        Typing by a bound is off while an ``Object`` method's receiver is
        typed (ADR-81 rule 4); an erasure is not.
        """
        if not erase and not self._bounds:
            return None
        bounds = parser.child_by_type(parameter, "type_bound")
        bound = bounds.named_children[0] if bounds is not None and bounds.named_children else None
        if bound is None:
            return "java.lang.Object" if erase else None
        text = parser.node_text(self.units[path].source, bound).strip()
        variable = self._type_variable(text, bound) if bound.type == "type_identifier" else None
        if variable is not None:
            return self._bound(path, variable, owner, erase=erase, depth=depth + 1) if depth < 8 else None
        return self.resolve(path, text, owner, allow_ambiguous=True, at=bound)

    def _erasure(self, path: str, type_node: Node, owner: str) -> str | None:
        """The erasure of a parameter type written as a type variable or an
        array of one; ``None`` for any other type ``resolve`` leaves open."""
        dimensions = 0
        element = type_node
        while element.type == "array_type":
            inner = element.child_by_field_name("element")
            written = element.child_by_field_name("dimensions")
            if inner is None or written is None:
                return None
            dimensions += parser.node_text(self.units[path].source, written).count("[")
            element = inner
        if element.type != "type_identifier":
            return None
        variable = self._type_variable(parser.node_text(self.units[path].source, element).strip(), element)
        if variable is None:
            return None
        erased = self._bound(path, variable, owner, erase=True)
        return erased + "[]" * dimensions if erased is not None else None

    def _declared_type(self, path: str, type_node: Node, owner: str, env: _Env | None = None) -> str | None:
        """A written type, with its type variable substituted (BL-26).

        ``env`` holds the type arguments of the declaring types, read at a
        use through a receiver: a type variable takes its argument there,
        and nothing when the receiver does not prove it. Without ``env``
        the use is inside the generic code itself, so a type variable takes
        its bound, and no bound leaves it untyped.
        """
        text = parser.node_text(self.units[path].source, type_node).strip()
        if type_node.type == "type_identifier":
            variable = self._type_variable(text, type_node)
            if variable is not None:
                if env is not None:
                    return env.get(text)
                return self._bound(path, variable, owner)
        return self.resolve(path, text, owner, allow_ambiguous=True, at=type_node)

    def _type_bounds(self, path: str, declaration: Node, owner: str) -> _Env:
        """A generic declaration's type parameters, each bound to its bound:
        the arguments of a raw type, or of the declaration seen from its own
        code."""
        return {
            name: self._bound(path, parameter, owner)
            for name, parameter in self._declared_type_parameters(declaration)
        }

    def _owner_env(self, owner: str, module: str) -> _Env:
        """The arguments of ``owner`` seen from its own code: its bounds."""
        locations = self._module_locations(owner, module)
        if len(locations) != 1:
            return {}
        location, declaration = locations[0]
        return self._type_bounds(location, declaration, owner)

    def _type_argument_value(self, path: str, argument: Node, owner: str, env: _Env | None) -> str | None:
        """A written type argument: ``? extends X`` gives ``X``, any other
        wildcard and a nested parameterized type nothing."""
        if argument.type == "wildcard":
            bound = argument.named_children[-1] if argument.named_children else None
            text = parser.node_text(self.units[path].source, argument)
            if bound is None or not re.match(r"\?\s*extends\b", text):
                return None
            argument = bound
        if argument.type == "generic_type":
            return None
        return self._declared_type(path, argument, owner, env)

    def _view_argument(self, path: str, argument: Node, owner: str, env: _Env | None) -> _Arg | None:
        """A written type argument of a JDK container view (BL-41): a JDK
        container written there, with the arguments its count matches, is
        a nested view, its own arguments substituted from ``env``; any
        other argument is read as :meth:`_type_argument_value` reads it."""
        if argument.type == "wildcard":
            bound = argument.named_children[-1] if argument.named_children else None
            text = parser.node_text(self.units[path].source, argument)
            if bound is None or not re.match(r"\?\s*extends\b", text):
                return None
            argument = bound
        if argument.type != "generic_type":
            return self._type_argument_value(path, argument, owner, env)
        name_node = argument.named_children[0]
        container = self.resolve(
            path, parser.node_text(self.units[path].source, name_node), owner, allow_ambiguous=True, at=name_node,
        )
        if container not in _GENERIC_CONTAINERS:
            return None
        kind, arity = _GENERIC_CONTAINERS[container]
        written = parser.child_by_type(argument, "type_arguments")
        inner = written.named_children if written is not None else []
        if len(inner) != arity:
            return None
        return _Nested(container, kind, tuple(
            self._type_argument_value(path, value, owner, env) for value in inner
        ))

    def _parameterized(
        self, path: str, type_node: Node, owner: str, env: _Env | None, *, strict: bool = False,
    ) -> tuple[str, _Env] | None:
        """A written source type and its type arguments by parameter name.

        A raw type gives each parameter its bound (its erasure); a diamond
        or a count mismatch gives nothing. ``None`` when ``type_node`` names
        no unique source type. ``strict`` keeps only the arguments the code
        writes as concrete types (:meth:`_strict_argument`): a raw type then
        gives no argument (BL-45).
        """
        source = self.units[path].source
        name_node, arguments = type_node, None
        if type_node.type == "generic_type":
            name_node = type_node.named_children[0]
            arguments = parser.child_by_type(type_node, "type_arguments")
        elif type_node.type not in {"type_identifier", "scoped_type_identifier"}:
            return None
        resolved = self.resolve(path, parser.node_text(source, name_node), owner, allow_ambiguous=True, at=name_node)
        locations = self._type_candidates(resolved, path) if resolved is not None else []
        if resolved is None or len(locations) != 1:
            return None
        location, declaration = locations[0]
        parameters = self._declared_type_parameters(declaration)
        if arguments is None:
            if strict:
                return resolved, {name: None for name, _parameter in parameters}
            return resolved, self._type_bounds(location, declaration, resolved)
        written = arguments.named_children
        if len(written) != len(parameters):
            return resolved, {}
        return resolved, {
            name: (
                self._strict_argument(path, argument, owner, env) if strict
                else self._type_argument_value(path, argument, owner, env)
            )
            for (name, _parameter), argument in zip(parameters, written, strict=True)
        }

    def _strict_argument(self, path: str, argument: Node, owner: str, env: _Env | None) -> str | None:
        """A type argument written as a concrete type, or an array of one;
        a type variable takes its value in ``env`` only. A wildcard and a
        nested parameterized type give nothing (BL-45)."""
        element: Node | None = argument
        while element is not None and element.type == "array_type":
            element = element.child_by_field_name("element")
        if element is None or element.type in {"wildcard", "generic_type"}:
            return None
        text = parser.node_text(self.units[path].source, argument).strip()
        if element.type == "type_identifier" and self._type_variable(
            parser.node_text(self.units[path].source, element).strip(), element,
        ) is not None:
            return (env or {}).get(text) if element is argument else None
        return self.resolve(path, text, owner, allow_ambiguous=True, at=argument)

    def _supertype_env(
        self, owner: str, module: str, env: _Env, target: str, depth: int = 0, *, strict: bool = False,
    ) -> _Env | None:
        """The type arguments ``owner`` gives its ancestor ``target``.

        Read through the written ``extends`` and ``implements`` clauses, each
        argument substituted by the arguments of the type that writes it.
        ``None`` when no path or two disagreeing paths reach ``target``.
        """
        if owner == target:
            return env
        if depth > 16:
            return None
        found: list[_Env] = []
        for path, declaration in self._module_locations(owner, module):
            for clause in declaration.named_children:
                if clause.type not in _TYPE_CLAUSES:
                    continue
                for type_node in clause.named_children:
                    for candidate in type_node.named_children if type_node.type == "type_list" else [type_node]:
                        parameterized = self._parameterized(path, candidate, owner, env, strict=strict)
                        if parameterized is None:
                            continue
                        base, base_env = parameterized
                        base_module = self._module_of(base, path)
                        if base != target and all(
                            ancestor != target for ancestor, _ in self.ancestors(base, base_module)
                        ):
                            continue
                        result = self._supertype_env(
                            base, base_module, base_env, target, depth + 1, strict=strict,
                        )
                        if result is not None:
                            found.append(result)
        return found[0] if found and all(result == found[0] for result in found) else None

    def _module_of(self, owner: str, path: str) -> str:
        locations = self._type_candidates(owner, path)
        return self.path_modules.get(locations[0][0], "") if len(locations) == 1 else self.path_modules.get(path, "")

    def _hidden_local(self, qualified_name: str, path: str, owner: str, at: Node | None) -> bool:
        """Whether a local class in ``qualified_name`` is out of scope here.

        A local class is no member of its enclosing type: it is visible from
        its declaration to the end of the enclosing block, and inside itself.
        """
        if not self.local_types:
            return False
        parts = qualified_name.split(".")
        for end in range(len(parts), 0, -1):
            prefix = ".".join(parts[:end])
            scopes = self.local_types.get(prefix)
            if not scopes:
                continue
            if owner == prefix or owner.startswith(prefix + "."):
                return False
            return not (at is not None and any(
                scope_path == path and declaration.start_byte <= at.start_byte < block.end_byte
                for scope_path, declaration, block in scopes
            ))
        return False

    def ancestors(self, owner: str, module: str) -> set[tuple[str, str]]:
        key = (owner, module)
        if key not in self._ancestors:
            seen: set[tuple[str, str]] = set()
            pending = [key]
            while pending:
                current = pending.pop()
                if current not in seen:
                    seen.add(current)
                    pending.extend(self.bases.get(current, ()))
            self._ancestors[key] = seen
        return self._ancestors[key]

    def implementations(self, contract: _Method) -> list[_Method]:
        if contract.binds_statically:
            # No subtype body, even a homonym, overrides a static or
            # private method.
            return [contract] if contract.concrete else []
        name = contract.method.qualified_method.rsplit(".", 1)[-1]
        if not any(parameter is None for parameter in contract.parameters):
            return [
                *self.dispatch.get((contract.owner, contract.module, name, contract.parameters), []),
                *self._unresolved_overrides(contract, name),
            ]
        # Generic contracts can expose a type variable in the source AST
        # while a concrete implementation has a resolved parameter type.
        # Gather compatible arities and let the caller reject ambiguity. A
        # ``T...`` parameter is overridden only by ``T[]`` (BC-08).
        expected_types = (
            (*contract.parameters[:-1], contract.spread + "[]")
            if contract.varargs and contract.spread is not None else contract.parameters
        )
        candidates = {
            candidate.method.id: candidate
            for parameters in self._dispatch_signatures.get(
                (contract.owner, contract.module, name, len(contract.parameters)), ()
            )
            for candidate in self.dispatch[(contract.owner, contract.module, name, parameters)]
            if all(
                expected is None or actual == expected
                for expected, actual in zip(expected_types, candidate.parameters)
            )
        }
        # A concrete body with an unresolved parameter type is never indexed
        # for dispatch, yet it still implements itself.
        if contract.concrete:
            candidates.setdefault(contract.method.id, contract)
        for override in self._unresolved_overrides(contract, name):
            candidates.setdefault(override.method.id, override)
        return list(candidates.values())

    def _unresolved_overrides(self, contract: _Method, name: str) -> list[_Method]:
        """Subtype bodies that may override ``contract`` but whose signature
        stays unresolved (varargs, an external wildcard import): possible
        implementors, never proof of a unique one (S9, ADR-81)."""
        key = (contract.owner, contract.module)
        return [
            info for info in self._unresolved_bodies.get((name, len(contract.parameters)), [])
            if info is not contract and (info.owner, info.module) != key
            and key in self.ancestors(info.owner, info.module)
            and all(
                expected == actual if expected is not None and actual is not None
                else expected_written == actual_written
                for expected, actual, expected_written, actual_written in zip(
                    contract.parameters, info.parameters, contract.written, info.written, strict=True,
                )
            )
        ]

    def constant_body_overrides(self, method: IntegrationMethod) -> list[IntegrationMethod]:
        """Bodies of enum constants that override ``method`` (H9).

        A typed engine that does not model a constant body reports a call
        on the enum type as ``exact`` to the enum's own body; these bodies
        are the other possible receivers.
        """
        contract = self.methods.get(method.id)
        if contract is None or contract.binds_statically:
            return []
        return [
            candidate.method for candidate in self.implementations(contract)
            if candidate.method.id != method.id and candidate.concrete
            and candidate.node.parent is not None
            and candidate.node.parent.type == "class_body"
            and candidate.node.parent.parent is not None
            and candidate.node.parent.parent.type == "enum_constant"
        ]

    def bridge(self, method: IntegrationMethod) -> IntegrationMethod | None:
        contract = self.methods.get(method.id)
        if contract is None or contract.concrete:
            return None
        candidates = self.implementations(contract)
        return candidates[0].method if len(candidates) == 1 else None

    def target(
        self, receiver_type: str, name: str, arity: int, module: str, path: str,
        argument_types: tuple[str | None, ...] | None = None,
    ) -> IntegrationMethod | None:
        candidates = self.possible_targets(
            receiver_type, name, arity, module, path, argument_types=argument_types,
        )
        return candidates[0] if len(candidates) == 1 else None

    def possible_targets(
        self, receiver_type: str, name: str, arity: int, module: str, path: str,
        argument_types: tuple[str | None, ...] | None = None,
    ) -> list[IntegrationMethod]:
        """Return all source-backed targets compatible with a receiver call."""
        return self._candidate_targets(
            receiver_type, name, arity, module, path, argument_types=argument_types,
        )[0]

    def _select_overload(
        self, contracts: list[_Method], argument_types: tuple[str | None, ...] | None,
    ) -> list[_Method]:
        """Keep the overload Java selects for the argument types.

        Java tries three phases (JLS 15.12.2): identity, widening primitive
        and subtyping first; then boxing and unboxing too; varargs last. The
        first two read a ``T...`` parameter as ``T[]`` and only at its own
        arity; the third expands it to the call's arity (BC-08). The first
        phase with an applicable overload chooses the most specific one. A
        phase decides only when every argument and parameter type is known,
        so unknown arguments never narrow. Otherwise an overload survives
        when no known argument is provably incompatible with it.
        """
        if len({candidate.parameters for candidate in contracts}) <= 1 or argument_types is None:
            return contracts
        arity = len(argument_types)

        def fixed(candidate: _Method) -> tuple[str | None, ...] | None:
            """The signature of the first two phases, if it takes part."""
            if len(candidate.parameters) != arity:
                return None
            if not candidate.varargs:
                return candidate.parameters
            return (*candidate.parameters[:-1], None if candidate.spread is None else candidate.spread + "[]")

        def variable(candidate: _Method) -> tuple[str | None, ...] | None:
            """The signature of the third phase, expanded to the arity."""
            if not candidate.varargs or len(candidate.parameters) - 1 > arity:
                return None
            fixed_count = len(candidate.parameters) - 1
            return (*candidate.parameters[:-1], *(candidate.spread,) * (arity - fixed_count))

        phases = [
            (boxing, {candidate.method.id: fixed(candidate) for candidate in contracts}) for boxing in (False, True)
        ] + [(True, {candidate.method.id: variable(candidate) for candidate in contracts})]
        for boxing, by_candidate in phases:
            signatures = {signature for signature in by_candidate.values() if signature is not None}
            if None in argument_types or any(None in signature for signature in signatures):
                break
            verdicts = {
                signature: [
                    self._applicable(parameter, argument, boxing=boxing)
                    for parameter, argument in zip(signature, argument_types, strict=True)
                ]
                for signature in signatures
            }
            if any(False not in verdict and None in verdict for verdict in verdicts.values()):
                # An overload may apply in this phase: the choice is open.
                break
            applicable = [signature for signature, verdict in verdicts.items() if all(verdict)]
            if not applicable:
                continue
            specific = [
                signature for signature in applicable
                if all(
                    other == signature or all(
                        self._applicable(wider, narrower, boxing=False) is True
                        for narrower, wider in zip(signature, other, strict=True)
                    )
                    for other in applicable
                )
            ]
            if len(specific) != 1:
                return contracts
            return [candidate for candidate in contracts if by_candidate[candidate.method.id] == specific[0]]
        selected = [
            candidate for candidate in contracts
            if any(
                signature is not None and not any(
                    self._applicable(parameter, argument, boxing=True) is False
                    for parameter, argument in zip(signature, argument_types, strict=True)
                )
                for signature in (fixed(candidate), variable(candidate))
            )
        ]
        if len({candidate.parameters for candidate in selected}) == 1:
            return selected
        return contracts

    def _applicable(self, parameter: str | None, argument: str | None, *, boxing: bool) -> bool | None:
        """Whether an argument type converts to a parameter type on invocation.

        ``None`` when the sources cannot tell: an external type's hierarchy
        is unknown. Primitives, their boxes, ``String``, arrays and source
        types are decided (JLS 5.3), boxing and unboxing only when allowed.
        """
        if parameter is None or argument is None:
            return None
        if parameter == argument or parameter == "java.lang.Object" and argument not in _PRIMITIVES:
            return True
        if argument in _PRIMITIVES:
            if parameter in _PRIMITIVES:
                return parameter in _WIDENING.get(argument, ())
            if not boxing or argument not in _BOXED:
                return False
            return parameter in _FIXED_SUPERTYPES[_BOXED[argument]] or parameter == _BOXED[argument]
        if parameter in _PRIMITIVES:
            unboxed = _UNBOXED.get(argument)
            if not boxing or unboxed is None:
                return False
            return parameter == unboxed or parameter in _WIDENING.get(unboxed, ())
        if argument in _FIXED_SUPERTYPES:
            return parameter in _FIXED_SUPERTYPES[argument]
        if argument.endswith("[]") or parameter.endswith("[]"):
            if argument.endswith("[]") and parameter.endswith("[]"):
                element = self._applicable(parameter[:-2], argument[:-2], boxing=False)
                return element if parameter[:-2] not in _PRIMITIVES and argument[:-2] not in _PRIMITIVES else False
            return None if parameter not in self.types and parameter not in _FIXED_SUPERTYPES else False
        if argument in self.types:
            modules = {self.path_modules.get(path, "") for path, _node in self.types[argument]}
            ancestors = {
                ancestor for module in modules for ancestor in self.ancestors(argument, module)
            }
            if any(owner == parameter for owner, _module in ancestors):
                return True
            externals = {base for ancestor in ancestors for base in self.external_bases.get(ancestor, ())}
            if parameter in externals or any(
                parameter in self._implicit_supertypes(ancestor) for ancestor in ancestors
            ):
                return True
            if parameter in self.types or parameter in _FIXED_SUPERTYPES:
                # No external type extends a source type, and a source type
                # is never a ``String`` or a box.
                return False
            return None if externals else False
        # An external argument: only a source type, ``String`` or a box is
        # provably out of its reach.
        if parameter in self.types or parameter in _FIXED_SUPERTYPES:
            return False
        return None

    def _most_specific(self, contracts: list[_Method]) -> _Method | None:
        """The one declaration no other candidate overrides, if unique."""
        unique = {candidate.method.id: candidate for candidate in contracts}
        specific = [
            candidate for candidate in unique.values()
            if not any(
                other is not candidate
                and (other.owner, other.module) != (candidate.owner, candidate.module)
                and (candidate.owner, candidate.module) in self.ancestors(other.owner, other.module)
                for other in unique.values()
            )
        ]
        return specific[0] if len(specific) == 1 else None

    def _candidate_targets(
        self, receiver_type: str, name: str, arity: int, module: str, path: str,
        argument_types: tuple[str | None, ...] | None = None,
        signature: tuple[str | None, ...] | None = None,
    ) -> tuple[list[IntegrationMethod], bool, _Method | None]:
        """Return compatible targets, whether module preference pruned some,
        and the declaration the call statically binds to, if unique.

        ``signature`` names the overload by its parameter types instead of
        selecting it from ``argument_types`` (ADR-87).

        Preferring the caller's module is a heuristic, not a proof: a call
        whose candidates it pruned never becomes an exact dispatch.
        """
        receiver_locations = self._type_candidates(receiver_type, path)
        receiver_is_interface = any(node.type == "interface_declaration" for _path, node in receiver_locations)
        receiver_module = (
            self.path_modules.get(receiver_locations[0][0], module)
            if len(receiver_locations) == 1 else module
        )
        receiver_bases = self.ancestors(receiver_type, receiver_module)
        contracts = self._receiver_contracts(
            receiver_type, name, arity, receiver_locations, receiver_bases,
            variable_arity=signature is None and argument_types is not None,
        )
        if signature is not None:
            contracts = [candidate for candidate in contracts if candidate.parameters == signature]
        else:
            contracts = self._select_overload(contracts, argument_types)
        if len({candidate.parameters for candidate in contracts}) != 1:
            return [], False, None
        return self._dispatch_targets(
            receiver_type, receiver_is_interface, receiver_module, receiver_bases, contracts, module, path,
        )

    def typing_notes(self, method_id: str) -> list[dict[str, object]]:
        """The typing notes of one caller after :meth:`fallback_calls`: its
        calls left with unresolved overloads, and its JDK accessor calls
        whose receiver has no container view (ADR-87), by line."""
        info = self.methods.get(method_id)
        if info is None:
            return []
        notes: dict[tuple[int, str, str], dict[str, object]] = {}
        for line, name, targets in self.unresolved_overloads.get(method_id, []):
            notes[(line, "overload unresolved", name)] = {
                "line": line, "reason": "overload unresolved", "name": name, "targets": list(targets),
            }
        start, end = info.node.start_byte, info.node.end_byte
        for (path, offset), (line, name) in self.broken_accessors.items():
            if path == info.method.path and start <= offset < end:
                notes[(line, "accessor chain broken", name)] = {
                    "line": line, "reason": "accessor chain broken", "name": name, "targets": [],
                }
        return [notes[key] for key in sorted(notes)]

    def _receiver_contracts(
        self, receiver_type: str, name: str, arity: int, receiver_locations: list[tuple[str, Node]],
        receiver_bases: set[tuple[str, str]], *, variable_arity: bool = False,
    ) -> list[_Method]:
        """The declarations of ``name``/``arity`` a receiver type sees, its
        own and its ancestors', or the varargs ones when none has the arity.

        ``variable_arity`` adds the varargs ones beside those of the arity,
        for :meth:`_select_overload` to try in Java's third phase (BC-08).
        """
        contracts = [candidate for owner in receiver_bases
                     for candidate in self.by_owner.get((owner[0], name, arity, owner[1]), [])]
        if len(receiver_locations) > 1:
            contracts = list(self._by_owner_name.get((receiver_type, name, arity), []))
        if not contracts:
            keys = sorted(
                (key for key in (
                    (owner, name, arity, owner_module) for owner, owner_module in receiver_bases
                ) if key in self._by_owner_order),
                key=self._by_owner_order.__getitem__,
            )
            contracts = [candidate for key in keys for candidate in self.by_owner[key]]
        if not contracts or variable_arity:
            # Java's third phase: a varargs method takes any arity from its
            # fixed parameter count up, when no fixed arity applies (S14).
            seen = {candidate.method.id for candidate in contracts}
            contracts = contracts + [
                candidate for owner, owner_module in receiver_bases
                for candidate in self.varargs_by_owner.get((owner, name, owner_module), [])
                if len(candidate.parameters) - 1 <= arity and candidate.method.id not in seen
            ]
        return contracts

    def _unresolved_overloads(
        self, receiver_type: str, name: str, arity: int, module: str, path: str,
        argument_types: tuple[str | None, ...],
    ) -> list[IntegrationMethod]:
        """The targets of every same-arity overload the arguments cannot
        separate, when Java's selection stays open (ADR-87).

        An overload that a known argument provably excludes is left out. A
        varargs candidate keeps the call unresolved: its arity is not fixed.
        """
        locations = self._type_candidates(receiver_type, path)
        receiver_module = self.path_modules.get(locations[0][0], module) if len(locations) == 1 else module
        contracts = self._receiver_contracts(
            receiver_type, name, arity, locations, self.ancestors(receiver_type, receiver_module),
        )
        if not contracts or any(
            candidate.varargs or len(candidate.parameters) != arity for candidate in contracts
        ):
            return []
        if len({candidate.parameters for candidate in self._select_overload(contracts, argument_types)}) < 2:
            return []
        signatures = sorted(
            {
                candidate.parameters for candidate in contracts
                if not any(
                    self._applicable(parameter, argument, boxing=True) is False
                    for parameter, argument in zip(candidate.parameters, argument_types, strict=True)
                )
            },
            key=lambda parameters: tuple(parameter or "" for parameter in parameters),
        )
        if len(signatures) < 2:
            return []
        targets: dict[str, IntegrationMethod] = {}
        for signature in signatures:
            found, _pruned, _declaration = self._candidate_targets(
                receiver_type, name, arity, module, path, signature=signature,
            )
            targets.update((target.id, target) for target in found)
        return list(targets.values())

    def _dispatch_targets(
        self, receiver_type: str, receiver_is_interface: bool, receiver_module: str,
        receiver_bases: set[tuple[str, str]], contracts: list[_Method], module: str, path: str,
    ) -> tuple[list[IntegrationMethod], bool, _Method | None]:
        """The bodies one overload's declarations dispatch to from a
        receiver type: see :meth:`_candidate_targets`."""
        declaration = self._most_specific(contracts)
        candidates = {candidate.method.id: candidate for contract in contracts
                      for candidate in self.implementations(contract)
                      if any(
                          ancestor_owner == receiver_type
                          for ancestor_owner, _ancestor_module in self.ancestors(
                              candidate.owner, candidate.module
                          )
                      )
                      or (candidate.owner, candidate.module) in receiver_bases
                      or candidate.method.id in self.inherited_dispatch.get((receiver_type, receiver_module), set())}
        for contract in contracts:
            modifiers = next(
                (child for child in contract.node.children if child.type == "modifiers"), None
            )
            if (
                contract.concrete and modifiers is not None
                and re.search(r"\bfinal\b", parser.node_text(
                    self.units[contract.method.path].source, modifiers
                ))
            ):
                # A final inherited body remains a unique target even when a
                # generic parameter cannot be resolved to a concrete type.
                candidates[contract.method.id] = contract
        receiver_modules = {
            self.path_modules.get(candidate_path)
            for candidate_path, _ in self._type_candidates(receiver_type, path)
        }
        receiver_modules.discard(None)
        preferred_modules = receiver_modules or {module}
        local_owner_candidates = [
            candidate for candidate in candidates.values()
            if candidate.owner == receiver_type and candidate.module in preferred_modules
        ]
        inherited_candidates = [
            candidate for candidate in candidates.values()
            if candidate.owner != receiver_type
        ]
        candidates = {
            candidate.method.id: candidate
            for candidate in [*local_owner_candidates, *inherited_candidates]
        }
        # Overrides present on the declared receiver hide ancestor bodies.
        # Possible overrides on other runtime subtypes remain ambiguous.
        hidden: set[str] = set()
        for candidate in candidates.values():
            if (candidate.owner, candidate.module) in receiver_bases:
                hidden.update(
                    ancestor_owner
                    for ancestor_owner, _ancestor_module in self.ancestors(
                        candidate.owner, candidate.module
                    )
                    if ancestor_owner != candidate.owner
                )
        remaining = [candidate for candidate in candidates.values() if candidate.owner not in hidden]
        local_remaining = [candidate for candidate in remaining if candidate.module == module]
        if local_remaining and (not inherited_candidates or receiver_is_interface):
            pruned = len(local_remaining) < len(remaining)
            remaining = local_remaining
        else:
            pruned = False
        return [candidate.method for candidate in remaining], pruned, declaration

    def expression_type(
        self, info: _Method, node: Node | None, invocation: Node, depth: int = 0,
    ) -> str | None:
        """Return the static type of a simple expression, or None."""
        if node is None or depth > 16:
            return None
        source = self.units[info.method.path].source
        if node.type in _LITERAL_TYPES:
            return _LITERAL_TYPES[node.type]
        if node.type in _INTEGER_LITERALS or node.type in _FLOATING_LITERALS:
            return _literal_type(node.type, parser.node_text(source, node))
        if (
            node.type == "unary_expression" and node.named_children
            and parser.node_text(source, node).lstrip()[:1] in {"-", "+"}
        ):
            operand = self.expression_type(info, node.named_children[-1], invocation, depth + 1)
            # Unary numeric promotion (JLS 5.6).
            if operand in {"byte", "short", "char"}:
                return "int"
            return operand if operand in {"int", "long", "float", "double"} else None
        if node.type in {"object_creation_expression", "cast_expression"}:
            type_node = node.child_by_field_name("type")
            return self.resolve(
                info.method.path, parser.node_text(source, type_node), info.owner,
                allow_ambiguous=True, at=type_node,
            ) if type_node is not None else None
        if node.type == "parenthesized_expression" and node.named_children:
            return self.expression_type(info, node.named_children[0], invocation, depth + 1)
        if node.type == "class_literal":
            # ``X.class`` (BL-44).
            return "java.lang.Class"
        if node.type == "ternary_expression":
            # Typed only when both branches have the same type. A ``null``
            # branch takes the other branch's type, boxed when primitive
            # (JLS 15.25, BL-42).
            nodes = [node.child_by_field_name(branch) for branch in ("consequence", "alternative")]
            typed = [branch for branch in nodes if branch is None or branch.type != "null_literal"]
            branches = {self.expression_type(info, branch, invocation, depth + 1) for branch in typed}
            if len(branches) != 1:
                return None
            found = branches.pop()
            return _BOXED.get(found, found) if found is not None and len(typed) < len(nodes) else found
        if node.type == "this" and (creation := self._anonymous_instance(info, node)) is not None:
            # Inside an anonymous class body, ``this`` is that instance: its
            # one supertype stands for it, as an argument (BC-10).
            type_node = creation.child_by_field_name("type")
            return self.resolve(
                info.method.path, parser.node_text(source, type_node), self.owner(info.method.path, creation),
                allow_ambiguous=True, at=type_node,
            ) if type_node is not None else None
        if node.type in {"identifier", "this", "method_invocation", "field_access", "array_access"}:
            return self.receiver_type(info, node, invocation, depth + 1)
        return None

    @staticmethod
    def _anonymous_instance(info: _Method, node: Node) -> Node | None:
        """The anonymous class creation whose body holds ``node`` innermost,
        below the code of ``info``; ``None`` outside any."""
        current = node.parent
        while current is not None and current != info.node:
            if _is_anonymous_body(current):
                return current.parent
            current = current.parent
        return None

    def variable_type(self, info: _Method, name: str, invocation: Node, *, field_only: bool = False) -> str | None:
        """The declared type of the variable ``name`` visible at ``invocation``.

        Java resolves a simple name lexically, innermost scope first: the
        statements and parameters of every enclosing block, lambda and
        method, anonymous and local class methods included; then the fields
        of each enclosing class, declared or inherited, before those of its
        outer classes. ``field_only`` (``this.name``) reads only the fields
        of the innermost class. A declaration whose type is not written
        still shadows the outer ones (S5, S6, S14). An untyped lambda
        parameter takes a type only from a JDK callback (BL-14).
        """
        scope = invocation.parent
        while scope is not None:
            if not field_only:
                declarator = self._declarator(info, scope, name, invocation)
                if declarator is not None:
                    return self._declarator_type(info, declarator)
            if scope.type in _TYPES or _is_class_scope_body(scope):
                declared, field_type = self._class_scope_field(info, scope, name)
                if declared or field_only:
                    return field_type
            scope = scope.parent
        return None

    def created_value(self, info: _Method, receiver: Node, invocation: Node) -> tuple[Node, str] | None:
        """The one value a variable holds at ``invocation``, and the type
        whose scope it is written in: the initializer of a local assigned
        only at its declaration, or of a ``final`` field the enclosing class
        declares and never assigns again (ADR-86)."""
        source = self.units[info.method.path].source
        field_only = False
        if receiver.type == "identifier":
            name = parser.node_text(source, receiver)
        elif receiver.type == "field_access":
            target = receiver.child_by_field_name("object")
            field = receiver.child_by_field_name("field")
            if target is None or field is None or target.type != "this" or field.type != "identifier":
                return None
            name, field_only = parser.node_text(source, field), True
        else:
            return None
        scope = invocation.parent
        while scope is not None:
            if not field_only:
                declarator = self._declarator(info, scope, name, invocation)
                if declarator is not None:
                    return self._created_local(info, name, declarator)
            if _is_class_scope_body(scope):
                declared, _type = self._class_scope_field(info, scope, name)
                if declared or field_only:
                    return None
            elif scope.type in _TYPES:
                declared, _type = self._class_scope_field(info, scope, name)
                if declared or field_only:
                    return self._created_field(info, scope, name)
            scope = scope.parent
        return None

    def _created_local(self, info: _Method, name: str, declarator: _Declarator) -> tuple[Node, str] | None:
        value = declarator.value
        if (
            declarator.type_node is None or value is None
            or declarator.lambda_slot is not None or declarator.element_of is not None
        ):
            return None
        variable = value.parent
        declaration = variable.parent if variable is not None else None
        if declaration is None or declaration.type != "local_variable_declaration" or declaration.parent is None:
            return None
        source = self.units[info.method.path].source
        if _assigned(source, declaration.parent, name):
            return None
        return value, info.owner

    def _created_field(self, info: _Method, scope: Node, name: str) -> tuple[Node, str] | None:
        path = info.method.path
        source = self.units[path].source
        body = scope.child_by_field_name("body")
        found = [
            (member, child)
            for member in (body.named_children if body is not None else [])
            if member.type == "field_declaration"
            for child in member.named_children
            if child.type == "variable_declarator"
            and (ident := child.child_by_field_name("name")) is not None
            and parser.node_text(source, ident) == name
        ]
        # An inherited field keeps its declared type.
        if len(found) != 1 or body is None:
            return None
        member, variable = found[0]
        modifiers = parser.modifiers_node(member)
        if modifiers is None or "final" not in parser.node_text(source, modifiers).split():
            return None
        if _assigned(source, body, name):
            return None
        value = variable.child_by_field_name("value")
        return (value, self.owner(path, scope)) if value is not None else None

    def _creation(self, path: str, value: Node | None, owner: str) -> str | None:
        """The named source class ``new T(...)`` creates; an anonymous class
        body is no named type (see ``_implicit_targets``)."""
        if value is None or value.type != "object_creation_expression" or any(
            child.type == "class_body" for child in value.children
        ):
            return None
        type_node = value.child_by_field_name("type")
        if type_node is None:
            return None
        created = self.resolve(path, parser.node_text(self.units[path].source, type_node), owner, at=type_node)
        return created if created in self.types else None

    def _narrowed_targets(
        self, info: _Method, receiver: Node, invocation: Node, receiver_type: str,
        name: str, arity: int, argument_types: tuple[str | None, ...] | None,
        targets: list[IntegrationMethod],
    ) -> tuple[tuple[str, ...], list[IntegrationMethod], bool] | None:
        """The dispatch on the types a receiver always holds (BL-29, BL-46),
        as ``(types, targets, pruned)``: the union of each type's targets,
        when every type is a source subtype of the declared receiver type,
        a source type or an external one (BL-31), and its targets are among
        ``targets``."""
        held = self._held_types(info, receiver, invocation)
        if held is None or receiver_type in held:
            return None
        reached = {target.id for target in targets}
        narrowed: dict[str, IntegrationMethod] = {}
        pruned = False
        for created in held:
            module = self._receiver_module(created, info)
            if not self._subtype_of(created, module, receiver_type):
                return None
            found, found_pruned, _declaration = self._candidate_targets(
                created, name, arity, info.module, info.method.path, argument_types=argument_types,
            )
            if not found or any(target.id not in reached for target in found):
                return None
            narrowed.update((target.id, target) for target in found)
            pruned = pruned or found_pruned
        return held, list(narrowed.values()), pruned

    def _held_types(
        self, info: _Method, receiver: Node, invocation: Node, depth: int = 0,
    ) -> tuple[str, ...] | None:
        """The named source classes a receiver always holds an instance of,
        or ``None`` when the sources do not prove them.

        A variable holds what ADR-86 proves it was given once (a creation,
        a returned creation or a ternary, BL-46); a parameter of a private
        method, the one class its call sites pass (ADR-89 rule 3). Any
        other receiver is read as a value (:meth:`_value_types`).
        """
        node = receiver
        while node.type == "parenthesized_expression" and node.named_children:
            node = node.named_children[0]
        if node.type not in {"identifier", "field_access"}:
            return self._value_types(info, node, info.owner, depth)
        created = self.created_value(info, node, invocation)
        if created is not None:
            return self._value_types(info, created[0], created[1], depth)
        if node.type != "identifier":
            return None
        position = self._own_parameter(info, parser.node_text(self.units[info.method.path].source, node), invocation)
        found = self._context_type(info, position, depth) if position is not None else None
        return (found,) if found is not None else None

    def _value_types(
        self, info: _Method, value: Node | None, owner: str, depth: int = 0,
    ) -> tuple[str, ...] | None:
        """The named source classes an expression evaluates to (ADR-86,
        ADR-89): ``new T(..)``; a call to a source method that returns only
        ``new T(..)``; the union of a ternary's branches, each typed by its
        own value or else its static source type, a ``null`` branch giving
        nothing. A lambda, a method reference or an anonymous class is no
        named class (ADR-72)."""
        while value is not None and value.type == "parenthesized_expression" and value.named_children:
            value = value.named_children[0]
        if value is None or depth > 8 or _implicit_value(value):
            return None
        if value.type == "object_creation_expression":
            created = self._creation(info.method.path, value, owner)
            return (created,) if created is not None else None
        if value.type == "method_invocation":
            returned = self._returned_creation(info, value, depth + 1)
            return (returned,) if returned is not None else None
        if value.type != "ternary_expression":
            return None
        found: list[str] = []
        for field in ("consequence", "alternative"):
            branch = value.child_by_field_name(field)
            while branch is not None and branch.type == "parenthesized_expression" and branch.named_children:
                branch = branch.named_children[0]
            if branch is None or _implicit_value(branch):
                return None
            if branch.type == "null_literal":
                continue
            types = (
                self._held_types(info, branch, branch, depth + 1)
                if branch.type in {"identifier", "field_access"}
                else self._value_types(info, branch, owner, depth + 1)
            )
            if types is None:
                static = self.expression_type(info, branch, branch)
                types = (static,) if static is not None and static in self.types else None
            if types is None:
                return None
            found.extend(found_type for found_type in types if found_type not in found)
        return tuple(found) or None

    def _returned_creation(self, info: _Method, call: Node, depth: int) -> str | None:
        """The class ``T`` a call returns when it reaches one source method,
        exact under ADR-81, whose every own ``return`` is ``new T(..)``
        (ADR-89 rule 1)."""
        if depth > 8:
            return None
        path = info.method.path
        parent_receiver, name, arguments = parser.invocation_parts(call, self.units[path].source)
        if parent_receiver is not None and parent_receiver.type == "super":
            return None
        owner = (
            self._lexical_owner(info, name, call) if parent_receiver is None
            else self.receiver_type(info, parent_receiver, call)
        )
        if owner is None or owner not in self.types:
            return None
        arity = len(arguments)
        targets, pruned, declaration = self._candidate_targets(
            owner, name, arity, info.module, path,
            argument_types=tuple(self.expression_type(info, argument, call) for argument in arguments),
        )
        method = self.methods.get(targets[0].id) if len(targets) == 1 and not pruned else None
        if method is None or not method.concrete:
            return None
        if not method.binds_statically and (
            self._incomplete_implementors(owner, info, name, arity, targets)
            or self._implicitly_implemented(owner, name, arity, declaration)
        ):
            return None
        return self._creation_return(method)

    def _creation_return(self, method: _Method) -> str | None:
        """The one named source class every ``return`` of ``method``'s own
        body creates, a subtype of its declared return type, if any."""
        key = method.method.id
        if key in self._creation_returns:
            return self._creation_returns[key]
        found: str | None = None
        path = method.method.path
        source = self.units[path].source
        body = method.node.child_by_field_name("body")
        type_node = method.node.child_by_field_name("type")
        returns = [
            node for node in parser.walk(body) if node.type == "return_statement"
            and self._own_return(node, method.node)
        ] if body is not None else []
        created: set[str | None] = set()
        for node in returns:
            value = next((child for child in node.named_children if "comment" not in child.type), None)
            while value is not None and value.type == "parenthesized_expression" and value.named_children:
                value = value.named_children[0]
            created.add(self._creation(path, value, method.owner))
        if len(created) == 1 and type_node is not None:
            single = created.pop()
            declared = self.resolve(
                path, parser.node_text(source, type_node), method.owner, allow_ambiguous=True, at=type_node,
            )
            if single is not None and declared is not None and (
                single == declared or self._subtype_of(single, self._module_of(single, path), declared)
            ):
                found = single
        self._creation_returns[key] = found
        return found

    @staticmethod
    def _own_return(node: Node, method: Node) -> bool:
        """Whether a ``return`` belongs to ``method`` itself, not to a lambda
        or a nested class body inside it."""
        current = node.parent
        while current is not None and current != method:
            if current.type in {"lambda_expression", "class_body"} or current.type in CONSTRUCTOR_DECLARATIONS:
                return False
            current = current.parent
        return current is not None

    def _own_parameter(self, info: _Method, name: str, invocation: Node) -> int | None:
        """The position of the parameter of ``info`` that ``name`` denotes at
        ``invocation``, when no inner declaration shadows it."""
        if info.node.type != "method_declaration":
            return None
        scope = invocation.parent
        while scope is not None and scope != info.node:
            if self._declarator(info, scope, name, invocation) is not None:
                return None
            if scope.type in _TYPES or _is_class_scope_body(scope):
                declared, _type = self._class_scope_field(info, scope, name)
                if declared:
                    return None
            scope = scope.parent
        if scope is None:
            return None
        source = self.units[info.method.path].source
        parameters = declaration_parameters(info.node)
        formals = [
            parameter for parameter in (parameters.named_children if parameters is not None else [])
            if parameter.type in {"formal_parameter", "spread_parameter"}
        ]
        for position, parameter in enumerate(formals):
            ident = parameter.child_by_field_name("name")
            if parameter.type == "formal_parameter" and ident is not None and parser.node_text(source, ident) == name:
                return position
        return None

    def _context_type(self, info: _Method, position: int, depth: int) -> str | None:
        """The one source class every call site of a private method passes
        at ``position`` (ADR-89 rule 3), when it narrows the declared
        parameter type. A site in progress counts as unproven."""
        key = (info.method.id, position)
        if key in self._context_types:
            return self._context_types[key]
        self._context_types[key] = None
        found = self._call_context_type(info, position, depth)
        self._context_types[key] = found
        return found

    def _call_context_type(self, info: _Method, position: int, depth: int) -> str | None:
        if depth > 8 or info.varargs or "private" not in self._modifiers(info):
            return None
        path = info.method.path
        source = self.units[path].source
        parameters = declaration_parameters(info.node)
        formals = [
            parameter for parameter in (parameters.named_children if parameters is not None else [])
            if parameter.type in {"formal_parameter", "spread_parameter"}
        ]
        body = info.node.child_by_field_name("body")
        parameter = formals[position] if position < len(formals) else None
        type_node = parameter.child_by_field_name("type") if parameter is not None else None
        ident = parameter.child_by_field_name("name") if parameter is not None else None
        if body is None or type_node is None or ident is None or _assigned(source, body, parser.node_text(source, ident)):
            return None
        declared = self._declared_type(path, type_node, info.owner)
        top: Node | None = None
        current: Node | None = info.node
        while current is not None:
            if current.type in _TYPES:
                top = current
            current = current.parent
        if declared is None or top is None:
            return None
        name = info.method.qualified_method.rsplit(".", 1)[-1]
        found: set[str] = set()
        for node in parser.walk(top):
            if node.type == "method_reference":
                parts = node.named_children
                if parts and parts[-1].type == "identifier" and parser.node_text(source, parts[-1]) == name:
                    return None
                continue
            if node.type != "method_invocation":
                continue
            _receiver, called, arguments = parser.invocation_parts(node, source)
            if called != name or len(arguments) != len(formals):
                continue
            caller = self._context_method(path, node)
            if caller is None:
                return None
            argument = arguments[position]
            types = self._held_types(caller, argument, node, depth + 1)
            if types is None:
                static = self.expression_type(caller, argument, node)
                types = (static,) if static is not None else None
            if types is None or len(types) != 1:
                return None
            found.add(types[0])
        single = found.pop() if len(found) == 1 else None
        if single is None or single == declared or single not in self.types:
            return None
        return single if self._subtype_of(single, self._module_of(single, path), declared) else None

    def _context_method(self, path: str, node: Node) -> _Method | None:
        """The indexed method or constructor whose code holds ``node``."""
        context = _call_context(node)
        if context is None:
            return None
        if self._context_methods is None:
            self._context_methods = {
                (info.method.path, info.node.start_byte, info.node.end_byte): info
                for info in self.methods.values()
            }
        return self._context_methods.get((path, context.start_byte, context.end_byte))

    def _implicit_supertypes(self, owner: tuple[str, str]) -> frozenset[str]:
        """The JDK supertypes a source enum or record has without naming
        them (BC-06): empty for any other type."""
        kinds = {node.type for _path, node in self._module_locations(*owner)}
        return frozenset().union(*(_IMPLICIT_SUPERTYPES.get(kind, frozenset()) for kind in kinds))

    def _subtype_of(self, owner: str, module: str, supertype: str) -> bool:
        """Whether ``owner`` extends or implements ``supertype``, through its
        source ancestors and the external types they name directly."""
        for ancestor in self.ancestors(owner, module):
            if ancestor[0] == supertype or supertype in self._implicit_supertypes(ancestor):
                return True
            for base in self.external_bases.get(ancestor, ()):
                if base == supertype or supertype in _KNOWN_EXTERNAL_SUPERTYPES.get(base or "", ()):
                    return True
        return False

    def _contradicted_implementors(
        self, info: _Method, receiver: Node, invocation: Node, receiver_type: str,
    ) -> Callable[[str, str], bool] | None:
        """Whether an implementor ``(type, module)`` is excluded by the type
        arguments of the receiver (BL-45, ADR-81 amendment): ``None`` when
        the receiver's written type gives no concrete argument.

        An implementor is excluded when its ``implements`` or ``extends``
        clauses give one of the receiver type's parameters a concrete
        argument provably incompatible with the receiver's own.
        """
        written = self._receiver_arguments(info, receiver, invocation)
        if written is None or written[0] != receiver_type:
            return None
        arguments = {name: value for name, value in written[1].items() if value is not None}
        if not arguments:
            return None
        path = info.method.path

        def contradicted(owner: str, module: str) -> bool:
            implemented = self._implementor_arguments(owner, module, receiver_type)
            return implemented is not None and any(
                (other := implemented.get(name)) is not None and self._unrelated_arguments(value, other, path)
                for name, value in arguments.items()
            )

        return contradicted

    def _receiver_arguments(self, info: _Method, receiver: Node, invocation: Node) -> tuple[str, _Env] | None:
        """The type arguments a receiver's declaration writes, strictly
        (:meth:`_strict_argument`): a local, a parameter or a field of the
        caller's class, a cast, a creation, or a source method's declared
        return type. An inherited field and a ``var`` give nothing."""
        path = info.method.path
        source = self.units[path].source
        node = receiver
        while node.type == "parenthesized_expression" and node.named_children:
            node = node.named_children[0]
        if node.type in {"cast_expression", "object_creation_expression"}:
            type_node = node.child_by_field_name("type")
            return self._parameterized(path, type_node, info.owner, None, strict=True) if type_node else None
        field: Node | None = None
        if node.type == "identifier":
            field = node
        elif node.type == "field_access" and (
            (target := node.child_by_field_name("object")) is not None and target.type == "this"
        ):
            field = node.child_by_field_name("field")
        if field is not None:
            written = self._written_variable(
                info, parser.node_text(source, field), invocation, field_only=node.type == "field_access",
            )
            if written is None or written[3] is not None:
                return None
            return self._parameterized(written[0], written[1], written[2], None, strict=True)
        if node.type != "method_invocation":
            return None
        found = self._return_declarations(info, node, 0)
        if found is None:
            return None
        results: list[tuple[str, _Env] | None] = []
        for declaration in found[1]:
            type_node = declaration.node.child_by_field_name("type")
            results.append(self._parameterized(
                declaration.method.path, type_node, declaration.owner, None, strict=True,
            ) if type_node is not None else None)
        return results[0] if results and all(result == results[0] for result in results) else None

    def _implementor_arguments(self, owner: str, module: str, target: str) -> _Env | None:
        """The type arguments the clauses of ``owner`` write for its source
        ancestor ``target``, strictly; a type variable of ``owner`` gives
        nothing. ``None`` when ``owner`` is ``target`` or no subtype of it."""
        key = (owner, module, target)
        if key not in self._implemented:
            found: _Env | None = None
            locations = self._module_locations(owner, module)
            if owner != target and len(locations) == 1:
                own = {name: None for name, _parameter in self._declared_type_parameters(locations[0][1])}
                found = self._supertype_env(owner, module, dict(own), target, strict=True)
            self._implemented[key] = found
        return self._implemented[key]

    def _unrelated_arguments(self, first: str, second: str, path: str) -> bool:
        """Whether two concrete type arguments are provably incompatible:
        different and not related by subtyping, as far as the sources and
        the JDK's final classes prove it (BL-45). An array relates only to
        an array of a related element and to the array supertypes."""
        if first == second or "java.lang.Object" in {first, second}:
            return False
        first_array, second_array = first.endswith("[]"), second.endswith("[]")
        if first_array and second_array:
            elements = first[:-2], second[:-2]
            if any(element in _PRIMITIVES for element in elements):
                return elements[0] != elements[1]
            return self._unrelated_arguments(*elements, path)
        if first_array or second_array:
            other = second if first_array else first
            return other not in _ARRAY_SUPERTYPES and self._placed_type(other, path)
        if not (self._placed_type(first, path) and self._placed_type(second, path)):
            return False
        return not self._may_subtype(first, second, path) and not self._may_subtype(second, first, path)

    def _placed_type(self, type_name: str, path: str) -> bool:
        """A unique source type or a JDK type: one whose supertypes the
        subtyping check of :meth:`_unrelated_arguments` can judge."""
        if type_name.endswith("[]"):
            element = type_name[:-2]
            return element in _PRIMITIVES or self._placed_type(element, path)
        return len(self._type_candidates(type_name, path)) == 1 or (
            type_name not in self.types and type_name.startswith("java.")
        )

    def _may_subtype(self, subtype: str, supertype: str, path: str) -> bool:
        """Whether ``subtype`` is, or may be, a subtype of ``supertype``.

        A source type is one when its source ancestors or the external
        types they name reach ``supertype``, and may be one when it has an
        external ancestor whose hierarchy the sources do not hold. A JDK
        type is never a subtype of a source type; a final JDK class has the
        supertypes ``_FINAL_JDK_SUPERTYPES`` lists, and any other JDK pair
        may be related.
        """
        if subtype in self.types:
            if supertype in self.types:
                return self._subtype_of(subtype, self._module_of(subtype, path), supertype)
            module = self._module_of(subtype, path)
            return self._subtype_of(subtype, module, supertype) or any(
                self.external_bases.get(ancestor) for ancestor in self.ancestors(subtype, module)
            )
        if supertype in self.types:
            return False
        if subtype in _FINAL_JDK_SUPERTYPES:
            return supertype in _FINAL_JDK_SUPERTYPES[subtype]
        return supertype not in _FINAL_JDK_SUPERTYPES

    def _implicit_targets(
        self, info: _Method, value: Node, value_owner: str, receiver_type: str, name: str, arity: int,
        argument_types: tuple[str | None, ...] | None, declaration: _Method | None,
    ) -> tuple[list[IntegrationMethod], bool]:
        """The targets of a call on a variable that always holds a lambda, a
        method reference or an anonymous class instance (BL-30, ADR-72), and
        whether the one target is exact.

        The instance's own body is no indexed method (ADR-62): when it
        implements the called method, no named body is a target. A lambda
        implements the abstract method its target type declares; an
        anonymous class the methods its body declares. Otherwise the
        instance inherits the body its supertype resolves to, which is
        exact unless an external supertype may declare the name.
        """
        path = info.method.path
        while value.type == "parenthesized_expression" and value.named_children:
            value = value.named_children[0]
        if value.type == "object_creation_expression":
            body = parser.child_by_type(value, "class_body")
            implements = (name, arity) in _declared_members(self.units[path].source, body)
            type_node = value.child_by_field_name("type")
            base = self.resolve(
                path, parser.node_text(self.units[path].source, type_node), value_owner, at=type_node,
            ) if type_node is not None else None
        else:
            implements = (name, arity) not in _OBJECT_DISPATCH and (
                receiver_type not in self.types or declaration is None or not declaration.concrete
            )
            base = receiver_type
        if implements or base is None or base not in self.types:
            return [], False
        module = self._receiver_module(base, info)
        targets = self._super_target((base, module), name, arity, argument_types)
        exact = len(targets) == 1 and (
            self.methods[targets[0].id].owner == base or not self._may_inherit_unknown(base, module, name)
        )
        return targets, exact

    def _implicit_implementations(self) -> tuple[dict[str, set[tuple[str, int]]], set[str]]:
        """Where the sources hold a body no indexed method stands for
        (BL-30, ADR-72): the ``(name, arity)`` anonymous class bodies
        declare, by each type they extend or implement, ancestors included;
        and the target types of lambdas and method references, with their
        ancestors."""
        if self._implicit is None:
            overridden: dict[str, set[tuple[str, int]]] = defaultdict(set)
            for supertype, members in self.anonymous_members.items():
                for owner in self._ancestor_owners(supertype):
                    overridden[owner].update(members)
            functional: set[str] = set()
            for path, node in self._functional_values:
                for target in self._functional_targets(path, node):
                    functional.update(self._ancestor_owners(target))
            self._implicit = dict(overridden), functional
        return self._implicit

    def _ancestor_owners(self, owner: str) -> set[str]:
        modules = {self.path_modules.get(path, "") for path, _node in self.types.get(owner, [])} or {""}
        return {ancestor for module in modules for ancestor, _module in self.ancestors(owner, module)}

    def _implicitly_implemented(
        self, runtime_type: str, name: str, arity: int, declaration: _Method | None,
    ) -> bool:
        """Whether a lambda, a method reference or an anonymous class can be
        the receiver of a call on ``runtime_type`` and implement it (ADR-72)."""
        overridden, functional = self._implicit_implementations()
        if (name, arity) in overridden.get(runtime_type, ()):
            return True
        return (
            runtime_type in functional and (name, arity) not in _OBJECT_DISPATCH
            and declaration is not None and not declaration.concrete
        )

    def _functional_targets(self, path: str, node: Node) -> set[str]:
        """The source types a lambda or a method reference implements, when
        the code writes them: the type of the variable it initializes or is
        assigned to, a cast, the return type of its method, or a source
        parameter type it is passed to. A switch arm or ``yield`` takes the
        switch expression's target, and an array initializer element its
        array's element type (BC-12). Any other target does not count."""
        current, parent = node, node.parent
        while parent is not None:
            if (
                parent.type == "parenthesized_expression"
                or parent.type == "ternary_expression" and current != parent.child_by_field_name("condition")
            ):
                current, parent = parent, parent.parent
            elif parent.type in {"switch_rule", "yield_statement"} or (
                parent.type == "expression_statement" and parent.parent is not None
                and parent.parent.type == "switch_rule"
            ):
                switch: Node | None = parent
                while switch is not None and switch.type != "switch_expression":
                    switch = None if switch.type in _FUNCTIONAL_BOUNDARIES else switch.parent
                if switch is None:
                    return set()
                current, parent = switch, switch.parent
            else:
                break
        if parent is None:
            return set()
        owner = self.owner(path, node)
        written: list[Node | None] = []
        if parent.type == "array_initializer":
            written = [self._initialized_element(parent)]
        elif parent.type == "variable_declarator" and parent.child_by_field_name("value") == current:
            written = [parent.parent.child_by_field_name("type") if parent.parent is not None else None]
        elif parent.type == "cast_expression":
            written = [parent.child_by_field_name("type")]
        elif parent.type == "return_statement":
            scope = parent.parent
            while scope is not None and scope.type not in {
                "method_declaration", "lambda_expression", "class_body", *CONSTRUCTOR_DECLARATIONS,
            }:
                scope = scope.parent
            if scope is not None and scope.type == "method_declaration":
                written = [scope.child_by_field_name("type")]
        elif parent.type == "assignment_expression" and parent.child_by_field_name("right") == current:
            return self._assigned_target(path, parent)
        elif parent.type == "argument_list" and parent.parent is not None:
            return {
                found for type_node, declared_path, declared_owner in self._parameter_type_nodes(
                    path, parent.parent, current, owner,
                )
                if (found := self._written_target(declared_path, type_node, declared_owner)) is not None
            }
        return {
            found for type_node in written
            if (found := self._written_target(path, type_node, owner)) is not None
        }

    @staticmethod
    def _initialized_element(initializer: Node) -> Node | None:
        """The element type an array initializer's values have: that of
        the array creation or the declaration it initializes (BC-12)."""
        while initializer.parent is not None and initializer.parent.type == "array_initializer":
            initializer = initializer.parent
        holder = initializer.parent
        type_node: Node | None = None
        if holder is not None and holder.type == "array_creation_expression":
            type_node = holder.child_by_field_name("type")
        elif holder is not None and holder.type == "variable_declarator" and holder.parent is not None:
            type_node = holder.parent.child_by_field_name("type")
        while type_node is not None and type_node.type == "array_type":
            type_node = type_node.child_by_field_name("element")
        return type_node

    def _written_target(self, path: str, type_node: Node | None, owner: str) -> str | None:
        if type_node is None:
            return None
        name_node = type_node.named_children[0] if type_node.type == "generic_type" else type_node
        resolved = self.resolve(
            path, parser.node_text(self.units[path].source, name_node), owner, allow_ambiguous=True, at=name_node,
        )
        return resolved if resolved in self.types else None

    def _assigned_target(self, path: str, assignment: Node) -> set[str]:
        """The source type of the variable an assignment writes, read from
        the indexed method holding it."""
        info = self._context_method(path, assignment)
        left = assignment.child_by_field_name("left")
        if info is None or left is None:
            return set()
        source = self.units[path].source
        if left.type == "identifier":
            found = self.variable_type(info, parser.node_text(source, left), assignment)
        elif left.type == "field_access" and (field := left.child_by_field_name("field")) is not None and (
            (target := left.child_by_field_name("object")) is not None and target.type == "this"
        ):
            found = self.variable_type(info, parser.node_text(source, field), assignment, field_only=True)
        else:
            return set()
        return {found} if found in self.types else set()

    def _parameter_type_nodes(
        self, path: str, call: Node, argument: Node, owner: str,
    ) -> list[tuple[Node | None, str, str]]:
        """The written parameter types an argument may bind to: those of every
        source method of the call's name and arity, or of every constructor
        of the created type, of ``this(..)`` or ``super(..)``, or of an enum
        constant's enum; a varargs parameter takes every trailing argument
        (BC-12). A superset of the true target, so a lambda never escapes
        the rule."""
        source = self.units[path].source
        arguments_node = argument.parent
        arguments = [
            child for child in (arguments_node.named_children if arguments_node is not None else [])
            if "comment" not in child.type
        ]
        if call.type == "method_invocation":
            _receiver, name, arguments = parser.invocation_parts(call, source)
            declarations = self._methods_named(name, len(arguments), variable_arity=True)
        elif call.type in {"object_creation_expression", "explicit_constructor_invocation", "enum_constant"}:
            if call.type == "object_creation_expression":
                created = self._written_target(path, call.child_by_field_name("type"), owner)
            elif call.type == "enum_constant":
                created = owner
            elif any(child.type == "super" for child in call.children):
                module = self.path_modules.get(path, "")
                superclass = self._superclass(owner, module)
                created = superclass[0] if superclass is not None else None
            else:
                created = owner
            declarations = [
                info for info in self._methods_named("<init>", len(arguments), variable_arity=True)
                if info.owner == created
            ] if created is not None else []
        else:
            return []
        index = next((
            number for number, candidate in enumerate(arguments)
            if (candidate.start_byte, candidate.end_byte) == (argument.start_byte, argument.end_byte)
        ), None)
        if index is None:
            return []
        found: list[tuple[Node | None, str, str]] = []
        for declaration in declarations:
            formals = declaration.node.child_by_field_name("parameters")
            typed = [
                formal for formal in (formals.named_children if formals is not None else [])
                if formal.type in {"formal_parameter", "spread_parameter"}
            ]
            if index < len(typed) and typed[index].type == "formal_parameter":
                found.append((typed[index].child_by_field_name("type"), declaration.method.path, declaration.owner))
            elif typed and typed[-1].type == "spread_parameter" and index >= len(typed) - 1:
                found.append((_spread_type(typed[-1]), declaration.method.path, declaration.owner))
        return found

    def _methods_named(self, name: str, arity: int, *, variable_arity: bool = False) -> list[_Method]:
        """The methods of a name and arity; ``variable_arity`` adds the
        varargs ones that take that arity."""
        if self._by_name_arity is None:
            self._by_name_arity = defaultdict(list)
            for info in self.methods.values():
                name_of = info.method.qualified_method.rsplit(".", 1)[-1]
                self._by_name_arity[(name_of, len(info.parameters))].append(info)
                if info.varargs:
                    self._varargs_by_name[name_of].append(info)
        found = self._by_name_arity.get((name, arity), [])
        if not variable_arity:
            return found
        return found + [
            info for info in self._varargs_by_name.get(name, [])
            if len(info.parameters) - 1 <= arity and len(info.parameters) != arity
        ]

    def _declarator(self, info: _Method, scope: Node, name: str, invocation: Node) -> _Declarator | None:
        """How ``scope`` declares ``name`` for ``invocation``: as a variable
        the scope itself introduces, or as a preceding local."""
        declarator = self._scoped_declaration(info, scope, name, invocation)
        if declarator is not None:
            return declarator
        source = self.units[info.method.path].source
        for declaration in reversed(scope.named_children):
            if declaration.type != "local_variable_declaration" or declaration.start_byte > invocation.start_byte:
                continue
            type_node = declaration.child_by_field_name("type")
            # Each declarator is in scope from its own start: in
            # ``Foo a = new Foo(), b = a.n();`` the second one sees ``a``
            # (BC-11).
            for child in declaration.named_children:
                ident = child.child_by_field_name("name")
                if (
                    ident is not None and parser.node_text(source, ident) == name and type_node is not None
                    and child.start_byte <= invocation.start_byte
                ):
                    return _Declarator(
                        type_node, child.child_by_field_name("value"), dimensions=_dimensions(source, child),
                    )
        return None

    def _declarator_type(self, info: _Method, declarator: _Declarator) -> str | None:
        if declarator.lambda_slot is not None:
            return self._lambda_parameter_type(info, *declarator.lambda_slot)
        if declarator.element_of is not None:
            # ``for (var c : circles)``: the element type the iterable's
            # written type argument gives, read in the loop header.
            view = self._generic_view(info, declarator.element_of, declarator.element_of)
            if view is not None:
                return _flat(view[1][0]) if view[0] == "iterable" else None
            # ``for (var o : orders)`` over an array: its element type (BL-42).
            array = self.expression_type(info, declarator.element_of, declarator.element_of)
            return array[:-2] if array is not None and array.endswith("[]") else None
        type_node = declarator.type_node
        if type_node is None:
            return None
        path = info.method.path
        text = parser.node_text(self.units[path].source, type_node).strip()
        if text == "var":
            # ``var`` takes the type of its initializer.
            return self._initializer_type(info, declarator.value)
        # A local or parameter typed ``T`` takes the bound of ``T`` (BL-26).
        declared = self._declared_type(path, type_node, info.owner)
        return declared + "[]" * declarator.dimensions if declared is not None else None

    def _lambda_parameter_type(self, info: _Method, lambda_node: Node, position: int, count: int) -> str | None:
        """The type of an untyped lambda parameter, when its target proves it.

        The lambda must be a direct argument of a JDK collection, stream,
        optional or map method whose functional parameter is fixed by the
        receiver's written type arguments, as ``Iterable<T>.forEach`` takes
        a ``Consumer<? super T>``. Any other target leaves it unknown.
        """
        site = self._argument_site(info, lambda_node)
        if site is None:
            # ``Predicate<Order> p = o -> ..`` (BL-37).
            parameters = self._assigned_signature(info, lambda_node)
            return parameters[position] if parameters is not None and len(parameters) == count else None
        call, receiver, name, call_arguments, index = site
        view = self._generic_view(info, receiver, call) if receiver is not None else None
        if view is not None:
            kind, type_arguments = view
            slots = _LAMBDA_PARAMETERS.get((kind, name, len(call_arguments), index, count))
            return _flat(type_arguments[slots[position]]) if slots is not None else None
        # A source method whose parameter is a source (BL-24) or JDK (BL-37)
        # functional interface: the parameter types of its abstract method.
        parameters = self._functional_parameters(info, call, receiver, name, call_arguments, index)
        return parameters[position] if parameters is not None and len(parameters) == count else None

    def _assigned_signature(self, info: _Method, node: Node) -> tuple[str | None, ...] | None:
        """The parameter types of the functional interface a lambda or a
        method reference implements when it initializes, or is assigned to,
        a variable whose type is written (BL-37)."""
        parent = node.parent
        path = info.method.path
        source = self.units[path].source
        if parent is None:
            return None
        if parent.type == "variable_declarator" and parent.child_by_field_name("value") == node:
            declaration = parent.parent
            type_node = declaration.child_by_field_name("type") if declaration is not None else None
            if type_node is None or declaration is None or declaration.type not in {
                "local_variable_declaration", "field_declaration",
            }:
                return None
            return self._functional_signature(path, type_node, info.owner, None)
        if parent.type == "assignment_expression" and parent.child_by_field_name("right") == node and (
            parser.node_text(source, parent).split("=", 1)[0].strip()[-1:] not in set("+-*/%&|^<>")
        ):
            target = parent.child_by_field_name("left")
            field_only = target is not None and target.type == "field_access" and (
                (this := target.child_by_field_name("object")) is not None and this.type == "this"
            )
            if target is not None and field_only:
                field = target.child_by_field_name("field")
                name = parser.node_text(source, field) if field is not None else ""
            elif target is not None and target.type == "identifier":
                name = parser.node_text(source, target)
            else:
                return None
            written = self._written_variable(info, name, parent, field_only=field_only)
            if written is None:
                return None
            location, type_node, owner, env = written
            return self._functional_signature(location, type_node, owner, env)
        return None

    def _argument_site(
        self, info: _Method, node: Node,
    ) -> tuple[Node, Node | None, str, list[Node], int] | None:
        """The method call ``node`` is a direct argument of: the call, its
        receiver, name and arguments, and the position of ``node``."""
        arguments = node.parent
        call = arguments.parent if arguments is not None and arguments.type == "argument_list" else None
        if call is None or call.type != "method_invocation":
            return None
        receiver, name, call_arguments = parser.invocation_parts(call, self.units[info.method.path].source)
        index = next((
            number for number, argument in enumerate(call_arguments)
            if (argument.start_byte, argument.end_byte) == (node.start_byte, node.end_byte)
        ), None)
        return (call, receiver, name, call_arguments, index) if index is not None else None

    def _reference_parameters(self, info: _Method, reference: Node) -> tuple[str | None, ...] | None:
        """The parameter types of the functional interface a method
        reference implements, when the sources prove its target: a direct
        argument of a JDK callback of a written container, or of a source
        method parameter typed by a source or JDK functional interface
        (BL-24), or a variable whose type is written (BL-37). Their count is
        the arity the reference binds; an unknown type is ``None``.
        ``None`` for any other target."""
        site = self._argument_site(info, reference)
        if site is None:
            return self._assigned_signature(info, reference)
        call, receiver, name, call_arguments, index = site
        view = self._generic_view(info, receiver, call) if receiver is not None else None
        if view is not None:
            slots = {
                slot for key, slot in _LAMBDA_PARAMETERS.items()
                if key[:4] == (view[0], name, len(call_arguments), index)
            }
            if len(slots) != 1:
                return None
            return tuple(_flat(view[1][slot]) for slot in next(iter(slots)))
        return self._functional_parameters(info, call, receiver, name, call_arguments, index)

    def _functional_parameters(
        self, info: _Method, call: Node, receiver: Node | None, name: str, arguments: list[Node], index: int,
    ) -> tuple[str | None, ...] | None:
        """The parameter types of the functional interface a source method
        declares at argument ``index`` (BL-24).

        Every declaration of the name and arity on the receiver's type and
        its ancestors must agree; a varargs overload, or an external
        supertype that may declare the name, leaves the target unknown.
        """
        path = info.method.path
        if receiver is None:
            owner = self._lexical_owner(info, name, call)
            if owner is None:
                return None
            context: tuple[str, _Env] | None = (owner, self._owner_env(owner, self._module_of(owner, path)))
        else:
            context = self._receiver_env(info, receiver, call)
        if context is None or context[0] not in self.types:
            return None
        owner = context[0]
        locations = self._type_candidates(owner, path)
        if len(locations) != 1:
            return None
        module = self.path_modules.get(locations[0][0], info.module)
        if self._may_inherit_unknown(owner, module, name):
            return None
        ancestors = self.ancestors(owner, module)
        if any(self.varargs_by_owner.get((base, name, base_module)) for base, base_module in ancestors):
            return None
        declarations = [
            candidate for base, base_module in ancestors
            for candidate in self.by_owner.get((base, name, len(arguments), base_module), [])
        ]
        found: set[tuple[str | None, ...] | None] = set()
        for declaration in declarations:
            formals = declaration.node.child_by_field_name("parameters")
            typed = [
                formal for formal in (formals.named_children if formals is not None else [])
                if formal.type in {"formal_parameter", "spread_parameter"}
            ]
            type_node = typed[index].child_by_field_name("type") if len(typed) == len(arguments) else None
            if type_node is None or typed[index].type != "formal_parameter":
                return None
            env = self._call_env(info, declaration, context, call, arguments, 1)
            found.add(self._functional_signature(declaration.method.path, type_node, declaration.owner, env))
        return next(iter(found)) if len(found) == 1 else None

    def _functional_signature(
        self, path: str, type_node: Node, owner: str, env: _Env | None,
    ) -> tuple[str | None, ...] | None:
        """The parameter types of the single abstract method of a written
        functional interface type.

        A source interface counts when it and its source ancestors are
        interfaces with no external supertype and declare one abstract
        method name and arity, ``Object`` methods aside. Its parameter types
        take the written type arguments, through ``extends`` clauses. A JDK
        functional type of ``_JDK_FUNCTIONAL_SIGNATURES`` takes them at its
        fixed positions (BL-37); a raw one, and any other JDK functional
        interface, gives its arity only, untyped.
        """
        source = self.units[path].source
        name_node = type_node.named_children[0] if type_node.type == "generic_type" else type_node
        written = self.resolve(path, parser.node_text(source, name_node), owner, allow_ambiguous=True, at=name_node)
        if written in _JDK_FUNCTIONAL_SIGNATURES:
            count, positions, _result = _JDK_FUNCTIONAL_SIGNATURES[written]
            arguments = self._functional_arguments(path, type_node, owner, env, count)
            if arguments is None:
                return (None,) * len(positions)
            return tuple(arguments[position] for position in positions)
        if written in _JDK_FUNCTIONAL_ARITIES:
            return (None,) * _JDK_FUNCTIONAL_ARITIES[written]
        parameterized = self._parameterized(path, type_node, owner, env)
        if parameterized is None:
            return None
        interface, interface_env = parameterized
        module = self._module_of(interface, path)
        abstract: list[tuple[tuple[str | None, int], str, str, Node]] = []
        for ancestor, ancestor_module in self.ancestors(interface, module):
            if self.external_bases.get((ancestor, ancestor_module)):
                return None
            for location, declaration in self._module_locations(ancestor, ancestor_module):
                if declaration.type != "interface_declaration":
                    return None
                body = declaration.child_by_field_name("body")
                ancestor_source = self.units[location].source
                for member in body.named_children if body is not None else []:
                    if member.type != "method_declaration" or member.child_by_field_name("body") is not None:
                        continue
                    modifiers = parser.modifiers_node(member)
                    if modifiers is not None and {"static", "default", "private"} & set(
                        parser.node_text(ancestor_source, modifiers).split()
                    ):
                        continue
                    formals = member.child_by_field_name("parameters")
                    count = len([
                        formal for formal in (formals.named_children if formals is not None else [])
                        if formal.type in {"formal_parameter", "spread_parameter"}
                    ])
                    key = (parser.declaration_name(member, ancestor_source), count)
                    if key not in _OBJECT_DISPATCH:
                        abstract.append((key, ancestor, location, member))
        if len({key for key, _ancestor, _location, _member in abstract}) != 1:
            return None
        signatures: set[tuple[str | None, ...]] = set()
        for _key, ancestor, location, member in abstract:
            ancestor_env = self._supertype_env(interface, module, interface_env, ancestor) or {}
            types: list[str | None] = []
            formals = member.child_by_field_name("parameters")
            for formal in formals.named_children if formals is not None else []:
                if formal.type == "spread_parameter":
                    return None
                formal_type = formal.child_by_field_name("type") if formal.type == "formal_parameter" else None
                if formal_type is not None:
                    types.append(self._declared_type(location, formal_type, ancestor, ancestor_env))
            signatures.add(tuple(types))
        return next(iter(signatures)) if len(signatures) == 1 else None

    def _functional_arguments(
        self, path: str, type_node: Node, owner: str, env: _Env | None, count: int,
    ) -> tuple[str | None, ...] | None:
        """The written type arguments of a JDK functional type, as its
        single abstract method sees them (JLS 9.9): ``? extends X`` and
        ``? super X`` give ``X``, ``?`` and a nested type nothing. ``None``
        for a raw type or a count mismatch."""
        written = parser.child_by_type(type_node, "type_arguments") if type_node.type == "generic_type" else None
        arguments = written.named_children if written is not None else []
        if len(arguments) != count:
            return None
        source = self.units[path].source
        found: list[str | None] = []
        for argument in arguments:
            if argument.type == "wildcard":
                bound = argument.named_children[-1] if argument.named_children else None
                if bound is None or not re.match(r"\?\s*(extends|super)\b", parser.node_text(source, argument)):
                    found.append(None)
                    continue
                argument = bound
            found.append(self._type_argument_value(path, argument, owner, env))
        return tuple(found)

    def _lambda_result(self, info: _Method, lambda_node: Node, depth: int) -> str | None:
        """The type of the value a lambda returns: its body expression, or
        the one type every ``return`` of its block gives (BL-37).

        Typed under the typing guard, since the body's names may be lambda
        parameters whose type depends on the call being inferred.
        """
        body = lambda_node.child_by_field_name("body")
        key = (info.method.path, lambda_node.start_byte)
        if body is None or key in self._typing:
            return None
        if body.type == "block":
            returns = [
                node for node in parser.walk(body)
                if node.type == "return_statement" and self._enclosing_body(node, body)
            ]
            if not returns or any(not node.named_children for node in returns):
                return None
            values = [node.named_children[0] for node in returns]
        else:
            values = [body]
        self._typing.add(key)
        try:
            types = {self.expression_type(info, value, value, depth + 1) for value in values}
        finally:
            self._typing.discard(key)
        found = types.pop() if len(types) == 1 else None
        return _BOXED.get(found, found) if found is not None and found != "void" else None

    @staticmethod
    def _enclosing_body(node: Node, body: Node) -> bool:
        """Whether ``body`` is the nearest lambda or method body around
        ``node``: a ``return`` of a nested lambda or class is not its own."""
        current = node.parent
        while current is not None and current != body:
            if current.type in {"lambda_expression", "class_body", "method_declaration"}:
                return False
            current = current.parent
        return current == body

    def _generic_view(
        self, info: _Method, node: Node | None, invocation: Node, depth: int = 0,
    ) -> _View | None:
        """The JDK container kind of an expression and its type arguments.

        Read from the written type of a variable, field or ``this.field``,
        from the initializer of a ``var`` local, from the declared return
        type of a source method (BL-35), then through calls that keep the
        arguments (``stream()``, ``filter``, ``findFirst``, ``keySet()``).
        ``None`` for anything else.
        """
        if node is None or depth > 16:
            return None
        source = self.units[info.method.path].source
        if node.type == "parenthesized_expression" and node.named_children:
            return self._generic_view(info, node.named_children[0], invocation, depth + 1)
        if node.type == "object_creation_expression":
            return self._created_view(info, node, depth + 1)
        if node.type == "class_literal" and node.named_children:
            # ``Order.class`` is a ``Class<Order>``; a primitive, array or
            # ``void`` literal gives nothing (BL-44, ADR-90).
            type_node = node.named_children[0]
            if type_node.type not in {"type_identifier", "scoped_type_identifier"}:
                return None
            literal = self.resolve(
                info.method.path, parser.node_text(source, type_node), info.owner, allow_ambiguous=True,
                at=type_node,
            )
            return ("class", (literal,)) if literal is not None else None
        if node.type == "method_invocation":
            receiver, name, arguments = parser.invocation_parts(node, source)
            jdk_owner = self._jdk_static_owner(info, receiver, node)
            if jdk_owner is not None:
                # ``List.of(a, b)``, ``Optional.of(o)`` (BL-36).
                return self._factory_view(info, node, jdk_owner, name, arguments, depth + 1)
            inner = self._generic_view(info, receiver, node, depth + 1)
            if inner is None:
                # ``catalog.list()``: a source method's declared return, or
                # ``repo.findAll()`` inherited from Spring Data (BL-43).
                return self._returned_view(info, node, depth + 1) or self._repository_view(info, node, depth + 1)
            if inner[0] == "stream" and name == "collect" and len(arguments) == 1 and (
                self._collects_to_collection(info, arguments[0])
            ):
                # ``stream.collect(Collectors.toList())`` (BL-36).
                return "iterable", inner[1]
            flattens = _ELEMENT_TRANSFORMS.get((inner[0], name))
            if flattens is not None and arguments and (
                len(arguments) == 1 or name.endswith("Async") and len(arguments) == 2
            ):
                # ``stream().map(Order::getLine)`` (BL-40).
                element = self._transformed(info, arguments[0], inner[0], flattens, depth + 1)
                return (inner[0], (element,)) if element is not None else None
            index = _ELEMENT_ACCESSORS.get((inner[0], name, len(arguments)))
            if index is not None:
                # ``nested.get(k)`` on ``Map<String, List<Order>>`` (BL-41).
                element = inner[1][index]
                return (element.kind, element.arguments) if isinstance(element, _Nested) else None
            if inner[0] == "map" and name == "entrySet" and not arguments:
                # ``Set<Map.Entry<K, V>> entrySet()`` (BL-41).
                return "iterable", (_Nested("java.util.Map.Entry", "entry", tuple(
                    _flat(argument) for argument in inner[1]
                )),)
            rule = _GENERIC_CALLS.get((inner[0], name, len(arguments)))
            if rule is None:
                return None
            return rule[0], tuple(inner[1][index] for index in rule[1])
        field_only = False
        if node.type == "field_access":
            target = node.child_by_field_name("object")
            field = node.child_by_field_name("field")
            if target is None or target.type != "this" or field is None:
                return None
            name, field_only = parser.node_text(source, field), True
        elif node.type == "identifier":
            name = parser.node_text(source, node)
        else:
            return None
        written = self._written_variable(info, name, invocation, field_only=field_only)
        if written is None:
            return None
        path, type_node, owner, env = written
        if parser.node_text(self.units[path].source, type_node).strip() == "var":
            loop = type_node.parent
            if loop is not None and loop.type == "enhanced_for_statement":
                # ``for (var e : m.entrySet())``: the nested element of the
                # iterable, read in the loop header (BL-41).
                iterable = loop.child_by_field_name("value")
                if path != info.method.path or iterable is None:
                    return None
                view = self._generic_view(info, iterable, iterable, depth + 1)
                element = view[1][0] if view is not None and view[0] == "iterable" else None
                return (element.kind, element.arguments) if isinstance(element, _Nested) else None
            # ``var list = catalog.list()``: the view of the initializer,
            # read where it is written, under the typing cycle guard.
            value = self._var_value(path, type_node, name)
            if value is None or (key := (path, value.start_byte)) in self._typing:
                return None
            self._typing.add(key)
            try:
                return self._generic_view(info, value, value, depth + 1)
            finally:
                self._typing.discard(key)
        container = self.resolve(
            path, parser.node_text(self.units[path].source, type_node), owner, allow_ambiguous=True, at=type_node,
        )
        # The written declaration must be the one the type pass reads.
        if container not in _GENERIC_CONTAINERS or container != self.variable_type(
            info, name, invocation, field_only=field_only,
        ):
            return None
        kind, arity = _GENERIC_CONTAINERS[container]
        type_arguments = parser.child_by_type(type_node, "type_arguments")
        written_arguments = type_arguments.named_children if type_arguments is not None else []
        if len(written_arguments) != arity:
            return None
        return kind, tuple(
            self._view_argument(path, argument, owner, env) for argument in written_arguments
        )

    def _transformed(
        self, info: _Method, function: Node, kind: str, flattens: bool, depth: int,
    ) -> _Arg | None:
        """The element a ``map``-like call gives its result (BL-40).

        The function is a method reference whose name has a single declared
        arity on its receiver type, read from its declared return type, or
        a lambda the pass types (``_lambda_result``). For ``flatMap`` and
        ``thenCompose`` the result must be a container of ``kind``, and its
        element is the new one: a lambda whose body is one expression, or a
        reference whose declared return type is written. Anything else
        leaves the chain untyped.
        """
        while function.type == "parenthesized_expression" and function.named_children:
            function = function.named_children[0]
        if function.type == "method_reference":
            returns = self._reference_returns(info, function)
            if returns is None:
                return None
            if not flattens:
                types = {
                    self.resolve(declaration.method.path, parser.node_text(
                        self.units[declaration.method.path].source, type_node,
                    ), declaration.owner, at=type_node)
                    for declaration, type_node in returns
                }
                found = types.pop() if len(types) == 1 else None
                return _BOXED.get(found, found) if found not in {None, "void"} else None
            elements: set[_Arg | None] = set()
            for declaration, type_node in returns:
                path = declaration.method.path
                if type_node.type != "generic_type":
                    return None
                name_node = type_node.named_children[0]
                container = self.resolve(
                    path, parser.node_text(self.units[path].source, name_node), declaration.owner,
                    allow_ambiguous=True, at=name_node,
                )
                written = parser.child_by_type(type_node, "type_arguments")
                values = written.named_children if written is not None else []
                if container not in _GENERIC_CONTAINERS or _GENERIC_CONTAINERS[container] != (kind, 1) or (
                    len(values) != 1
                ):
                    return None
                elements.add(self._view_argument(path, values[0], declaration.owner, None))
            return elements.pop() if len(elements) == 1 else None
        if function.type != "lambda_expression":
            return None
        if not flattens:
            return self._lambda_result(info, function, depth)
        body = function.child_by_field_name("body")
        key = (info.method.path, function.start_byte)
        if body is None or body.type == "block" or key in self._typing:
            return None
        self._typing.add(key)
        try:
            view = self._generic_view(info, body, body, depth + 1)
        finally:
            self._typing.discard(key)
        return view[1][0] if view is not None and view[0] == kind and len(view[1]) == 1 else None

    def _reference_returns(self, info: _Method, reference: Node) -> list[tuple[_Method, Node]] | None:
        """The declarations a method reference names and their written
        return types, when its name has a single declared arity on the
        receiver's source type and every declaration writes a return type."""
        parts = reference.named_children
        if len(parts) < 2 or parts[-1].type != "identifier":
            return None
        source = self.units[info.method.path].source
        receiver, name = parts[0], parser.node_text(source, parts[-1])
        owner = self.receiver_type(info, receiver, reference)
        if owner is None or owner not in self.types:
            return None
        arities = self._declared_arities(owner, name, info.method.path, info.module)
        if len(arities) != 1 or not arities <= {0, 1}:
            # A ``Function`` binds one parameter: an unbound reference takes
            # it as the receiver, any other as the argument.
            return None
        arity = next(iter(arities))
        locations = self._type_candidates(owner, info.method.path)
        if len(locations) != 1:
            return None
        module = self.path_modules.get(locations[0][0], info.module)
        declarations = self.by_owner.get((owner, name, arity, module), []) or [
            candidate for base, base_module in self.ancestors(owner, module)
            for candidate in self.by_owner.get((base, name, arity, base_module), [])
        ]
        found: list[tuple[_Method, Node]] = []
        for declaration in declarations:
            type_node = declaration.node.child_by_field_name("type")
            if type_node is None or type_node.type == "type_identifier" and self._type_variable(
                parser.node_text(self.units[declaration.method.path].source, type_node).strip(), type_node,
            ) is not None:
                # A type variable's argument is not read here.
                return None
            found.append((declaration, type_node))
        return found or None

    def _jdk_static_owner(self, info: _Method, receiver: Node | None, invocation: Node) -> str | None:
        """The external type a static call names: ``List`` in ``List.of(..)``
        or ``java.util.List``, or the owner of a single static import for an
        unqualified call (BL-36).

        A capitalized name or a qualified name that is no variable, resolved
        where it is written to a type outside the sources. An unqualified
        call needs one single static import of its name, no enclosing
        anonymous class, and no enclosing type that declares or may inherit
        the name, which would shadow the import (JLS 6.4.1).
        """
        path = info.method.path
        source = self.units[path].source
        unit = self.units[path]
        if receiver is None:
            name_node = invocation.child_by_field_name("name")
            name = parser.node_text(source, name_node) if name_node is not None else ""
            owners = unit.static_imports.get(name, [])
            if len(owners) != 1:
                return None
            node = invocation.parent
            while node is not None and node != info.node:
                if _is_anonymous_body(node):
                    return None
                node = node.parent
            scope = info.owner
            while scope and scope != unit.package:
                if scope in self.types and (
                    self._declared_arities(scope, name, path, info.module)
                    or self._may_inherit_unknown(scope, self._receiver_module(scope, info), name)
                ):
                    return None
                scope = scope.rpartition(".")[0]
            text = owners[0]
        elif receiver.type == "identifier":
            text = parser.node_text(source, receiver)
            if not text[:1].isupper() or self.variable_type(info, text, invocation) is not None:
                return None
        elif receiver.type in {"field_access", "scoped_identifier"}:
            text = re.sub(r"\s+", "", parser.node_text(source, receiver))
            if not re.fullmatch(r"[a-z_$][\w$]*(?:\.[\w$]+)+", text) or self.variable_type(
                info, text.split(".", 1)[0], invocation,
            ) is not None:
                return None
        else:
            return None
        resolved = self.resolve(path, text, info.owner, at=receiver)
        return resolved if resolved is not None and resolved not in self.types else None

    def _factory_view(
        self, info: _Method, invocation: Node, owner: str, name: str, arguments: list[Node], depth: int,
    ) -> _View | None:
        """The view a static JDK factory returns (BL-36, ``_JDK_FACTORIES``).

        Arguments of different types give nothing: the pass computes no
        common supertype, so ``List.of(circle, square)`` stays untyped. An
        array argument also gives nothing to ``elements``, whose overloads
        then differ (``List.of(E)`` against ``Arrays.asList(T...)``).
        """
        factory = _JDK_FACTORIES.get((owner, name))
        if factory is None or not arguments:
            return None
        kind, rule = factory
        if rule == "argument":
            return self._generic_view(info, arguments[0], invocation, depth + 1) if len(arguments) <= 2 else None
        if rule == "copy":
            view = self._generic_view(info, arguments[0], invocation, depth + 1) if len(arguments) == 1 else None
            return view if view is not None and view[0] == kind else None
        if rule == "array":
            array = self.expression_type(info, arguments[0], invocation, depth + 1) if len(arguments) == 1 else None
            if array is None or not array.endswith("[]") or array[:-2] in _PRIMITIVES:
                return None
            return kind, (array[:-2],)
        groups = [arguments] if rule == "elements" else [arguments[0::2], arguments[1::2]]
        if rule == "pairs" and len(arguments) % 2:
            return None
        values: list[str | None] = []
        for group in groups:
            types = {self.expression_type(info, argument, invocation, depth + 1) for argument in group}
            found = types.pop() if len(types) == 1 else None
            if found is None or found.endswith("[]") or found == "void":
                return None
            values.append(_BOXED.get(found, found))
        return kind, tuple(values)

    def _collects_to_collection(self, info: _Method, argument: Node) -> bool:
        """Whether ``argument`` is ``Collectors.toList()`` or a kin that
        collects the stream's elements unchanged (BL-36)."""
        if argument.type != "method_invocation":
            return False
        receiver, name, arguments = parser.invocation_parts(argument, self.units[info.method.path].source)
        return not arguments and name in _COLLECTION_COLLECTORS and (
            self._jdk_static_owner(info, receiver, argument) == "java.util.stream.Collectors"
        )

    def _created_view(self, info: _Method, creation: Node, depth: int) -> _View | None:
        """The view of ``new ArrayList<Order>(..)`` or of a diamond (BL-36).

        Written type arguments give the view. A diamond takes the view of
        its one constructor argument when it is of the same kind, as
        ``new ArrayList<>(orders)``; any other diamond, a raw type and an
        anonymous class give nothing.
        """
        path = info.method.path
        source = self.units[path].source
        type_node = creation.child_by_field_name("type")
        if (
            type_node is None or type_node.type != "generic_type"
            or any(child.type == "class_body" for child in creation.children)
        ):
            return None
        name_node = type_node.named_children[0]
        container = self.resolve(
            path, parser.node_text(source, name_node), info.owner, allow_ambiguous=True, at=name_node,
        )
        if container not in _GENERIC_CONTAINERS:
            return None
        kind, arity = _GENERIC_CONTAINERS[container]
        written = parser.child_by_type(type_node, "type_arguments")
        written_arguments = written.named_children if written is not None else []
        if len(written_arguments) == arity:
            return kind, tuple(
                self._view_argument(path, argument, info.owner, None) for argument in written_arguments
            )
        arguments = parser.argument_nodes(creation)
        if written_arguments or len(arguments) != 1:
            return None
        view = self._generic_view(info, arguments[0], creation, depth + 1)
        return view if view is not None and view[0] == kind and len(view[1]) == arity else None

    def _var_value(self, path: str, type_node: Node, name: str) -> Node | None:
        """The initializer of the ``var`` local ``name`` that ``type_node``
        declares: a local variable or a ``try`` resource."""
        declaration = type_node.parent
        if declaration is None:
            return None
        if declaration.type == "resource":
            return declaration.child_by_field_name("value")
        for child in declaration.named_children:
            ident = child.child_by_field_name("name") if child.type == "variable_declarator" else None
            if ident is not None and parser.node_text(self.units[path].source, ident) == name:
                return child.child_by_field_name("value")
        return None

    def _written_variable(
        self, info: _Method, name: str, invocation: Node, *, field_only: bool = False,
    ) -> tuple[str, Node, str, _Env | None] | None:
        """The written type of the variable ``name`` at ``invocation``:
        ``(path, type node, owner, arguments)``, following
        :meth:`variable_type`'s lexical lookup, or ``None`` when it is not
        written. ``arguments`` are those the scope gives the type declaring
        an inherited field, ``None`` when the declaration is in scope."""
        path = info.method.path
        scope = invocation.parent
        while scope is not None:
            if not field_only:
                declarator = self._declarator(info, scope, name, invocation)
                if declarator is not None:
                    if declarator.type_node is None or declarator.lambda_slot is not None or declarator.dimensions:
                        return None
                    return path, declarator.type_node, info.owner, None
            if _is_class_scope_body(scope):
                declared, _type = self._class_scope_field(info, scope, name)
                if declared or field_only:
                    return None
            elif scope.type in _TYPES:
                owner = self.owner(path, scope)
                module = self.path_modules.get(path, info.module)
                for level in self._ancestor_levels(owner, module):
                    found = [
                        (location, type_node, candidate)
                        for candidate, candidate_module in level
                        for location, declaration in self._module_locations(candidate, candidate_module)
                        for type_node in self._field_type_nodes(location, declaration, name)
                    ]
                    if found:
                        location, type_node, candidate = found[0]
                        if len(found) != 1 or type_node is None or type_node.type == "dimensions":
                            return None
                        env = None if candidate == owner else self._inherited_env(owner, module, None, candidate)
                        return location, type_node, candidate, env
                if field_only:
                    return None
            scope = scope.parent
        return None

    def _class_scope_field(self, info: _Method, scope: Node, name: str) -> tuple[bool, str | None]:
        """A field ``name`` that the class ``scope`` declares or inherits.

        ``scope`` is a named type declaration, an anonymous class body or
        an enum constant's body. An anonymous class's own fields come first,
        then those of its source supertype; an external supertype is assumed
        to declare none. A constant body's own fields come first; the enum's
        are then found as those of the enclosing type (BC-14).
        """
        path = info.method.path
        source = self.units[path].source
        if scope.type in _TYPES:
            return self._member_field(self.owner(path, scope), self.path_modules.get(path, info.module), name)
        found: set[str | None] = set()
        for member in scope.named_children:
            type_node = member.child_by_field_name("type")
            if member.type != "field_declaration" or type_node is None:
                continue
            for child in member.named_children:
                ident = child.child_by_field_name("name")
                if ident is not None and parser.node_text(source, ident) == name:
                    found.add(self._declared_type(path, type_node, info.owner))
        if found:
            return True, next(iter(found)) if len(found) == 1 else None
        type_node = scope.parent.child_by_field_name("type") if scope.parent is not None else None
        supertype = self.resolve(
            path, parser.node_text(source, type_node), info.owner, allow_ambiguous=True, at=type_node,
        ) if type_node is not None else None
        if supertype is None or supertype not in self.types:
            return False, None
        # ``new Box<Circle>() { ... }``: the arguments the creation writes.
        parameterized = self._parameterized(path, type_node, info.owner, None) if type_node is not None else None
        env = parameterized[1] if parameterized is not None and parameterized[0] == supertype else {}
        return self._member_field(supertype, self._receiver_module(supertype, info), name, env)

    def _ancestor_levels(self, owner: str, module: str) -> list[list[tuple[str, str]]]:
        """``owner`` and its source ancestors, grouped by hierarchy distance."""
        key = (owner, module)
        levels = self._levels.get(key)
        if levels is None:
            levels = [[key]]
            seen = {key}
            while True:
                following = []
                for current in levels[-1]:
                    for base in sorted(self.bases.get(current, ())):
                        if base not in seen:
                            seen.add(base)
                            following.append(base)
                if not following:
                    break
                levels.append(following)
            self._levels[key] = levels
        return levels

    def _module_locations(self, owner: str, module: str) -> list[tuple[str, Node]]:
        locations = self.types.get(owner, [])
        same = [location for location in locations if self.path_modules.get(location[0], "") == module]
        return same or locations

    def _member_field(
        self, owner: str, module: str, name: str, env: _Env | None = None,
    ) -> tuple[bool, str | None]:
        """A field ``name`` of a source type, the nearest declaration first.

        A field hides any field of the same name farther up the hierarchy:
        the type itself is searched, then its direct supertypes, and so on
        (S11). Fields, interface constants, enum constants and record
        components count. Several declarations at one distance leave the
        type unknown, as Java reports them ambiguous.

        ``env`` holds the type arguments of ``owner`` at the use; ``None``
        when the use is in ``owner``'s own code. A field typed by a type
        variable of an ancestor takes the argument ``owner``'s ``extends``
        or ``implements`` clause writes (BL-26).
        """
        for level in self._ancestor_levels(owner, module):
            found: set[str | None] = set()
            for candidate, candidate_module in level:
                candidate_env = env if candidate == owner else self._inherited_env(owner, module, env, candidate)
                for path, declaration in self._module_locations(candidate, candidate_module):
                    found.update(self._declared_fields(path, declaration, candidate, name, candidate_env))
            if found:
                return True, next(iter(found)) if len(found) == 1 else None
        return False, None

    def _inherited_env(self, owner: str, module: str, env: _Env | None, ancestor: str) -> _Env:
        """The type arguments ``owner`` gives ``ancestor``; ``env`` holds
        ``owner``'s own, ``None`` for its own code (its bounds)."""
        start = env if env is not None else self._owner_env(owner, module)
        return self._supertype_env(owner, module, start, ancestor) or {}

    def _declared_fields(
        self, path: str, declaration: Node, owner: str, name: str, env: _Env | None = None,
    ) -> set[str | None]:
        """Types of the fields ``name`` one type declaration writes."""
        found: set[str | None] = set()
        for type_node in self._field_type_nodes(path, declaration, name):
            if type_node is None:
                found.add(owner)
            elif type_node.type == "dimensions":
                member = type_node.parent.parent if type_node.parent is not None else None
                written = member.child_by_field_name("type") if member is not None else None
                element = self._declared_type(path, written, owner, env) if written is not None else None
                count = parser.node_text(self.units[path].source, type_node).count("[")
                found.add(element + "[]" * count if element is not None else None)
            else:
                found.add(self._declared_type(path, type_node, owner, env))
        return found

    def _field_type_nodes(self, path: str, declaration: Node, name: str) -> list[Node | None]:
        """Written types of the fields ``name`` one type declaration holds;
        ``None`` stands for an enum constant, typed by the enum itself, and
        a ``dimensions`` node for a C-style array declarator."""
        source = self.units[path].source
        found: list[Node | None] = []
        if declaration.type == "record_declaration":
            parameters = declaration.child_by_field_name("parameters")
            for parameter in parameters.named_children if parameters is not None else []:
                ident = parameter.child_by_field_name("name")
                type_node = parameter.child_by_field_name("type")
                if ident is not None and type_node is not None and parser.node_text(source, ident) == name:
                    found.append(type_node)
        body = declaration.child_by_field_name("body")
        members = list(body.named_children) if body is not None else []
        for member in list(members):
            if member.type == "enum_body_declarations":
                members.extend(member.named_children)
        for member in members:
            if member.type in {"field_declaration", "constant_declaration"}:
                type_node = member.child_by_field_name("type")
                for child in member.named_children:
                    ident = child.child_by_field_name("name")
                    if ident is not None and type_node is not None and parser.node_text(source, ident) == name:
                        # ``Foo x[]`` stands as its ``dimensions`` node (BC-11).
                        found.append(child.child_by_field_name("dimensions") or type_node)
            elif member.type == "enum_constant" and declaration.type == "enum_declaration":
                # Enum constants are fields of the enum type itself.
                ident = member.child_by_field_name("name")
                if ident is not None and parser.node_text(source, ident) == name:
                    found.append(None)
        return found

    def _initializer_type(self, info: _Method, value: Node | None) -> str | None:
        """The type of a ``var`` initializer, read where it is written.

        Names in the initializer resolve at the initializer's position, not
        at the call site: a later local never shadows the field it names, and
        two locals cannot type each other in a cycle. A declaration already
        being typed has no type, which ends any remaining cycle (S1, S2).
        """
        if value is None:
            return None
        key = (info.method.path, value.start_byte)
        if key in self._typing:
            return None
        self._typing.add(key)
        try:
            return self.expression_type(info, value, value)
        finally:
            self._typing.discard(key)

    def _scoped_declaration(
        self, info: _Method, scope: Node, name: str, invocation: Node,
    ) -> _Declarator | None:
        """A variable that ``scope`` itself declares for the code it encloses.

        Returns ``None`` when ``scope`` does not declare ``name``. A
        declarator without a written type (multi-catch, varargs), an untyped
        lambda parameter or a ``var`` loop variable still shadows fields.
        """
        source = self.units[info.method.path].source

        def named(node: Node | None) -> bool:
            return node is not None and parser.node_text(source, node) == name

        if scope.type == "enhanced_for_statement":
            body = scope.child_by_field_name("body")
            if named(scope.child_by_field_name("name")) and body is not None and (
                body.start_byte <= invocation.start_byte < body.end_byte
            ):
                type_node = scope.child_by_field_name("type")
                if type_node is not None and parser.node_text(source, type_node).strip() == "var":
                    return _Declarator(type_node, element_of=scope.child_by_field_name("value"))
                return _Declarator(type_node, dimensions=_dimensions(source, scope))
        elif scope.type == "catch_clause":
            parameter = parser.child_by_type(scope, "catch_formal_parameter")
            if parameter is not None and named(parameter.child_by_field_name("name")):
                catch_type = parser.child_by_type(parameter, "catch_type")
                alternatives = catch_type.named_children if catch_type is not None else []
                return _Declarator(alternatives[0] if len(alternatives) == 1 else None)
        elif scope.type == "try_with_resources_statement":
            # A resource is in scope in the later resources and the try
            # body, never in ``catch`` or ``finally`` (JLS 14.20.3, BC-11).
            resources = parser.child_by_type(scope, "resource_specification")
            body = scope.child_by_field_name("body")
            in_scope = resources is not None and resources.start_byte <= invocation.start_byte < resources.end_byte or (
                body is not None and body.start_byte <= invocation.start_byte < body.end_byte
            )
            for resource in resources.named_children if resources is not None and in_scope else []:
                if (
                    resource.type == "resource"
                    and resource.end_byte <= invocation.start_byte
                    and named(resource.child_by_field_name("name"))
                ):
                    return _Declarator(resource.child_by_field_name("type"), resource.child_by_field_name("value"))
        elif scope.type == "lambda_expression":
            parameters = scope.child_by_field_name("parameters")
            if parameters is not None and parameters.type == "identifier":
                if named(parameters):
                    return _Declarator(lambda_slot=(scope, 0, 1))
            elif parameters is not None:
                untyped = [parameter for parameter in parameters.named_children if parameter.type == "identifier"]
                for parameter in parameters.named_children:
                    if parameter.type == "identifier" and named(parameter):
                        return _Declarator(lambda_slot=(scope, untyped.index(parameter), len(untyped)))
                    if parameter.type == "formal_parameter" and named(parameter.child_by_field_name("name")):
                        return _Declarator(
                            parameter.child_by_field_name("type"), dimensions=_dimensions(source, parameter),
                        )
        elif scope.type in _PATTERN_SCOPES:
            # A pattern variable where its ``instanceof`` is proven, or in
            # its ``switch`` case (JLS 6.3.1).
            child = invocation
            while child.parent is not None and child.parent != scope:
                child = child.parent
            type_node = patterns.flow_binding(source, scope, child, name) if child.parent == scope else None
            if type_node is not None:
                return _Declarator(type_node)
        elif scope.type == "method_declaration" or scope.type in CONSTRUCTOR_DECLARATIONS:
            # ``info`` itself, an anonymous class method in its body, or the
            # method enclosing a local class. A compact constructor declares
            # the record components as its parameters (BL-39).
            parameters = declaration_parameters(scope)
            for parameter in parameters.named_children if parameters is not None else []:
                if parameter.type == "formal_parameter" and named(parameter.child_by_field_name("name")):
                    return _Declarator(
                        parameter.child_by_field_name("type"), dimensions=_dimensions(source, parameter),
                    )
                if parameter.type == "spread_parameter" and any(
                    named(child.child_by_field_name("name"))
                    for child in parameter.named_children if child.type == "variable_declarator"
                ):
                    # A varargs parameter is an array; it shadows outer names.
                    return _Declarator()
        return None

    def receiver_type(self, info: _Method, receiver: Node | None, invocation: Node, depth: int = 0) -> str | None:
        """Read declared receiver/return types without evaluating method bodies."""
        if depth > 16:
            return None
        source = self.units[info.method.path].source
        if receiver is None or receiver.type == "this":
            return info.owner
        if receiver.type == "super":
            superclass = self._superclass(info.owner, info.module)
            return superclass[0] if superclass is not None else None
        if receiver.type == "identifier":
            name = parser.node_text(source, receiver)
            declared = self.variable_type(info, name, invocation)
            if declared is not None or not name[:1].isupper():
                return declared
            # ``Type.method()``: a capitalized name that is no variable is a
            # static call on that source type.
            static_owner = self.resolve(info.method.path, name, info.owner, at=receiver)
            return static_owner if static_owner in self.types else None
        if receiver.type in {"object_creation_expression", "cast_expression", "parenthesized_expression"}:
            return self.expression_type(info, receiver, invocation, depth + 1)
        if receiver.type == "array_access":
            # ``values[0].name()``: the element type of the array.
            array = self.expression_type(info, receiver.child_by_field_name("array"), invocation, depth + 1)
            return array[:-2] if array is not None and array.endswith("[]") else None
        if receiver.type == "field_access" and (
            (this := receiver.child_by_field_name("field")) is not None and this.type == "this"
        ):
            return self._qualified_this(info, receiver)
        if receiver.type == "field_access" and parser.node_text(source, receiver).startswith("this."):
            return self.variable_type(info, parser.node_text(source, receiver)[5:], invocation, field_only=True)
        if receiver.type == "field_access":
            # ``obj.field`` or ``Type.CONSTANT``: the declared field type.
            target = receiver.child_by_field_name("object")
            field = receiver.child_by_field_name("field")
            typed = self._receiver_env(info, target, invocation, depth + 1) if target is not None else None
            if typed is None or field is None or typed[0] not in self.types:
                return None
            return self._field_type(typed[0], parser.node_text(source, field), info.module, typed[1])
        if receiver.type != "method_invocation":
            return None
        parent_receiver, name, arguments = parser.invocation_parts(receiver, source)
        if (name, len(arguments)) in _OBJECT_RETURNS:
            # ``java.lang.Object`` fixes these return types for every receiver.
            return _OBJECT_RETURNS[(name, len(arguments))]
        if name == "requireNonNull" and 1 <= len(arguments) <= 2 and (
            self._jdk_static_owner(info, parent_receiver, receiver) == "java.util.Objects"
        ):
            # ``static <T> T requireNonNull(T obj)`` (BL-36).
            return self.expression_type(info, arguments[0], receiver, depth + 1)
        if (name, len(arguments)) in _ACCESSOR_CALLS:
            # ``circles.get(0)``, ``maybe.get()``: the element type a JDK
            # container's written type arguments give (BL-27).
            view = self._generic_view(info, parent_receiver, receiver, depth + 1)
            index = _ELEMENT_ACCESSORS.get((view[0], name, len(arguments))) if view is not None else None
            if view is not None and index is not None:
                return _flat(view[1][index])
        owner = self.receiver_type(info, parent_receiver, receiver, depth + 1)
        if (name, len(arguments)) in _ACCESSOR_CALLS and owner not in self.types:
            # A JDK accessor whose receiver has no container view: the chain
            # breaks here, which the call-edge diagnostic shows (ADR-87).
            self.broken_accessors[(info.method.path, receiver.start_byte)] = (
                receiver.start_point.row + 1, name,
            )
        if owner is None:
            return None
        position = _CLASS_TYPED.get((owner, name, len(arguments)))
        if position is not None and owner not in self.types:
            # ``ctx.getBean(Order.class)`` returns ``T`` of its ``Class<T>``
            # argument (BL-44, ADR-90).
            if position and self.expression_type(info, arguments[0], receiver, depth + 1) != "java.lang.String":
                return None
            view = self._generic_view(info, arguments[position], receiver, depth + 1)
            return _flat(view[1][0]) if view is not None and view[0] == "class" else None
        locations = self._type_candidates(owner, info.method.path)
        if len(locations) != 1:
            return None
        module = self.path_modules.get(locations[0][0], info.module)
        declarations = self._returning_declarations(info, owner, module, name, arguments, receiver, depth)
        if not declarations and (inherited := self._repository_return(info, receiver, depth + 1)) is not None:
            # ``repo.save(o)``, ``repo.findAll()`` inherited from Spring
            # Data: the type the signature table gives (BL-43, ADR-90).
            return inherited[0] if inherited[0] is not None else inherited[1][0]
        if not declarations and locations[0][1].type == "enum_declaration" and (
            (name, len(arguments)) in {("valueOf", 1), ("values", 0)}
        ):
            # The implicit static methods of an enum (JLS 8.9.3, BL-42).
            return owner if name == "valueOf" else owner + "[]"
        if not declarations and not arguments:
            # A record component or a Lombok getter is an implicit accessor;
            # a declared accessor of the same name was found above and wins.
            return (
                self._record_component_type(owner, name, locations[0][0])
                or self._lombok_getter_type(owner, name, module)
            )
        if len({candidate.parameters for candidate in declarations}) != 1:
            return None
        returns: set[str | None] = set()
        context: tuple[str, _Env] | None = None
        for candidate in declarations:
            type_node = candidate.node.child_by_field_name("type")
            if type_node is None:
                returns.add(None)
                continue
            text = parser.node_text(self.units[candidate.method.path].source, type_node)
            returned = self.resolve(candidate.method.path, text, candidate.owner, at=type_node)
            if returned is None and type_node.type == "type_identifier" and self._type_variable(text, type_node):
                # ``box.get()`` returning ``T``: the argument the receiver
                # or the call proves for ``T`` (BL-26).
                if context is None:
                    context = self._receiver_env(info, parent_receiver, receiver, depth + 1) or (owner, {})
                env = self._call_env(info, candidate, context, receiver, arguments, depth + 1)
                returned = self._declared_type(candidate.method.path, type_node, candidate.owner, env)
            returns.add(returned)
        return next(iter(returns)) if len(returns) == 1 else None

    def _receiver_env(
        self, info: _Method, node: Node | None, invocation: Node, depth: int = 0,
    ) -> tuple[str, _Env] | None:
        """The static type of a receiver and its type arguments (BL-26).

        The arguments are read where the sources write them: the declared
        type of a variable, parameter or field, a creation or a cast, or a
        method's declared return type. ``this`` and an unqualified call see
        the caller's type from its own code: each type variable takes its
        bound. Any other receiver has an unknown argument for every
        parameter.
        """
        if depth > 16:
            return None
        path = info.method.path
        source = self.units[path].source
        if node is None or node.type == "this":
            return info.owner, self._owner_env(info.owner, self._module_of(info.owner, path))
        if node.type == "parenthesized_expression" and node.named_children:
            return self._receiver_env(info, node.named_children[0], invocation, depth + 1)
        typed = self.receiver_type(info, node, invocation, depth + 1)
        if typed is None:
            return None
        parameterized: tuple[str, _Env] | None = None
        if node.type in {"object_creation_expression", "cast_expression"}:
            type_node = node.child_by_field_name("type")
            if type_node is not None:
                parameterized = self._parameterized(path, type_node, info.owner, None)
        elif node.type == "identifier" or (
            node.type == "field_access" and (target := node.child_by_field_name("object")) is not None
            and target.type == "this" and node.child_by_field_name("field") is not None
        ):
            field = node.child_by_field_name("field") if node.type == "field_access" else node
            written = self._written_variable(
                info, parser.node_text(source, field), invocation, field_only=node.type == "field_access",
            ) if field is not None else None
            if written is not None:
                parameterized = self._parameterized(*written)
        elif node.type == "method_invocation":
            parameterized = self._returned(info, node, depth + 1)
        if parameterized is not None and parameterized[0] == typed:
            return parameterized
        return typed, {}

    def _return_declarations(
        self, info: _Method, invocation: Node, depth: int,
    ) -> tuple[tuple[str, _Env], list[_Method], list[Node]] | None:
        """The declarations a call on a source receiver reads its return
        type from: ``(receiver context, declarations, arguments)``.

        They are looked up as :meth:`receiver_type` does: the receiver's own
        declarations, else those of its source ancestors. Several
        declarations share one parameter list, as an interface method and
        the abstract class declaring it again; overloads of one arity that
        the call does not tell apart give ``None``.
        """
        source = self.units[info.method.path].source
        parent_receiver, name, arguments = parser.invocation_parts(invocation, source)
        context = self._receiver_env(info, parent_receiver, invocation, depth + 1)
        if context is None:
            return None
        owner = context[0]
        locations = self._type_candidates(owner, info.method.path)
        if len(locations) != 1:
            return None
        module = self.path_modules.get(locations[0][0], info.module)
        declarations = self._returning_declarations(info, owner, module, name, arguments, invocation, depth)
        if len({candidate.parameters for candidate in declarations}) != 1:
            return None
        return context, declarations, arguments

    def _returning_declarations(
        self, info: _Method, owner: str, module: str, name: str, arguments: list[Node], invocation: Node,
        depth: int,
    ) -> list[_Method]:
        """The declarations of ``name`` a call on ``owner`` binds to, for
        its return type: the receiver's own and its source ancestors',
        narrowed by Java's overload selection, then without those an
        override in a subtype hides (BC-07). Several parameter lists left
        mean the call is not told apart."""
        declarations = [
            candidate for base, base_module in self.ancestors(owner, module)
            for candidate in self.by_owner.get((base, name, len(arguments), base_module), [])
        ]
        if len({candidate.parameters for candidate in declarations}) > 1:
            declarations = self._select_overload(declarations, tuple(
                self.expression_type(info, argument, invocation, depth + 1) for argument in arguments
            ))
        return [
            candidate for candidate in declarations
            if not any(
                other.parameters == candidate.parameters
                and (other.owner, other.module) != (candidate.owner, candidate.module)
                and (candidate.owner, candidate.module) in self.ancestors(other.owner, other.module)
                for other in declarations
            )
        ]

    def _returned(self, info: _Method, invocation: Node, depth: int) -> tuple[str, _Env] | None:
        """The declared return type of a call and its type arguments, when
        the declarations on a source receiver agree on it."""
        found = self._return_declarations(info, invocation, depth)
        if found is None:
            return None
        context, declarations, arguments = found
        results: list[tuple[str, _Env] | None] = []
        for declaration in declarations:
            type_node = declaration.node.child_by_field_name("type")
            if type_node is None:
                return None
            env = self._call_env(info, declaration, context, invocation, arguments, depth + 1)
            results.append(self._parameterized(declaration.method.path, type_node, declaration.owner, env))
        return results[0] if all(result == results[0] for result in results) else None

    def _returned_view(
        self, info: _Method, invocation: Node, depth: int,
    ) -> _View | None:
        """The JDK container view of a call to a source method (BL-35).

        ``List<Order> list()`` gives ``("iterable", ("p.Order",))``: the
        declared return type is read through the declarations and the type
        arguments :meth:`_returned` uses. Declarations that disagree, and a
        return type that is no JDK container or omits its arguments, give
        ``None``.
        """
        found = self._return_declarations(info, invocation, depth)
        if found is None:
            return None
        context, declarations, arguments = found
        views: set[_View] = set()
        for declaration in declarations:
            type_node = declaration.node.child_by_field_name("type")
            if type_node is None or type_node.type != "generic_type":
                return None
            path = declaration.method.path
            name_node = type_node.named_children[0]
            container = self.resolve(
                path, parser.node_text(self.units[path].source, name_node), declaration.owner,
                allow_ambiguous=True, at=name_node,
            )
            if container not in _GENERIC_CONTAINERS:
                return None
            kind, arity = _GENERIC_CONTAINERS[container]
            written = parser.child_by_type(type_node, "type_arguments")
            written_arguments = written.named_children if written is not None else []
            if len(written_arguments) != arity:
                return None
            env = self._call_env(info, declaration, context, invocation, arguments, depth + 1)
            views.add((kind, tuple(
                self._view_argument(path, argument, declaration.owner, env)
                for argument in written_arguments
            )))
        return views.pop() if len(views) == 1 else None

    def _repository_supertypes(
        self, owner: str, module: str, env: _Env, depth: int = 0,
    ) -> set[tuple[str, tuple[str | None, ...]]]:
        """The Spring Data repository interfaces of ``_REPOSITORY_RETURNS``
        that the source type ``owner`` extends, with their type arguments
        (BL-43, ADR-90).

        Read through the written ``extends`` and ``implements`` clauses of
        ``owner`` and of its source ancestors, each argument substituted by
        the arguments of the type that writes it (BL-26). A raw interface
        or an argument count mismatch gives nothing.
        """
        found: set[tuple[str, tuple[str | None, ...]]] = set()
        if depth > 16:
            return found
        for path, declaration in self._module_locations(owner, module):
            source = self.units[path].source
            for clause in declaration.named_children:
                if clause.type not in _TYPE_CLAUSES:
                    continue
                for type_node in clause.named_children:
                    for candidate in type_node.named_children if type_node.type == "type_list" else [type_node]:
                        parameterized = self._parameterized(path, candidate, owner, env)
                        if parameterized is not None:
                            base, base_env = parameterized
                            found |= self._repository_supertypes(
                                base, self._module_of(base, path), base_env, depth + 1,
                            )
                            continue
                        if candidate.type != "generic_type":
                            continue
                        name_node = candidate.named_children[0]
                        interface = self.resolve(
                            path, parser.node_text(source, name_node), owner, allow_ambiguous=True, at=name_node,
                        )
                        written = parser.child_by_type(candidate, "type_arguments")
                        if (
                            interface not in _REPOSITORY_ARITIES or interface in self.types or written is None
                            or len(written.named_children) != _REPOSITORY_ARITIES[interface]
                        ):
                            continue
                        found.add((interface, tuple(
                            self._type_argument_value(path, argument, owner, env)
                            for argument in written.named_children
                        )))
        return found

    def _repository_return(
        self, info: _Method, invocation: Node, depth: int,
    ) -> tuple[str | None, tuple[str | None, ...]] | None:
        """The return of a call to a member a source receiver inherits from
        a Spring Data repository interface (BL-43, ADR-90): a JDK container
        and its type arguments, or ``(None, (T,))`` for a plain type.

        A source declaration of the name and arity, on the receiver or a
        source ancestor, wins: the table only fills a missing one. When the
        receiver extends several interfaces of the table, the most specific
        declared return wins (``List<T>`` over ``Iterable<T>``); when none
        is more specific than every other, the call gets nothing.
        """
        source = self.units[info.method.path].source
        parent_receiver, name, arguments = parser.invocation_parts(invocation, source)
        if not any(key[1:] == (name, len(arguments)) for key in _REPOSITORY_RETURNS):
            return None
        context = self._receiver_env(info, parent_receiver, invocation, depth + 1)
        if context is None or context[0] not in self.types:
            return None
        owner, env = context
        locations = self._type_candidates(owner, info.method.path)
        if len(locations) != 1:
            return None
        module = self.path_modules.get(locations[0][0], info.module)
        if self.by_owner.get((owner, name, len(arguments), module)) or any(
            self.by_owner.get((base, name, len(arguments), base_module))
            for base, base_module in self.ancestors(owner, module)
        ):
            return None
        results: set[tuple[str | None, tuple[str | None, ...]]] = set()
        for interface, written in self._repository_supertypes(owner, module, env):
            entry = _REPOSITORY_RETURNS.get((interface, name, len(arguments)))
            if entry is None:
                continue
            returned, indices = entry
            values = tuple(written[index] for index in indices)
            if returned == _SAVED:
                # ``<S extends T> S save(S entity)``: the argument's type.
                typed = self.expression_type(info, arguments[0], invocation, depth + 1)
                if typed is not None and typed not in _PRIMITIVES and not typed.endswith("[]"):
                    values = (typed,)
                returned = None
            results.add((returned, values))
        if len(results) == 1:
            return results.pop()
        specific = [
            (returned, values) for returned, values in results
            if all(
                (other, other_values) == (returned, values) or other_values == values
                and returned is not None and other in _RETURN_SUPERTYPES.get(returned, frozenset())
                for other, other_values in results
            )
        ]
        return specific[0] if len(specific) == 1 else None

    def _repository_view(self, info: _Method, invocation: Node, depth: int) -> _View | None:
        """The JDK container view of an inherited repository member
        (BL-43): ``repo.findById(id)`` gives ``Optional<T>``."""
        found = self._repository_return(info, invocation, depth)
        if found is None or found[0] is None or found[0] not in _GENERIC_CONTAINERS:
            return None
        return _GENERIC_CONTAINERS[found[0]][0], found[1]

    def _call_env(
        self, info: _Method, declaration: _Method, context: tuple[str, _Env], invocation: Node,
        arguments: list[Node], depth: int,
    ) -> _Env:
        """The type arguments a call gives its declaration's type variables:
        those of the receiver, through the ``extends`` and ``implements``
        clauses up to the declaring type, then the method's own."""
        receiver_type, receiver_env = context
        module = self._module_of(receiver_type, info.method.path)
        env = dict(self._supertype_env(receiver_type, module, receiver_env, declaration.owner) or {})
        env.update(self._method_arguments(info, declaration, invocation, arguments, depth))
        return env

    def _method_arguments(
        self, info: _Method, declaration: _Method, invocation: Node, arguments: list[Node], depth: int,
    ) -> _Env:
        """The type arguments of a generic method at a call (JLS 18).

        Written ones (``this.<Circle>first(xs)``) come first. Otherwise a
        type variable takes the one type its arguments prove: an argument
        passed as ``T``, or a type argument of a JDK container or source
        generic type passed as ``List<T>`` or ``Box<? extends T>``. Two
        different types, an unknown one, or another position leave it
        untyped; a variable that no parameter names takes its bound.
        """
        parameters = self._declared_type_parameters(declaration.node)
        if not parameters:
            return {}
        path = info.method.path
        written = invocation.child_by_field_name("type_arguments")
        if written is not None:
            if len(written.named_children) != len(parameters):
                return {name: None for name, _parameter in parameters}
            return {
                name: self._type_argument_value(path, argument, info.owner, None)
                for (name, _parameter), argument in zip(parameters, written.named_children, strict=True)
            }
        names = {name for name, _parameter in parameters}
        formals = declaration.node.child_by_field_name("parameters")
        typed = [
            formal for formal in (formals.named_children if formals is not None else [])
            if formal.type in {"formal_parameter", "spread_parameter"}
        ]
        constraints: dict[str, set[str | None]] = defaultdict(set)
        exact = not declaration.varargs and len(typed) == len(arguments)
        for position, formal in enumerate(typed):
            type_node = formal.child_by_field_name("type") if formal.type == "formal_parameter" else None
            argument = arguments[position] if exact else None
            if type_node is None or argument is None:
                # A varargs parameter or a different arity: nothing proven.
                for name in self._mentioned(declaration.method.path, formal, names):
                    constraints[name].add(None)
                continue
            for name, value in self._inferred(info, declaration, type_node, argument, invocation, names, depth):
                constraints[name].add(value)
        return {
            name: self._bound(declaration.method.path, parameter, declaration.owner)
            if name not in constraints else (
                next(iter(constraints[name])) if len(constraints[name]) == 1 else None
            )
            for name, parameter in parameters
        }

    def _mentioned(self, path: str, node: Node, names: set[str]) -> list[str]:
        """The type variables among ``names`` that ``node`` writes."""
        text = parser.node_text(self.units[path].source, node)
        return [name for name in names if re.search(rf"(?<![\w$.]){re.escape(name)}(?![\w$])", text)]

    def _inferred(
        self, info: _Method, declaration: _Method, type_node: Node, argument: Node, invocation: Node,
        names: set[str], depth: int,
    ) -> list[tuple[str, str | None]]:
        """``(type variable, argument type)`` pairs one parameter proves."""
        path = declaration.method.path
        source = self.units[path].source
        mentioned = self._mentioned(path, type_node, names)
        if not mentioned:
            return []
        text = parser.node_text(source, type_node).strip()
        if type_node.type == "type_identifier" and text in names:
            value = self.expression_type(info, argument, invocation, depth + 1)
            return [(text, _BOXED.get(value, value) if value is not None else None)]
        if type_node.type != "generic_type":
            return [(name, None) for name in mentioned]
        written = parser.child_by_type(type_node, "type_arguments")
        slots: dict[int, str] = {}
        for index, slot in enumerate(written.named_children if written is not None else []):
            if slot.type == "wildcard" and re.match(r"\?\s*extends\b", parser.node_text(source, slot)):
                slot = slot.named_children[-1]
            if slot.type == "type_identifier" and parser.node_text(source, slot) in names:
                slots[index] = parser.node_text(source, slot)
        values: tuple[str | None, ...] | None = None
        name_node = type_node.named_children[0]
        container = self.resolve(
            path, parser.node_text(source, name_node), declaration.owner, allow_ambiguous=True, at=name_node,
        )
        count = len(written.named_children) if written is not None else 0
        lambda_node = argument
        while lambda_node.type == "parenthesized_expression" and lambda_node.named_children:
            lambda_node = lambda_node.named_children[0]
        if container in _JDK_FUNCTIONAL_SIGNATURES and lambda_node.type == "lambda_expression" and (
            count == _JDK_FUNCTIONAL_SIGNATURES[container][0]
        ):
            # An implicitly typed lambda is not pertinent to the type
            # variables of its parameters (JLS 15.12.2.2): it constrains
            # only the result, by its body's type (BL-37).
            result = _JDK_FUNCTIONAL_SIGNATURES[container][2]
            parameters = lambda_node.child_by_field_name("parameters")
            implicit = parameters is None or parameters.type == "identifier" or all(
                parameter.type == "identifier" for parameter in parameters.named_children
            )
            arguments = written.named_children if written is not None else []
            at_result = (
                set(self._mentioned(path, arguments[result], names)) if result is not None else set()
            )
            found = [
                (name, self._lambda_result(info, lambda_node, depth + 1) if index == result else None)
                for index, name in slots.items() if index == result or not implicit
            ]
            given = {name for name, _value in found}
            # A variable written elsewhere in the result, or anywhere in an
            # explicitly typed lambda's parameters, stays unproven.
            found.extend(
                (name, None) for name in mentioned if name not in given and (name in at_result or not implicit)
            )
            return found
        if container in _GENERIC_CONTAINERS:
            view = self._generic_view(info, argument, invocation)
            if view is not None and view[0] == _GENERIC_CONTAINERS[container][0] and len(view[1]) == count:
                values = tuple(_flat(value) for value in view[1])
        elif container is not None and container in self.types:
            passed = self._receiver_env(info, argument, invocation, depth + 1)
            locations = self._type_candidates(container, path)
            if passed is not None and len(locations) == 1:
                env = self._supertype_env(
                    passed[0], self._module_of(passed[0], info.method.path), passed[1], container,
                )
                declared = self._declared_type_parameters(locations[0][1])
                if env is not None and len(declared) == count:
                    values = tuple(env.get(name) for name, _parameter in declared)
        found = [(name, values[index] if values is not None else None) for index, name in slots.items()]
        # A variable written elsewhere in the type, as ``List<Box<T>>``.
        found.extend((name, None) for name in mentioned if name not in slots.values())
        return found

    def _dispatch_receiver_type(
        self, info: _Method, receiver: Node | None, invocation: Node, object_method: bool,
    ) -> str | None:
        """The receiver type a call dispatches on. For an ``Object`` method a
        type variable is not typed by its bound: its runtime type is unknown
        (ADR-81 rule 4)."""
        if not object_method:
            return self.receiver_type(info, receiver, invocation)
        self._bounds = False
        try:
            return self.receiver_type(info, receiver, invocation)
        finally:
            self._bounds = True

    def _qualified_this(self, info: _Method, receiver: Node) -> str | None:
        """``Outer.this``: the named type, when it encloses the expression."""
        target = receiver.child_by_field_name("object")
        if target is None:
            return None
        path = info.method.path
        named = self.resolve(path, parser.node_text(self.units[path].source, target), info.owner, at=target)
        scope = receiver.parent
        while scope is not None:
            if scope.type in _TYPES and self.owner(path, scope) == named:
                return named
            scope = scope.parent
        return None

    def _record_component_type(self, owner: str, name: str, path: str) -> str | None:
        """Declared type of the record component ``name`` of ``owner``."""
        for declaration_path, declaration in self._type_candidates(owner, path):
            if declaration.type != "record_declaration":
                continue
            source = self.units[declaration_path].source
            parameters = declaration.child_by_field_name("parameters")
            for parameter in parameters.named_children if parameters is not None else []:
                ident = parameter.child_by_field_name("name")
                type_node = parameter.child_by_field_name("type")
                if (
                    parameter.type == "formal_parameter" and ident is not None and type_node is not None
                    and parser.node_text(source, ident) == name
                ):
                    return self.resolve(
                        declaration_path, parser.node_text(source, type_node), owner,
                        allow_ambiguous=True, at=type_node,
                    )
        return None

    def _lombok_getter_type(self, owner: str, name: str, module: str) -> str | None:
        """Declared field type behind a getter that Lombok generates.

        ``@Data``, ``@Value`` or a class-level ``@Getter`` imported from
        ``lombok`` generates ``getX()`` (``isX()`` for a ``boolean``) for every
        non-static field; a field-level ``@Getter`` generates it for that
        field. Fields of source ancestors count, under their own annotations.
        """
        if name.startswith("get") and len(name) > 3:
            suffix, boolean_only = name[3:], False
        elif name.startswith("is") and len(name) > 2:
            suffix, boolean_only = name[2:], True
        else:
            return None
        # The nearest getter hides any farther up the hierarchy (S11).
        for level in self._ancestor_levels(owner, module):
            found: set[str | None] = set()
            locations = [
                (candidate, path, declaration) for candidate, candidate_module in level
                for path, declaration in self._module_locations(candidate, candidate_module)
            ]
            for candidate, path, declaration in locations:
                if declaration.type != "class_declaration":
                    continue
                source = self.units[path].source
                class_level = self._lombok_annotated(path, declaration)
                body = declaration.child_by_field_name("body")
                for member in body.named_children if body is not None else []:
                    if member.type != "field_declaration":
                        continue
                    modifiers = parser.modifiers_node(member)
                    if modifiers is not None and "static" in parser.node_text(source, modifiers).split():
                        continue
                    if not class_level and not self._lombok_annotated(path, member, {"Getter"}):
                        continue
                    type_node = member.child_by_field_name("type")
                    if type_node is None:
                        continue
                    type_text = parser.node_text(source, type_node).strip()
                    if boolean_only and type_text != "boolean":
                        continue
                    for child in member.named_children:
                        ident = child.child_by_field_name("name")
                        field_name = parser.node_text(source, ident) if ident is not None else ""
                        if field_name and field_name[:1].upper() + field_name[1:] == suffix:
                            found.add(self.resolve(path, type_text, candidate, allow_ambiguous=True, at=type_node))
            if found:
                return next(iter(found)) if len(found) == 1 else None
        return None

    def _lombok_annotated(self, path: str, declaration: Node, names: set[str] = _LOMBOK_GETTERS) -> bool:
        unit = self.units[path]
        for annotation in parser.annotations_of(declaration):
            written = parser.node_text(unit.source, annotation.child_by_field_name("name") or annotation)
            simple = written.rsplit(".", 1)[-1]
            if simple not in names:
                continue
            if written == f"lombok.{simple}" or unit.imports.get(simple) == f"lombok.{simple}" or (
                "." not in written and "lombok" in unit.wildcards
            ):
                return True
        return False

    def _field_type(self, owner: str, name: str, module: str, env: _Env) -> str | None:
        """Declared type of a field on a source type or its ancestors, read
        through a receiver whose type arguments are ``env``."""
        locations = self.types.get(owner, [])
        if len(locations) == 1:
            module = self.path_modules.get(locations[0][0], module)
        return self._member_field(owner, module, name, env)[1]

    def _receiver_module(self, receiver_type: str, caller: _Method) -> str:
        locations = self._type_candidates(receiver_type, caller.method.path)
        return self.path_modules.get(locations[0][0], caller.module) if len(locations) == 1 else caller.module

    def _declared_concretely(self, method: IntegrationMethod, receiver_type: str, caller: _Method) -> bool:
        """A concrete body on the receiver type or one of its ancestors."""
        info = self.methods.get(method.id)
        if info is None or not info.concrete:
            return False
        if info.owner == receiver_type:
            return True
        module = self._receiver_module(receiver_type, caller)
        return any(owner == info.owner for owner, _module in self.ancestors(receiver_type, module))

    def _unimplemented_contract(
        self, receiver_type: str, name: str, arity: int, caller: _Method,
    ) -> list[IntegrationMethod]:
        """The one abstract source declaration that no source type implements.

        A Spring Data query method (``findByOrderId``) is declared in the
        sources and implemented by the runtime: the call reaches the
        declaration, a possible target, as it does in a typed engine.
        """
        module = self._receiver_module(receiver_type, caller)
        contracts = {
            contract.method.id: contract
            for owner, owner_module in self.ancestors(receiver_type, module)
            for contract in self.by_owner.get((owner, name, arity, owner_module), [])
        }
        if len(contracts) != 1:
            return []
        contract = next(iter(contracts.values()))
        if contract.concrete or self.implementations(contract):
            return []
        return [contract.method]

    def _concrete(self, method: IntegrationMethod) -> bool:
        info = self.methods.get(method.id)
        return info is not None and info.concrete

    def _declared_arities(self, receiver_type: str, name: str, path: str, module: str) -> set[int]:
        """Arities of ``name`` declared on a receiver type and its ancestors."""
        locations = self._type_candidates(receiver_type, path)
        receiver_module = self.path_modules.get(locations[0][0], module) if len(locations) == 1 else module
        owners = {receiver_type, *(owner for owner, _module in self.ancestors(receiver_type, receiver_module))}
        return {arity for owner in owners for arity in self._arities.get((owner, name), ())}

    def _static_import_owner(self, info: _Method, name: str, arity: int) -> str | None:
        """The source type a static import makes ``name`` refer to, if unique.

        Java looks up a simple method name in the enclosing type, its
        ancestors and its outer types before static imports: any declaration
        of that name there, whatever its arity, shadows them. So may one an
        external supertype with unknown members declares (BC-13), as in
        :meth:`_lexical_owner`. A single static import wins over static
        wildcards; several wildcards that declare the name stay ambiguous.
        """
        path = info.method.path
        unit = self.units[path]
        scope = info.owner
        while scope and scope != unit.package:
            if scope in self.types and (
                self._declared_arities(scope, name, path, info.module)
                or self._may_inherit_unknown(scope, self._receiver_module(scope, info), name)
            ):
                return None
            scope = scope.rpartition(".")[0]

        def declaring(owners: list[str]) -> set[str]:
            resolved = {self.resolve(path, owner, info.owner) for owner in owners}
            return {
                owner for owner in resolved
                if owner is not None and owner in self.types
                and arity in self._declared_arities(owner, name, path, info.module)
            }

        single = declaring(unit.static_imports.get(name, []))
        if single:
            return next(iter(single)) if len(single) == 1 else None
        wildcard = declaring(unit.static_wildcards)
        return next(iter(wildcard)) if len(wildcard) == 1 else None

    def _may_inherit_unknown(self, owner: str, module: str, name: str) -> bool:
        """Whether an external supertype of ``owner`` may declare ``name``."""
        for ancestor in self.ancestors(owner, module):
            for base in self.external_bases.get(ancestor, ()):
                known = _KNOWN_EXTERNAL_MEMBERS.get(base or "")
                if known is None or name in known:
                    return True
        return False

    def _lexical_owner(self, info: _Method, name: str, invocation: Node) -> str | None:
        """The type an unqualified call ``name(...)`` binds to, if provable.

        Java searches the innermost enclosing type, anonymous classes
        included, whose members (declared or inherited) carry the name, and
        only then the outer types. A scope that inherits from an external
        supertype with unknown members stops the search: that supertype may
        declare the name.
        """
        if name in _OBJECT_METHODS:
            return None
        path = info.method.path
        source = self.units[path].source
        node = invocation.parent
        while node is not None and node != info.node:
            if node.type == "class_body" and node.parent is not None and node.parent.type == "object_creation_expression":
                if any(
                    member.type == "method_declaration" and parser.declaration_name(member, source) == name
                    for member in node.named_children
                ):
                    return None
                type_node = node.parent.child_by_field_name("type")
                supertype = self.resolve(
                    path, parser.node_text(source, type_node), info.owner, allow_ambiguous=True,
                    at=type_node,
                ) if type_node is not None else None
                if supertype is None:
                    return None
                if supertype in self.types:
                    module = self._receiver_module(supertype, info)
                    if (
                        self._declared_arities(supertype, name, path, info.module)
                        or self._may_inherit_unknown(supertype, module, name)
                    ):
                        return None
                else:
                    known = _KNOWN_EXTERNAL_MEMBERS.get(supertype)
                    if known is None or name in known:
                        return None
            node = node.parent
        scope = info.owner
        package = self.units[path].package
        while scope and scope != package and scope in self.types:
            if self._declared_arities(scope, name, path, info.module):
                return scope
            if self._may_inherit_unknown(scope, self._receiver_module(scope, info), name) or (
                name in _ENUM_METHODS
                and any(node.type == "enum_declaration" for _path, node in self._type_candidates(scope, path))
            ):
                return None
            scope = scope.rpartition(".")[0]
        return None

    def visible_modules(self, module: str) -> frozenset[str]:
        """``module`` and its transitive build dependencies (ADR-71).

        A module without a known dependency sees only itself.
        """
        visible = self._visible_modules.get(module)
        if visible is None:
            seen = {module}
            pending = [module]
            while pending:
                for target in self.module_dependencies.get(pending.pop(), ()):
                    if target not in seen:
                        seen.add(target)
                        pending.append(target)
            visible = self._visible_modules[module] = frozenset(seen)
        return visible

    def _external_dispatch(
        self, interface: str, name: str, arity: int, argument_types: tuple[str | None, ...] | None,
        visible: frozenset[str],
    ) -> list[IntegrationMethod]:
        """Source bodies a call on an external interface or class can
        dispatch to (ADR-70, ADR-74).

        Each source subtype of the external type contributes the most specific
        concrete, non-static, non-private body of ``name``/``arity`` it
        declares or inherits from a source ancestor. A subtype whose
        same-arity overloads the arguments cannot separate makes the whole
        call unresolved: the implemented signature is unknown. Only bodies
        declared in a ``visible`` module are kept (ADR-71): a library never
        dispatches into a module that depends on it.
        """
        key = (interface, name, arity, argument_types, visible)
        if key in self._external_targets:
            return self._external_targets[key]
        direct = self.external_implementors.get(interface, set()) | self.external_subclasses.get(interface, set())
        bodies: dict[str, IntegrationMethod] = {}
        subtypes = {
            (owner, self.path_modules.get(path, ""))
            for owner, locations in self.types.items()
            for path, _node in locations
        } if direct else set()
        for owner, module in sorted(subtypes):
            ancestors = self.ancestors(owner, module)
            if not ancestors & direct:
                continue
            declarations = self._select_overload([
                candidate for base_owner, base_module in ancestors
                for candidate in self.by_owner.get((base_owner, name, arity, base_module), [])
            ], argument_types)
            if not declarations:
                continue
            if len({candidate.parameters for candidate in declarations}) != 1:
                bodies = {}
                break
            body = self._most_specific([
                candidate for candidate in declarations
                if candidate.concrete and not self._modifiers(candidate) & {"static", "private"}
            ])
            if body is not None and body.method.module in visible:
                bodies[body.method.id] = body.method
        targets = sorted(bodies.values(), key=lambda method: method.id)
        self._external_targets[key] = targets
        return targets

    def _superclass(self, owner: str, module: str) -> tuple[str, str] | None:
        """The one source superclass of a class, if it has one."""
        superclasses = self.superclasses.get((owner, module), set())
        return next(iter(superclasses)) if len(superclasses) == 1 else None

    def _super_base(
        self, info: _Method, receiver: Node, receiver_type: str,
    ) -> tuple[str, str] | None:
        """The type whose members ``super.m()`` or ``X.super.m()`` names.

        ``super`` names the caller's superclass. ``X.super`` names ``X`` when
        ``X`` is an interface the caller implements, and the superclass of
        ``X`` when it is an enclosing class. ``None`` when that type is not a
        unique source type.
        """
        if receiver.type == "super":
            return self._superclass(info.owner, info.module)
        locations = self._type_candidates(receiver_type, info.method.path)
        if len(locations) != 1:
            return None
        module = self.path_modules.get(locations[0][0], info.module)
        if locations[0][1].type == "interface_declaration":
            return receiver_type, module
        return self._superclass(receiver_type, module)

    def _super_target(
        self, base: tuple[str, str], name: str, arity: int,
        argument_types: tuple[str | None, ...] | None,
    ) -> list[IntegrationMethod]:
        """The body a ``super`` call invokes: no dispatch, one declaration.

        Java binds ``super.m()`` to the most specific declaration among
        ``base`` and its ancestors, grandparents included, after overload
        selection by argument type (H5, H6). A private ancestor method is
        not inherited. An abstract or ambiguous result reaches nothing.
        """
        contracts = self._select_overload([
            candidate for owner, module in self.ancestors(*base)
            for candidate in self.by_owner.get((owner, name, arity, module), [])
            if not (candidate.binds_statically and "private" in self._modifiers(candidate))
        ], argument_types)
        if len({candidate.parameters for candidate in contracts}) != 1:
            return []
        declaration = self._most_specific(contracts)
        return [declaration.method] if declaration is not None and declaration.concrete else []

    def _modifiers(self, info: _Method) -> set[str]:
        modifiers = parser.modifiers_node(info.node)
        return set(parser.node_text(self.units[info.method.path].source, modifiers).split()) if modifiers else set()

    def _interface_witness(
        self, receiver_type: str, declaration: _Method | None, target: IntegrationMethod, path: str,
    ) -> IntegrationMethod | None:
        """The abstract source interface method a call names, kept as a witness.

        A typed engine reports the statically bound interface declaration next to
        the concrete dispatch target; the source engine does the same so the
        exported call tree keeps the caller-to-contract hop. A static or
        un-overridden default method that is itself the target, and an
        overridden default body, get no witness.
        """
        if declaration is None or declaration.concrete or declaration.method.id == target.id:
            return None
        locations = self._type_candidates(receiver_type, path)
        if not locations or any(node.type != "interface_declaration" for _path, node in locations):
            return None
        owner_node = parser.enclosing(declaration.node, *_TYPES)
        if owner_node is None or owner_node.type != "interface_declaration":
            return None
        return declaration.method

    def _declared_row_opens_no_route(
        self, declaration: _Method, targets: list[IntegrationMethod], module: str,
    ) -> bool:
        """Whether the abstract declaration a call names can join its
        possible targets as a declared row (BL-28).

        The row has no body, so it adds no route in a fresh join. A join
        rebuilt from persisted edges dispatches a declared abstract edge to
        every implementation, those of the caller's module first; the row
        is kept only when that dispatch reaches no body beyond ``targets``,
        so flows stay unchanged.
        """
        if declaration.concrete or any(target.id == declaration.method.id for target in targets):
            return False
        implementations = [candidate.method for candidate in self.implementations(declaration)]
        local = [method for method in implementations if method.module == module] or implementations
        reached = {target.id for target in targets}
        return all(method.id in reached for method in local)

    def _call_sites(self, info: _Method) -> list[tuple[Node, Node]]:
        """The call sites whose caller is ``info``, with their context.

        A method or a constructor owns the sites of its own body. A type's
        instance initializer code runs in each constructor that does not
        delegate to ``this(...)``, or in the implicit constructor; its
        static initializer code runs in ``<clinit>`` (ADR-75).
        """
        name = info.method.qualified_method.rsplit(".", 1)[-1]
        if info.node.type in _TYPES:
            contexts = initializer_members(
                info.node, self.units[info.method.path].source,
                static=name == STATIC_INITIALIZER,
            )
        else:
            contexts = [info.node]
            if name == CONSTRUCTOR and not delegates_to_this(info.node):
                type_node = parser.enclosing(info.node, *_TYPES)
                if type_node is not None:
                    contexts.extend(initializer_members(
                        type_node, self.units[info.method.path].source, static=False,
                    ))
        return [
            (node, context)
            for context in contexts
            for node in parser.walk(context)
            if node.type in _CALL_SITES | _CONSTRUCTOR_SITES and _call_context(node) == context
        ]

    def _listened_event_types(self, info: _Method) -> list[str]:
        """The event types an ``@EventListener`` method receives (PDR-16).

        The ``classes`` or ``value`` attribute names them; without it the
        single parameter does. A class literal that does not resolve adds
        no type.
        """
        source = self.units[info.method.path].source
        types: list[str] = []
        for annotation in parser.annotations_of(info.node):
            if parser.annotation_name(annotation, source) not in _EVENT_LISTENERS:
                continue
            value = (
                parser.annotation_argument(annotation, source, "classes")
                or parser.annotation_argument(annotation, source, "value")
                or parser.annotation_argument(annotation, source)
            )
            literals = [
                node.named_children[0] for node in parser.walk(value)
                if node.type == "class_literal" and node.named_children
            ] if value is not None else []
            if literals:
                types.extend(
                    resolved for node in literals
                    if (resolved := self.resolve(
                        info.method.path, parser.node_text(source, node), info.owner, at=node,
                    )) is not None
                )
            elif len(info.erased) == 1 and info.erased[0] is not None:
                types.append(info.erased[0])
        return types

    def _type_module(self, qualified: str, path: str) -> str | None:
        candidates = self._type_candidates(qualified, path)
        return self.path_modules.get(candidates[0][0]) if len(candidates) == 1 else None

    def event_dispatch_calls(self) -> list[MethodCall]:
        """``publishEvent(x)`` calls to the listeners of ``x``'s type (PDR-16).

        The receiver must be typed as an ``ApplicationEventPublisher`` or an
        ``ApplicationContext``. A listener receives the event when its event
        type is the static type of ``x`` or one of that type's source
        ancestors. Spring dispatches through a runtime registry and listener
        conditions, so every call is ``possible``. An untyped ``x`` adds none.
        """
        listeners: list[tuple[_Method, str]] = [
            (info, event)
            for info in self.methods.values()
            if info.concrete and info.node.type == "method_declaration"
            and b"EventListener" in self.units[info.method.path].source
            for event in self._listened_event_types(info)
        ]
        if not listeners:
            return []
        calls: list[MethodCall] = []
        for info in self.methods.values():
            source = self.units[info.method.path].source
            if not info.concrete or b"publishEvent" not in source:
                continue
            for invocation, _context in self._call_sites(info):
                if invocation.type != "method_invocation":
                    continue
                receiver, name, arguments = parser.invocation_parts(invocation, source)
                if name != "publishEvent" or len(arguments) != 1:
                    continue
                publisher = self.receiver_type(info, receiver, invocation)
                if publisher is None or publisher.rsplit(".", 1)[-1] not in _EVENT_PUBLISHERS:
                    continue
                event = self.expression_type(info, arguments[0], invocation)
                if event is None:
                    continue
                module = self._type_module(event, info.method.path)
                received = {event} | (
                    {owner for owner, _module in self.ancestors(event, module)}
                    if module is not None else set()
                )
                line = invocation.start_point.row + 1
                calls.extend(
                    MethodCall(
                        info.method.qualified_method, info.method.path, info.method.start_line,
                        listener.method.qualified_method, listener.method.path,
                        listener.method.start_line, line, "possible",
                    )
                    for listener, listened in listeners
                    if listened in received
                )
        return calls

    def receiver_sites(self, method_id: str) -> dict[tuple[int, str], bool]:
        """Whether each ``(line, name)`` call site of a method may run on ``this``.

        A call without receiver, on ``this``, ``super`` or ``Outer.this``,
        and ``this(...)`` or ``super(...)``, may run on the caller's own
        instance; a call on any other expression, and ``new T(...)``, does
        not. A line holding both kinds counts as on ``this``. Whether a
        call without receiver reaches an outer class or a static import is
        left to the caller, which knows the target (ADR-43, BL-47). An
        unknown method gives no site.
        """
        info = self.methods.get(method_id)
        if info is None:
            return {}
        source = self.units[info.method.path].source
        sites: dict[tuple[int, str], bool] = {}
        for invocation, _context in self._call_sites(info):
            line = invocation.start_point.row + 1
            receiver: Node | None
            if invocation.type == "object_creation_expression":
                name, on_this = CONSTRUCTOR, False
            elif invocation.type == "explicit_constructor_invocation":
                name = CONSTRUCTOR
                on_this = invocation.child_by_field_name("object") is None
            else:
                if invocation.type == "method_reference":
                    parts = invocation.named_children
                    if len(parts) < 2 or parts[-1].type != "identifier":
                        continue
                    receiver, name = parts[0], parser.node_text(source, parts[-1])
                else:
                    name_node = invocation.child_by_field_name("name")
                    if name_node is None:
                        continue
                    receiver, name = invocation.child_by_field_name("object"), parser.node_text(source, name_node)
                on_this = receiver is None or receiver.type in {"this", "super"} or (
                    receiver.type == "field_access"
                    and (field := receiver.child_by_field_name("field")) is not None
                    and field.type == "this"
                )
            sites[(line, name)] = sites.get((line, name), False) or on_this
        return sites

    def _constructor_targets(
        self, info: _Method, invocation: Node,
    ) -> tuple[list[IntegrationMethod], str]:
        """The constructors ``new T(...)``, ``this(...)`` or ``super(...)`` runs.

        The constructor the arguments select is ``exact``; overloads the
        arguments cannot separate are all ``possible``. An anonymous class
        creation, a qualified ``outer.new`` or ``outer.super(...)``, and an
        external type reach nothing (ADR-62, ADR-75).
        """
        path = info.method.path
        source = self.units[path].source
        if invocation.type == "object_creation_expression":
            type_node = invocation.child_by_field_name("type")
            if (
                type_node is None or parser.child_by_type(invocation, "class_body") is not None
                or invocation.named_children[0] != type_node
            ):
                return [], ""
            owner = self.resolve(path, parser.node_text(source, type_node), info.owner, at=type_node)
            if owner is None or owner not in self.types:
                return [], ""
            modules = {
                self.path_modules.get(location, "")
                for location, _node in self._type_candidates(owner, path)
            }
        else:
            keyword = invocation.child_by_field_name("constructor")
            if keyword is None or invocation.child_by_field_name("object") is not None:
                return [], ""
            if keyword.type == "this":
                owner, modules = info.owner, {info.module}
            else:
                parents = self.superclasses.get((info.owner, info.module), set())
                if len(parents) != 1:
                    return [], ""
                owner, parent_module = next(iter(parents))
                modules = {parent_module}
        arguments = parser.argument_nodes(invocation)
        arity = len(arguments)
        contracts = [
            candidate for module in sorted(modules)
            for candidate in self.by_owner.get((owner, CONSTRUCTOR, arity, module), [])
        ] or [
            candidate for module in sorted(modules)
            for candidate in self.varargs_by_owner.get((owner, CONSTRUCTOR, module), [])
            if len(candidate.parameters) - 1 <= arity
        ]
        if not contracts:
            return [], ""
        selected = self._select_overload(contracts, tuple(
            self.expression_type(info, argument, invocation) for argument in arguments
        ))
        return [candidate.method for candidate in selected], (
            "exact" if len(selected) == 1 else "possible"
        )

    def fallback_calls(
        self, resolved_sites: set[tuple[str, int, str]], *, closed_world: bool = False,
        exact_sites: set[tuple[str, int, str]] | None = None,
        callers: set[str] | None = None,
    ) -> list[MethodCall]:
        """Receiver-typed calls between indexed methods.

        ``closed_world`` applies when this pass is the whole call graph (the
        ``source`` engine): every implementation is then in the sources, so a
        unique implementation of an interface is an exact target. It also
        adds the shapes of the typed engine's query (ADR-70): a possible witness edge
        to the source interface method the call names next to its exact
        implementation, and dispatch on an external interface to its source
        implementations in the caller's visible modules, always possible
        (ADR-71).

        ``exact_sites`` holds the call sites whose concrete target the engine
        resolved exactly. When one of an invocation's candidates is such a
        target, the invocation adds nothing: the engine already chose its
        implementation, and the fallback must not reopen the others.

        ``callers`` limits the pass to those method IDs, as the call-edge
        diagnostic does to read one caller's typing notes.
        """
        exact_sites = exact_sites or set()
        calls: list[MethodCall] = []
        # Witness call -> the exact call it accompanies at the same site.
        self.interface_witnesses = {}
        self.unresolved_overloads = defaultdict(list)
        for info in self.methods.values():
            if not info.concrete or callers is not None and info.method.id not in callers:
                continue
            source = self.units[info.method.path].source
            for invocation, context in self._call_sites(info):
                line = invocation.start_point.row + 1
                if invocation.type in _CONSTRUCTOR_SITES:
                    targets, dispatch = self._constructor_targets(info, invocation)
                    calls.extend(
                        MethodCall(
                            info.method.qualified_method, info.method.path, info.method.start_line,
                            target.qualified_method, target.path, target.start_line, line, dispatch,
                        )
                        for target in targets
                        if (info.method.id, line, target.id) not in resolved_sites
                        and not (
                            exact_sites and (info.method.id, line, target.id) in exact_sites
                        )
                    )
                    continue
                in_anonymous_body = _in_anonymous_body(invocation, context)
                receiver: Node | None
                argument_types: tuple[str | None, ...] | None
                if invocation.type == "method_reference":
                    # ``receiver::method`` names a method without its arity:
                    # resolve only when the receiver type declares one arity.
                    parts = invocation.named_children
                    if len(parts) < 2 or parts[-1].type != "identifier":
                        continue
                    receiver, name = parts[0], parser.node_text(source, parts[-1])
                    if in_anonymous_body and _refers_to_anonymous_instance(source, receiver):
                        continue
                    receiver_type = self._dispatch_receiver_type(
                        info, receiver, invocation, name in {method for method, _arity in _OBJECT_DISPATCH},
                    )
                    if receiver_type is None:
                        continue
                    arities = self._declared_arities(receiver_type, name, info.method.path, info.module)
                    parameters = self._reference_parameters(info, invocation)
                    if len(arities) > 1:
                        # Overloads: the target functional interface's
                        # parameter count picks one arity (BL-24). A type
                        # name may also name an unbound instance method,
                        # which takes the first parameter as receiver.
                        target = len(parameters) if parameters is not None else None
                        expression = receiver.type in _EXPRESSION_RECEIVERS or (
                            receiver.type == "identifier" and self.variable_type(
                                info, parser.node_text(source, receiver), invocation,
                            ) is not None
                        ) or parser.node_text(source, receiver).startswith("this.")
                        accepted = (
                            set() if target is None else {target} if expression else {target, target - 1}
                        )
                        arities &= accepted
                    if len(arities) != 1:
                        continue
                    arity = next(iter(arities))
                    # The proven parameter types pick one overload of that
                    # arity; an unbound reference passes all but the first
                    # (BL-37).
                    argument_types = (
                        None if parameters is None
                        else parameters if arity == len(parameters)
                        else parameters[1:] if arity == len(parameters) - 1
                        else None
                    )
                else:
                    receiver, name, arguments = parser.invocation_parts(invocation, source)
                    # An unqualified call binds to the innermost enclosing
                    # type that has the name, possibly an outer type.
                    lexical_owner = (
                        self._lexical_owner(info, name, invocation) if receiver is None else None
                    )
                    if (
                        in_anonymous_body and lexical_owner is None
                        and _refers_to_anonymous_instance(source, receiver)
                    ):
                        # ``this`` is the anonymous instance, whose members
                        # come from an often external supertype.
                        continue
                    receiver_type = lexical_owner or self._dispatch_receiver_type(
                        info, receiver, invocation, (name, len(arguments)) in _OBJECT_DISPATCH,
                    )
                    if receiver_type is None:
                        continue
                    arity = len(arguments)
                    argument_types = tuple(
                        self.expression_type(info, argument, invocation) for argument in arguments
                    )
                arguments_count = arity
                dispatch_types: tuple[str, ...] = ()
                pruned = False
                external = False
                declaration: _Method | None = None
                contradicted: Callable[[str, str], bool] | None = None
                # ``super.m()`` binds statically: no runtime type matters.
                virtual = not (receiver is not None and (
                    receiver.type == "super" or any(child.type == "super" for child in invocation.children)
                ))
                if receiver is not None and not virtual:
                    super_base = self._super_base(info, receiver, receiver_type)
                    if super_base is None:
                        continue
                    receiver_type = super_base[0]
                    targets = self._super_target(super_base, name, arguments_count, argument_types)
                else:
                    targets, pruned, declaration = self._candidate_targets(
                        receiver_type, name, arguments_count, info.module, info.method.path,
                        argument_types=argument_types,
                    )
                    if (
                        closed_world and targets and receiver is not None
                        and invocation.type != "method_reference" and receiver_type in self.types
                    ):
                        # A variable that always holds a created ``T``
                        # dispatches on ``T``; the declaration it names
                        # stays the declared type's (BL-29, ADR-86).
                        narrowed = self._narrowed_targets(
                            info, receiver, invocation, receiver_type, name, arguments_count,
                            argument_types, targets,
                        )
                        if narrowed is not None:
                            dispatch_types, targets, pruned = narrowed
                        # An implementor whose type argument contradicts the
                        # receiver's cannot be its runtime type (BL-45). When
                        # it would exclude every target, the call keeps them.
                        contradicted = self._contradicted_implementors(info, receiver, invocation, receiver_type)
                        kept = [
                            target for target in targets
                            if contradicted is None or target.id not in self.methods or not contradicted(
                                self.methods[target.id].owner, self.methods[target.id].module,
                            )
                        ]
                        if kept:
                            targets = kept
                        else:
                            contradicted = None
                    if (
                        not targets and closed_world and argument_types is not None
                        and invocation.type != "method_reference" and receiver_type in self.types
                    ):
                        # Overloads the arguments cannot separate: every one
                        # stays a possible target (ADR-87).
                        targets = self._unresolved_overloads(
                            receiver_type, name, arguments_count, info.module, info.method.path,
                            argument_types,
                        )
                        if targets:
                            pruned = True
                            self.unresolved_overloads[info.method.id].append((
                                line, name, tuple(sorted(f"{target.qualified_method}:{target.start_line}" for target in targets)),
                            ))
                    if not targets and receiver is None:
                        static_owner = self._static_import_owner(info, name, arguments_count)
                        if static_owner is not None:
                            receiver_type = static_owner
                            targets, pruned, declaration = self._candidate_targets(
                                receiver_type, name, arguments_count, info.module,
                                info.method.path, argument_types=argument_types,
                            )
                    if not targets and closed_world and receiver_type not in self.types:
                        # An external interface or class dispatches to its
                        # source implementations and overrides (ADR-71,
                        # BL-31, ADR-74).
                        targets = self._external_dispatch(
                            receiver_type, name, arguments_count, argument_types,
                            self.visible_modules(info.module),
                        )
                        # A library can supply the implementation: one source
                        # body proves nothing (ADR-71).
                        external = bool(targets)
                        # An anonymous class or a lambda is a body no
                        # indexed method stands for: the dispatch stays open.
                        pruned = bool(targets) and (
                            receiver_type in self.anonymous_supertypes or receiver_type in _FUNCTIONAL_JDK
                        )
                        if targets and receiver is not None and invocation.type != "method_reference":
                            # A variable created once as a source subclass
                            # dispatches on it, under ADR-81 (ADR-86, BL-31).
                            narrowed = self._narrowed_targets(
                                info, receiver, invocation, receiver_type, name, arguments_count,
                                argument_types, targets,
                            )
                            if narrowed is not None:
                                dispatch_types, targets, pruned = narrowed
                                external = False
                    if not targets:
                        targets = self._unimplemented_contract(receiver_type, name, arguments_count, info)
                implicit = (
                    self.created_value(info, receiver, invocation)
                    if closed_world and virtual and receiver is not None
                    and invocation.type != "method_reference" else None
                )
                implicit_exact: bool | None = None
                if implicit is not None and _implicit_value(implicit[0]):
                    # The variable always holds a lambda, a method reference
                    # or an anonymous class instance (BL-30, ADR-72).
                    targets, implicit_exact = self._implicit_targets(
                        info, implicit[0], implicit[1], receiver_type, name, arguments_count,
                        argument_types, declaration,
                    )
                    dispatch_types, pruned, external = (), False, False
                    if (
                        not targets and declaration is not None
                        and self._declared_row_opens_no_route(declaration, [], info.module)
                    ):
                        # The declared row of ADR-70 as amended by BL-28,
                        # when its dispatch reaches no named body.
                        targets, implicit_exact = [declaration.method], False
                if not targets:
                    continue
                if exact_sites and any(
                    (info.method.id, line, target.id) in exact_sites for target in targets
                ):
                    continue
                # A narrowed union is checked on each of its types (ADR-89).
                runtime_types = dispatch_types or (receiver_type,)
                statically_bound = any(
                    self.methods[target.id].binds_statically for target in targets if target.id in self.methods
                )
                incomplete = implicit_exact is None and virtual and not statically_bound and any(
                    runtime_type in self.types and self._incomplete_implementors(
                        runtime_type, info, name, arguments_count, targets, contradicted,
                    )
                    for runtime_type in runtime_types
                )
                if (
                    closed_world and implicit_exact is None and virtual and not statically_bound
                    and any(
                        runtime_type in self.types
                        and self._implicitly_implemented(runtime_type, name, arguments_count, declaration)
                        for runtime_type in runtime_types
                    )
                ):
                    # A lambda, a method reference or an anonymous class
                    # can be the receiver: its body is no indexed method,
                    # so no named body is exact (BL-30, ADR-72).
                    pruned = True
                if (
                    incomplete and declaration is not None and not declaration.concrete
                    and all(target.id != declaration.method.id for target in targets)
                ):
                    # The declaration the call names is a target, as a typed
                    # engine reports it when no implementation is exact; on
                    # a Feign client it carries the HTTP call (ADR-81 rule 3).
                    targets = [declaration.method, *targets]
                # A concrete method declared by the receiver's own type is an
                # exact call. Interface dispatch to a unique source
                # implementation is exact only in a closed world, like
                # a typed engine's ``exactVirtualMethod``; otherwise dependencies may
                # add implementations and the call stays possible. A target
                # left unique by preferring the caller's module stays
                # possible: the preference is a heuristic, not a proof. A call
                # on an external interface stays possible (ADR-71). So does a
                # call whose receiver has an implementor without a known
                # body (ADR-81).
                dispatch = (
                    "exact"
                    if len(targets) == 1 and not pruned and not external and not incomplete and (
                        all(
                            self._declared_concretely(targets[0], runtime_type, info)
                            for runtime_type in runtime_types
                        )
                        or closed_world and self._concrete(targets[0])
                    )
                    else "possible"
                ) if implicit_exact is None else ("exact" if implicit_exact else "possible")
                if (
                    closed_world and virtual and dispatch == "possible" and declaration is not None
                    and self._declared_row_opens_no_route(declaration, targets, info.module)
                ):
                    # BL-28, ADR-70 amendment: without an exact target a
                    # typed engine also reports the abstract declaration the
                    # call names, beside its possible implementations.
                    targets = [declaration.method, *targets]
                site_calls = [
                    MethodCall(
                        info.method.qualified_method, info.method.path, info.method.start_line,
                        target.qualified_method, target.path, target.start_line, line, dispatch,
                    )
                    for target in targets
                    if (info.method.id, line, target.id) not in resolved_sites
                ]
                calls.extend(site_calls)
                witness = (
                    self._interface_witness(receiver_type, declaration, targets[0], info.method.path)
                    if closed_world and dispatch == "exact" and site_calls else None
                )
                if witness is not None:
                    witness_call = replace(
                        site_calls[0], callee=witness.qualified_method, callee_path=witness.path,
                        callee_line=witness.start_line, dispatch_confidence="possible",
                    )
                    calls.append(witness_call)
                    self.interface_witnesses[witness_call] = site_calls[0]
        return calls
