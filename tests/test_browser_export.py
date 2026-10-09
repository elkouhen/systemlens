from __future__ import annotations

import json
import os
import re
import struct
import zlib
from dataclasses import replace
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Playwright, sync_playwright

from systemlens.domain.models import MessageEndpoint, compute_endpoint_id
from systemlens.domain.graph import GraphEdge
from systemlens.domain.code_flows import CodeFlow, CodeFlowStep
from systemlens.domain.module_inventory import DiscoveredModule, JpaEntity, MongoField, MongoPersistenceClass
from systemlens.render import render_graph_html


pytestmark = pytest.mark.integration


@pytest.mark.slow
def test_service_inspector_shows_persisted_jpa_entity() -> None:
    module = DiscoveredModule(
        name="payment", path=Path("/project/payment"), build_system="maven",
        version="1.0", kind="library", starts_application=True,
        configuration_example="",
        jpa_entities=(JpaEntity(
            "example.Customer", "payment/src/main/java/example/Customer.java", 3,
        ),),
    )
    document = render_graph_html(
        {"payment": []}, [], modules_by_service={"payment": module},
        build_modules=[module],
    )
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"Aucun navigateur Playwright ne peut être lancé : {error}")
        context = browser.new_context(viewport={"width": 800, "height": 600})
        page = context.new_page()
        page.set_default_timeout(5_000)
        page.set_content(document, wait_until="load")
        page.locator(".graph-node-card-label").filter(has_text="payment").click(modifiers=["Shift"])
        assert page.locator("#inspector-modal").is_visible()
        assert page.locator("#details").is_hidden()
        assert page.locator("#inspector-body").get_by_text("Entités JPA déclarées").is_visible()
        assert page.locator("#inspector-body").get_by_text("example.Customer", exact=False).is_visible()
        context.close()
        browser.close()


@pytest.mark.slow
def test_resource_catalogue_updates_docked_preview() -> None:
    document = _current_simple_dataset_document()
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"Aucun navigateur Playwright ne peut être lancé : {error}")
        context = browser.new_context(viewport={"width": 1200, "height": 800})
        page = context.new_page()
        page.set_default_timeout(5_000)
        page.set_content(document, wait_until="load")
        page.locator("#microservices-tab").click()
        page.locator("#microservices-list .reference-title").filter(has_text="order-service").click()
        assert page.locator("#inspector-modal").is_hidden()
        assert page.locator("#resource-catalogue-preview-title").inner_text() == "order-service"
        assert page.locator("#resource-catalogue-preview").get_by_text("Microservice", exact=True).is_visible()
        assert page.locator("#microservices-panel").is_visible()
        context.close()
        browser.close()


@pytest.mark.slow
def test_resource_catalogue_filters_routes_and_services() -> None:
    document = _current_simple_dataset_document()
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"Aucun navigateur Playwright ne peut être lancé : {error}")
        context = browser.new_context(viewport={"width": 1200, "height": 800})
        page = context.new_page()
        page.set_default_timeout(5_000)
        page.set_content(document, wait_until="load")
        page.locator("#microservices-tab").click()
        assert page.locator("#resource-catalogue-categories button").count() == 10
        assert page.locator("#microservices-list .reference-item").count() > 0
        page.locator("#resource-kind-filter").select_option("kafka_topic")
        topic_count = page.locator("#microservices-list .reference-item").count()
        assert topic_count > 0
        page.locator("#microservices-filter").fill("topic")
        assert page.locator("#microservices-list .reference-item").count() <= topic_count
        context.close()
        browser.close()


@pytest.mark.slow
def test_topic_catalogue_uses_shared_resource_inspector() -> None:
    document = _current_simple_dataset_document()
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"Aucun navigateur Playwright ne peut être lancé : {error}")
        context = browser.new_context(viewport={"width": 1200, "height": 800})
        page = context.new_page()
        page.set_default_timeout(5_000)
        page.set_content(document, wait_until="load")
        page.locator("#microservices-tab").click()
        page.locator("#resource-kind-filter").select_option("kafka_topic")
        topic = page.locator("#microservices-list .reference-title").first
        topic.wait_for(state="visible")
        topic.click()
        assert page.locator("#inspector-modal").is_hidden()
        assert page.locator("#resource-catalogue-preview").is_visible()
        assert page.locator("#resource-catalogue-preview .resource-catalogue-preview-kind").inner_text() == "Topic Kafka"
        graph_topic = page.locator(".graph-node-card-label").filter(has_text="supermarket.stock.restock-requested").first
        assert page.locator("#graph-call-tree").is_hidden()
        graph_topic.click(modifiers=["Shift"])
        assert page.locator("#inspector-modal").is_visible()
        assert page.locator("#inspector-title").inner_text().startswith("Topic ·")
        assert page.locator("#details").is_hidden()
        context.close()
        browser.close()


@pytest.mark.slow
def test_microservice_called_route_opens_target_route_inspector() -> None:
    document = _current_simple_dataset_document()
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"Aucun navigateur Playwright ne peut être lancé : {error}")
        context = browser.new_context(viewport={"width": 1200, "height": 800})
        page = context.new_page()
        page.set_default_timeout(5_000)
        page.set_content(document, wait_until="load")
        page.locator(".graph-node-card-label").filter(has_text="order-service").click(modifiers=["Shift"])
        called_routes = page.locator(
            "#inspector-body .architecture-node-inspector "
            "[data-inspector-kind='route'][data-inspector-route='POST /api/reservations']"
        )
        assert called_routes.count() >= 1
        called_routes.first.click()
        assert page.locator("#inspector-title").inner_text().startswith("Route HTTP · POST /api/reservations")
        page.locator("#inspector-back").click()
        assert page.locator("#inspector-title").inner_text().startswith("Microservice · order-service")
        context.close()
        browser.close()


@pytest.mark.slow
def test_graph_node_opens_called_route_in_inspection_modal() -> None:
    document = _current_simple_dataset_document()
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"Aucun navigateur Playwright ne peut être lancé : {error}")
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()
        page.set_default_timeout(5_000)
        page.set_content(document, wait_until="load")
        page.wait_for_function(
            "() => Number(document.querySelector('#graph')?.dataset.visibleNodeCount || 0) > 0"
        )
        page.locator(".graph-node-card-label").filter(has_text="order-service").click(force=True, modifiers=["Shift"])
        called_route = page.locator(
            "#inspector-body [data-inspector-kind='route'][data-inspector-route='POST /api/reservations']"
        )
        assert called_route.count() >= 1
        called_route.first.click()
        assert page.locator("#inspector-title").inner_text().startswith("Route HTTP · POST /api/reservations")
        context.close()
        browser.close()


@pytest.mark.slow
def test_architecture_arc_opens_node_inspection_modal() -> None:
    document = _current_simple_dataset_document()
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"Aucun navigateur Playwright ne peut être lancé : {error}")
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()
        page.set_default_timeout(5_000)
        page.set_content(document, wait_until="load")
        page.wait_for_function(
            "() => document.querySelector('#graph')?.dataset.visibleNodeCount > 0"
        )
        page.wait_for_function(
            "() => [...document.querySelectorAll('#graph-port-paths .graph-dependency-hit-area')]"
            ".some(path => !path.getAttribute('d')?.includes('NaN'))"
        )
        hit_area = page.locator("#graph-port-paths .graph-dependency-hit-area").first
        hit_area.click(force=True, modifiers=["Shift"])
        assert page.locator("#inspector-title").inner_text().startswith(("Topic ·", "Route HTTP ·", "Microservice ·"))
        context.close()
        browser.close()


@pytest.mark.slow
def test_call_tree_nodes_and_kafka_arcs_open_node_inspection_modal() -> None:
    document = _current_simple_dataset_document()
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"Aucun navigateur Playwright ne peut être lancé : {error}")
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()
        page.set_default_timeout(5_000)
        page.set_content(document, wait_until="load")
        page.locator("#flows-mode-tab").click()
        page.locator(".code-flow-item").first.click()
        page.locator(".graph-call-tree-node").first.click(modifiers=["Shift"])
        assert page.locator("#inspector-title").inner_text().startswith("Microservice ·")
        page.locator("#inspector-close").click()
        http_label = page.locator(".graph-call-tree-edge-label.is-clickable").filter(has_text="HTTP").first
        http_label.click(modifiers=["Shift"])
        assert page.locator("#inspector-title").inner_text().startswith("Route HTTP ·")
        page.locator("#inspector-close").click()
        page.locator("#call-tree-depth-increase").click()
        page.locator(".graph-call-tree-edge-label.is-clickable").filter(has_text="Kafka").first.wait_for(
            state="visible"
        )
        topic_label = page.locator(".graph-call-tree-edge-label.is-clickable").filter(has_text="Kafka").first
        topic_label.click(modifiers=["Shift"])
        assert page.locator("#inspector-title").inner_text().startswith("Topic ·")
        context.close()
        browser.close()


_COMPLEX_DATASET_EXPORT = (
    Path(__file__).parents[1] / "examples" / "supermarket" / "supermarket.html"
)
_SIMPLE_DATASET_EXPORT = (
    Path(__file__).parents[1] / "docs" / "models" / "simple-supermarket.html"
)
_GRAPH_TEMPLATE = (
    Path(__file__).parents[1] / "src" / "systemlens" / "render" / "assets" / "graph.html"
)
_LAYER_GEOMETRY = (
    Path(__file__).parents[1] / "src" / "systemlens" / "render" / "assets" / "layer_geometry.js"
)
_GRAPH_ASSETS = Path(__file__).parents[1] / "src" / "systemlens" / "render" / "assets"
_GRAPH_CSS_MODULES = tuple(sorted(_GRAPH_ASSETS.joinpath("graph").glob("*.css")))
_GRAPH_JS_MODULES = tuple(sorted(_GRAPH_ASSETS.joinpath("graph").glob("*.js")))


