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

from systemlens.domain.models import GraphFact, MessageEndpoint, compute_endpoint_id
from systemlens.domain.graph import GraphEdge
from systemlens.domain.code_flows import CodeFlow, CodeFlowStep
from systemlens.domain.module_inventory import DiscoveredModule, JpaEntity, MongoField, MongoPersistenceClass
from systemlens.render import render_graph_html


pytestmark = pytest.mark.integration


@pytest.mark.slow
def test_nested_supermarket_flows_remain_in_selected_call_tree() -> None:
    document = _current_simple_dataset_document()
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"No Chromium browser is available: {error}")
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.set_content(document, wait_until="load")
        page.locator("#flows-tab").click()
        assert page.locator("#code-flows .code-flow-item").count() == 2
        page.locator("#code-flows .code-flow-item").filter(has_text="GET /api/error").click()
        page.locator("#flow-call-tree-depth-increase").click()
        assert page.locator("#graph-call-tree .graph-call-tree-node").filter(
            has_text="restock-service"
        ).count() == 1
        page.locator("#graph-call-tree .graph-call-tree-node").filter(
            has_text="inventory-service"
        ).first.hover()
        tooltip = page.locator("#graph-flow-tooltips .graph-call-tree-entity-tooltip").inner_text()
        assert "IN " in tooltip
        assert "ProductEntity" not in tooltip
        page.locator("#flow-call-tree-depth-increase").click()
        page.locator("#flow-call-tree-depth-increase").click()
        inventory_nodes = page.locator("#graph-call-tree .graph-call-tree-node").filter(
            has_text="inventory-service"
        )
        assert inventory_nodes.count() == 2
        first_badges = inventory_nodes.first.locator(
            ".graph-call-tree-access-badge, .graph-call-tree-diagnostic-badge"
        ).all_inner_texts()
        assert "C/U StockMovementEntity ?" in first_badges
        assert sum(label.endswith("ProductEntity") for label in first_badges) <= 1
        assert inventory_nodes.last.locator(
            ".graph-call-tree-access-badge, .graph-call-tree-diagnostic-badge"
        ).all_inner_texts() == ["R (C/U) ProductEntity ?"]
        diagnostic_badges = page.locator("#graph-call-tree .graph-call-tree-diagnostic-badge")
        assert diagnostic_badges.count() > 0
        diagnostic_badges.first.click(force=True)
        assert page.locator(
            "#graph-call-tree .graph-call-tree-node.is-diagnostic-async, "
            "#graph-call-tree .graph-call-tree-node.is-diagnostic-sync, "
            "#graph-call-tree .graph-call-tree-node.is-diagnostic-interaction"
        ).count() > 0
        browser.close()


@pytest.mark.slow
def test_call_tree_marks_target_without_indexed_consumer_as_unknown() -> None:
    # Given a source-backed publication with no indexed consumer flow.
    producer = _producer("OrderCreated")
    consumer = replace(
        producer, id="payments-in", role="consume", path="Payments.java",
        start_line=8, end_line=8,
    )
    flow = CodeFlow(
        id="orders-flow", module="orders", method="Orders.publish",
        path="Publisher.java", start_line=1, end_line=4,
        status="potential", confidence="medium", reason="test",
        steps=(
            CodeFlowStep(1, "cron_entry", "cron", "Publisher.java", 1, 1),
            CodeFlowStep(2, "message_publish", "orders.created", "Publisher.java", 4, 4, producer.id),
        ),
    )
    document = render_graph_html(
        {"orders": [producer], "payments": [consumer]},
        [GraphEdge("kafka", "orders", "payments", producer, consumer)],
        code_flows=[flow],
    )

    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"No Chromium browser is available: {error}")
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        page.set_content(document, wait_until="load")

        # When the user opens the call tree and reveals its first child.
        page.locator("#flows-tab").click()
        page.locator("#code-flows .code-flow-item").first.click()
        page.locator("#flow-call-tree-depth-increase").click()
        target = page.locator("#graph-call-tree .graph-call-tree-node").filter(has_text="payments")

        # Then the target stays visible without claiming a resolved continuation.
        assert target.count() == 1
        target.hover()
        assert "suite inconnue" in page.locator(
            "#graph-flow-tooltips .graph-call-tree-entity-tooltip"
        ).inner_text()
        browser.close()


