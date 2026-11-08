package example.orders;

import org.springframework.stereotype.Component;

@Component
public class BinaryHandler implements MessageHandler<byte[]> {
    private final PaymentClient paymentClient;

    public BinaryHandler(PaymentClient paymentClient) {
        this.paymentClient = paymentClient;
    }

    @Override
    public void handle(byte[] message) {
        paymentClient.charge(new String(message));
    }
}
