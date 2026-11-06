package example;
class Worker implements Runnable {
  private Generator generator;
  public void run() { generator.generate(); }
}
