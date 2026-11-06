package m;
public class Use {
    public String go(User u) {
        u.setName("x");
        return u.getName() + u.describe();
    }
}