@pytest.mark.slow
def test_asyncapi_viewer_loads_lazily_and_preserves_inspector_navigation(tmp_path: Path) -> None:
    contract = {
        "module": "orders", "path": "orders.asyncapi.yaml",
        "spec": {"asyncapi": "3.0.0", "info": {"title": "Orders", "version": "1.0.0"},
                 "channels": {}},
    }
    document = render_graph_html({"orders": []}, [], asyncapi_contracts=[contract])
    document = document.replace(
        "async function openAsyncApiContract(contract)",
        "window.openAsyncApiContract = openAsyncApiContract; "
        "window.openInspector = openInspector; async function openAsyncApiContract(contract)",
    )
    target = tmp_path / "asyncapi.html"
    target.write_text(document)
    with sync_playwright() as playwright:
        browser = _launch_visual_browser(playwright)
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(target.as_uri(), wait_until="load")
        assert not page.evaluate('!!customElements.get("asyncapi-component")')
        # Concurrent requests share initialization; navigating away cancels display.
        page.evaluate("""async contract => {
            const first = openAsyncApiContract(contract);
            const second = openAsyncApiContract(contract);
            openInspector('Another resource');
            await Promise.all([first, second]);
        }""", contract)
        assert page.locator("asyncapi-component").count() == 0
        assert page.evaluate('!!customElements.get("asyncapi-component")')
        assert page.locator("#asyncapi-assets").count() == 0
        page.evaluate("contract => openAsyncApiContract(contract)", contract)
        assert page.locator("asyncapi-component").count() == 1
        page.wait_for_function("document.querySelector('asyncapi-component').shadowRoot?.textContent.length > 0")
        assert not errors
        page.reload(wait_until="load")
        page.evaluate("window.DecompressionStream = undefined")
        page.evaluate("contract => openAsyncApiContract(contract)", contract)
        assert json.loads(page.locator("#inspector-body pre").inner_text()) == contract["spec"]
        assert not errors
        browser.close()


@pytest.mark.slow
def test_resource_graph_labels_break_at_name_boundaries() -> None:
    document = _current_simple_dataset_document()
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"No Chromium browser is available: {error}")
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.set_content(document, wait_until="load")
        topic = page.locator(
            '.graph-node-card-label.is-topic .graph-node-card-name[title="supermarket.order.placed"]'
        )
        topic.wait_for(state="visible")
        assert topic.locator("wbr").count() == 2
        assert topic.evaluate("element => element.parentElement.offsetWidth") == 110
        entity = page.locator(
            '.graph-node-card-label.is-jpa-entity .graph-node-card-name[title="ProductEntity"]'
        )
        assert entity.locator("wbr").count() == 1
        browser.close()


