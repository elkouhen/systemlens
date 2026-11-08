package example.orders;

import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

@Component
public class OrderCreatedListener {
    private final EventPublisher eventPublisher;

    public OrderCreatedListener(EventPublisher eventPublisher) {
        this.eventPublisher = eventPublisher;
    }

    @EventListener
    public void onCreated(OrderCreatedEvent event) {
        eventPublisher.publishProcessed(event.id());
    }
}
