package example.orders;

import example.shared.Notifier;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

// BL-51: plugin-lib also implements Notifier, out of this module's reach.
@RestController
public class NoticeController {
    private final Notifier notifier;

    public NoticeController(Notifier notifier) {
        this.notifier = notifier;
    }

    @PostMapping("/notices")
    public void send(@RequestBody String message) {
        notifier.announce(message);
    }
}
