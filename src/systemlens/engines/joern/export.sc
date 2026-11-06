// SystemLens export of a Java code property graph (ADR-85).
//
// Writes one JSON document with every internal method and every call that
// is not an operator. Each call lists the methods Joern's call graph links
// it to; the Python adapter decides which of them become call-graph edges.
// File paths are relative to the directory given to joern-parse.

@main def exec(cpgFile: String, outFile: String) = {
  importCpg(cpgFile)

  def line(value: Option[Int]): Int = value.getOrElse(-1)

  def methodJson(m: io.shiftleft.codepropertygraph.generated.nodes.Method) = ujson.Obj(
    "full_name" -> m.fullName,
    "name" -> m.name,
    "file" -> m.filename,
    "line" -> line(m.lineNumber.map(_.toInt)),
    "line_end" -> line(m.lineNumberEnd.map(_.toInt)),
    "external" -> m.isExternal,
    "abstract" -> m.isAbstract.nonEmpty,
  )

  val methods = cpg.method.isExternal(false).l.map(methodJson)
  val calls = cpg.call.filterNot(_.name.startsWith("<operator")).l.map { call =>
    val caller = call.method
    ujson.Obj(
      "caller" -> caller.fullName,
      "caller_file" -> caller.filename,
      "caller_line" -> line(caller.lineNumber.map(_.toInt)),
      "line" -> line(call.lineNumber.map(_.toInt)),
      "target" -> call.methodFullName,
      "dispatch" -> call.dispatchType,
      "callees" -> ujson.Arr.from(call.callee.l.map(methodJson)),
    )
  }
  val document = ujson.Obj(
    "schema" -> "systemlens-joern-v1",
    "methods" -> ujson.Arr.from(methods),
    "calls" -> ujson.Arr.from(calls),
  )
  os.write.over(os.Path(outFile), ujson.write(document))
}
