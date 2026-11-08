package example.orders;

import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

// A second inheritor of the CRUD route: its hook publishes, and only its
// own route reaches the hook (ADR-88).
@RestController
@RequestMapping("/users")
public class UserController extends AbstractCrudController {
    private final PaymentClient paymentClient;
    private final EventPublisher eventPublisher;

    public UserController(PaymentClient paymentClient, EventPublisher eventPublisher) {
        this.paymentClient = paymentClient;
        this.eventPublisher = eventPublisher;
    }

    @Override
    protected PaymentClient client() {
        return paymentClient;
    }

    @Override
    protected void afterCreate(String body) {
        eventPublisher.publishProcessed(body);
    }
}
