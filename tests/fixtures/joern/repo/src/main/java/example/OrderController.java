package example;

import java.util.List;

public class OrderController {
    private final OrderService service;
    private final Repo repo = new RepoImpl();

    public OrderController(OrderService service) {
        this.service = service;
        init();
    }

    private void init() {}

    public void create(String id) {
        service.place(id);
        repo.save(id);
        List.of(id).forEach(x -> helper(x));
        Runnable r = new Runnable() { public void run() { helper("a"); } };
        unknown.Thing.call();
        Nested.go();
    }

    void helper(String x) { Util.log(x); }

    static class Nested { static void go() {} }
}