@pytest.mark.slow
@pytest.mark.parametrize("include_call_graph_annotations", [True, False])
def test_call_flow_lists_data_access_on_sibling_path(
    include_call_graph_annotations: bool,
) -> None:
    entry = MessageEndpoint(
        id="orders-entry", role="serve", system="rest", topic="POST /orders",
        topic_dynamic=False, source="code", framework="spring-mvc",
        path="Orders.java", start_line=1, end_line=1, snippet="",
    )
    publish = CodeFlow(
        "a-publish", "orders", "Orders.place", "Orders.java", 1, 3,
        "potential", "medium", "test", (
            CodeFlowStep(1, "http_entry", "POST /orders", "Orders.java", 1, 1, entry.id),
            CodeFlowStep(2, "message_publish", "orders.created", "Orders.java", 2, 2),
        ),
    )
    read = CodeFlow(
        "b-read", "orders", "Orders.place", "Orders.java", 1, 3,
        "potential", "medium", "test", (
            CodeFlowStep(1, "http_entry", "POST /orders", "Orders.java", 1, 1, entry.id),
            CodeFlowStep(2, "data_read", "ProductEntity", "Orders.java", 3, 3,
                         operation="findById", action="read", resource="jpa:orders:ProductEntity"),
        ),
    )
    write = CodeFlow(
        "c-write", "orders", "Orders.place", "Orders.java", 1, 4,
        "potential", "medium", "test", (
            CodeFlowStep(1, "http_entry", "POST /orders", "Orders.java", 1, 1, entry.id),
            CodeFlowStep(2, "data_write", "ProductEntity", "Orders.java", 4, 4,
                         operation="save", action="create_or_update",
                         resource="jpa:orders:ProductEntity"),
        ),
    )
    other_method_read = replace(
        read, id="d-other-method-read", method="Orders.other",
        steps=(read.steps[0], replace(
            read.steps[1], name="UnrelatedEntity", resource="jpa:orders:UnrelatedEntity",
        )),
    )
    imported = GraphFact(
        id="ai::node::orders-delete", fact_type="node", kind="flow_data_access",
        name="OrderEntity", source_kind=None, source_name=None,
        target_kind=None, target_name=None, relation=None, origin="ai",
        confidence="medium", evidence_path="Orders.java", evidence_line=5,
        metadata={"flow_id": "a-publish", "action": "delete"},
        namespace="ai", status="proposed",
    )
    imported_product_delete = replace(
        imported, id="ai::node::product-delete", name="ProductEntity",
        evidence_line=6,
        metadata={"flow_id": "a-publish", "action": "delete"},
    )
    distinct_product_read = replace(
        imported, id="ai::node::other-product-read",
        name="jpa:orders:other.ProductEntity", evidence_line=7,
        metadata={"flow_id": "a-publish", "action": "read"},
    )
    document = render_graph_html(
        {"orders": [entry]}, [], code_flows=[publish, read, write, other_method_read],
        graph_facts=[imported, imported_product_delete, distinct_product_read],
        include_call_graph_annotations=include_call_graph_annotations,
    )
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"No Chromium browser is available: {error}")
        context = browser.new_context(viewport={"width": 1200, "height": 800})
        page = context.new_page()
        page.set_content(document, wait_until="load")
        page.locator("#architecture-mode-tab").click()
        page.locator(".graph-node-card-label").filter(has_text="orders").first.hover()
        service_tooltip = page.locator("#graph-flow-tooltips .graph-entity-tooltip")
        assert "ProductEntity" not in service_tooltip.inner_text()
        page.locator("#flows-tab").click()
        assert page.locator("#code-flows .code-flow-item").count() == 1
        page.locator(".code-flow-item").first.click()
        assert page.locator("#flow-data-access-lane").count() == 0
        assert page.locator("#code-flow-data-summary").count() == 0
        assert page.locator("#flow-diagnostics").count() == 0
        assert page.locator(".code-flow-item.is-selected").count() == 1
        badges = page.locator(".graph-call-tree-node .graph-call-tree-access-badge")
        if include_call_graph_annotations:
            assert set(badges.all_inner_texts()) == {
                "D OrderEntity", "RD (C/U) ProductEntity (orders) ?",
                "R ProductEntity (other)",
            }
            assert badges.count() == 3
            assert "UnrelatedEntity" not in badges.all_inner_texts()
        else:
            assert badges.count() == 0
        page.locator(".graph-call-tree-node").first.hover()
        tooltip = page.locator("#graph-flow-tooltips .graph-call-tree-entity-tooltip")
        tooltip_text = tooltip.inner_text()
        assert "RD (C/U) ProductEntity" not in tooltip_text
        assert "D OrderEntity" not in tooltip_text
        assert page.locator("#code-flows .code-flow-item").count() == 1
        assert page.locator("#code-flow-show-data-paths").count() == 0
        context.close()
        browser.close()


@pytest.mark.slow
def test_access_badge_marks_divergent_method_route_as_uncertain() -> None:
    entry = MessageEndpoint(
        id="orders-entry", role="serve", system="rest", topic="POST /orders",
        topic_dynamic=False, source="code", framework="spring-mvc",
        path="Orders.java", start_line=1, end_line=1, snippet="",
    )
    trigger = CodeFlowStep(1, "http_entry", entry.topic, "Orders.java", 1, 1, entry.id)
    shared = CodeFlowStep(2, "method_call", "Orders.common", "Orders.java", 2, 2)
    route_a = CodeFlowStep(3, "method_call", "Orders.pathA", "Orders.java", 3, 3)
    route_b = CodeFlowStep(3, "method_call", "Orders.pathB", "Orders.java", 4, 4)
    def flow(flow_id: str, route: CodeFlowStep, effect: CodeFlowStep) -> CodeFlow:
        return CodeFlow(
            flow_id, "orders", "Orders.place", "Orders.java", 1, 7,
            "potential", "medium", "test", (trigger, shared, route, effect),
        )

    document = render_graph_html({"orders": [entry]}, [], code_flows=[
        flow("output", route_a, CodeFlowStep(
            4, "message_publish", "orders.created", "Orders.java", 5, 5,
        )),
        flow("same-route", route_a, CodeFlowStep(
            4, "data_read", "SameRoute", "Orders.java", 6, 6,
            action="read", resource="jpa:orders:SameRoute",
        )),
        flow("other-route", route_b, CodeFlowStep(
            4, "data_read", "OtherRoute", "Orders.java", 7, 7,
            action="read", resource="jpa:orders:OtherRoute",
        )),
    ])
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"No Chromium browser is available: {error}")
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        page.set_content(document, wait_until="load")
        page.locator("#flows-tab").click()
        page.locator("#code-flows .code-flow-item").first.click()
        assert set(page.locator(".graph-call-tree-access-badge").all_inner_texts()) == {
            "R SameRoute", "R OtherRoute ?",
        }
        browser.close()


