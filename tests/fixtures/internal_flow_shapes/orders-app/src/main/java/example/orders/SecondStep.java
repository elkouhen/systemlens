package example.orders;

import org.springframework.stereotype.Component;

@Component
public class SecondStep extends BaseStep {
    private final EventPublisher eventPublisher;

    public SecondStep(EventPublisher eventPublisher) {
        this.eventPublisher = eventPublisher;
    }

    @Override
    protected void doStep(String message) {
        eventPublisher.publishProcessed(message);
    }
}
