package example.orders;

import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.jms.annotation.JmsListener;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

// Entry triggers other than REST, Kafka and a cron schedule.
@Component
public class MessagingEntries {
    private final EventPublisher eventPublisher;

    public MessagingEntries(EventPublisher eventPublisher) {
        this.eventPublisher = eventPublisher;
    }

    @RabbitListener(queues = "rabbit-in")
    public void onRabbit(String message) {
        eventPublisher.publishProcessed(message);
    }

    @JmsListener(destination = "jms-in")
    public void onJms(String message) {
        eventPublisher.publishProcessed(message);
    }

    @Scheduled(fixedDelay = 5000)
    public void tick() {
        eventPublisher.publishProcessed("tick");
    }
}