@pytest.mark.slow
@pytest.mark.parametrize("include_call_graph_annotations", [True, False])
def test_flow_view_keeps_delete_access_out_of_service_tooltip(
    include_call_graph_annotations: bool,
) -> None:
    entry = MessageEndpoint(
        id="orders-delete", role="serve", system="rest", topic="DELETE /orders",
        topic_dynamic=False, source="code", framework="spring-mvc",
        path="Orders.java", start_line=1, end_line=1, snippet="",
    )
    flow = CodeFlow(
        id="mongo-delete", module="orders", method="Orders.delete",
        path="Orders.java", start_line=1, end_line=3,
        status="potential", confidence="medium", reason="source invocation",
        steps=(
            CodeFlowStep(1, "http_entry", "DELETE /orders", "Orders.java", 1, 1, entry.id),
            CodeFlowStep(2, "data_write", "orders", "Orders.java", 2, 2,
                         operation="deleteById", action="delete"),
        ),
    )
    output_flow = CodeFlow(
        id="orders-output", module="orders", method="Orders.publish",
        path="Orders.java", start_line=4, end_line=5,
        status="potential", confidence="medium", reason="source invocation",
        steps=(
            CodeFlowStep(1, "http_entry", "DELETE /orders", "Orders.java", 1, 1, entry.id),
            CodeFlowStep(2, "message_publish", "orders.created", "Orders.java", 5, 5),
        ),
    )
    document = render_graph_html(
        {"orders": [entry]}, [], code_flows=[output_flow, flow],
        include_call_graph_annotations=include_call_graph_annotations,
    )
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"Aucun navigateur Playwright ne peut être lancé : {error}")
        context = browser.new_context(viewport={"width": 1000, "height": 700})
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content(document, wait_until="load")
        page.locator("#architecture-mode-tab").click()
        page.locator(".graph-node-card-label").filter(has_text="orders").first.hover()
        assert "Suppression · orders" not in page.locator(
            "#graph-flow-tooltips .graph-entity-tooltip"
        ).inner_text()
        page.locator("#flows-tab").click()
        assert page.locator("#code-flows .code-flow-item").count() == 1
        assert page.locator("#code-flow-data-summary").count() == 0
        assert "Suppression de données" not in page.locator(
            "#code-flows .code-flow-item"
        ).first.inner_text()
        page.locator("#code-flows .code-flow-item").first.click()
        page.locator(".graph-call-tree-node").first.hover()
        assert "Suppression · orders" not in page.locator(
            "#graph-flow-tooltips .graph-call-tree-entity-tooltip"
        ).inner_text()
        assert page.locator(".graph-call-tree-access-badge").count() == 0
        assert page.locator("#graph").get_attribute("data-selected-code-flow")
        assert not errors
        context.close()
        browser.close()


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
        page.locator("#contracts-mode-tab").click()
        page.locator("#resource-catalogue-categories button").filter(has_text="Microservices").click()
        page.locator("#microservices-list .resource-catalogue-row").first.click()
        assert page.locator("#resource-catalogue-preview").get_attribute("class").endswith("has-selection")
        page.locator("#microservices-list .reference-title").filter(has_text="order-service").click()
        assert page.locator("#inspector-modal").is_hidden()
        assert page.locator("#resource-catalogue-preview-title").inner_text() == "order-service"
        assert page.locator("#resource-catalogue-preview").get_by_text(
            "Microservices", exact=True
        ).is_visible()
        assert page.locator("#microservices-panel").is_visible()
        page.locator("#microservices-list .resource-catalogue-inspect").first.click()
        assert page.locator("#inspector-modal").is_visible()
        assert page.locator("#inspector-body").get_by_text("Relations", exact=True).is_visible()
        page.locator("#inspector-close").click()
        assert page.locator("#inspector-modal").is_hidden()
        assert page.locator("#details").is_hidden()
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
        page.locator("#contracts-mode-tab").click()
        assert page.locator("#resource-catalogue-categories button").count() == 11
        assert page.locator("#microservices-list .reference-item").count() > 0
        page.locator("#resource-catalogue-categories button").filter(has_text="Topics").click()
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
        page.locator("#contracts-mode-tab").click()
        page.locator("#resource-catalogue-categories button").filter(has_text="Topics").click()
        topic = page.locator("#microservices-list .reference-title").first
        topic.wait_for(state="visible")
        topic.click()
        assert page.locator("#inspector-modal").is_hidden()
        assert page.locator("#resource-catalogue-preview").is_visible()
        assert page.locator("#resource-catalogue-preview .resource-catalogue-preview-kind").inner_text() in {
            "Topics",
            "Topic Kafka",
        }
        page.locator("#architecture-mode-tab").click()
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
            "() => { const areas = [...document.querySelectorAll('#graph-port-paths .graph-architecture-path.is-kafka + .graph-dependency-hit-area')];"
            " return areas.length > 0 && areas.every(path => !path.getAttribute('d')?.includes('NaN')); }"
        )
        hit_area = page.locator(
            "#graph-port-paths .graph-architecture-path.is-kafka + .graph-dependency-hit-area"
        ).last
        hit_area.click(modifiers=["Shift"])
        page.locator("#inspector-modal").wait_for(state="visible")
        assert page.locator("#inspector-title").inner_text().startswith("Topic ·")
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
        page.locator("#architecture-mode-tab").click()
        page.locator("#flows-tab").click()
        page.locator(".code-flow-item").first.click()
        page.locator(".graph-call-tree-node").first.click(modifiers=["Shift"])
        assert page.locator("#inspector-title").inner_text().startswith("Microservice ·")
        page.locator("#inspector-close").click()
        http_label = page.locator(".graph-call-tree-edge-label.is-clickable").filter(has_text="HTTP").first
        http_label.click(modifiers=["Shift"])
        assert page.locator("#inspector-title").inner_text().startswith("Route HTTP ·")
        page.locator("#inspector-close").click()
        page.locator("#flow-call-tree-depth-increase").wait_for(state="visible")
        page.locator("#flow-call-tree-depth-increase").click()
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
    )


