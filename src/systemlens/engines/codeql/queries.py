"""QL text of the SystemLens queries and their builders.

Every query reports source locations rather than database-internal IDs so
the Python join can match rows to indexed ``IntegrationMethod`` facts.
"""

import re
from collections.abc import Sequence

from systemlens.domain.code_flows import IntegrationMethod


# Keep the query pack reproducible and offline during indexing.  Users install
# this one dependency as part of provisioning CodeQL; indexing must never
# download or upgrade an analyzer package implicitly.
CODEQL_JAVA_PACK_VERSION = "9.3.0"
_QLPACK = (
    "name: systemlens/codeql-flow\nversion: 0.0.0\ndependencies:\n"
    f"  codeql/java-all: \"{CODEQL_JAVA_PACK_VERSION}\"\n"
)
_QUERY = """import java
import semmle.code.java.dispatch.VirtualDispatch

/**
 * Keep the call graph scoped to application source.  The source-only
 * database can still contain calls originating in extracted dependencies;
 * they cannot join SystemLens' source-backed method inventory and only add
 * work to the dispatch predicates.
 */
class SourceMethodCall extends MethodCall {
  SourceMethodCall() { this.getEnclosingCallable().fromSource() }
}

/**
 * A callable of a Maven or Gradle test source set (`src/test`,
 * `src/integrationTest`). Test sources are never part of the world
 * (ADR-81 rule 2): a user-built database may hold them.
 */
predicate testSource(Callable callable) {
  callable.getFile().getRelativePath().regexpMatch("(.*/)?src/(test|[^/]*Test)/.*")
}

predicate productionSource(Callable callable) {
  callable.fromSource() and not testSource(callable)
}

/** A type of the sources outside test source sets (ADR-81 rule 2). */
predicate productionType(RefType type) {
  type.fromSource() and
  not type.getFile().getRelativePath().regexpMatch("(.*/)?src/(test|[^/]*Test)/.*")
}

/**
 * The method a call names, as declared in the sources. A call on a
 * parameterized receiver (`Gateway<String>.send`) names a parameterized
 * member that is not itself `fromSource()`; its source declaration is.
 */
Method declaredMethod(MethodCall call) {
  result = call.getMethod().getSourceDeclaration()
}

/**
 * A call Spring may dispatch to a `@FeignClient` proxy, which no source
 * body stands for: its fallback class is never exact (ADR-81 rule 3).
 */
predicate proxiedCall(MethodCall call) {
  exists(RefType client |
    client.getAnAnnotation().getType().hasName("FeignClient") and
    client.getASourceSupertype*() = declaredMethod(call).getDeclaringType()
  )
}

/**
 * Resolve a call when CodeQL can identify one exact virtual-dispatch target,
 * `exactVirtualMethod` without the implementations of test sources. The
 * target must also be source-backed because the Python join needs a file
 * and line that exist in the indexed repository.
 */
predicate exactTarget(MethodCall call, Method target) {
  uniqueTarget(call, target) and not unwrittenImplementor(call)
}

predicate uniqueTarget(MethodCall call, Method target) {
  target = viableCallable(call) and productionSource(target) and
  not proxiedCall(call) and
  forall(Callable other | other = viableCallable(call) and other != target | testSource(other))
}

/**
 * The bound of the receiver's runtime type that the dispatch uses: the
 * type flow's bound, or else the qualifier's type, an upper bound of a
 * type variable, or the enclosing type of an unqualified call. A created
 * instance is an exact bound.
 */
predicate receiverBound(VirtualMethodCall call, RefType bound, boolean exact) {
  exprTypeFlow(call.getQualifier(), bound, exact)
  or
  not exprTypeFlow(call.getQualifier(), _, _) and
  (
    exists(Expr qualifier | qualifier = call.getQualifier() |
      (
        bound = qualifier.getType().(BoundedType).getAnUltimateUpperBoundType()
        or
        bound = qualifier.getType() and not bound instanceof BoundedType
      ) and
      if qualifier instanceof ClassInstanceExpr then exact = true else exact = false
    )
    or
    not exists(call.getQualifier()) and exact = false and
    (
      call.isOwnMethodCall() and bound = call.getEnclosingCallable().getDeclaringType()
      or
      call.isEnclosingMethodCall(bound)
    )
  )
}

/**
 * A concrete source type the receiver may hold at run time whose body
 * for the call is no source method: a record accessor, a body inherited
 * from a library class or `Object`, or one CodeQL cannot see because a
 * supertype is unresolved. No source target is then exact (ADR-81 rule 1).
 * A Lombok member is a source method `viableCallable` already reports.
 */
predicate unwrittenImplementor(VirtualMethodCall call) {
  exists(Method declared, RefType bound, boolean exact, RefType runtime |
    uniqueTarget(call, _) and
    declared = declaredMethod(call) and
    receiverBound(call, bound, exact) and
    productionType(runtime) and
    runtime instanceof Class and not runtime.(Class).isAbstract() and
    (
      exact = true and runtime = bound.getSourceDeclaration()
      or
      exact = false and runtime.getASourceSupertype*() = bound.getSourceDeclaration()
    ) and
    not exists(Method body |
      body = exactMethodImpl(declared, runtime) and productionSource(body) and
      not body.isCompilerGenerated()
    )
  )
}

/**
 * A call to an `Object` method on a receiver typed `Object` or by a type
 * variable: its runtime type is unknown, so it has no edge (ADR-81 rule 4).
 */
predicate unknownObjectReceiver(MethodCall call) {
  (
    call.getMethod().hasName(["hashCode", "toString"]) and call.getNumArgument() = 0
    or
    call.getMethod().hasName("equals") and call.getNumArgument() = 1
  ) and
  (
    call.getQualifier().getType() instanceof TypeObject or
    call.getQualifier().getType() instanceof BoundedType
  )
}

/**
 * A same-name overload the call could also reach when CodeQL could not
 * type one of its arguments. In build mode `none`, an argument of an
 * `ErrorType` makes CodeQL pick the first declared overload of the
 * matching arity, reported as exact. A candidate must take as many
 * parameters as the call has arguments and agree with the declared
 * overload on every argument whose type is known.
 */
predicate unresolvedOverload(MethodCall call, Method candidate) {
  exists(Method declared |
    declared = declaredMethod(call) and
    exists(int i | call.getArgument(i).getType() instanceof ErrorType) and
    candidate.getDeclaringType() = declared.getDeclaringType() and
    candidate.getName() = declared.getName() and
    candidate != declared and
    candidate.fromSource() and
    candidate.getNumberOfParameters() = call.getNumArgument() and
    declared.getNumberOfParameters() = call.getNumArgument() and
    forall(int j |
      j in [0 .. call.getNumArgument() - 1] and
      not call.getArgument(j).getType() instanceof ErrorType
    |
      candidate.getParameterType(j).getErasure() = declared.getParameterType(j).getErasure()
    )
  )
}

predicate ambiguousOverloadCall(MethodCall call) {
  exists(Method candidate | unresolvedOverload(call, candidate))
}

/**
 * Pick one resolution strategy for each call.
 *
 * Exact dispatch has priority.  The possible-dispatch branches are evaluated
 * only when no exact target exists, so one call does not produce both an
 * exact edge and a wider set of possible edges.
 *
 * The final branch keeps the declared source method when a buildless database
 * cannot expose its concrete implementation.  Python may then add a
 * conservative source-backed bridge if it can prove one.
 * A source-backed interface declaration is also kept as a witness when
 * CodeQL exposes a concrete virtual target, so downstream dispatch enrichment
 * retains the caller-to-contract edge.
 *
 * A call whose overload CodeQL could not choose reaches each candidate
 * overload as a possible target, and nothing as exact.
 */
predicate resolvedTarget(MethodCall call, Method target, string confidence) {
  not ambiguousOverloadCall(call) and resolvedSingleOverload(call, target, confidence)
  or
  ambiguousOverloadCall(call) and confidence = "possible" and
  (target = declaredMethod(call) or unresolvedOverload(call, target)) and
  target.fromSource()
}

predicate resolvedSingleOverload(MethodCall call, Method target, string confidence) {
  exactTarget(call, target) and confidence = "exact"
  or
  not exists(Method exact | exactTarget(call, exact)) and
  target = viableCallable(call) and productionSource(target) and confidence = "possible"
  or
  // In buildless databases a virtual implementation can be unavailable even
  // though the source-declared interface method is indexed. Keep that
  // declared source method so the Python join can conservatively bridge it
  // to a unique source implementation carrying an integration endpoint.
  not exists(Method exact | exactTarget(call, exact)) and
  target = declaredMethod(call) and target.fromSource() and confidence = "possible"
  or
  // An interface method that is itself the exact target (a static or an
  // unoverridden default method) is already reported as exact.
  target = declaredMethod(call) and
  target.getDeclaringType() instanceof Interface and
  target.fromSource() and not exactTarget(call, target) and confidence = "possible"
}

predicate callerInScope(Callable enclosing) {
  __CALLER_SCOPE__
}

/**
 * A constructor call written in the sources (ADR-75): `new T(...)`,
 * `this(...)`, `super(...)` or a constructor reference `T::new`. The
 * implicit `super()` CodeQL adds to a constructor has that constructor's
 * own location and is left out, as are the constructors of anonymous
 * classes and lambdas (ADR-62). The `new T(...)` CodeQL synthesizes for
 * `T::new` shares its synthetic method's location too, and stays (BC-19).
 */
predicate sourceConstructorCall(ConstructorCall call) {
  call.getEnclosingCallable().fromSource() and
  not call.getConstructor().getDeclaringType() instanceof AnonymousClass and
  not (
    call instanceof SuperConstructorInvocationStmt and
    exists(Callable enclosing | enclosing = call.getEnclosingCallable() |
      call.getLocation().getStartLine() = enclosing.getLocation().getStartLine() and
      call.getLocation().getStartColumn() = enclosing.getLocation().getStartColumn()
    )
  )
}

/** A same-arity constructor overload a call with an untyped argument may select. */
predicate unresolvedConstructorOverload(ConstructorCall call, Constructor candidate) {
  exists(Constructor declared |
    declared = call.getConstructor().getSourceDeclaration() and
    exists(int i | call.getArgument(i).getType() instanceof ErrorType) and
    candidate.getDeclaringType() = declared.getDeclaringType() and
    candidate != declared and
    candidate.fromSource() and
    candidate.getNumberOfParameters() = call.getNumArgument() and
    declared.getNumberOfParameters() = call.getNumArgument() and
    forall(int j |
      j in [0 .. call.getNumArgument() - 1] and
      not call.getArgument(j).getType() instanceof ErrorType
    |
      candidate.getParameterType(j).getErasure() = declared.getParameterType(j).getErasure()
    )
  )
}

/** The constructor a call selects is exact unless an untyped argument leaves overloads open. */
predicate constructorTarget(ConstructorCall call, Constructor target, string confidence) {
  target = call.getConstructor().getSourceDeclaration() and target.fromSource() and
  if exists(Constructor other | unresolvedConstructorOverload(call, other))
  then confidence = "possible"
  else confidence = "exact"
  or
  unresolvedConstructorOverload(call, target) and confidence = "possible"
}

/**
 * The node a call belongs to (ADR-75). Instance initializer code
 * (`<obinit>`) runs in each constructor that calls it, which CodeQL
 * models with a call from every constructor not delegating to `this(...)`.
 */
predicate callerOf(Call call, Callable caller) {
  exists(Callable enclosing | enclosing = call.getEnclosingCallable() |
    not enclosing instanceof InstanceInitializer and caller = enclosing
    or
    enclosing instanceof InstanceInitializer and
    exists(MethodCall init | init.getMethod() = enclosing and init.getEnclosingCallable() = caller)
  )
}

/** A constructor is `Owner.<init>`, the name the index gives it. */
string nodeName(Callable callable) {
  if callable instanceof Constructor
  then result = callable.getDeclaringType().getQualifiedName() + ".<init>"
  else result = callable.getQualifiedName()
}

predicate callTarget(Call call, Callable target, string confidence) {
  resolvedTarget(call, target, confidence) and not target instanceof InitializerMethod and
  not unknownObjectReceiver(call)
  or
  constructorTarget(call, target, confidence)
}

/**
 * The declared parameter types, separated by `;`. Two same-name overloads
 * declared on one line share a name, a file and a start line; the join
 * tells them apart with these types.
 */
string parameterTypes(Callable callable) {
  result = concat(int i |
    i in [0 .. callable.getNumberOfParameters() - 1]
  |
    callable.getSourceDeclaration().getParameterType(i).getName(), ";" order by i
  )
}

from Call call, Callable enclosing, Callable invoked, string dispatch_confidence
where (call instanceof SourceMethodCall or sourceConstructorCall(call)) and
  callerOf(call, enclosing) and
  callerInScope(enclosing) and
  callTarget(call, invoked, dispatch_confidence)
// Return source locations rather than database-internal IDs.  SystemLens uses
// these locations to join CodeQL rows to its indexed IntegrationMethod facts.
select nodeName(enclosing) as caller,
  enclosing.getFile().getRelativePath() as caller_path,
  enclosing.getLocation().getStartLine() as caller_line,
  nodeName(invoked) as callee,
  invoked.getFile().getRelativePath() as callee_path,
  invoked.getLocation().getStartLine() as callee_line,
  call.getLocation().getStartLine() as call_line,
  dispatch_confidence,
  parameterTypes(enclosing) as caller_parameter_types,
  parameterTypes(invoked) as callee_parameter_types
"""

