package example.orders;

import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;

@Component
public class StartupRunner implements CommandLineRunner {
    private final EventPublisher eventPublisher;

    public StartupRunner(EventPublisher eventPublisher) {
        this.eventPublisher = eventPublisher;
    }

    @Override
    public void run(String... args) {
        eventPublisher.publishProcessed("startup");
    }
}