def _code_flow_document(
    *, call_graphs_only: bool = False, include_call_graph_annotations: bool = True,
) -> str:
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
        call_graphs_only=call_graphs_only,
        include_call_graph_annotations=include_call_graph_annotations,
    )


@pytest.mark.slow
def test_call_graph_only_export_selects_flow_after_browser_initialization() -> None:
    document = _code_flow_document(
        call_graphs_only=True, include_call_graph_annotations=False,
    )
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"No Chromium browser is available: {error}")
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content(document, wait_until="load")
        assert page.locator("#flows-tab").get_attribute("aria-selected") == "true"
        assert page.locator("#graph").get_attribute("data-selected-code-flow")
        assert page.locator("#code-flows .code-flow-item.is-selected").count() == 1
        assert not errors
        browser.close()


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
        page.wait_for_function("""() => [...document.querySelectorAll('.graph-node-card-label')]
            .some(element => getComputedStyle(element).backgroundColor === 'rgb(19, 30, 43)')""")
        node_count = page.locator(".graph-node-card-label").count()
        page.locator("#theme-toggle").click()
        assert page.locator("html").get_attribute("data-theme") == "light"
        assert page.locator(".graph-node-card-label").count() == node_count
        page.reload()
        page.locator(".graph-node-card-label").first.wait_for()
        assert page.locator("html").get_attribute("data-theme") == "light"
        page.locator("#theme-toggle").click()
        page.locator("#architecture-mode-tab").click()
        page.locator("#flows-tab").click()
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


