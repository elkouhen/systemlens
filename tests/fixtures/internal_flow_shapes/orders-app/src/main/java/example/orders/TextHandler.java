package example.orders;

import org.springframework.stereotype.Component;

@Component
public class TextHandler implements MessageHandler<String> {
    private final EventPublisher eventPublisher;

    public TextHandler(EventPublisher eventPublisher) {
        this.eventPublisher = eventPublisher;
    }

    @Override
    public void handle(String message) {
        eventPublisher.publishProcessed(message);
    }
}