def _current_simple_dataset_document() -> str:
    """Use the checked-in model data with the current renderer assets."""
    source = _SIMPLE_DATASET_EXPORT.read_text(encoding="utf-8")
    match = re.search(r'<script id="graph-data"[^>]*>([\s\S]*?)</script>', source)
    assert match, "The simple export must contain a graph-data script"
    template = _GRAPH_TEMPLATE.read_text(encoding="utf-8")
    return (
        template
        .replace(
            "__GRAPH_CSS__",
            "".join(path.read_text(encoding="utf-8") for path in _GRAPH_CSS_MODULES),
        )
        .replace(
            "__GRAPH_JS__",
            "\n".join(path.read_text(encoding="utf-8") for path in _GRAPH_JS_MODULES),
        )
        .replace("__GRAPH_DATA__", match.group(1))
        .replace("__LAYER_GEOMETRY__", _LAYER_GEOMETRY.read_text(encoding="utf-8"))
    )


def _complex_dataset_document() -> str:
    """Inject a deterministic 50-service/130-resource stress graph."""
    source = _COMPLEX_DATASET_EXPORT.read_text(encoding="utf-8")
    match = re.search(
        r'<script id="graph-data"[^>]*>([\s\S]*?)</script>', source
    )
    assert match, "The complex dataset must contain a graph-data script"
    data = json.loads(match.group(1))
    services = [node for node in data["nodes"] if node["kind"] == "microservice"][:50]
    assert len(services) == 50, "The source complex dataset must contain 50 services"
    namespaces = [
        "platform-edge/sub-1", "platform-edge/sub-2", "platform-edge/sub-3",
        "platform-core", "platform-domain", "platform-infra", "platform-shared",
        "platform-ops", "platform-data", "platform-security", "platform-workflow",
        "platform-reporting",
    ]
    layers = ["api", "application", "orchestration", "infrastructure", "domain", "persistence"]
    for index, node in enumerate(services):
        node["metadata"] = {"namespace": namespaces[index % len(namespaces)]}
        node["architecture_layer"] = layers[index % len(layers)]
        node["layer"] = node["architecture_layer"]

    resources: list[dict[str, object]] = []
    for index in range(100):
        owner = services[index % len(services)]
        resources.append({
            "id": f"kafka_topic:stress-topic-{index:03d}",
            "kind": "message_channel",
            "name": f"stress-topic-{index:03d}",
            "label": f"stress-topic-{index:03d}",
            "owner_service": owner["name"],
            "architecture_layer": owner["architecture_layer"],
            "metadata": {"namespace": namespaces[index % len(namespaces)]},
            "color": "#009E73",
        })
    for index in range(30):
        owner = services[(index * 3) % len(services)]
        resources.append({
            "id": f"mongodb_collection:stress-collection-{index:03d}",
            "kind": "data_schema",
            "name": f"stress-collection-{index:03d}",
            "label": f"stress-collection-{index:03d}",
            "owner_service": owner["name"],
            "architecture_layer": owner["architecture_layer"],
            "metadata": {"namespace": namespaces[index % len(namespaces)]},
            "color": "#CC79A7",
        })

    links: list[dict[str, object]] = []
    for index in range(100):
        topic = resources[index]
        producer = services[index % len(services)]
        consumer = services[(index + 1) % len(services)]
        links.extend([
            {"source": producer["id"], "target": topic["id"], "kind": "enrichment_publishes", "label": "publishes"},
            {"source": topic["id"], "target": consumer["id"], "kind": "enrichment_consumes", "label": "consumes"},
        ])
    for index in range(30):
        collection = resources[100 + index]
        first = services[(index * 3) % len(services)]
        second = services[(index * 3 + 1) % len(services)]
        links.extend([
            {"source": first["id"], "target": collection["id"], "kind": "enrichment_writes", "label": "writes"},
            {"source": second["id"], "target": collection["id"], "kind": "enrichment_reads", "label": "reads"},
        ])
    for index in range(40):
        topic = resources[(index * 7) % 100]
        producer = services[(index * 5 + 2) % len(services)]
        links.append({
            "source": producer["id"], "target": topic["id"],
            "kind": "enrichment_publishes", "label": "publishes",
        })
    assert len(resources) == 130
    assert len(links) == 300
    data["nodes"] = services + resources
    data["links"] = links
    nested_namespace_ids = [
        node["id"] for node in data["nodes"]
        if str(node.get("metadata", {}).get("namespace", "")).startswith("platform-edge/")
    ]
    data["groups"] = [{
        "name": "platform-edge",
        "namespace": "platform-edge",
        "children": nested_namespace_ids,
    }]
    for node in data["nodes"]:
        namespace = node.get("metadata", {}).get("namespace") or "root"
        # The supermarket export stores each bounded context namespace in
        # metadata. Promote it to the current renderer contract so the test
        # exercises real cluster packing rather than one ROOT cluster.
        node["project_namespace_path"] = namespace
        node["cluster_path"] = namespace
        node["runtime_namespaces"] = [namespace]
        node.setdefault("architecture_layer", node.get("layer") or "application")
        node.setdefault("layer", node["architecture_layer"])
    template = _GRAPH_TEMPLATE.read_text(encoding="utf-8")
    return (
        template
        .replace(
            "__GRAPH_CSS__",
            "".join(path.read_text(encoding="utf-8") for path in _GRAPH_CSS_MODULES),
        )
        .replace(
            "__GRAPH_JS__",
            "\n".join(path.read_text(encoding="utf-8") for path in _GRAPH_JS_MODULES),
        )
        .replace("__GRAPH_DATA__", json.dumps(data))
        .replace("__LAYER_GEOMETRY__", _LAYER_GEOMETRY.read_text(encoding="utf-8"))
    )


def _code_flow_document() -> str:
    def kafka_endpoint(role: str, topic: str, path: str, module: str) -> MessageEndpoint:
        return MessageEndpoint(
            id=compute_endpoint_id(role, topic, path),
            role=role,
            system="kafka",
            topic=topic,
            topic_dynamic=False,
            source="code",
            framework="spring-kafka",
            path=path,
            start_line=42,
            end_line=42,
            snippet="",
            module=module,
        )

    orders_publish = kafka_endpoint("produce", "orders.created", "OrderPublisher.java", "orders")
    payments_consume = kafka_endpoint("consume", "orders.created", "PaymentHandler.java", "payments")
    payments_publish = kafka_endpoint("produce", "payments.completed", "PaymentHandler.java", "payments")
    payments_unrelated = kafka_endpoint("produce", "payments.audit", "PaymentAuditPublisher.java", "payments")
    inventory_consume = kafka_endpoint("consume", "payments.completed", "InventoryHandler.java", "inventory")
    flow = CodeFlow(
        id="flow-readable",
        module="payments",
        method="com.example.payments.application.PaymentHandler.handle",
        path="payments/src/main/java/com/example/payments/application/PaymentHandler.java",
        start_line=42,
        end_line=73,
        status="potential",
        confidence="medium",
        reason="The entry point and external effects occur in the same Java method.",
        steps=(
            CodeFlowStep(
                order=1,
                kind="message_entry",
                name="orders.created",
                path="payments/src/main/java/com/example/payments/application/PaymentHandler.java",
                start_line=42,
                end_line=42,
                endpoint_id=payments_consume.id,
            ),
            CodeFlowStep(
                order=2,
                kind="message_publish",
                name="payments.completed",
                path="payments/src/main/java/com/example/payments/application/PaymentHandler.java",
                start_line=68,
                end_line=68,
                endpoint_id=payments_publish.id,
            ),
            CodeFlowStep(
                order=3,
                kind="message_entry",
                name="payments.completed",
                path="inventory/src/main/java/com/example/inventory/application/InventoryHandler.java",
                start_line=42,
                end_line=42,
                endpoint_id=inventory_consume.id,
            ),
        ),
    )
    return render_graph_html(
        {
            "orders": [orders_publish],
            "payments": [payments_consume, payments_publish, payments_unrelated],
            "inventory": [inventory_consume],
        },
        [
            GraphEdge("kafka", "orders", "payments", orders_publish, payments_consume),
            GraphEdge("kafka", "payments", "inventory", payments_publish, inventory_consume),
        ],
        code_flows=[flow],
    )


@pytest.mark.slow
def test_obsidian_default_and_saved_theme_preserve_graph_state() -> None:
    with sync_playwright() as playwright:
        browser = _launch_visual_browser(playwright)
        context = browser.new_context(
            viewport={"width": 1280, "height": 850}, color_scheme="light"
        )
        page = context.new_page()
        document = _code_flow_document()
        page.route(
            "http://systemlens.test/",
            lambda route: route.fulfill(body=document, content_type="text/html"),
        )
        page.goto("http://systemlens.test/")
        page.locator(".graph-node-card-label").first.wait_for()
        assert page.locator("html").get_attribute("data-theme") == "dark"
        assert page.locator("#graph").evaluate(
            "element => getComputedStyle(element).backgroundColor"
        ) == "rgb(11, 16, 24)"
        assert page.locator(".graph-node-card-label").first.evaluate(
            "element => getComputedStyle(element).backgroundColor"
        ) == "rgb(19, 30, 43)"
        node_count = page.locator(".graph-node-card-label").count()
        page.locator("#theme-toggle").click()
        assert page.locator("html").get_attribute("data-theme") == "light"
        assert page.locator(".graph-node-card-label").count() == node_count
        page.reload()
        page.locator(".graph-node-card-label").first.wait_for()
        assert page.locator("html").get_attribute("data-theme") == "light"
        page.locator("#theme-toggle").click()
        page.locator("#flows-mode-tab").click()
        page.locator(".code-flow-item").click()
        page.locator("#graph-call-tree").wait_for(state="visible")
        selected_flow = page.locator(".code-flow-item.is-selected").inner_text()
        for _ in range(2):
            page.locator("#theme-toggle").click()
            assert page.locator(".code-flow-item.is-selected").inner_text() == selected_flow
            assert page.locator("#graph-call-tree").is_visible()
        page.reload()
        page.locator(".graph-node-card-label").first.wait_for()
        assert page.locator("html").get_attribute("data-theme") == "dark"
        context.close()
        browser.close()


