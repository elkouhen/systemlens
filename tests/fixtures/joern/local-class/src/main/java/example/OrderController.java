package example;
import org.springframework.web.bind.annotation.*;
import java.util.function.Supplier;
@RestController
@RequestMapping("/orders")
class OrderController {
  private Generator generator;
  private Runnable task;
  @PostMapping("/generate") public void create() {
    class Local {
      void go() { generator.generate(); }
    }
    new Local().go();
    Supplier<Runnable> outer = () -> () -> generator.generate();
    outer.get().run();
    task.run();
  }
  void install() {
    task = new Runnable() { public void run() { generator.generate(); } };
  }
}
