package example.orders;

import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/products")
public class ProductController extends AbstractCrudController {
    private final PaymentClient paymentClient;

    public ProductController(PaymentClient paymentClient) {
        this.paymentClient = paymentClient;
    }

    @Override
    protected PaymentClient client() {
        return paymentClient;
    }
}