def _chrome_executable(playwright: Playwright) -> str | None:
    """Prefer an explicit browser, then the Chromium revision Playwright pins."""
    configured = os.environ.get("SYSTEMLENS_CHROME_BIN")
    candidates = [
        Path(configured) if configured else None,
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path(playwright.chromium.executable_path),
    ]
    return next((str(candidate) for candidate in candidates if candidate and candidate.is_file()), None)


def _launch_visual_browser(playwright: Playwright):
    """Launch only the Chromium test browser used by the export contract."""
    executable = _chrome_executable(playwright)
    if executable:
        return playwright.chromium.launch(headless=True, executable_path=executable)
    return playwright.chromium.launch(headless=True)


def _producer(message_type: str) -> MessageEndpoint:
    return MessageEndpoint(
        id=compute_endpoint_id("produce", "orders.created", "Publisher.java", 4),
        role="produce",
        system="kafka",
        topic="orders.created",
        topic_dynamic=False,
        source="code",
        framework="spring-kafka",
        path="Publisher.java",
        start_line=4,
        end_line=4,
        snippet="",
        message_type=message_type,
    )


def _assert_filtered_graph_is_valid(
    page, previous_node_count: int | None = None, excluded_kind: str | None = None
) -> int:
    graph = page.locator("#graph")
    assert graph.get_attribute("data-invalid-coordinates") == "false"
    count = int(graph.get_attribute("data-visible-node-count") or "0")
    assert count >= 0
    visible_kinds = (graph.get_attribute("data-visible-node-kinds") or "").split(",")
    if excluded_kind is not None:
        assert excluded_kind not in visible_kinds
    if previous_node_count is not None:
        assert count < previous_node_count
    return count


def _assert_architecture_cards_have_uniform_size(page) -> tuple[float, float]:
    size = page.evaluate(
        """() => {
            const cards = [...document.querySelectorAll('.graph-node-card-label')];
            if (!cards.length) return null;
            const first = cards[0].getBoundingClientRect();
            const uniform = cards.every(card => {
                const rect = card.getBoundingClientRect();
                return Math.abs(rect.width - first.width) < 0.01
                    && Math.abs(rect.height - first.height) < 0.01;
            });
            return uniform ? [first.width, first.height] : false;
        }"""
    )
    assert size is not False and size is not None
    return float(size[0]), float(size[1])


def _assert_architecture_cards_match_size(page, expected: tuple[float, float]) -> None:
    actual = _assert_architecture_cards_have_uniform_size(page)
    assert actual[0] == pytest.approx(expected[0], abs=0.01)
    assert actual[1] == pytest.approx(expected[1], abs=0.01)


def _assert_architecture_cards_keep_size_after_camera_change(
    page, expected: tuple[float, float]
) -> None:
    """Cards stay screen-sized while positions change with zoom or pan."""
    _assert_architecture_cards_match_size(page, expected)
    page.wait_for_timeout(120)
    _assert_architecture_cards_match_size(page, expected)


def _assert_architecture_cards_do_not_overlap(page) -> None:
    """Reject intersections between the rendered card bounding boxes."""
    geometry = page.evaluate(
        """() => {
            const cards = [...document.querySelectorAll('.graph-node-card-label')].map(card => {
                const rect = card.getBoundingClientRect();
                return { id: card.dataset.nodeId, left: rect.left, right: rect.right,
                    top: rect.top, bottom: rect.bottom, width: rect.width, height: rect.height };
            });
            const overlap = (left, right) => left.left < right.right - .5
                && left.right > right.left + .5
                && left.top < right.bottom - .5
                && left.bottom > right.top + .5;
            const invalid = cards.filter(card => ![card.left, card.top, card.width, card.height].every(Number.isFinite)
                || card.width <= 0 || card.height <= 0);
            const intersections = [];
            cards.forEach((left, index) => cards.slice(index + 1).forEach(right => {
                if (overlap(left, right)) intersections.push([left.id, right.id]);
            }));
            return { valid: !invalid.length && !intersections.length, invalid, intersections };
        }"""
    )
    assert geometry["valid"], geometry


def _assert_architecture_cards_are_valid(page) -> None:
    """Require finite, positive card rectangles when an overview may be dense."""
    assert page.evaluate(
        """() => [...document.querySelectorAll('.graph-node-card-label')].every(card => {
            const rect = card.getBoundingClientRect();
            return [rect.x, rect.y, rect.width, rect.height].every(Number.isFinite)
                && rect.width > 0 && rect.height > 0;
        })"""
    )


def _assert_all_node_centers_are_visible(page) -> None:
    result = page.evaluate(
        """() => {
            const graph = document.querySelector('#graph').getBoundingClientRect();
            const outside = [...document.querySelectorAll('.graph-node-card-label')]
                .filter(card => {
                    const rect = card.getBoundingClientRect();
                    const x = (rect.left + rect.right) / 2;
                    const y = (rect.top + rect.bottom) / 2;
                    return x < graph.left || x > graph.right || y < graph.top || y > graph.bottom;
                })
                .map(card => card.dataset.nodeId);
            return { outside, cards: document.querySelectorAll('.graph-node-card-label').length };
        }"""
    )
    assert result["outside"] == [], result


def _graph_card_metrics(page) -> dict[str, float | int]:
    return page.evaluate(
        """() => {
            const cards = [...document.querySelectorAll('.graph-node-card-label')]
                .map(card => {
                    const rect = card.getBoundingClientRect();
                    return {
                        left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom,
                        x: (rect.left + rect.right) / 2, y: (rect.top + rect.bottom) / 2,
                    };
                });
            let overlaps = 0;
            const requiredZooms = [];
            for (let index = 0; index < cards.length; index += 1) {
                for (let otherIndex = index + 1; otherIndex < cards.length; otherIndex += 1) {
                    const left = cards[index], right = cards[otherIndex];
                    if (left.left < right.right && left.right > right.left
                        && left.top < right.bottom && left.bottom > right.top) {
                        overlaps += 1;
                        requiredZooms.push(Math.min(
                            (left.right - left.left + 4) / Math.max(Math.abs(left.x - right.x), 1),
                            (left.bottom - left.top + 4) / Math.max(Math.abs(left.y - right.y), 1),
                        ));
                    }
                }
            }
            requiredZooms.sort((left, right) => left - right);
            const percentile = value => requiredZooms.length
                ? requiredZooms[Math.floor((requiredZooms.length - 1) * value)]
                : 1;
            return {
                count: cards.length,
                overlaps,
                zoomP50: percentile(.5),
                zoomP75: percentile(.75),
                zoomP90: percentile(.9),
                zoomP95: percentile(.95),
                span: Math.max(
                    Math.max(...cards.map(point => point.x)) - Math.min(...cards.map(point => point.x)),
                    Math.max(...cards.map(point => point.y)) - Math.min(...cards.map(point => point.y)),
                ),
            };
        }"""
    )


