# Verification

Parent: [Technical specification](../SPEC-TECH.md).


Unit tests use fixture repositories with real Java source and assert source
locations, roles, dynamic flags and derived relations. Static checks are Ruff
and mypy. The project does not require an external scanner in development or
at runtime.
