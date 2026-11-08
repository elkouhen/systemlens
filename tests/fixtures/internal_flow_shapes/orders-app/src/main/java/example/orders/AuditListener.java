package example.orders;

import org.springframework.jms.core.JmsTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionalEventListener;

// A listener naming its event type in the annotation.
@Component
public class AuditListener {
    private final JmsTemplate jmsTemplate;

    public AuditListener(JmsTemplate jmsTemplate) {
        this.jmsTemplate = jmsTemplate;
    }

    @TransactionalEventListener(classes = OrderCreatedEvent.class)
    public void audit() {
        jmsTemplate.convertAndSend("audit-events", "created");
    }
}
