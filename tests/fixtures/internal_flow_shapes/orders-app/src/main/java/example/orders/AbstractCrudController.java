package example.orders;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

// A route declared on an abstract base controller and not overridden.
public abstract class AbstractCrudController {
    protected abstract PaymentClient client();

    // A hook only UserController overrides with an effect.
    protected void afterCreate(String body) {
    }

    @PostMapping("/crud")
    public String create(@RequestBody String body) {
        afterCreate(body);
        return client().charge(body);
    }
}
