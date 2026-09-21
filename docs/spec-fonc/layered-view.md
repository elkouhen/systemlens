# Layered-view rendering rules

Parent: [Functional specification](../SPEC-FONC.md).


The HTML architecture view MUST preserve these visual invariants:

- Each software layer is a bounded horizontal band whose width and height are
  calculated from its visible content. Layers MUST NOT be infinite full-width
  backgrounds.
- All visible layer bands MUST share the same left and right bounds. The first
  band starts immediately above its highest visible module content, and the
  last band ends immediately below its lowest visible module content.
- Each layer band MUST reserve a visible left gutter for its title. The title
  MUST NOT overlap a module; widening the band is
  preferred to moving or shrinking module content.
- The layer-band geometry MUST be calculated from one shared rectangle model:
  all bands use the same left/right bounds, and the title gutter is included
  before the first module envelope.
- Layers MUST be stacked vertically in the canonical order above, with the
  Persistence layer at the bottom.
- Each visible structural module MUST be represented by a bounded rectangle
  fully contained inside its owning layer, including its header and padding.
- A module MAY use several rows. The default placement uses at most five
  boxes per row; additional boxes wrap onto subsequent rows.
- Microservices, Topics, message channels, Data resources, data
  schemas and other rendered resources MUST NOT overlap. Placement MUST keep a
  positive horizontal and vertical gap greater than the projected card size.
- Microservice and resource cards MUST use one shared rendered width, height,
  and scale in every view. Type-specific styling MUST NOT change card geometry.
- Layer and module bounds MUST be recomputed after filtering, zooming,
  camera updates and layout changes so containers continue to contain their
  visible children.
- Selecting a layer or module MUST rebuild the visible graph without
  turning remaining cards white, losing isolated services, or leaving stale
  containers on screen.
- In the layers and modules views, selecting a module
  title MUST highlight that module and display its name and sorted list of
  currently visible elements in the details panel. Each listed element MUST
  open its ordinary node details.
- Changing node-type or relation filters MUST remain valid when no
  microservice layer is visible or when the filtered graph is empty; the
  renderer MUST clear stale layer and module containers without producing
  invalid coordinates.
- Changing a node-type filter MUST refresh the main graph renderer and its
  overlays immediately and MUST reapply the active graph layout to the
  filtered network.
- The layered view extends the module packing: each canonical
  software layer is a separate horizontal band ordered from top to bottom,
  modules are packed inside that band, and each module uses a first
  microservice sub-layer followed by a resource sub-layer. ELK compound-node placement is
  used as a seed when available, while the deterministic layer-aware packing
  is the final collision guard. If ELK is unavailable or fails, the fallback
  MUST retain the same layer order, module containment and non-overlap
  guarantees.
- If a module becomes too tall and risks crossing a neighbouring
  layer, the renderer MUST add columns to that module and recompute the
  layout. The additional horizontal space MUST expand the diagram rather than
  overlap another layer or module.
Every layout switch MUST refit the camera to the resulting graph using the
selected fit mode. In a compound view, both modes MUST honor the collision-free
camera limit; this may leave peripheral nodes outside the viewport, and users
can pan to reach them. `Readable distance` MAY zoom in further.
For graphs of at most 12 nodes, the readable mode MUST retain the complete
overview instead of applying its normal zoom and making the small diagram
appear empty. It adds only the zoom-out required to keep the full fixed-size
card envelopes inside the graph viewport; the screen-space collision pass
handles card separation.

The details panel MUST display the resolved software layer and the module
path once, in its `Architecture` section, for microservices and resources
(Topics, Data resources, and enriched resources). Architecture fields already
shown there MUST NOT be repeated as header badges or raw metadata. The module
path MUST be the slash-separated path of grouping
directories, such as `group1/group2`, without a structural-group prefix.
For a resource modified in writing, the path MUST be inherited from its
producing or owning microservice.
If several microservices modify the same resource in writing, the renderer
MUST associate the resource with the microservice belonging to the lowest
software layer in the canonical visual order.

