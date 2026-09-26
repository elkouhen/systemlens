# Verification

Parent: [Technical specification](../SPEC-TECH.md).


Unit tests use fixture repositories with real Java source and assert source
locations, roles, dynamic flags and derived relations. ArchUnitPython tests
enforce the package dependency direction and the absence of import cycles.
Static checks are Ruff and mypy. Bandit scans Python security patterns, and the
repository-local `.semgrep.yml` checks high-risk dynamic execution and shell invocation
patterns. Coverage reports branch coverage without making a global threshold
claim. Hypothesis is available for property-based tests.

The security checks are development-time checks only. They do not run during
indexing or at runtime. Bandit suppresses rules for deliberate assertions and
explicit argument-list subprocess calls; each SQL placeholder suppression is
limited to generated placeholder text while values remain bound parameters.
