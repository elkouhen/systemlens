#!/usr/bin/env bash

set -euo pipefail

# List one row per module, input flow/type and output flow/type combination.
# Additional arguments are forwarded to `systemlens flows list`, for example:
#   scripts/list-kafka-flows.sh --root /path/to/indexed-repository
systemlens flows list "$@" --publishes-to-topic --json \
  | jq -r '
      unique_by([.module, .input_flow, .input_java_type, .output_flow, .output_java_type])
      | .[]
      | [
          .id,
          .module,
          (.input_flow // "-"),
          (.input_java_type // "-"),
          (.output_flow // "-"),
          (.output_java_type // "-")
        ]
      | @tsv
    '
