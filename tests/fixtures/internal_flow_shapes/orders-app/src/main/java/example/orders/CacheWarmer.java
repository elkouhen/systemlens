package example.orders;

import jakarta.annotation.PostConstruct;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;

// Startup code: a @PostConstruct method and an ApplicationRunner.
@Component
public class CacheWarmer implements ApplicationRunner {
    private final EventPublisher eventPublisher;

    public CacheWarmer(EventPublisher eventPublisher) {
        this.eventPublisher = eventPublisher;
    }

    @PostConstruct
    public void warm() {
        eventPublisher.publishProcessed("warm");
    }

    @Override
    public void run(ApplicationArguments args) {
        eventPublisher.publishProcessed("ready");
    }
}
