package example.plugin;

import example.shared.Notifier;
import org.springframework.kafka.core.KafkaTemplate;

// Built on shared-lib, but orders-app does not depend on this module.
public class PluginNotifier implements Notifier {
    private final KafkaTemplate<String, String> kafkaTemplate;

    public PluginNotifier(KafkaTemplate<String, String> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    @Override
    public void announce(String message) {
        kafkaTemplate.send("plugin-notices", message);
    }
}
