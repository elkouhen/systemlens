package example.orders;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

// s17, s19: receivers narrowed by a ternary and by a call context.
@RestController
public class ExportController {
    private final CsvExporter csvExporter;
    private final JsonExporter jsonExporter;

    public ExportController(CsvExporter csvExporter, JsonExporter jsonExporter) {
        this.csvExporter = csvExporter;
        this.jsonExporter = jsonExporter;
    }

    @PostMapping("/export/ternary")
    public void ternary(@RequestBody String message) {
        Exporter chosen = message.isEmpty() ? csvExporter : jsonExporter;
        chosen.export(message);
    }

    @PostMapping("/export/helper")
    public void helper(@RequestBody String message) {
        exportWith(csvExporter, message);
    }

    private void exportWith(Exporter exporter, String message) {
        exporter.export(message);
    }
}
