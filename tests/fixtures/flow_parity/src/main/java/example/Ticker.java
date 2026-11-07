package example;

import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.scheduling.annotation.Scheduled;

class Ticker {
    private KafkaTemplate<String, String> template;
    private Notifier notifier;

    @Scheduled(cron = "0 * * * * *")
    void tick() {
        template.send("ticks", "tick");
        notifier.notifyCreated(template);
    }
}
