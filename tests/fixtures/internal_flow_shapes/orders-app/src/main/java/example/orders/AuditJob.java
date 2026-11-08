package example.orders;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class AuditJob extends BaseJob {
    private final EventPublisher eventPublisher;

    public AuditJob(EventPublisher eventPublisher) {
        this.eventPublisher = eventPublisher;
    }

    @Scheduled(cron = "0 15 * * * *")
    public void trigger() {
        execute();
    }

    @Override
    protected void doRun() {
        eventPublisher.publishProcessed("audit");
    }
}
