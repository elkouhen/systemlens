package example.orders;

import org.springframework.amqp.core.AmqpTemplate;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.jms.core.JmsTemplate;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

// Rabbit and JMS publications are effects (PDR-15).
@RestController
public class BrokerController {
    private final RabbitTemplate rabbitTemplate;
    private final AmqpTemplate amqpTemplate;
    private final JmsTemplate jmsTemplate;

    public BrokerController(RabbitTemplate rabbitTemplate, AmqpTemplate amqpTemplate, JmsTemplate jmsTemplate) {
        this.rabbitTemplate = rabbitTemplate;
        this.amqpTemplate = amqpTemplate;
        this.jmsTemplate = jmsTemplate;
    }

    // Default exchange: the routing key names the queue.
    @PostMapping("/broker/rabbit")
    public void rabbit(@RequestBody String body) {
        rabbitTemplate.convertAndSend("rabbit-in", body);
    }

    // A named exchange: the channel is exchange/routing-key.
    @PostMapping("/broker/exchange")
    public void exchange(@RequestBody String body) {
        amqpTemplate.convertAndSend("orders-exchange", "orders.created", body);
    }

    @PostMapping("/broker/jms")
    public void jms(@RequestBody String body) {
        jmsTemplate.convertAndSend("jms-in", body);
    }
}
