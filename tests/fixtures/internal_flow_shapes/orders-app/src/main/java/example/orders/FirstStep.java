package example.orders;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class FirstStep extends BaseStep {
    private final SecondStep secondStep;

    public FirstStep(SecondStep secondStep) {
        this.secondStep = secondStep;
    }

    @PostMapping("/steps")
    public void entry(@RequestBody String message) {
        secondStep.execute(message);
    }

    @Override
    protected void doStep(String message) {
        message.length();
    }
}