def _inspect_png_content(image: bytes, region: dict[str, float]) -> dict[str, int]:
    """Inspect screenshot pixels without depending on a GUI or image library."""
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    width = height = color_type = bit_depth = None
    compressed = bytearray()
    while offset < len(image):
        length = struct.unpack(">I", image[offset:offset + 4])[0]
        kind = image[offset + 4:offset + 8]
        payload = image[offset + 8:offset + 8 + length]
        offset += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", payload[:10])
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            break
    assert width and height and bit_depth == 8 and color_type in (2, 6)
    channels = 4 if color_type == 6 else 3
    row_size = width * channels
    decoded = zlib.decompress(compressed)
    rows: list[bytes] = []
    previous = bytearray(row_size)
    cursor = 0
    for _ in range(height):
        filter_type = decoded[cursor]
        cursor += 1
        current = bytearray(decoded[cursor:cursor + row_size])
        cursor += row_size
        for index in range(row_size):
            left = current[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                current[index] = (current[index] + left) & 255
            elif filter_type == 2:
                current[index] = (current[index] + above) & 255
            elif filter_type == 3:
                current[index] = (current[index] + ((left + above) // 2)) & 255
            elif filter_type == 4:
                estimate = left + above - upper_left
                distances = abs(estimate - left), abs(estimate - above), abs(estimate - upper_left)
                predictor = left if distances[0] <= distances[1] and distances[0] <= distances[2] else (
                    above if distances[1] <= distances[2] else upper_left
                )
                current[index] = (current[index] + predictor) & 255
            elif filter_type != 0:
                raise AssertionError(f"Unsupported PNG filter: {filter_type}")
        rows.append(bytes(current))
        previous = current
    left = max(0, min(width, int(region.get("x", 0))))
    top = max(0, min(height, int(region.get("y", 0))))
    right = max(left, min(width, int(region.get("x", 0) + region.get("width", width))))
    bottom = max(top, min(height, int(region.get("y", 0) + region.get("height", height))))
    dark_pixels = 0
    distinct_pixels = 0
    for row in rows[top:bottom]:
        for x in range(left, right):
            pixel = row[x * channels:x * channels + 3]
            if sum(pixel) < 680:
                dark_pixels += 1
            if max(pixel) - min(pixel) > 18 or sum(pixel) < 650:
                distinct_pixels += 1
    return {"width": width, "height": height, "dark_pixels": dark_pixels, "distinct_pixels": distinct_pixels}


def _capture_render_snapshot(page, name: str) -> None:
    output = Path("output/playwright")
    output.mkdir(parents=True, exist_ok=True)
    image = page.screenshot(path=str(output / f"{name}.png"), full_page=False)
    metrics = page.evaluate(
        """() => ({
            viewport: { width: innerWidth, height: innerHeight },
            graph: document.querySelector('#graph')?.getBoundingClientRect().toJSON(),
            cards: [...document.querySelectorAll('.graph-node-card-label')].map(card => {
                const rect = card.getBoundingClientRect();
                return { id: card.dataset.nodeId, x: rect.x, y: rect.y, width: rect.width, height: rect.height };
            }),
            clusters: [...document.querySelectorAll('.graph-namespace-group, .graph-project-group')].map(group => {
                const rect = group.getBoundingClientRect();
                return { className: group.className, x: rect.x, y: rect.y, width: rect.width, height: rect.height };
            }),
        })"""
    )
    screenshot = _inspect_png_content(image, metrics["graph"])
    assert screenshot["width"] == metrics["viewport"]["width"]
    assert screenshot["height"] == metrics["viewport"]["height"]
    if metrics["cards"] or metrics["clusters"]:
        assert screenshot["dark_pixels"] > 40, f"Screenshot graph area is empty: {screenshot}"
    assert screenshot["distinct_pixels"] > 100, f"Screenshot has no rendered content: {screenshot}"
    metrics["screenshot"] = screenshot
    (output / f"{name}.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")


def _assert_architecture_cards_are_contained_in_clusters(page) -> None:
    assert page.evaluate(
        """() => {
                const cards = [...document.querySelectorAll('.graph-node-card-label')]
                    .map(card => card.getBoundingClientRect())
                    .filter(card => card.right > 0 && card.left < innerWidth && card.bottom > 0 && card.top < innerHeight);
                const groups = [...document.querySelectorAll('.graph-namespace-group')]
                    .map(group => group.getBoundingClientRect());
                if (!groups.length) return true;
                return cards.every(card => groups.some(bounds => (
                card.left >= bounds.left && card.right <= bounds.right
                && card.top >= bounds.top && card.bottom <= bounds.bottom
            )));
        }"""
    )


def _assert_architecture_clusters_do_not_overlap(page) -> None:
    """Reject intersections between architecture-module sibling rectangles."""
    result = page.evaluate(
        """() => {
            const rects = [...document.querySelectorAll('.graph-namespace-group')].map(group => {
                const rect = group.getBoundingClientRect();
                return { name: group.dataset.namespace, left: rect.left, right: rect.right,
                    top: rect.top, bottom: rect.bottom, width: rect.width, height: rect.height };
            });
            const overlap = (left, right) => left.left < right.right - .5
                && left.right > right.left + .5
                && left.top < right.bottom - .5
                && left.bottom > right.top + .5;
            const intersections = [];
            rects.forEach((left, index) => rects.slice(index + 1).forEach(right => {
                if (overlap(left, right)) intersections.push([left.name, right.name]);
            }));
            return { valid: !intersections.length, intersections, rects };
        }"""
    )
    assert result["valid"], result


def _assert_pan_moves_cluster_overlays_as_one_surface(page) -> None:
    before = page.evaluate(
        """() => [...document.querySelectorAll(
            '.graph-node-card-label, .graph-namespace-group, .graph-project-group'
        )].map(element => {
            const rect = element.getBoundingClientRect();
            return [rect.left, rect.top, rect.right, rect.bottom];
        })"""
    )
    assert before
    start = page.locator(".graph-node-card-label").first.bounding_box()
    assert start
    page.mouse.move(start["x"] + start["width"] / 2, start["y"] + start["height"] / 2)
    page.mouse.down()
    page.mouse.move(start["x"] + start["width"] / 2 - 80, start["y"] + start["height"] / 2 + 65, steps=10)
    page.mouse.up()
    page.wait_for_timeout(250)
    after = page.evaluate(
        """() => [...document.querySelectorAll(
            '.graph-node-card-label, .graph-namespace-group, .graph-project-group'
        )].map(element => {
            const rect = element.getBoundingClientRect();
            return [rect.left, rect.top, rect.right, rect.bottom];
        })"""
    )
    assert len(after) == len(before)
    delta_x = after[0][0] - before[0][0]
    delta_y = after[0][1] - before[0][1]
    assert abs(delta_x) > 1 or abs(delta_y) > 1
    for old, new in zip(before, after):
        assert new[0] - old[0] == pytest.approx(delta_x, abs=1.5)
        assert new[1] - old[1] == pytest.approx(delta_y, abs=1.5)
        assert new[2] - old[2] == pytest.approx(delta_x, abs=1.5)
        assert new[3] - old[3] == pytest.approx(delta_y, abs=1.5)
    assert delta_x < -5
    assert delta_y > 5


def _surface_rects(page) -> list[list[float]]:
    return page.evaluate(
        """() => [...document.querySelectorAll(
            '.graph-node-card-label, .graph-namespace-group, .graph-project-group'
        )].map(element => {
            const rect = element.getBoundingClientRect();
            return [rect.left, rect.top, rect.right, rect.bottom];
        })"""
    )


def _node_centers(page) -> dict[str, list[float]]:
    return page.evaluate(
        """() => Object.fromEntries([...document.querySelectorAll(
            '.graph-node-card-label'
        )].map(card => {
            const rect = card.getBoundingClientRect();
            return [card.dataset.nodeId, [rect.left + rect.width / 2, rect.top + rect.height / 2]];
        }))"""
    )


def _assert_node_centers_unchanged(before, after) -> None:
    assert after.keys() == before.keys()
    for node_id, center in before.items():
        assert after[node_id] == pytest.approx(center, abs=0.5)


def _graph_background_point(page, *, end_dx: int = 0, end_dy: int = 0) -> dict:
    point = page.evaluate(
        """({ endDx, endDy }) => {
            const graph = document.querySelector('#graph').getBoundingClientRect();
            for (let y = graph.top + 40; y <= graph.bottom - 100; y += 30) {
                for (let x = graph.right - 40; x >= graph.left + 140; x -= 40) {
                    const startsOnGraph = document.elementFromPoint(x, y)?.closest('#graph');
                    const endsOnGraph = document.elementFromPoint(
                        x + endDx, y + endDy
                    )?.closest('#graph');
                    if (startsOnGraph && endsOnGraph) return { x, y };
                }
            }
            return null;
        }""",
        {"endDx": end_dx, "endDy": end_dy},
    )
    assert point, "No unobstructed graph background was available for the gesture"
    return point


def _assert_zoom_is_monotonic_and_settles_without_a_release_jump(page) -> None:
    """A zoom gesture must change scale, then remain stable after release."""
    before = _surface_rects(page)
    assert before
    before_distance = ((before[1][0] - before[0][0]) ** 2 + (before[1][1] - before[0][1]) ** 2) ** .5 if len(before) > 1 else 0
    zoom_point = _graph_background_point(page)
    page.mouse.move(zoom_point["x"], zoom_point["y"])
    page.mouse.wheel(0, -450)
    page.wait_for_timeout(80)
    during = _surface_rects(page)
    page.wait_for_timeout(700)
    after = _surface_rects(page)
    page.wait_for_timeout(300)
    settled = _surface_rects(page)
    assert len(during) == len(before) == len(after)
    during_distance = ((during[1][0] - during[0][0]) ** 2 + (during[1][1] - during[0][1]) ** 2) ** .5 if len(during) > 1 else 0
    after_distance = ((after[1][0] - after[0][0]) ** 2 + (after[1][1] - after[0][1]) ** 2) ** .5 if len(after) > 1 else 0
    if before_distance:
        assert during_distance > before_distance * 1.01
        settled_distance = ((settled[1][0] - settled[0][0]) ** 2 + (settled[1][1] - settled[0][1]) ** 2) ** .5 if len(settled) > 1 else 0
        assert settled_distance == pytest.approx(after_distance, rel=0.04)

    page.locator("#zoom-out").click()
    page.wait_for_timeout(300)
    restored = _surface_rects(page)
    if after_distance and len(restored) > 1:
        restored_distance = ((restored[1][0] - restored[0][0]) ** 2 + (restored[1][1] - restored[0][1]) ** 2) ** .5
        # Zoom-out is never clamped to a collision-derived camera state; it
        # must only change the camera and preserve card dimensions.
        assert restored_distance <= after_distance * 1.01


def _assert_background_pan_preserves_overlay_scale(page) -> None:
    before = page.locator(".graph-namespace-group").first.bounding_box()
    assert before
    start = _graph_background_point(page, end_dx=-100, end_dy=80)
    page.mouse.move(start["x"], start["y"])
    page.mouse.down()
    page.mouse.move(start["x"] - 100, start["y"] + 80, steps=10)
    page.mouse.up()
    page.wait_for_timeout(250)
    after = page.locator(".graph-namespace-group").first.bounding_box()
    assert after
    assert after["x"] < before["x"] - 5
    assert after["y"] > before["y"] + 5
    assert after["width"] == pytest.approx(before["width"], abs=0.1)
    assert after["height"] == pytest.approx(before["height"], abs=0.1)


def _assert_clusters_only_overlap_when_nested(page) -> None:
    """Allow an overlap only when one module rectangle contains the other."""
    result = page.evaluate(
        """() => {
            const rects = [...document.querySelectorAll(
                '.graph-namespace-group, .graph-project-group'
            )].map(element => {
                const rect = element.getBoundingClientRect();
                return { name: element.dataset.namespace || element.dataset.namespaceGroup,
                    left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom,
                    width: rect.width, height: rect.height };
            });
            const overlap = (left, right) => left.left < right.right - .5
                && left.right > right.left + .5
                && left.top < right.bottom - .5
                && left.bottom > right.top + .5;
            const contains = (outer, inner) => inner.left >= outer.left - .5
                && inner.right <= outer.right + .5 && inner.top >= outer.top - .5
                && inner.bottom <= outer.bottom + .5;
            const invalid = [];
            rects.forEach((left, index) => rects.slice(index + 1).forEach(right => {
                if (overlap(left, right) && !contains(left, right) && !contains(right, left)) {
                    invalid.push([left.name, right.name]);
                }
            }));
            return { valid: !invalid.length, invalid, rects };
        }"""
    )
    assert result["valid"], result


def _assert_nested_namespace_cluster_contains_three_children(page) -> None:
    result = page.evaluate(
        """() => {
            const parent = document.querySelector(
                '.graph-project-group[data-namespace-group="platform-edge"]'
            );
            const children = [...document.querySelectorAll(
                '.graph-namespace-group[data-namespace^="platform-edge/"]'
            )];
            if (!parent || children.length !== 3) return { valid: false, children: children.length };
            const outer = parent.getBoundingClientRect();
            const rects = children.map(child => child.getBoundingClientRect());
            const contains = rect => (
                rect.left >= outer.left && rect.right <= outer.right
                && rect.top >= outer.top && rect.bottom <= outer.bottom
            );
            return {
                valid: rects.every(contains),
                parent: [outer.left, outer.top, outer.width, outer.height],
                children: rects.map(rect => [rect.left, rect.top, rect.width, rect.height]),
            };
        }"""
    )
    assert result["valid"], result


def _assert_layer_bands_are_disjoint_and_contain_clusters(page) -> None:
    result = page.evaluate(
        """() => {
                const bands = [...document.querySelectorAll('.graph-layer-band')]
                    .map(element => ({ layer: element.dataset.layer,
                        rect: element.getBoundingClientRect() }));
                const clusters = [...document.querySelectorAll('.graph-namespace-group')]
                    .map(element => ({ layer: element.dataset.layer,
                        namespace: element.dataset.namespace,
                        rect: element.getBoundingClientRect() }));
            const clip = rect => ({
                left: Math.max(0, rect.left), right: Math.min(innerWidth, rect.right),
                top: Math.max(0, rect.top), bottom: Math.min(innerHeight, rect.bottom),
            });
            const visible = rect => rect.right > 0 && rect.left < innerWidth
                && rect.bottom > 0 && rect.top < innerHeight;
            const overlap = (left, right) => (
                left.left < right.right - .5 && left.right > right.left + .5
                && left.top < right.bottom - .5 && left.bottom > right.top + .5
            );
            const contains = (outer, inner) => (
                inner.left >= outer.left && inner.right <= outer.right
                && inner.top >= outer.top && inner.bottom <= outer.bottom
            );
            const visibleBands = bands.filter(item => visible(item.rect));
            const visibleClusters = clusters.filter(item => visible(item.rect));
            const intersections = [];
            visibleBands.forEach((band, index) => visibleBands.slice(index + 1).forEach(other => {
                if (overlap(clip(band.rect), clip(other.rect))) {
                    intersections.push([band.layer, other.layer]);
                }
            }));
            const outside = visibleClusters.filter(cluster => {
                const owner = visibleBands.find(band => band.layer === cluster.layer);
                return !owner || !contains(clip(owner.rect), clip(cluster.rect));
            }).map(cluster => ({ layer: cluster.layer, namespace: cluster.namespace }));
            return { valid: !intersections.length && !outside.length,
                intersections, outside, bands, clusters };
        }"""
    )
    assert result["valid"], result


def _assert_geometry_contract(page, *, layered: bool) -> None:
    graph = page.locator("#graph")
    assert graph.get_attribute("data-invalid-coordinates") == "false"
    _assert_architecture_cards_have_uniform_size(page)
    if page.locator(".graph-namespace-group").count():
        _assert_architecture_cards_do_not_overlap(page)
    else:
        _assert_architecture_cards_are_valid(page)
    _assert_architecture_cards_are_contained_in_clusters(page)
    if not layered:
        _assert_clusters_only_overlap_when_nested(page)
    if layered:
        _assert_layer_bands_are_disjoint_and_contain_clusters(page)


@pytest.mark.slow
def test_port_to_port_paths_attach_to_rendered_anchors() -> None:
    producer = _producer("com.example.OrderCreated")
    consumer = replace(
        producer,
        id=compute_endpoint_id("consume", "orders.created", "Consumer.java", 8),
        role="consume",
        path="Consumer.java",
        start_line=8,
        end_line=8,
    )
    payment_output = replace(
        producer,
        id=compute_endpoint_id("produce", "payments.completed", "PaymentPublisher.java", 12),
        topic="payments.completed",
        path="PaymentPublisher.java",
        start_line=12,
        end_line=12,
    )
    document = render_graph_html(
        {"orders": [producer], "payments": [consumer, payment_output]},
        [GraphEdge("kafka", "orders", "payments", producer, consumer)],
        code_flows=[CodeFlow(
            id="payment-flow", module="payments", method="PaymentHandler.handle",
            path="Consumer.java", start_line=8, end_line=12,
            status="potential", confidence="medium", reason="test",
            steps=(
                CodeFlowStep(1, "message_entry", "orders.created", "Consumer.java", 8, 8, consumer.id),
                CodeFlowStep(2, "message_publish", "payments.completed", "PaymentPublisher.java", 12, 12, payment_output.id),
            ),
        )],
    )
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"Aucun navigateur Playwright ne peut être lancé : {error}")
        context = browser.new_context(viewport={"width": 900, "height": 600})
        page = context.new_page()
        page.set_default_timeout(5_000)
        page.set_content(document, wait_until="load")
        assert page.locator(".graph-node-port-reference").count() == 0
        assert page.locator(".graph-port-path").count() == 0
        context.close()
        browser.close()


@pytest.mark.slow
def test_code_flow_widget_is_readable_in_both_themes() -> None:
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"Aucun navigateur Playwright ne peut être lancé : {error}")
        context = browser.new_context(viewport={"width": 390, "height": 760})
        page = context.new_page()
        page.set_default_timeout(10_000)
        page.set_content(_code_flow_document(), wait_until="load")
        assert page.locator("#graph-tab").get_attribute("aria-selected") == "true"
        assert page.locator(".graph-node-card-label").count() == 6
        assert page.locator(".graph-node-port-reference").count() == 0
        assert page.locator(".graph-port-path").count() == 0
        toolbar_before_collapse = page.locator(".toolbar").bounding_box()
        assert toolbar_before_collapse is not None
        page.locator("#toolbar-collapse").click()
        assert page.locator(".toolbar").get_attribute("class") == "toolbar is-collapsed"
        assert page.locator("#toolbar-collapse").get_attribute("aria-expanded") == "false"
        assert page.locator("#toolbar-collapse").get_attribute("aria-label") == "Développer le panneau"
        toolbar_after_collapse = page.locator(".toolbar").bounding_box()
        assert toolbar_after_collapse is not None
        assert toolbar_after_collapse["width"] < toolbar_before_collapse["width"]
        page.locator("#toolbar-collapse").click()
        assert page.locator(".toolbar").get_attribute("class") == "toolbar"
        assert page.locator("#toolbar-collapse").get_attribute("aria-expanded") == "true"
        page.set_viewport_size({"width": 1100, "height": 760})
        page.locator("#flows-mode-tab").click()
        page.locator(".code-flow-item").wait_for(state="visible")
        assert page.locator("#flows-tab").get_attribute("aria-selected") == "true"
        for overlay_id in (
            "graph",
            "graph-layers",
            "graph-groups",
            "graph-port-paths",
            "graph-node-labels",
            "graph-call-tree",
            "graph-flow-tooltips",
        ):
            assert page.locator(f"#{overlay_id}").is_hidden(), overlay_id
        assert page.locator(
            "#graph-port-paths .graph-architecture-path, "
            "#graph-port-paths .graph-port-path, "
            "#graph-port-paths .graph-call-path"
        ).count() == 0

        assert page.locator(".code-flow-step").count() == 0
        assert page.locator(".code-flow-reason").count() == 1
        assert page.locator(".code-flow-badges").count() == 0
        assert page.locator("#flows-panel .reference-meta").inner_text() == "payments"
        metrics = page.evaluate(
            """() => {
                const rgb = value => value.match(/[\\d.]+/g).slice(0, 3).map(Number);
                const luminance = value => {
                    const channels = rgb(value).map(channel => {
                        const normalized = channel / 255;
                        return normalized <= .04045
                            ? normalized / 12.92
                            : ((normalized + .055) / 1.055) ** 2.4;
                    });
                    return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2];
                };
                const contrast = (foreground, background) => {
                    const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
                    return (values[0] + .05) / (values[1] + .05);
                };
                const inspect = theme => {
                    document.documentElement.dataset.theme = theme;
                    const item = document.querySelector('.code-flow-item');
                    const pairs = [
                        ['.code-flow-title', item],
                        ['.reference-meta', item],
                    ];
                    return {
                        cardBackground: getComputedStyle(item).backgroundColor,
                        contrasts: pairs.map(([selector, background]) => contrast(
                            getComputedStyle(document.querySelector(selector)).color,
                            getComputedStyle(background).backgroundColor,
                        )),
                    };
                };
                const item = document.querySelector('.code-flow-item');
                return {
                    light: inspect('light'),
                    dark: inspect('dark'),
                    overflowWidths: {
                        item: [item.clientWidth, item.scrollWidth],
                    },
                    noHorizontalOverflow: item.scrollWidth <= item.clientWidth,
                };
            }"""
        )
        assert metrics["light"]["cardBackground"] == "rgb(244, 247, 251)"
        assert metrics["dark"]["cardBackground"] == "rgb(19, 30, 43)"
        assert min(metrics["light"]["contrasts"]) >= 4.5
        assert min(metrics["dark"]["contrasts"]) >= 4.5
        assert metrics["noHorizontalOverflow"], metrics["overflowWidths"]

        toolbar_before_selection = page.locator(".toolbar").bounding_box()
        page.locator(".code-flow-item").click()
        assert toolbar_before_selection is not None
        assert page.locator(".code-flow-item.is-selected").count() == 1
        page.locator("#graph-call-tree").wait_for(state="visible")
        assert page.locator("#graph-call-tree .graph-call-tree-node").count() == 2
        trigger_badge = page.locator("#graph-call-tree .graph-node-trigger-badge")
        assert trigger_badge.count() == 1
        assert "Kafka" in trigger_badge.inner_text()
        page.locator("#architecture-mode-tab").click()
        page.locator("#graph").wait_for(state="visible")
        assert page.locator("#graph-call-tree").is_hidden()
        page.locator("#flows-mode-tab").click()
        page.locator(".code-flow-item").wait_for(state="visible")
        assert page.locator("#graph").is_hidden()
        assert page.locator("#graph-port-paths .graph-architecture-path").count() == 0
        context.close()
        browser.close()


@pytest.mark.slow
def test_primary_view_selector_opens_each_view_directly() -> None:
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"Aucun navigateur Playwright ne peut être lancé : {error}")
        context = browser.new_context(viewport={"width": 800, "height": 450})
        page = context.new_page()
        page.set_default_timeout(10_000)
        page.set_content(_complex_dataset_document(), wait_until="load")
        page.wait_for_function(
            "() => Number(document.querySelector('#graph')?.dataset.visibleNodeCount || 0) >= 60"
        )

        # Enriched aliases must obey the same selectors as native graph kinds.
        page.locator("#display-controls > summary").click()
        page.locator("#node-kafka-topic").uncheck()
        page.wait_for_function(
            "() => document.querySelector('#graph')?.dataset.visibleNodeCount === '80'"
        )
        assert page.locator("#graph").get_attribute("data-relation-count") == "60"
        page.locator("#node-kafka-topic").check()
        page.wait_for_function(
            "() => document.querySelector('#graph')?.dataset.visibleNodeCount === '180'"
        )
        page.locator("#relation-kafka").uncheck()
        page.wait_for_function(
            "() => document.querySelector('#graph')?.dataset.relationCount === '60'"
        )
        page.locator("#relation-kafka").check()
        page.wait_for_function(
            "() => document.querySelector('#graph')?.dataset.relationCount === '300'"
        )

        view_controls = page.get_by_role("group", name="Mode de visualisation")
        assert view_controls.is_visible()
        assert view_controls.get_by_role("button").all_text_contents() == [
                "Graphe statique", "Vue par couches", "Vue par modules",
        ]
        assert page.locator(".graph-mode-context-actions").bounding_box() is not None
        for button_id, status_text in (
            ("layout-elk", "vue par couches actif."),
            ("layout-cluster", "vue par modules actif."),
            ("layout-forceatlas2-noverlap", "vue par graphe actif."),
        ):
            page.locator(f"#{button_id}").click()
            page.locator("#layout-status").filter(has_text=status_text).wait_for(
                state="visible"
            )
            assert page.locator(f"#{button_id}").get_attribute("aria-pressed") == "true"
            assert page.locator(".view-mode[aria-pressed='true']").count() == 1

        context.close()
        browser.close()