_METHODS_QUERY = """import java

from Method method
where method.fromSource() and not method instanceof InitializerMethod__METHOD_SCOPE__
select method.getQualifiedName() as qualified_method,
  method.getFile().getRelativePath() as path,
  method.getLocation().getStartLine() as start_line
"""


def _methods_query(source_prefix: str = "") -> str:
    normalized_prefix = source_prefix.strip("/")
    if not normalized_prefix:
        scope = ""
    else:
        # CodeQL's ``regexpMatch`` matches the whole string.
        pattern = _ql_string(f"^{re.escape(normalized_prefix)}/.*")
        scope = (
            " and exists(string path | "
            "path = method.getFile().getRelativePath() and "
            f"path.regexpMatch({pattern})"
            ")"
        )
    return _METHODS_QUERY.replace("__METHOD_SCOPE__", scope)

_KAFKA_MESSAGE_TYPES_QUERY = """import java
import semmle.code.java.dataflow.DataFlow

/**
 * Strategy1 names the topic convention, but the payload can be hidden behind
 * a local variable or a method parameter. CodeQL resolves that expression's
 * declared Java type without guessing from the topic name or serializer.
 */
predicate wrappedPayloadType(Expr payload, string messageType) {
  exists(ParameterizedType type |
    type = payload.getType() and
    (
      type.getGenericType().getName() = ["Message", "GenericMessage"] and
      messageType = type.getTypeArgument(0).getName() and
      not messageType in ["?", "Object"]
      or
      type.getGenericType().getName() = "ProducerRecord" and
      messageType = type.getTypeArgument(1).getName() and
      not messageType in ["?", "Object"]
    )
  )
}

predicate sourcePayloadType(Callable enclosing, Expr payload, string messageType) {
  exists(Expr source |
    source != payload and
    source.getEnclosingCallable() = enclosing and
    DataFlow::localFlow(DataFlow::exprNode(source), DataFlow::exprNode(payload)) and
    not source.getType().getName() in ["Object", "Message", "GenericMessage", "ProducerRecord"] and
    messageType = source.getType().getName()
  )
}

predicate inferredPayloadType(Callable enclosing, Expr payload, string messageType) {
  wrappedPayloadType(payload, messageType)
  or
  not exists(ParameterizedType type | type = payload.getType() and
    type.getGenericType().getName() = ["Message", "GenericMessage", "ProducerRecord"]
  ) and (
    sourcePayloadType(enclosing, payload, messageType)
    or
    not exists(string sourceType | sourcePayloadType(enclosing, payload, sourceType)) and
    messageType = payload.getType().getName()
  )
}

from MethodCall call, Callable enclosing, Expr payload, string messageType
where call.getMethod().getName().regexpMatch("^envoyerMessageKafka.*")
  and enclosing = call.getEnclosingCallable()
  and enclosing.fromSource()
  and payload = call.getArgument(1)
  and inferredPayloadType(enclosing, payload, messageType)
select call.getFile().getRelativePath() as path,
  call.getLocation().getStartLine() as line,
  messageType as message_type
"""


