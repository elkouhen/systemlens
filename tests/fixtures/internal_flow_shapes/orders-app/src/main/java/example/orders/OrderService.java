package example.orders;

import org.springframework.stereotype.Service;

@Service
public class OrderService {
    private final PaymentClient paymentClient;

    public OrderService(PaymentClient paymentClient) {
        this.paymentClient = paymentClient;
    }

    public void create(String body) {
        paymentClient.charge(body);
    }
}
