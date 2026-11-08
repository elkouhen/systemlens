package example.orders;

import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

@Component
public class JsonExporter implements Exporter {
    private final KafkaTemplate<String, String> kafkaTemplate;

    public JsonExporter(KafkaTemplate<String, String> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    @Override
    public void export(String data) {
        kafkaTemplate.send("export-json", data);
    }
}
