package example.orders;

import org.springframework.stereotype.Service;

@Service
public class PingService {
    public int compute(int value) {
        return MathHelper.square(value);
    }
}
