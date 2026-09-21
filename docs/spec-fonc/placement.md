# Placement and interaction model

Parent: [Functional specification](../SPEC-FONC.md).


For the graph export, a module is a structural group that can contain child
modules and projects. Module membership comes from project directory
paths and MUST NOT be inferred from Kubernetes namespaces. Projects located
directly at the indexed repository root are assigned to the synthetic `root`
module. The legacy internal `project_namespace*` fields remain compatibility
aliases for the canonical `cluster_path`; they do not denote Kubernetes
namespaces.

The module layout is independent of the layer order and uses deterministic
two-level grid packing without ELK or fCoSE. Resources are placed locally
inside each module, then modules are placed in an outer grid with fixed graph
coordinate margins. After projection, the camera fit measures card and module
envelopes in screen coordinates and zooms to the smallest scale at which every
sibling rectangle is disjoint. The projected-envelope check is the
authoritative collision guard.

Node identifiers and module names are sorted only to make the result
reproducible; there is no semantic order between modules. Neither resources
nor module rectangles may overlap. If fCoSE is unavailable, the same
deterministic grid is used without the local fCoSE ordering.

ELK is used only for the architectural layer layout, while Sigma.js provides
the interactive rendering for both views. Architecture relations remain
visible even when they are not used as placement edges.

It also provides dedicated OpenAPI, Topics, and Data views, which keep their
domain inventories separate.

Changing a relation-type filter rebuilds and relayouts the graph from only the
selected dependency types; excluded relations do not influence the resulting
graph layout.

