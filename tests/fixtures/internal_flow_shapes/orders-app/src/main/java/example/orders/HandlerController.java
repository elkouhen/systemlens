package example.orders;

import java.util.Map;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

// s3, s4: dispatch decided by a generic type argument.
@RestController
public class HandlerController {
    private final Map<String, MessageHandler<?>> handlers;
    private final MessageHandler<String> textHandler;

    public HandlerController(Map<String, MessageHandler<?>> handlers, MessageHandler<String> textHandler) {
        this.handlers = handlers;
        this.textHandler = textHandler;
    }

    @PostMapping("/handlers/registry")
    @SuppressWarnings("unchecked")
    public void registry(@RequestBody String message) {
        MessageHandler<String> handler = (MessageHandler<String>) handlers.get("text");
        handler.handle(message);
    }

    @PostMapping("/handlers/field")
    public void field(@RequestBody String message) {
        textHandler.handle(message);
    }
}
