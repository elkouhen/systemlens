"""Write a dependency-free XLSX diagnostic export from an indexed snapshot."""

from __future__ import annotations

from collections import defaultdict
from html import escape
from pathlib import Path
from typing import Iterable
from zipfile import ZIP_DEFLATED, ZipFile

from systemlens.domain.code_flows import CodeFlow, IntegrationMethod
from systemlens.domain.models import MessageEndpoint


def _cell(value: object, style: int = 2) -> str:
    text = "" if value is None else str(value)
    return f'<c s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def _sheet_xml(
    rows: list[list[object]], widths: list[int], row_styles: list[int] | None = None
) -> str:
    body = "".join(
        f'<row r="{row_number}" ht="{max(18, 15 * max(str(value).count(chr(10)) + 1 for value in row) + 3)}" customHeight="1">' + "".join(
            _cell(
                value,
                1 if row_number == 1 else (row_styles[row_number - 2] if row_styles else 2),
            )
            for value in row
        ) + "</row>"
        for row_number, row in enumerate(rows, 1)
    )
    columns = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(widths, 1)
    )
    last_column = chr(64 + len(widths))
    view = (
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        '</sheetView></sheetViews>'
    )
    filter_xml = f'<autoFilter ref="A1:{last_column}{len(rows)}"/>'
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"{view}<cols>{columns}</cols>{filter_xml}<sheetData>{body}</sheetData></worksheet>"
    )


