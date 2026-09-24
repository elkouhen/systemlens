# Diagnose missing internal code flows

This guide explains how to determine why SystemLens does not show an
interesting internal code flow.

## 1. Rebuild the index

Run a full index so that the current extractor and code-flow signatures are
used:

```bash
systemlens index --full --codeql-progress 2>&1 | tee indexing.log
```

If source-generated Java classes are part of the application, use:

```bash
systemlens index --full --generate-sources --codeql-progress 2>&1 | tee indexing.log
```

The repository is never compiled or tested by `--generate-sources`; it only
runs the Maven or Gradle source-generation phase in a temporary copy.

## 2. Check the important index counters

Extract the relevant lines from the log:

```bash
rg -n -i \
  'ports détectés|CodeQL|indisponible|flux interprocéduraux|parcours de code|limite|appel\(s\)|jointure' \
  indexing.log
```

The final counters answer most first questions:

| Log message | Interpretation |
| --- | --- |
| `ports détectés (X IN, Y OUT)` | The endpoint extractor found the inputs and outputs required for a flow. `OUT=0` means no input-to-output flow can be materialized. |
| `CodeQL : préparation de l'analyse interprocédurale`  | An interprocedural engine is active. |
| `indisponible ; flux interprocéduraux ignorés` | Only same-method AST flows are available. |
| `N appel(s) extrait(s)` | Number of Java calls returned by the method-call engine. |
| `N jointure(s)` | Number of calls attached to indexed integration methods. A high call count with zero joins usually indicates a source-location or method-name resolution problem. |
| `N flux interprocédural(aux)` | Flows that cross method boundaries and reach an indexed output endpoint. |
| `limite atteinte (N transitions)` | The bounded method-call exploration stopped before considering every transition. |
| `N parcours de code potentiel(s) matérialisé(s)` | Total persisted flows after local and interprocedural materialization. |

The direct CodeQL reachability query is not limited by the configured hop
count: it starts at indexed outputs and computes the
transitive caller relation until indexed inputs are reached. The configuration
values remain for forward fallback materialization, including CodeQL. Direct
CodeQL pairs and fallback pairs are combined; a nonempty direct result does
not suppress other fallback paths. Their
default values are:

```yaml
analysis:
  codeql_max_hops: 12
```

This value affects only the fallback path exploration. For a very branching
codebase, increasing it can confirm whether depth truncation is the cause:

```yaml
analysis:
  codeql_max_hops: 20
```

Increase this value carefully: it can substantially increase indexing time and
the number of low-confidence candidate flows.

For inheritance across modules, check the fully qualified receiver type,
imports, transitive base classes and method parameter signatures. The fallback
requires one compatible concrete implementation and may traverse intermediate
helper methods. Unknown receivers, ambiguous overloads/implementations,
duplicate qualified types and unsupported generic substitutions or varargs
remain unresolved. An unrelated method with the same name must not repair a
missing flow. CodeQL may resolve cases beyond this conservative fallback.

## 3. Inspect the persisted flow inventory

List all persisted flows, including local flows:

```bash
systemlens flows --json > flows.json
```

The HTML export defaults to the `Inter-services` scope. Select `Internal
flows` or `All flows` in the Flux tab before concluding that local flows are
missing.

To verify the HTML itself rather than only checking that the `code_flows` key
exists, regenerate the export and run this standard-library-only diagnostic:

```bash
systemlens export microservices --html architecture.html
python - "architecture.html" <<'PY'
import json
import re
import sys
from collections import Counter
from pathlib import Path

html = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(
    r'<script id="graph-data" type="application/json">(.*?)</script>',
    html,
    re.DOTALL,
)
if not match:
    raise SystemExit("graph-data not found in the HTML export")

data = json.loads(match.group(1))
flows = data.get("code_flows", [])
endpoint_services = {}
for node in data.get("nodes", []):
    if node.get("kind") != "microservice":
        continue
    for port in node.get("ports", []):
        endpoint_id = port.get("endpoint_id")
        if endpoint_id:
            endpoint_services.setdefault(endpoint_id, set()).add(node["id"])

counts = Counter()
for flow in flows:
    endpoint_ids = {
        step.get("endpoint_id")
        for step in flow.get("steps", [])
        if step.get("endpoint_id")
    }
    services = set().union(
        *(endpoint_services.get(endpoint_id, set()) for endpoint_id in endpoint_ids)
    )
    if len(services) >= 2:
        category = "inter-services"
    elif len(endpoint_ids) >= 2 and len(services) == 1:
        category = "internal"
    else:
        category = "unclassified"
    counts[category] += 1

print(f"Flows embedded in HTML: {len(flows)}")
print(f"Inter-service flows:     {counts['inter-services']}")
print(f"Internal flows:          {counts['internal']}")
print(f"Unclassified flows:      {counts['unclassified']}")
print("Reconciliation:          ", dict(Counter(
    flow.get("reconciliation", "unknown") for flow in flows
)))
PY
```

