# GraphML export

Parent: [Functional specification](../SPEC-FONC.md).

`systemlens export microservices --graphml FILE` exports the indexed
inter-service interaction graph. The export includes one node per indexed
microservice and one directed edge per resolved REST or Kafka interaction. It
does not include internal Java method calls, topics as separate nodes, or
unresolved relationships.

Each node has `component_id` and `component_size` attributes. Components are
weakly connected components: edge direction is ignored when grouping services.
Each edge retains its transport, source and target resources, source and target
classes, endpoint roles, and dynamic-topic status.

The export is derived from the persisted architecture snapshot. It does not
reparse source files or infer missing targets while writing GraphML.