def _calls_query(caller_prefix: str) -> str:
    normalized_prefix = caller_prefix.strip("/")
    if not normalized_prefix:
        scope = "exists(Callable candidate | candidate = enclosing)"
    else:
        # CodeQL's ``regexpMatch`` matches the whole string.
        pattern = _ql_string(f"^{re.escape(normalized_prefix)}/.*")
        scope = (
            "exists(string path | "
            "path = enclosing.getFile().getRelativePath() and "
            f"path.regexpMatch({pattern})"
            ")"
        )
    return _QUERY.replace("__CALLER_SCOPE__", scope)


def _ql_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _anchor_predicate(
    name: str, methods: Sequence[IntegrationMethod], *, input_anchor: bool
) -> str:
    selected = [
        method for method in methods
        if bool(method.input_endpoint_ids if input_anchor else method.output_endpoint_ids)
    ]
    # The indexed method starts at its first annotation line while CodeQL
    # locates the declaration itself: anchor on the indexed line range.
    clauses = [
        "(method.getFile().getRelativePath() = "
        f"{_ql_string(method.path)} and method.getLocation().getStartLine() >= {method.start_line}"
        f" and method.getLocation().getStartLine() <= {max(method.start_line, method.end_line)})"
        for method in selected
    ]
    body = "\n    or\n    ".join(clauses) or "false"
    return f"""predicate {name}(Method method) {{
  method.fromSource() and (
    {body}
  )
}}"""