Interpret the result as follows:

- `Flows embedded in HTML: 0` means that the export is stale or was generated
  from a different indexed root;
- a non-zero `Internal flows` count means the flows are present and should be
  visible after selecting `Internal flows` or `All flows`;
- a non-zero `Unclassified flows` count means that the flow endpoints are not
  mapped to microservice nodes in the exported graph;
- zero `Inter-service flows` with many internal flows means the flows cross
  Java methods but not microservice boundaries.

An internal flow currently means a source-evidenced path from an indexed HTTP
or Kafka input to a distinct indexed HTTP, Kafka or data output in the same
service. A business-only chain such as `Controller -> Service -> Repository`,
without indexed integration endpoints, is not represented as an internal code
flow.

Useful fields in `flows.json` are:

- `module`: the owning service;
- `method`: the indexed entry method;
- `steps`: the endpoint and method-call sequence;
- `confidence`: `medium` for stronger evidence and `low` for fallback or
  possible dispatch evidence;
- `reconciliation`: `complete` or `partial` relative to the persisted
  topology snapshot.

## 4. Find where entry points disappear

Run the read-only flow diagnostic:

```bash
systemlens analyze flows-diagnostic --json > flow-diagnostic.json
```

This distinguishes common cases such as:

- an input endpoint with no indexed output;
- an output endpoint with no entry flow;
- a local flow that was found while its cross-service topology remains separate;
- a flow whose external relation could not be resolved.

Then inspect unresolved extraction evidence:

```bash
systemlens analyze indexing-issues --json > indexing-issues.json
```

Look for dynamic REST targets, ambiguous service aliases, dynamic Kafka
topics, parser diagnostics, unsupported framework constructs and unresolved
dispatch evidence.

## 5. Interpret the common failure patterns

### Inputs and outputs are missing

If the `IN` or `OUT` counter is unexpectedly low, the problem is in endpoint
extraction rather than method-call traversal. Check custom annotations, source
generation, non-standard Spring DSLs, malformed YAML and endpoint declarations
that are built dynamically.

### Calls are extracted but no flows are joined

If CodeQL reports calls but `joined` is zero, the engine saw Java calls
that could not be matched to the persisted integration methods. Common causes
are generated or relocated source paths, unresolved external types, overloaded
methods without a unique source location, and module boundaries that cannot be
proven from source-only analysis.

### The method-call engine is unavailable

Without CodeQL, SystemLens retains same-method flows but cannot
reliably follow `Controller -> Service -> Adapter` chains. Check the doctor
output and the engine availability line in `indexing.log`.

### The flow is ambiguous or dynamic

SystemLens intentionally does not invent a target for reflection, runtime bean
selection, dynamic routing, mutable REST URLs, ambiguous service aliases or
dynamic Kafka topics. These facts remain unresolved evidence and should appear
in `indexing-issues.json` where applicable.

### The flow is too deep or too branched

For CodeQL or a fallback materialization, look for the `limit reached` message.
Increase the fallback values temporarily, reindex, and compare the resulting
counters.

### The flow exists but is not visible in the UI

First switch the Flux scope from `Inter-services` to `Internal flows` or `All
flows`. Also check the search filter and remember that a partial flow can be
listed even when its topology path cannot be rendered completely.

## 6. Minimal report to share for further diagnosis

When asking for help, the following redacted output is sufficient; application
source code and secrets are not required:

```bash
rg -n -i \
  'ports détectés|CodeQL|indisponible|flux interprocéduraux|parcours de code|limite|appel\(s\)|jointure' \
  indexing.log

systemlens flows --json
systemlens analyze flows-diagnostic --json
systemlens analyze indexing-issues --json
```

Redact service names, URLs, database names and source paths if they are
sensitive. Keep the counters, statuses and diagnostic categories intact.
