package example.orders;

public interface MessageHandler<T> {
    void handle(T message);
}
