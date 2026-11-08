package example.shared;

import org.springframework.kafka.core.KafkaTemplate;

public class SharedNotifier implements Notifier {
    private final KafkaTemplate<String, String> kafkaTemplate;

    public SharedNotifier(KafkaTemplate<String, String> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    @Override
    public void announce(String message) {
        kafkaTemplate.send("shared-notices", message);
    }
}