def _reachability_query(methods: Sequence[IntegrationMethod]) -> str:
    """Build a reverse OUT-to-IN reachability query scoped to indexed methods."""
    return f"""import java
import semmle.code.java.dispatch.VirtualDispatch

class SourceMethodCall extends MethodCall {{
  SourceMethodCall() {{ this.getEnclosingCallable().fromSource() }}
}}

// Keep the exact and possible relations separate.  The final query can then
// report lower confidence when a route exists only through possible dispatch.
predicate exactTarget(MethodCall call, Method target) {{
  target = exactVirtualMethod(call) and target.fromSource()
}}

predicate possibleTarget(MethodCall call, Method target) {{
  target = exactVirtualMethod(call) and target.fromSource()
  or
  not exists(Method exact | exactTarget(call, exact)) and
  target = viableCallable(call) and target.fromSource()
  or
  not exists(Method exact | exactTarget(call, exact)) and
  target = call.getMethod().getSourceDeclaration() and target.fromSource()
}}

predicate exactEdge(Callable caller, Callable callee) {{
  exists(SourceMethodCall call |
    call.getEnclosingCallable() = caller and exactTarget(call, callee)
  )
}}

predicate possibleEdge(Callable caller, Callable callee) {{
  exists(SourceMethodCall call |
    call.getEnclosingCallable() = caller and possibleTarget(call, callee)
  )
}}

// The recursion is written backwards: it starts at an indexed output method
// and walks through callers until it reaches an indexed input method.  CodeQL
// computes the transitive closure of these recursive predicates.
predicate exactCallerReachable(Callable callee, Callable caller) {{
  outputAnchor(callee) and exactEdge(caller, callee)
  or
  exists(Callable previous |
    exactCallerReachable(callee, previous) and exactEdge(caller, previous)
  )
}}

predicate possibleCallerReachable(Callable callee, Callable caller) {{
  outputAnchor(callee) and possibleEdge(caller, callee)
  or
  exists(Callable previous |
    possibleCallerReachable(callee, previous) and possibleEdge(caller, previous)
  )
}}

// These anchors are generated from SystemLens' integration-method inventory.
// They prevent this query from returning arbitrary method-to-method paths.
{_anchor_predicate("inputAnchor", methods, input_anchor=True)}
{_anchor_predicate("outputAnchor", methods, input_anchor=False)}

// Prefer an exact route.  A possible route is reported only when no exact
// route exists for the same input/output pair.
from Method inputMethod, Method outputMethod, string confidence
where outputAnchor(outputMethod) and inputAnchor(inputMethod) and
  inputMethod != outputMethod and
  (exactCallerReachable(outputMethod, inputMethod) and confidence = "medium" or
   not exactCallerReachable(outputMethod, inputMethod) and
   possibleCallerReachable(outputMethod, inputMethod) and confidence = "low")
select inputMethod.getQualifiedName() as source,
  inputMethod.getFile().getRelativePath() as source_path,
  inputMethod.getLocation().getStartLine() as source_line,
  outputMethod.getQualifiedName() as target,
  outputMethod.getFile().getRelativePath() as target_path,
  outputMethod.getLocation().getStartLine() as target_line, confidence
"""