def _method_index(methods: Iterable[IntegrationMethod]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for method in methods:
        for endpoint_id in (*method.input_endpoint_ids, *method.output_endpoint_ids):
            result[endpoint_id].append(_java_class_name(method.qualified_method))
    return {endpoint_id: sorted(set(names)) for endpoint_id, names in result.items()}


def _java_class_name(qualified_method: str) -> str:
    """Return only the class part of a qualified Java method name."""
    parts = qualified_method.split(".")
    return parts[-2] if len(parts) > 1 else qualified_method


def _resource(endpoint: MessageEndpoint) -> str:
    return endpoint.topic if endpoint.system == "kafka" else ""


def _route(endpoint: MessageEndpoint) -> str:
    return endpoint.topic if endpoint.system == "rest" else ""


def _endpoint_context(endpoint: MessageEndpoint) -> str:
    direction = "IN" if endpoint.role in {"serve", "consume"} else "OUT"
    resource = _resource(endpoint) or _route(endpoint) or "?"
    return f"{direction} {endpoint.system.upper()} {resource} ({endpoint.path}:{endpoint.start_line})"


def _port_lines(
    endpoints: list[MessageEndpoint],
    methods_by_endpoint: dict[str, list[str]],
    roles: set[str],
) -> list[str]:
    lines: list[str] = []
    for endpoint in sorted(
        (item for item in endpoints if item.role in roles),
        key=lambda item: (item.system, item.topic, item.path, item.start_line, item.id),
    ):
        methods = methods_by_endpoint.get(endpoint.id) or ["?"]
        resource = _resource(endpoint) or _route(endpoint) or "?"
        for method in methods:
            lines.append(
                f"{endpoint.system.upper()}\n"
                f"Classe : {method}\n"
                f"Type : {endpoint.message_type or '?'}\n"
                f"{'Topic' if endpoint.system == 'kafka' else 'Route'} : {resource}"
            )
    return lines


def _flow_lines(
    endpoints_by_service: dict[str, list[MessageEndpoint]],
    methods: list[IntegrationMethod],
    flows: list[CodeFlow],
) -> dict[str, list[str]]:
    endpoint_by_id = {
        endpoint.id: endpoint
        for service_endpoints in endpoints_by_service.values()
        for endpoint in service_endpoints
    }
    methods_by_endpoint = _method_index(methods)
    lines_by_service: dict[str, list[str]] = defaultdict(list)
    for flow in sorted(flows, key=lambda item: (item.module, item.path, item.start_line, item.id)):
        input_endpoint = next(
            (
                endpoint_by_id.get(step.endpoint_id or "")
                for step in flow.steps
                if step.endpoint_id and endpoint_by_id.get(step.endpoint_id) is not None
                and endpoint_by_id[step.endpoint_id].role in {"serve", "consume"}
            ),
            None,
        )
        output_endpoints = [
            endpoint_by_id[step.endpoint_id]
            for step in flow.steps
            if step.endpoint_id in endpoint_by_id
            and endpoint_by_id[step.endpoint_id].role in {"call", "produce"}
        ]
        if input_endpoint is None or not output_endpoints:
            continue
        input_methods = methods_by_endpoint.get(input_endpoint.id) or [_java_class_name(flow.method)]
        for output_endpoint in output_endpoints:
            output_methods = methods_by_endpoint.get(output_endpoint.id) or [""]
            for method_in in input_methods:
                for method_out in output_methods:
                    lines_by_service[flow.module].append(
                        f"Classe IN : {method_in}\n"
                        f"Classe OUT : {method_out or '?'}\n"
                        f"Ressource IN : {input_endpoint.topic}\n"
                        f"Ressource OUT : {output_endpoint.topic}"
                    )
    return lines_by_service


def _summary_rows(
    endpoints_by_service: dict[str, list[MessageEndpoint]],
    methods: list[IntegrationMethod],
    flows: list[CodeFlow],
) -> tuple[list[list[object]], list[int]]:
    methods_by_endpoint = _method_index(methods)
    flow_lines_by_service = _flow_lines(endpoints_by_service, methods, flows)
    rows: list[list[object]] = [[
        "Microservice", "Statut", "Ports IN", "Ports OUT", "Flux internes", "Alertes",
    ]]
    row_styles: list[int] = []
    for service in sorted(endpoints_by_service):
        endpoints = endpoints_by_service[service]
        input_endpoints = [endpoint for endpoint in endpoints if endpoint.role in {"serve", "consume"}]
        output_endpoints = [endpoint for endpoint in endpoints if endpoint.role in {"call", "produce"}]
        flow_lines = flow_lines_by_service.get(service, [])
        issues: list[str] = []
        if not endpoints:
            issues.append("Aucun port indexé")
        if not flow_lines:
            issues.append("Aucun flux interne")
        issues.extend(
            f"Classe non résolue : {_endpoint_context(endpoint)}"
            for endpoint in endpoints
            if endpoint.id not in methods_by_endpoint
        )
        issues.extend(
            f"Type non résolu : {_endpoint_context(endpoint)}"
            for endpoint in endpoints
            if endpoint.system == "kafka" and endpoint.message_type is None
        )
        issues.extend(
            f"Topic dynamique : {_endpoint_context(endpoint)}"
            for endpoint in endpoints
            if endpoint.system == "kafka" and endpoint.topic_dynamic
        )
        if not endpoints:
            status, style = "Critique", 4
        elif issues:
            status, style = "À vérifier", 3
        else:
            status, style = "OK", 5
        row_styles.append(style)
        rows.append([
            service,
            status,
            len(input_endpoints),
            len(output_endpoints),
            len(flow_lines),
            "\n".join(issues) or "-",
        ])
    return rows, row_styles


def _port_detail_rows(
    endpoints_by_service: dict[str, list[MessageEndpoint]],
    methods: list[IntegrationMethod],
) -> list[list[object]]:
    methods_by_endpoint = _method_index(methods)
    rows: list[list[object]] = [[
        "Microservice", "Direction", "Transport", "Classe Java", "Type",
        "Topic / route", "Fichier", "Ligne", "Endpoint ID",
    ]]
    for service in sorted(endpoints_by_service):
        for endpoint in sorted(
            endpoints_by_service[service],
            key=lambda item: (item.role, item.system, item.topic, item.path, item.start_line, item.id),
        ):
            direction = "IN" if endpoint.role in {"serve", "consume"} else "OUT"
            resource = _resource(endpoint) or _route(endpoint) or "?"
            for class_name in methods_by_endpoint.get(endpoint.id) or ["?"]:
                rows.append([
                    service, direction, endpoint.system.upper(), class_name,
                    endpoint.message_type or "?", resource, endpoint.path,
                    endpoint.start_line, endpoint.id,
                ])
    return rows


def _flow_detail_rows(
    endpoints_by_service: dict[str, list[MessageEndpoint]],
    methods: list[IntegrationMethod],
    flows: list[CodeFlow],
) -> list[list[object]]:
    endpoint_by_id = {
        endpoint.id: endpoint
        for service_endpoints in endpoints_by_service.values()
        for endpoint in service_endpoints
    }
    methods_by_endpoint = _method_index(methods)
    rows: list[list[object]] = [[
        "Microservice", "Classe IN", "Classe OUT", "Ressource IN", "Ressource OUT",
        "Statut", "Confiance", "Fichier", "Ligne", "Flux ID",
    ]]
    for flow in sorted(flows, key=lambda item: (item.module, item.path, item.start_line, item.id)):
        input_endpoint = next(
            (
                endpoint_by_id.get(step.endpoint_id or "")
                for step in flow.steps
                if step.endpoint_id and endpoint_by_id.get(step.endpoint_id) is not None
                and endpoint_by_id[step.endpoint_id].role in {"serve", "consume"}
            ),
            None,
        )
        output_endpoints = [
            endpoint_by_id[step.endpoint_id]
            for step in flow.steps
            if step.endpoint_id in endpoint_by_id
            and endpoint_by_id[step.endpoint_id].role in {"call", "produce"}
        ]
        if input_endpoint is None or not output_endpoints:
            continue
        input_classes = methods_by_endpoint.get(input_endpoint.id) or [_java_class_name(flow.method)]
        for output_endpoint in output_endpoints:
            for input_class in input_classes:
                for output_class in methods_by_endpoint.get(output_endpoint.id) or ["?"]:
                    rows.append([
                        flow.module, input_class, output_class, input_endpoint.topic,
                        output_endpoint.topic, flow.status, flow.confidence,
                        flow.path, flow.start_line, flow.id,
                    ])
    return rows


def _workbook_xml(sheet_names: list[str]) -> str:
    sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheets}</sheets></workbook>"
    )


