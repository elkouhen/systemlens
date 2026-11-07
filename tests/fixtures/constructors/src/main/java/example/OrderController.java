package example;

import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/orders")
class OrderController {
    private KafkaTemplate<String, String> template;

    @PostMapping("/create")
    public void create() {
        new Publisher(template);
    }
}