@pytest.mark.slow
def test_cluster_and_resource_details_support_bidirectional_navigation() -> None:
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"Aucun navigateur Playwright ne peut être lancé : {error}")
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.set_default_timeout(10_000)
        page.set_content(_complex_dataset_document(), wait_until="load")
        page.wait_for_function(
            "() => Number(document.querySelector('#graph')?.dataset.visibleNodeCount || 0) >= 60"
        )
        page.locator("#layout-cluster").click()
        page.locator("#layout-status").filter(has_text="vue par modules actif.").wait_for(
            state="visible"
        )

        page.locator(".graph-project-group-title").filter(
            has_text="platform-edge"
        ).dispatch_event("click")
        assert page.locator("#details .details-title").inner_text() == "platform-edge"
        assert "Cluster" not in page.locator(".toolbar").inner_text()
        subclusters = page.get_by_role("heading", name="Sous-modules").locator("..").get_by_role(
            "button"
        )
        assert subclusters.all_text_contents() == [
            "platform-edge/sub-1", "platform-edge/sub-2", "platform-edge/sub-3",
        ]
        subclusters.first.click()
        page.locator("#details .details-title").filter(
            has_text="platform-edge/sub-1"
        ).wait_for(state="visible")

        resources = page.get_by_role("heading", name="Ressources contenues").locator(
            ".."
        ).get_by_role("button")
        assert resources.count() > 0
        resources.first.click()
        selected_id = page.locator(".graph-node-card-label.is-selected").get_attribute(
            "data-node-id"
        )
        assert selected_id
        cluster_link = page.get_by_role("heading", name="Module", exact=True).locator(
            ".."
        ).get_by_role("button")
        assert cluster_link.inner_text() == "platform-edge/sub-1"

        page.locator("#inspector-close").click()
        page.locator("#layout-forceatlas2-noverlap").click()
        page.locator("#layout-status").filter(has_text="vue par graphe actif.").wait_for(
            state="visible"
        )
        page.locator(f'.graph-node-card-label[data-node-id="{selected_id}"]').click(
            modifiers=["Shift"]
        )
        page.get_by_role("heading", name="Module", exact=True).locator("..").get_by_role(
            "button"
        ).click()
        page.locator("#layout-status").filter(has_text="vue par modules actif.").wait_for(
            state="visible"
        )
        assert page.locator("#details .details-title").inner_text() == "platform-edge/sub-1"
        page.get_by_role("heading", name="Module parent").locator("..").get_by_role(
            "button"
        ).click()
        assert page.locator("#details .details-title").inner_text() == "platform-edge"

        context.close()
        browser.close()


