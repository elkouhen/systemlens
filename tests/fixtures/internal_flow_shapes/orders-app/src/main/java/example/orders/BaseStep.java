package example.orders;

// S24: a template method reached through a sibling subtype.
public abstract class BaseStep {
    public void execute(String message) {
        doStep(message);
    }

    protected abstract void doStep(String message);
}
