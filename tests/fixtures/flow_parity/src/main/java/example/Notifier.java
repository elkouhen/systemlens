package example;

import org.springframework.kafka.core.KafkaTemplate;

class Notifier {
    void notifyCreated(KafkaTemplate<String, String> template) {
        template.send("notifications", "created");
    }
}
