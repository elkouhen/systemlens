# Diagnose missing internal code flows

This guide explains how to determine why SystemLens does not show an
interesting internal code flow.

## 1. Rebuild the index

Run a full index so that the current extractor and code-flow signatures are
used:

```bash
systemlens index --full --engine-progress 2>&1 | tee indexing.log
```

If source-generated Java classes are part of the application, use:

```bash
systemlens index --full --generate-sources --engine-progress 2>&1 | tee indexing.log
```

`--generate-sources` runs the Maven or Gradle source-generation task in the
indexed repository. It can create or update build outputs there. SystemLens
does not invoke the compile or test tasks.

## 2. Check the important index counters

Extract the relevant lines from the log:

```bash
rg -n -i \
  'IN, |indisponible|préparation de l.analyse|flux interprocédura|flux de code|jointure' \
  indexing.log
```

The final counters answer most first questions. `<engine>` stands for
the engine's label: `Source (tree-sitter)`, `CodeQL` or `Joern`.

| Log message | Interpretation |
| --- | --- |
| `<module> : X IN, Y OUT, N flux de code` | The endpoint extractor found the inputs and outputs of each module. `OUT=0` means no input-to-output flow can start or end there. |
| `statistiques globales : X IN, Y OUT, N flux de code` | The same counters for the whole repository. |
| `<engine> : préparation de l'analyse interprocédurale` | The selected engine builds the call graph. |
| `<engine> indisponible ; moteur source (tree-sitter) utilisé` | The selected engine is not installed; the `source` engine builds the call graph instead. |
| `<engine> : N appel(s), J jointure(s), T transition(s), F flux interprocédural(aux)` | Calls the engine returned (always 0 for `source`, whose edges come from the symbol pass), edges attached to indexed methods, transitions the traversal explored, and flows that cross method boundaries. Many calls with zero joins usually indicate a source-location or method-name resolution problem. |
| `<engine> : N méthode(s) d'entrée arrêtée(s) à la limite engine_max_hops=H` | Entry methods with at least one route that stopped at `H` calls while calls remained to follow. Flows beyond the bound are missing for them; `0` rules depth truncation out. |
| `jointure terminée · T transition(s), 0 reachability(s), F flux dédupliqué(s)` | The end of the traversal. The reachability count is always 0 during an index: ADR-84 removed the CodeQL reachability pass. |
| `Indexation : N flux de code et M annotation(s) d'accès aux données collectée(s)` | Total persisted flows after the same-method and interprocedural passes. |

Every engine builds its flows the same way: a breadth-first traversal of
the joined call graph from each indexed entry, bounded by
`engine_max_hops` calls. The index log counts the entry methods whose
traversal stopped at that bound with calls left to follow:

```text
→ source : 3 méthode(s) d'entrée arrêtée(s) à la limite engine_max_hops=12.
```

To see the routes it stops, rebuild one module with
`systemlens index --module <module> --show-call-chains`. The command walks
back from each output of the module to its callers and prints each route
it stops, as `Chaîne arrêtée en direct`, with the reason in brackets:
`[limite de profondeur]` at the bound, `[aucun appelant indexé]` when no
indexed method calls the route's first method, `[cycle détecté]`.

The default value is:

```yaml
analysis:
  engine_max_hops: 12
```

For a very branching codebase, increasing it can confirm whether depth
truncation is the cause:

```yaml
analysis:
  engine_max_hops: 20
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

The HTML export lists local and inter-service flows together. Use the search,
confidence, protocol, message-type, and cycle filters before concluding that
local flows are missing.

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

An internal flow means a source-evidenced path from a trigger to an effect
in the same service (PDR-15). A trigger is an indexed HTTP, Kafka, RabbitMQ
or JMS input, a `@Scheduled` method, startup code (`CommandLineRunner.run`,
`ApplicationRunner.run`, `@PostConstruct`) or an `@EventListener` method
(PDR-16). An effect is a distinct HTTP call, message publication or data
operation. `publishEvent` is not an effect: the flow continues into the
event's listeners.

A route whose call tree reaches no effect, such as `Controller -> Service
-> helper`, is not a flow. The flow diagnostic below reports it as an
input endpoint with no indexed output.

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

If the engine reports calls but zero joins, it saw Java calls that could
not be matched to the persisted integration methods. Common causes
are generated or relocated source paths, unresolved external types, overloaded
methods without a unique source location, and module boundaries that cannot be
proven from source-only analysis.

### The method-call engine is unavailable

When the selected engine is not installed, the index uses the `source`
engine, which follows `Controller -> Service -> Adapter` chains from the
declared types alone. Check the doctor output and the engine availability
line in `indexing.log`. With `call_graph_engine: none`, only same-method
flows remain.

### The flow is ambiguous or dynamic

SystemLens intentionally does not invent a target for reflection, runtime bean
selection, dynamic routing, mutable REST URLs, ambiguous service aliases or
dynamic Kafka topics. These facts remain unresolved evidence and should appear
in `indexing-issues.json` where applicable.

### The flow is too deep or too branched

Rebuild the module with `--show-call-chains` and look for routes ending
with `[limite de profondeur]`. Increase `engine_max_hops` temporarily,
reindex, and compare the resulting counters.

### The flow exists but is not visible in the UI

The Flux tab lists local and inter-service flows together. Check the search,
confidence, protocol, message-type, and cycle filters. A partial flow can be
listed even when its topology path cannot be rendered completely.

## 6. Minimal report to share for further diagnosis

When asking for help, the following redacted output is sufficient; application
source code and secrets are not required:

```bash
rg -n -i \
  'IN, |indisponible|préparation de l.analyse|flux interprocédura|flux de code|jointure' \
  indexing.log

systemlens flows --json
systemlens analyze flows-diagnostic --json
systemlens analyze indexing-issues --json
```

Redact service names, URLs, database names and source paths if they are
sensitive. Keep the counters, statuses and diagnostic categories intact.
