package example.orders;

import org.springframework.context.ApplicationEventPublisher;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

// publishEvent continues the flow into the listeners of the event's
// static type (PDR-16).
@RestController
public class EventController {
    private final ApplicationEventPublisher publisher;

    public EventController(ApplicationEventPublisher publisher) {
        this.publisher = publisher;
    }

    @PostMapping("/events")
    public void create(@RequestBody String id) {
        publisher.publishEvent(new OrderCreatedEvent(id));
    }
}
