# Placement and interaction model

Parent: [Functional specification](../SPEC-FONC.md).

The shared presentation and interaction rules are defined in
[UX/UI rules](../UX-UI.md). This page owns placement and navigation behavior
that is specific to the generated architecture graph.


For the graph export, a module is a structural group that can contain child
modules and projects. Module membership comes from project directory
paths and MUST NOT be inferred from Kubernetes namespaces. Projects located
directly at the indexed repository root are assigned to the synthetic `root`
module. The legacy internal `project_namespace*` fields remain compatibility
aliases for the canonical `cluster_path`; they do not denote Kubernetes
namespaces.

The architecture graph uses ForceAtlas2 and Noverlap for placement. It renders
resources and relations without layer bands or module rectangles. Module
membership remains available in resource and module details. Selecting a module
from a detail action does not change graph placement.

It also provides dedicated OpenAPI, Topics, and Data views, which keep their
domain inventories separate.

Changing a relation-type filter rebuilds and relayouts the graph from only the
selected dependency types; excluded relations do not influence the resulting
graph layout.

The left navigation panel provides a collapse button in its header. Collapsing
the panel keeps its header controls available, hides the navigation content,
and leaves the graph workspace unchanged. Expanding the panel restores the
previous navigation view.