@pytest.mark.slow
def test_graph_navigation_renders_each_projection_once_per_action() -> None:
    document = _code_flow_document()
    for name in ("rebuildGraph", "renderResourceCatalogue", "renderCodeFlows"):
        signature = f"function {name}() {{"
        assert document.count(signature) == 1
        document = document.replace(
            signature,
            signature + f" window.__{name} = (window.__{name} || 0) + 1;",
        )

    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"No Chromium browser is available: {error}")
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        page.set_default_timeout(5_000)
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content(document, wait_until="load")
        assert page.evaluate("window.__rebuildGraph") == 1
        assert page.locator("#graph-tab").get_attribute("aria-selected") == "true"

        page.locator("#contracts-mode-tab").click()
        catalogue_renders = page.evaluate("window.__renderResourceCatalogue")
        page.locator("#microservices-filter").fill("payments")
        assert page.evaluate("window.__renderResourceCatalogue") == catalogue_renders + 1
        page.evaluate("""() => {
            const filter = document.getElementById('resource-kind-filter');
            filter.value = 'microservice';
            filter.dispatchEvent(new Event('change'));
        }""")
        assert page.evaluate("window.__renderResourceCatalogue") == catalogue_renders + 2

        page.locator("#architecture-mode-tab").click()
        page.locator("#flows-tab").click()
        flow_renders = page.evaluate("window.__renderCodeFlows")
        flow_id = page.locator("#code-flows .code-flow-item").first.get_attribute("data-flow-id")
        assert flow_id
        page.evaluate("""flowId => document.getElementById('flows-panel').dispatchEvent(
            new CustomEvent('systemlens:code-flow-open-in-list', {
                detail: {flowId}
            })
        )""", flow_id)
        assert not errors
        assert page.evaluate("window.__renderCodeFlows") == flow_renders + 1
        page.locator("#code-flows .code-flow-item").first.click()
        assert page.locator("#graph").get_attribute("data-selected-code-flow") == flow_id
        page.locator("#architecture-mode-tab").click()
        assert page.locator("#graph").get_attribute("data-selected-code-flow") is None
        assert page.locator("#graph-tab").get_attribute("aria-selected") == "true"
        assert not errors
        browser.close()


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
                    })"""
    )
    screenshot = _inspect_png_content(image, metrics["graph"])
    assert screenshot["width"] == metrics["viewport"]["width"]
    assert screenshot["height"] == metrics["viewport"]["height"]
    if metrics["cards"]:
        assert screenshot["dark_pixels"] > 40, f"Screenshot graph area is empty: {screenshot}"
    assert screenshot["distinct_pixels"] > 100, f"Screenshot has no rendered content: {screenshot}"
    metrics["screenshot"] = screenshot
    (output / f"{name}.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")


def _surface_rects(page) -> list[list[float]]:
    return page.evaluate(
        """() => [...document.querySelectorAll(
            '.graph-node-card-label'
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

    page.locator("#graph-zoom-out").click()
    page.wait_for_timeout(300)
    restored = _surface_rects(page)
    if after_distance and len(restored) > 1:
        restored_distance = ((restored[1][0] - restored[0][0]) ** 2 + (restored[1][1] - restored[0][1]) ** 2) ** .5
        # Zoom-out is never clamped to a collision-derived camera state; it
        # must only change the camera and preserve card dimensions.
        assert restored_distance <= after_distance * 1.01


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
        page.locator("#architecture-mode-tab").click()
        page.locator("#flows-tab").click()
        page.locator(".code-flow-item").wait_for(state="visible")
        assert page.locator("#flows-tab").get_attribute("aria-selected") == "true"
        for overlay_id in (
            "graph",
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
        call_nodes = page.locator("#graph-call-tree .graph-call-tree-node")
        assert call_nodes.count() == 2
        source_box = call_nodes.nth(0).bounding_box()
        target_box = call_nodes.nth(1).bounding_box()
        edge_label_box = page.locator("#graph-call-tree .graph-call-tree-edge-label").bounding_box()
        assert source_box and target_box and edge_label_box
        gap = target_box["x"] - source_box["x"] - source_box["width"]
        assert gap >= edge_label_box["width"]
        trigger_badge = page.locator("#graph-call-tree .graph-node-trigger-badge")
        assert trigger_badge.count() == 1
        assert "Kafka" in trigger_badge.inner_text()
        page.locator("#architecture-mode-tab").click()
        page.locator("#graph").wait_for(state="visible")
        assert page.locator("#graph-call-tree").is_hidden()
        page.locator("#architecture-mode-tab").click()
        page.locator("#flows-tab").click()
        page.locator(".code-flow-item").wait_for(state="visible")
        assert page.locator("#graph").is_hidden()
        assert page.locator("#graph-port-paths .graph-architecture-path").count() == 0
        context.close()
        browser.close()


@pytest.mark.slow
def test_graph_view_has_no_layer_or_module_rendering() -> None:
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"Aucun navigateur Playwright ne peut être lancé : {error}")
        page = browser.new_page(viewport={"width": 800, "height": 450})
        page.set_content(_complex_dataset_document(), wait_until="load")
        page.wait_for_function(
            "() => Number(document.querySelector('#graph')?.dataset.visibleNodeCount || 0) >= 60"
        )
        assert page.locator(".view-controls, #layout-forceatlas2-noverlap").count() == 0
        assert page.locator("#graph-layers, #graph-groups, #layout-elk, #layout-cluster").count() == 0
        assert page.locator(".graph-layer-band, .graph-namespace-group, .graph-project-group").count() == 0
        page.locator("#layout-status").filter(has_text="vue par graphe actif.").wait_for()
        page.locator("#advanced-controls > summary").click()
        page.locator("#layout-noverlap").click()
        page.locator("#layout-status").filter(has_text="éviter les chevauchements actif.").wait_for()
        assert page.locator("#layout-noverlap").get_attribute("aria-pressed") == "true"
        page.locator("#layout-forceatlas2").click()
        page.locator("#layout-status").filter(has_text="regroupement des liens actif.").wait_for()
        assert page.locator("#layout-noverlap").get_attribute("aria-pressed") == "false"
        assert page.locator("#layout-forceatlas2").get_attribute("aria-pressed") == "true"
        browser.close()


@pytest.mark.slow
def test_resource_details_navigate_module_hierarchy_without_changing_layout() -> None:
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"Aucun navigateur Playwright ne peut être lancé : {error}")
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_content(_complex_dataset_document(), wait_until="load")
        page.wait_for_function(
            "() => Number(document.querySelector('#graph')?.dataset.visibleNodeCount || 0) >= 60"
        )
        node_id = page.evaluate(
            """() => JSON.parse(document.querySelector('#graph-data').textContent).nodes
                .find(node => node.cluster_path === 'platform-edge/sub-1').id"""
        )
        page.locator(f'.graph-node-card-label[data-node-id="{node_id}"]').click(modifiers=["Shift"])
        page.get_by_role("heading", name="Module", exact=True).locator("..").get_by_role(
            "button"
        ).click()
        assert page.locator("#details .details-title").inner_text() == "platform-edge/sub-1"
        page.get_by_role("heading", name="Module parent").locator("..").get_by_role(
            "button"
        ).click()
        assert page.locator("#details .details-title").inner_text() == "platform-edge"
        assert page.locator(".view-controls, #layout-forceatlas2-noverlap").count() == 0
        browser.close()


@pytest.mark.slow
def test_complex_dataset_graph_geometry() -> None:
    with sync_playwright() as playwright:
        try:
            browser = _launch_visual_browser(playwright)
        except PlaywrightError as error:
            pytest.skip(f"Aucun navigateur Playwright ne peut être lancé : {error}")
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content(_complex_dataset_document(), wait_until="load")
        page.wait_for_function(
            "() => Number(document.querySelector('#graph')?.dataset.visibleNodeCount || 0) >= 60"
        )
        graph = page.locator("#graph")
        assert graph.get_attribute("data-visible-node-count") == "180"
        assert graph.get_attribute("data-relation-count") == "300"
        page.locator("#layout-status").filter(has_text="vue par graphe actif.").wait_for(state="visible")
        card_size = _assert_architecture_cards_have_uniform_size(page)
        _assert_architecture_cards_do_not_overlap(page)
        _capture_render_snapshot(page, "complex-graph-initial")
        _assert_zoom_is_monotonic_and_settles_without_a_release_jump(page)
        _assert_architecture_cards_keep_size_after_camera_change(page, card_size)
        page.set_viewport_size({"width": 1024, "height": 640})
        page.wait_for_timeout(400)
        _assert_architecture_cards_keep_size_after_camera_change(page, card_size)
        assert graph.get_attribute("data-invalid-coordinates") == "false"
        assert not errors, errors
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
        assert page.locator(".view-controls, #layout-forceatlas2-noverlap").count() == 0
        assert graph.get_attribute("data-relation-count") == "4"
        assert page.locator("#details").is_hidden()
        assert page.locator("#details").evaluate(
            "details => details.parentElement.classList.contains('toolbar')"
        )
        page.locator("#layout-status").filter(has_text="vue par graphe actif.").wait_for(state="visible")
        card_size = _assert_architecture_cards_have_uniform_size(page)
        _assert_architecture_cards_do_not_overlap(page)
        assert page.locator("#graph-summary").count() == 0
        assert page.locator("#inventory-status").inner_text() == "Index complet"
        assert page.locator("#node-suggestions option").count() == 5
        display_controls = page.locator("#display-controls")
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
        assert not errors, errors
        _assert_architecture_cards_match_size(page, card_size)
        full_node_count = _assert_filtered_graph_is_valid(page)

        # Node filters must remove stale cards without changing the graph mode.
        page.locator("#node-kafka-topic").uncheck()
        without_topic_count = _assert_filtered_graph_is_valid(
            page, full_node_count, "kafka_topic"
        )
        page.locator("#node-mongodb-collection").uncheck()
        _assert_filtered_graph_is_valid(page, without_topic_count, "mongodb_collection")
        page.locator("#node-microservice").uncheck()
        page.locator("#node-external-microservice").uncheck()
        assert graph.get_attribute("data-visible-node-count") == "0"
        assert graph.get_attribute("data-invalid-coordinates") == "false"
        page.locator("#node-microservice").check()
        page.locator("#node-external-microservice").check()
        page.locator("#node-kafka-topic").check()
        page.locator("#node-mongodb-collection").check()
        assert graph.get_attribute("data-visible-node-count") == str(full_node_count)
        assert page.locator("#graph-layers, #graph-groups, #layout-elk, #layout-cluster").count() == 0

        page.locator("#contracts-mode-tab").click()
        page.locator("#resource-catalogue-categories button").filter(has_text="Topics").click()
        page.locator("#microservices-list .reference-title").first.click()
        assert page.locator("#resource-catalogue-preview").is_visible()
        _capture_render_snapshot(page, "constrained-after-kafka-catalogue")
        topic_filter = page.locator("#microservices-filter")
        topic_filter.fill("orders.created")
        topic = page.locator("#microservices-list .resource-catalogue-row")
        topic.first.wait_for(state="visible")
        _capture_render_snapshot(page, "constrained-after-topic-filter")
        assert topic.count() == 1

        topic_filter.fill("absent")
        page.locator("#microservices-empty").wait_for(state="visible")
        _capture_render_snapshot(page, "constrained-after-topic-empty")

        topic_filter.fill("")
        topic.first.scroll_into_view_if_needed()
        toolbar = page.locator(".toolbar").bounding_box()
        topic_box = topic.first.bounding_box()
        assert toolbar is not None and toolbar["y"] + toolbar["height"] <= 450
        assert topic_box is not None and topic_box["y"] + topic_box["height"] <= 450

        page.locator("#resource-catalogue-categories button").filter(has_text="Collections").click()
        mongo_filter = page.locator("#microservices-filter")
        mongo_filter.fill("orders")
        mongo_class = page.locator("#microservices-list .resource-catalogue-row")
        assert mongo_class.count() == 1
        _capture_render_snapshot(page, "constrained-after-mongo-filter")
        mongo_class.get_by_role("button", name="Inspecter").click()
        _capture_render_snapshot(page, "constrained-after-inspect-order")
        assert page.locator("#inspector-title").inner_text() == "Donnée · MongoDB · orders"
        page.locator("#inspector-body").get_by_role(
            "button", name="com.example.Order", exact=True
        ).click()
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
        assert page.locator("#details").is_hidden()
        assert page.locator("#reset").is_hidden()
        assert search.is_visible()
        assert page.locator("#graph-summary").count() == 0
        search.fill("does-not-exist")
        search.press("Enter")
        _capture_render_snapshot(page, "constrained-after-missing-search")
        assert "Nœud introuvable" in page.locator("#search-status").inner_text()
        search.fill("inventory")
        search.press("Enter")
        _capture_render_snapshot(page, "constrained-after-inventory-search")
        assert page.locator("#inspector-title").inner_text().startswith("Microservice · inventory")
        page.locator("#inspector-close").click()
        # Pan, zoom and resize retain finite graph geometry and card dimensions.
        page.locator("#graph-mode-center").click()
        _assert_architecture_cards_have_uniform_size(page)
        for control in ("#graph-zoom-out", "#graph-zoom-in"):
            page.locator(control).click()
            _assert_architecture_cards_are_valid(page)
        page.set_viewport_size({"width": 1024, "height": 600})
        page.wait_for_timeout(400)
        _assert_architecture_cards_are_valid(page)
        page.mouse.move(980, 80)
        page.mouse.down()
        page.mouse.move(900, 145, steps=10)
        page.mouse.up()
        page.wait_for_timeout(250)
        _assert_architecture_cards_are_valid(page)
        assert graph.get_attribute("data-invalid-coordinates") == "false"
        assert not errors
        context.close()
        browser.close()
