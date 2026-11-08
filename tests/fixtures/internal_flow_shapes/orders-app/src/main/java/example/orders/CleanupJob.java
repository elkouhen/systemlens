package example.orders;

import org.springframework.stereotype.Component;

@Component
public class CleanupJob extends BaseJob {
    private final PaymentClient paymentClient;

    public CleanupJob(PaymentClient paymentClient) {
        this.paymentClient = paymentClient;
    }

    @Override
    protected void doRun() {
        paymentClient.charge("cleanup");
    }
}
