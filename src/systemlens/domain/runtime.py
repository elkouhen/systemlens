"""Runtime facts attached conservatively to indexed architecture modules."""

from dataclasses import dataclass


@dataclass(frozen=True)
class KubernetesWorkload:
    """Resource dimensions aggregated across regular workload containers."""

    kind: str
    namespace: str
    name: str
    replicas: int | None
    cpu_request_millicores: int | None
    memory_request_bytes: int | None
    cpu_limit_millicores: int | None
    memory_limit_bytes: int | None
