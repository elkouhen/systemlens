package example.orders;

import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

@Component
public class CsvExporter implements Exporter {
    private final KafkaTemplate<String, String> kafkaTemplate;

    public CsvExporter(KafkaTemplate<String, String> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    @Override
    public void export(String data) {
        kafkaTemplate.send("export-csv", data);
    }
}
