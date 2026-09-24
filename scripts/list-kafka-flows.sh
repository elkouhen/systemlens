#!/usr/bin/env bash

set -euo pipefail

# List one row per module, input topic and output topic combination.
# Additional arguments are forwarded to `systemlens flows list`, for example:
#   scripts/list-kafka-flows.sh --root /path/to/indexed-repository
systemlens flows list "$@" --publishes-to-topic --json \
  | jq -r '
      unique_by([.module, .input_topic, (.output_topics | join(","))])
      | .[]
      | [
          .module,
          (.input_topic // "-"),
          (.output_topics | join(","))
        ]
      | @tsv
    '
