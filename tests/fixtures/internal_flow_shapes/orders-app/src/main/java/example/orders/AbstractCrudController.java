package example.orders;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

// A route declared on an abstract base controller and not overridden.
public abstract class AbstractCrudController {
    protected abstract PaymentClient client();

    @PostMapping("/crud")
    public String create(@RequestBody String body) {
        return client().charge(body);
    }
}
