// SystemLens export of a Java code property graph (ADR-85).
//
// Writes one JSON document with every internal method, every call that
// is not an operator, and the payload evidence of Strategy1 Kafka sends. Each call lists the methods Joern's call graph links
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
  // Strategy1 sends (ADR-85 amendment, ADR-5): the payload is the second
  // argument of an `envoyerMessageKafka*` call, as in the CodeQL query. The
  // adapter types it from the written declaration and the local
  // assignments of the calling method; Joern's own types are erased.
  def declarationCode(argument: Any): String =
    argument match {
      case identifier: io.shiftleft.codepropertygraph.generated.nodes.Identifier =>
        identifier.refsTo.l.headOption.map(_.propertiesMap.getOrDefault("CODE", "").toString).getOrElse("")
      case _ => ""
    }
  def variableName(argument: Any): String =
    argument match {
      case identifier: io.shiftleft.codepropertygraph.generated.nodes.Identifier => identifier.name
      case _ => ""
    }
  val payloadSends = cpg.call.name("envoyerMessageKafka.*").l.flatMap { call =>
    call.argument.l.find(_.argumentIndex == 2).map { payload =>
      val assignments = call.method.assignment.l.flatMap { assignment =>
        val target = assignment.argument.l.find(_.argumentIndex == 1)
        val source = assignment.argument.l.find(_.argumentIndex == 2)
        (target, source) match {
          case (Some(t), Some(v)) => Some(ujson.Obj(
            "target" -> variableName(t),
            "type" -> v.propertiesMap.getOrDefault("TYPE_FULL_NAME", "").toString,
            "variable" -> variableName(v),
          ))
          case _ => None
        }
      }
      ujson.Obj(
        "file" -> call.method.filename,
        "line" -> line(call.lineNumber.map(_.toInt)),
        "type" -> payload.propertiesMap.getOrDefault("TYPE_FULL_NAME", "").toString,
        "variable" -> variableName(payload),
        "declaration" -> declarationCode(payload),
        "assignments" -> ujson.Arr.from(assignments),
      )
    }
  }
  // `@FeignClient` interfaces and their direct supertypes (ADR-81 rule 3):
  // Spring implements them with a runtime proxy that no source body is.
  val proxiedTypes = cpg.typeDecl.l
    .filter(_.annotation.name("FeignClient").nonEmpty)
    .flatMap(declaration => declaration.fullName :: declaration.inheritsFromTypeFullName.l)
    .distinct
  // Internal types, for ADR-81 rule 1: a concrete type whose body for a
  // call is a record accessor, a Lombok member or inherited from a library
  // is a runtime type no linked source body stands for.
  def annotationsJson(annotations: List[io.shiftleft.codepropertygraph.generated.nodes.Annotation]) =
    ujson.Arr.from(annotations.map(a => ujson.Obj("name" -> a.name, "full_name" -> a.fullName)))
  val types = cpg.typeDecl.isExternal(false).l.map { declaration =>
    ujson.Obj(
      "full_name" -> declaration.fullName,
      "file" -> declaration.filename,
      "abstract" -> declaration.modifier.modifierType.l.contains("ABSTRACT"),
      "inherits" -> ujson.Arr.from(declaration.inheritsFromTypeFullName.l),
      // Joern drops the interfaces of a record from `inherits`; its generic
      // signature still names them (`LRecord;LNamed;`).
      "signature" -> declaration.genericSignature,
      "annotations" -> annotationsJson(declaration.annotation.l),
      "fields" -> ujson.Arr.from(declaration.member.l.map { member =>
        ujson.Obj(
          "name" -> member.name,
          "type" -> member.typeFullName,
          "static" -> member.modifier.modifierType.l.contains("STATIC"),
          "annotations" -> annotationsJson(member._annotationViaAstOut.l),
        )
      }),
    )
  }
  val document = ujson.Obj(
    "schema" -> "systemlens-joern-v1",
    "methods" -> ujson.Arr.from(methods),
    "calls" -> ujson.Arr.from(calls),
    "payload_sends" -> ujson.Arr.from(payloadSends),
    "proxied_types" -> ujson.Arr.from(proxiedTypes),
    "types" -> ujson.Arr.from(types),
  )
  os.write.over(os.Path(outFile), ujson.write(document))
}
