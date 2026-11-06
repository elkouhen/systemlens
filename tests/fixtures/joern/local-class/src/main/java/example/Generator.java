package example;
import org.springframework.kafka.core.KafkaTemplate;
class Generator {
  private KafkaTemplate<Long, String> template;
  void generate() { template.send("orders", "new"); }
}