@pytest.mark.slow
def test_complex_dataset_geometry_contract_across_all_views() -> None:
    """Stress the geometry invariants with the 50-service stress dataset."""
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"Aucun navigateur Playwright ne peut être lancé : {error}")
        # Playwright contexts are private/incognito browser profiles: no
        # cookies, local storage, cache, or service workers leak between runs.
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.set_default_timeout(10_000)
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content(_complex_dataset_document(), wait_until="load")
        page.wait_for_function(
            "() => Number(document.querySelector('#graph')?.dataset.visibleNodeCount || 0) >= 60"
        )

        graph = page.locator("#graph")
        assert graph.get_attribute("data-visible-node-count") == "180"
        assert graph.get_attribute("data-relation-count") == "300"
        assert not errors, errors
        page.locator("#layout-status").filter(has_text="vue par graphe actif.").wait_for(state="visible")
        _capture_render_snapshot(page, "complex-initial")

        # The same contract is checked after layout, resize, zoom and pan. A
        # failure here means the geometry is viewport-dependent, which is the
        # class of regression that unit tests on graph coordinates miss.
        for layout_id, layered in (
            ("layout-cluster", False),
            ("layout-elk", True),
            ("layout-forceatlas2-noverlap", False),
        ):
            # Exercise each permanently visible primary-view control against
            # the same geometry contract.
            previous_status = page.locator("#layout-status").text_content() or ""
            already_active = page.locator(f"#{layout_id}").get_attribute("aria-pressed") == "true"
            page.locator(f"#{layout_id}").dispatch_event("click")
            if not already_active:
                page.wait_for_function(
                    "previous => document.querySelector('#layout-status')?.textContent !== previous",
                    arg=previous_status,
                )
                expected_status = {
                    "layout-cluster": "vue par modules actif.",
                    "layout-elk": "vue par couches actif.",
                    "layout-forceatlas2-noverlap": "vue par graphe actif.",
                }[layout_id]
                page.locator("#layout-status").filter(
                    has_text=expected_status
                ).wait_for(state="visible")
            page.wait_for_timeout(700)
            _capture_render_snapshot(page, f"complex-{layout_id.removeprefix('layout-')}-after-action")
            card_size = _assert_architecture_cards_have_uniform_size(page)
            _capture_render_snapshot(page, f"complex-{layout_id.removeprefix('layout-')}-fit")
            _assert_geometry_contract(page, layered=layered)
            if layout_id == "layout-cluster":
                _assert_nested_namespace_cluster_contains_three_children(page)
                _capture_render_snapshot(page, "complex-cluster-final")
            _assert_architecture_cards_keep_size_after_camera_change(page, card_size)
            _assert_geometry_contract(page, layered=layered)
            _assert_zoom_is_monotonic_and_settles_without_a_release_jump(page)
            _assert_architecture_cards_keep_size_after_camera_change(page, card_size)
            _assert_geometry_contract(page, layered=layered)

            page.locator("#zoom-out").click()
            page.locator("#zoom-out").click()
            page.wait_for_timeout(300)
            _capture_render_snapshot(page, f"complex-{layout_id.removeprefix('layout-')}-after-zoom-out")
            _assert_architecture_cards_keep_size_after_camera_change(page, card_size)
            _assert_geometry_contract(page, layered=layered)

            page.mouse.move(1250, 780)
            page.mouse.down()
            page.mouse.move(1120, 700, steps=8)
            page.mouse.up()
            page.wait_for_timeout(300)
            _capture_render_snapshot(page, f"complex-{layout_id.removeprefix('layout-')}-after-pan")
            _assert_architecture_cards_keep_size_after_camera_change(page, card_size)
            _assert_geometry_contract(page, layered=layered)

            page.set_viewport_size({"width": 1024, "height": 640})
            page.wait_for_timeout(500)
            _capture_render_snapshot(page, f"complex-{layout_id.removeprefix('layout-')}-after-resize")
            _assert_architecture_cards_keep_size_after_camera_change(page, card_size)
            _assert_geometry_contract(page, layered=layered)
            page.set_viewport_size({"width": 1440, "height": 900})
            page.wait_for_timeout(300)

        assert not errors, errors
        context.close()
        browser.close()


