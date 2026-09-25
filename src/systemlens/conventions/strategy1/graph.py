"""Strategy1 adapter for the domain REST target-resolution port."""

from systemlens.conventions.strategy1.rest import external_service_name, rest_target_service_hint
from systemlens.domain.graph import RestTargetPolicy


STRATEGY1_REST_TARGET_POLICY = RestTargetPolicy(
    target_hint=rest_target_service_hint,
    external_service=external_service_name,
)

