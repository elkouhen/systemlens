package example.orders;

// S25: a template method on the entry's own base class.
public abstract class BaseJob {
    public void execute() {
        doRun();
    }

    protected abstract void doRun();
}
