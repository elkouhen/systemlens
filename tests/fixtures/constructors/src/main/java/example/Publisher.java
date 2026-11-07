package example;

import org.springframework.kafka.core.KafkaTemplate;

class Publisher {
    private final Audit audit = new Audit();
    private static final int LIMIT = Limits.limit();

    Publisher(KafkaTemplate<String, String> template) {
        publish(template);
    }

    Publisher() {
        this(null);
    }

    void publish(KafkaTemplate<String, String> template) {
        template.send("orders", "created");
    }
}