@pytest.mark.slow
def test_html_export_resources_are_usable_in_a_constrained_browser_viewport(tmp_path: Path) -> None:
    source_root = tmp_path / "orders" / "src" / "main" / "java" / "com" / "example"
    source_root.mkdir(parents=True)
    (source_root / "OrderCreated.java").write_text(
        "package com.example; public record OrderCreated(String orderId) {}",
        encoding="utf-8",
    )
    module = DiscoveredModule(
        name="orders",
        path=tmp_path / "orders",
        build_system="maven",
        version=None,
        kind="application",
        starts_application=True,
        configuration_example="",
        mongo_collections=("orders",),
        mongo_persistence_classes=(
            MongoPersistenceClass(
                collection="orders", name="Order", qualified_name="com.example.Order",
                path="src/main/java/com/example/Order.java", line=1,
                fields=(MongoField("address", "Address", ("com.example.Address",)),),
            ),
            MongoPersistenceClass(
                collection="orders", name="Address", qualified_name="com.example.Address",
                path="src/main/java/com/example/Address.java", line=1,
                fields=(MongoField("city", "String"),), root=False,
            ),
        ),
    )
    consumer = MessageEndpoint(
        id=compute_endpoint_id("consume", "orders.created", "Consumer.java", 8),
        role="consume", system="kafka", topic="orders.created", topic_dynamic=False,
        source="code", framework="spring-kafka", path="Consumer.java", start_line=8,
        end_line=8, snippet="", message_type="com.example.OrderCreated",
    )
    rest_call = MessageEndpoint(
        id=compute_endpoint_id("call", "GET /payments", "OrderClient.java", 12),
        role="call", system="rest", topic="GET /payments", topic_dynamic=False,
        source="code", framework="resttemplate", path="OrderClient.java", start_line=12,
        end_line=12, snippet="", message_type=None,
    )
    rest_server = MessageEndpoint(
        id=compute_endpoint_id("serve", "GET /payments", "PaymentController.java", 6),
        role="serve", system="rest", topic="GET /payments", topic_dynamic=False,
        source="code", framework="spring-mvc", path="PaymentController.java", start_line=6,
        end_line=6, snippet="", message_type=None,
    )
    document = render_graph_html(
        {
            "orders": [_producer("com.example.OrderCreated"), rest_call],
            "payments": [consumer, rest_server],
            "inventory": [],
        },
        [
            GraphEdge("kafka", "orders", "payments", _producer("com.example.OrderCreated"), consumer),
            GraphEdge("rest", "orders", "payments", rest_call, rest_server),
        ],
        collections_by_service={"orders": ["orders"]},
        modules_by_service={"orders": module},
        build_modules=[module],
    )

    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"Aucun navigateur Playwright ne peut être lancé : {error}")
        # Use a fresh private context for the constrained-viewport scenario as
        # well, so browser state cannot mask an export or rendering defect.
        context = browser.new_context(viewport={"width": 800, "height": 450})
        page = context.new_page()
        page.set_default_timeout(5_000)
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content(document, wait_until="load")
        page.wait_for_timeout(100)
        assert not errors
        _capture_render_snapshot(page, "constrained-initial")

        graph = page.locator("#graph")
        view_controls = page.get_by_role(
            "group", name="Mode de visualisation · un seul choix"
        )
        assert view_controls.is_visible()
        assert view_controls.get_by_role("button").all_text_contents() == [
                "Graphe statique", "Vue par couches", "Vue par modules",
        ]
        assert page.locator("#layout-forceatlas2-noverlap").get_attribute("aria-pressed") == "true"
        assert graph.get_attribute("data-relation-count") == "4"
        assert page.locator("#details").is_hidden()
        assert page.locator("#details").evaluate(
            "details => details.parentElement.classList.contains('toolbar')"
        )
        page.locator("#layout-status").filter(has_text="vue par graphe actif.").wait_for(state="visible")
        card_size = _assert_architecture_cards_have_uniform_size(page)
        _assert_architecture_cards_do_not_overlap(page)
        assert "1 ressource isolée" in page.locator("#graph-summary").inner_text()
        assert page.locator("#inventory-status").inner_text() == "Index complet"
        assert page.locator("#node-suggestions option").count() == 5
        display_controls = page.locator("#display-controls")
        assert not page.locator("#relation-http").is_visible()
        display_controls.locator(":scope > summary").click()
        _capture_render_snapshot(page, "constrained-after-open-controls")
        assert page.locator("#relation-http").is_visible()
        page.locator("#relation-http").uncheck()
        _capture_render_snapshot(page, "constrained-after-http-off")
        assert graph.get_attribute("data-relation-count") == "3"
        page.locator("#relation-kafka").uncheck()
        _capture_render_snapshot(page, "constrained-after-kafka-off")
        assert graph.get_attribute("data-relation-count") == "1"
        page.locator("#relation-http").check()
        _capture_render_snapshot(page, "constrained-after-http-on")
        assert graph.get_attribute("data-relation-count") == "2"
        page.locator("#relation-kafka").check()
        _capture_render_snapshot(page, "constrained-after-kafka-on")
        assert graph.get_attribute("data-relation-count") == "4"
        page.locator("#layout-elk").click()
        page.locator("#layout-status").filter(has_text="vue par couches actif.").wait_for(state="visible")
        _capture_render_snapshot(page, "constrained-after-layers")
        assert not errors, errors
        _assert_architecture_cards_match_size(page, card_size)
        _assert_architecture_cards_do_not_overlap(page)
        _assert_architecture_cards_are_contained_in_clusters(page)
        _assert_architecture_clusters_do_not_overlap(page)
        _assert_clusters_only_overlap_when_nested(page)
        full_node_count = _assert_filtered_graph_is_valid(page)

        # Changing node types must rebuild the graph and its layer overlays.
        # The filtered graph must contain no stale card for the removed type.
        page.locator("#node-kafka-topic").uncheck()
        page.locator("#layout-status").filter(has_text="vue par couches").wait_for(state="visible")
        _capture_render_snapshot(page, "constrained-after-topic-off")
        without_topic_count = _assert_filtered_graph_is_valid(
            page, full_node_count, "kafka_topic"
        )

        page.locator("#node-mongodb-collection").uncheck()
        page.locator("#layout-status").filter(has_text="vue par couches").wait_for(state="visible")
        _capture_render_snapshot(page, "constrained-after-mongodb-off")
        _assert_filtered_graph_is_valid(page, without_topic_count, "mongodb_collection")

        page.locator("#node-microservice").uncheck()
        _capture_render_snapshot(page, "constrained-after-microservice-off")
        page.locator("#node-external-microservice").uncheck()
        _capture_render_snapshot(page, "constrained-after-external-off")
        page.locator("#layout-status").filter(has_text="vue par couches").wait_for(state="visible")
        assert page.locator("#graph").get_attribute("data-visible-node-count") == "0"
        assert page.locator("#graph").get_attribute("data-invalid-coordinates") == "false"
        page.locator("#node-microservice").check()
        _capture_render_snapshot(page, "constrained-after-microservice-on")
        page.locator("#node-external-microservice").check()
        _capture_render_snapshot(page, "constrained-after-external-on")
        page.locator("#node-kafka-topic").check()
        _capture_render_snapshot(page, "constrained-after-topic-on")
        page.locator("#node-mongodb-collection").check()
        page.locator("#layout-status").filter(has_text="vue par couches").wait_for(state="visible")
        _capture_render_snapshot(page, "constrained-after-mongodb-on")

        page.locator("#layout-cluster").click()
        page.locator("#layout-status").filter(has_text="vue par modules actif.").wait_for(state="visible")
        _capture_render_snapshot(page, "constrained-after-clusters")
        page.wait_for_function("() => Boolean(document.querySelector('#graph').dataset.clusterLayout)")
        assert page.locator("#graph").get_attribute("data-cluster-sub-layers") == (
            "microservices-first,resources-second"
        )
        cluster_layout = json.loads(
            page.locator("#graph").get_attribute("data-cluster-layout") or "{}"
        )
        assert cluster_layout
        for cluster in {item["cluster"] for item in cluster_layout.values()}:
            services = [
                item["y"] for item in cluster_layout.values()
                if item["cluster"] == cluster and item["subLayer"] == "microservices"
            ]
            resources = [
                item["y"] for item in cluster_layout.values()
                if item["cluster"] == cluster and item["subLayer"] == "resources"
            ]
            if services and resources:
                assert min(services) > max(resources)
        page.wait_for_function(
            "() => document.querySelectorAll('#graph-layers .graph-namespace-group').length >= 1"
        )
        assert page.locator("#graph-layers .graph-cluster-sublayer-title").count() == 0
        _assert_architecture_cards_match_size(page, card_size)
        _assert_architecture_cards_do_not_overlap(page)
        _assert_architecture_cards_are_contained_in_clusters(page)
        _assert_architecture_clusters_do_not_overlap(page)
        _assert_clusters_only_overlap_when_nested(page)
        _assert_architecture_clusters_do_not_overlap(page)
        assert page.locator("#graph").get_attribute("data-invalid-coordinates") == "false"

        page.get_by_role("tab", name="Topics").click()
        page.locator("#kafka-panel").wait_for(state="visible")
        assert page.locator("#graph-context").is_hidden()
        _capture_render_snapshot(page, "constrained-after-kafka-tab")
        topic_filter = page.locator("#topics-filter")
        topic_filter.fill("orders.created")
        topic = page.locator("#topics-list li")
        topic.wait_for(state="visible")
        _capture_render_snapshot(page, "constrained-after-dto-filter")
        assert topic.count() == 1

        topic_filter.fill("absent")
        page.locator("#topics-empty").wait_for(state="visible")
        _capture_render_snapshot(page, "constrained-after-dto-empty")
        assert page.locator("#topics-empty").inner_text() == "Aucun topic ne correspond à ce filtre."

        topic_filter.fill("")
        topic.scroll_into_view_if_needed()
        toolbar = page.locator(".toolbar").bounding_box()
        topic_box = topic.bounding_box()
        assert toolbar is not None and toolbar["y"] + toolbar["height"] <= 450
        assert topic_box is not None and topic_box["y"] + topic_box["height"] <= 450

        page.locator("#contracts-mode-tab").click()
        page.get_by_role("tab", name="Mongo").click()
        page.locator("#persistence-panel").wait_for(state="visible")
        assert page.locator("#graph-context").is_hidden()
        _capture_render_snapshot(page, "constrained-after-mongo-tab")
        mongo_filter = page.locator("#mongo-class-reference-filter")
        mongo_filter.fill("com.example.Order")
        mongo_class = page.locator("#mongo-class-references li")
        assert mongo_class.count() == 1
        _capture_render_snapshot(page, "constrained-after-mongo-filter")
        mongo_class.get_by_role("button", name="Inspecter").click()
        _capture_render_snapshot(page, "constrained-after-inspect-order")
        assert page.locator("#inspector-title").inner_text() == "Données persistées · Order"
        assert "collection orders" not in page.locator("#inspector-body .dto-summary").first.inner_text()
        assert page.locator(
            '#inspector-body button[title="Afficher la donnée orders"]'
        ).is_visible()
        page.get_by_role("button", name="Address", exact=True).click()
        _capture_render_snapshot(page, "constrained-after-inspect-address")
        assert page.locator("#inspector-title").inner_text() == "Données persistées · Address"
        page.locator("#inspector-back").click()
        _capture_render_snapshot(page, "constrained-after-inspector-back")
        assert page.locator("#inspector-title").inner_text() == "Données persistées · Order"
        page.locator("#inspector-close").click()
        _capture_render_snapshot(page, "constrained-after-inspector-close")

        page.get_by_role("tab", name="Architecture").click()
        assert page.locator("#graph-context").is_visible()
        _capture_render_snapshot(page, "constrained-after-explorer-tab")
        search = page.locator("#search")
        search.fill("orders")
        assert page.locator("#details").is_hidden()
        search.fill("orders -> orders.created -> payments")
        search.press("Enter")
        orders_stop = page.get_by_role("button", name="1. orders : Microservice")
        orders_stop.wait_for(state="visible")
        _capture_render_snapshot(page, "constrained-after-path-search")
        assert page.get_by_role(
            "button", name=re.compile(r"^2\. orders\.created : Topic")
        ).is_visible()
        assert page.get_by_role("button", name="3. payments : Microservice").is_visible()
        assert not page.get_by_text("Flux de donnees").count()
        orders_stop.click()
        _capture_render_snapshot(page, "constrained-after-node-select")
        assert page.locator("#inspector-title").inner_text().startswith("Microservice · orders")
        assert "has-details" not in (page.locator(".toolbar").get_attribute("class") or "")
        assert page.locator("#reset").inner_text() == "Réinitialiser"
        assert page.locator("#details").is_hidden()
        module_action = page.locator("#inspector-body").get_by_role("link", name="Ouvrir le projet Maven dans VS Code")
        assert module_action.is_visible()
        assert module_action.get_attribute("href") == f"vscode://file/{module.path}"
        assert page.locator("#inspector-body .details-group > summary").all_text_contents() == [
            "Relations", "Architecture", "Ports d'intégration", "Sources"
        ]
        details_meta = page.locator("#inspector-body .details-meta").inner_text()
        assert "Relations : 3" in details_meta
        assert "Layer :" not in details_meta
        assert "Chemin des clusters :" not in details_meta
        architecture = page.locator("#inspector-body .details-group").filter(has_text="Architecture")
        assert "Application" in architecture.inner_text()
        assert "test_html_export_resources_are0" in architecture.inner_text()
        architecture_links = architecture.locator("button.relation-link")
        assert architecture_links.count() > 0
        assert all(
            box is not None and box["width"] > 100
            for box in [link.bounding_box() for link in architecture_links.all()]
        )
        assert page.get_by_role("button", name="orders.created", exact=True).is_visible()
        assert page.get_by_role("button", name="DTO · OrderCreated").is_visible()
        page.get_by_text("Sources", exact=True).click()
        _capture_render_snapshot(page, "constrained-after-sources-open")
        assert page.get_by_text("Publisher.java:4").is_visible()
        page.get_by_role("button", name="orders.created", exact=True).click()
        _capture_render_snapshot(page, "constrained-after-topic-select")
        consumers = page.locator("#inspector-body .details-section").filter(has_text="Services consommateurs")
        assert consumers.get_by_role("button", name="payments", exact=True).is_visible()
        assert not consumers.get_by_role("button", name="orders.created", exact=True).count()
        assert page.locator("#inspector-body").get_by_role(
            "heading", name="DTO de topic"
        ).is_visible()
        assert not page.locator("#inspector-body").get_by_text("Types publies", exact=True).count()
        assert not page.locator("#inspector-body").get_by_text("Types consommes", exact=True).count()

        page.locator("#inspector-close").click()
        page.locator("#reset").click()
        assert page.locator("#details").is_hidden()
        assert page.locator("#reset").is_disabled()
        assert search.is_visible()
        assert page.locator("#graph-summary").is_visible()
        search.fill("does-not-exist")
        search.press("Enter")
        _capture_render_snapshot(page, "constrained-after-missing-search")
        assert "Nœud introuvable" in page.locator("#search-status").inner_text()
        search.fill("inventory")
        search.press("Enter")
        _capture_render_snapshot(page, "constrained-after-inventory-search")
        assert page.locator("#inspector-title").inner_text().startswith("Microservice · inventory")
        page.locator("#inspector-close").click()
        # Run the same geometry contract against every primary view and every
        # camera state. This is intentionally one fixture so a layout fix for
        # one view cannot silently regress another view.
        for view_name, status_text in (
                ("Graphe statique", "vue par graphe actif."),
            ("Vue par couches", "vue par couches actif."),
            ("Vue par modules", "vue par modules actif."),
        ):
            page.get_by_role("button", name=view_name, exact=True).click()
            page.locator("#layout-status").filter(has_text=status_text).wait_for(state="visible")
            _capture_render_snapshot(page, f"constrained-{view_name.lower()}-after-view")
            _assert_architecture_cards_have_uniform_size(page)
            _assert_architecture_cards_do_not_overlap(page)
            for _ in range(2):
                page.locator("#zoom-out").click()
            page.wait_for_timeout(400)
            _capture_render_snapshot(page, f"constrained-{view_name.lower()}-after-zoom-out")
            _assert_architecture_cards_have_uniform_size(page)
            _assert_architecture_cards_do_not_overlap(page)
            for _ in range(2):
                page.locator("#zoom-in").click()
            page.wait_for_timeout(400)
            _capture_render_snapshot(page, f"constrained-{view_name.lower()}-after-zoom-in")
            _assert_architecture_cards_have_uniform_size(page)
            _assert_architecture_cards_do_not_overlap(page)
            page.set_viewport_size({"width": 1024, "height": 600})
            page.wait_for_timeout(400)
            _capture_render_snapshot(page, f"constrained-{view_name.lower()}-after-resize")
            _assert_architecture_cards_have_uniform_size(page)
            _assert_architecture_cards_do_not_overlap(page)
            if view_name == "Modules":
                _assert_background_pan_preserves_overlay_scale(page)
                _assert_pan_moves_cluster_overlays_as_one_surface(page)
            else:
                page.mouse.move(980, 80)
                page.mouse.down()
                page.mouse.move(900, 145, steps=10)
                page.mouse.up()
                page.wait_for_timeout(250)
                _capture_render_snapshot(page, f"constrained-{view_name.lower()}-after-pan")
            _assert_architecture_cards_have_uniform_size(page)
            _assert_architecture_cards_do_not_overlap(page)
            _assert_architecture_cards_are_contained_in_clusters(page)
            _assert_architecture_clusters_do_not_overlap(page)
            assert graph.get_attribute("data-invalid-coordinates") == "false"
            page.set_viewport_size({"width": 800, "height": 450})
            page.wait_for_timeout(400)
        assert not errors
        context.close()
        browser.close()
