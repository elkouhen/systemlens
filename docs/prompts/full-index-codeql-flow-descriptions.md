# Full CodeQL indexing and flow descriptions

Use this prompt with an agent operating from the root of an indexed Java repository.

```text
Run a complete SystemLens index for the current repository and use the existing
CodeQL Java database for method-call analysis.

Inputs:
- Repository root: <REPOSITORY_ROOT>
- CodeQL database: <CODEQL_DATABASE_PATH>
- Description report: <OUTPUT_REPORT_PATH>

Procedure:

1. Confirm that <REPOSITORY_ROOT> is the current working directory and that
   <CODEQL_DATABASE_PATH> exists.
2. Confirm that the CodeQL database was created from the same repository revision
   and source-root layout. If this cannot be confirmed, stop and report the risk.
3. Run the full index with CodeQL:

   uv run systemlens index --full \
     --call-graph-engine codeql \
     --codeql-database <CODEQL_DATABASE_PATH> \
     --codeql-progress

4. Treat a failed index as a blocking error. Do not silently fall back to AST-only
   flows, and do not use --no-codeql.
5. Verify the resulting inventory with:

   uv run systemlens flows list --json

   Record the number of persisted flows and check that flows containing method-call
   steps have CodeQL-backed call edges. Use `uv run systemlens flows show <FLOW_ID>
   --json` for each flow when step-level evidence is required.
6. For every persisted flow, write exactly one concise sentence in French in
   <OUTPUT_REPORT_PATH>. Include, when supported by the evidence:
   - the trigger, such as an HTTP route, Kafka topic, or scheduled task;
   - the originating service and the relevant downstream service or effect;
   - the main operation, such as an HTTP call, Kafka publication, or data access.
7. Ground every sentence in the indexed flow steps, source locations, and CodeQL
   method-call evidence. Do not infer runtime execution, ordering across branches,
   delivery guarantees, database writes, or a target service that the index does
   not prove.
8. Preserve the uncertainty of potential or partially reconciled flows. Use terms
   such as `peut`, `potentiel`, or `réconciliation partielle` when the persisted
   status requires them.
9. Use this report format:

   # Descriptions des flux

   Indexation: <date and repository revision if available>
   CodeQL database: <path>
   Nombre de flux: <count>

   ## <FLOW_ID>

   - Module: <module>
   - Méthode: <qualified method>
   - Description: <one concise sentence>
   - Statut: <status and reconciliation status>
   - Preuves: <relative source paths and relevant lines>

10. Check that the report contains one entry for every flow ID, no duplicate flow
    IDs, no empty descriptions, and no absolute machine-specific source paths.
11. Report the index command, the flow count, the report path, and any unresolved
    evidence or validation warning. Do not claim that CodeQL was used unless the
    index completed with the supplied database.
```

The prompt keeps flow descriptions in a companion report. It does not modify
source files or replace persisted extraction facts with generated prose.
