"""Persisted names that predate the engine-neutral vocabulary (ADR-85).

Existing SQLite indexes, exports and flow descriptions carry these
names. Renaming them would make those artifacts unreadable, so they stay
as they are; code refers to them only through these constants. This is
the one module outside ``engines/`` and the registry allowed to spell an
engine name.
"""

# SQLite tables of the persisted call graph and their indexes.
CALL_EDGES_TABLE = "codeql_call_edges"
CALL_GRAPH_METHODS_TABLE = "codeql_methods"
CALL_EDGES_CALLER_INDEX = "idx_codeql_call_edges_caller"
CALL_EDGES_CALLEE_INDEX = "idx_codeql_call_edges_callee"
CALL_GRAPH_METHODS_LOCATOR_INDEX = "idx_codeql_methods_locator"
CALL_GRAPH_METHODS_MODULE_INDEX = "idx_codeql_methods_module"

# ``meta`` keys of the call-graph stage.
META_CALL_GRAPH_STATUS = "codeql_call_graph_status"
META_CALL_GRAPH_EDGE_COUNT = "codeql_call_graph_edge_count"
META_INPUT_OUTPUT_STATUS = "codeql_input_output_status"
META_INPUT_OUTPUT_FLOW_COUNT = "codeql_input_output_flow_count"
META_JOIN_SIGNATURE = "codeql_join_signature"
META_JOIN_COMPLETED_ENTRIES = "codeql_join_completed_entries"
META_JOIN_METHODS_SIGNATURE = "codeql_join_methods_signature"

# Identifier prefix of a call-graph method that is not an integration method.
CALL_GRAPH_METHOD_ID_PREFIX = "codeql:"
# Identity marker of a flow built from a direct reachability answer; flow
# descriptions are keyed by flow identifiers that include it.
DIRECT_REACHABILITY_FLOW_MARKER = "direct-codeql"

# Key of the persisted call graph in the HTML export's view model.
EXPORT_CALL_GRAPH_KEY = "codeql_call_graph"