def write_debug_xlsx(
    destination: Path,
    endpoints_by_service: dict[str, list[MessageEndpoint]],
    methods: list[IntegrationMethod],
    flows: list[CodeFlow],
) -> None:
    """Export indexed ports and source-backed internal flows to an XLSX file."""
    summary_rows, summary_styles = _summary_rows(endpoints_by_service, methods, flows)
    sheets: dict[str, list[list[object]]] = {
        "Synthèse": summary_rows,
        "Ports": _port_detail_rows(endpoints_by_service, methods),
        "Flux internes": _flow_detail_rows(endpoints_by_service, methods, flows),
        "Légende": [
            ["Champ", "Signification"],
            ["Topic", "Renseigné uniquement pour Kafka."],
            ["Route HTTP", "Renseignée uniquement pour REST."],
            ["Classe Java", "Classe associée à l’endpoint par l’indexation AST."],
            ["Flux internes", "Flux de code persisté avec un endpoint IN et un endpoint OUT."],
            ["Vide", "Le fait correspondant n’a pas été résolu statiquement."],
        ],
    }
    rels = "".join(
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(sheets) + 1)
    ) + (
        f'<Relationship Id="rId{len(sheets) + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    content_types = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, len(sheets) + 1)
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
        "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            f"{content_types}</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        archive.writestr("xl/workbook.xml", _workbook_xml(list(sheets)))
        archive.writestr(
            "xl/styles.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="Arial"/></font>'
            '<font><b/><sz val="11"/><name val="Arial"/></font></fonts>'
            '<fills count="5"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FFFFC000"/></patternFill></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FFFF6666"/></patternFill></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FFC6EFCE"/></patternFill></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="6"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0"/>'
            '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" applyAlignment="1">'
            '<alignment wrapText="1" vertical="top"/></xf>'
            '<xf numFmtId="0" fontId="0" fillId="2" borderId="0" applyAlignment="1">'
            '<alignment wrapText="1" vertical="top"/></xf>'
            '<xf numFmtId="0" fontId="0" fillId="3" borderId="0" applyAlignment="1">'
            '<alignment wrapText="1" vertical="top"/></xf>'
            '<xf numFmtId="0" fontId="0" fillId="4" borderId="0" applyAlignment="1">'
            '<alignment wrapText="1" vertical="top"/></xf></cellXfs></styleSheet>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}</Relationships>",
        )
        widths_by_sheet = {
            "Synthèse": [28, 16, 12, 12, 16, 70],
            "Ports": [28, 12, 12, 28, 24, 45, 32, 8, 18],
            "Flux internes": [28, 28, 28, 40, 40, 14, 14, 32, 8, 18],
            "Légende": [24, 90],
        }
        for index, (name, rows) in enumerate(sheets.items(), 1):
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                _sheet_xml(
                    rows,
                    widths_by_sheet[name],
                    summary_styles if name == "Synthèse" else None,
                ),
            )
