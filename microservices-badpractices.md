# Microservices bad practices

Architecture review signals, to assess against the required consistency and failure handling.

| Practice | Main risk |
|---|---|
| Reading data synchronously after requesting an asynchronous write, in a causally related flow, without confirming visibility | The read may return the previous value. |
| Updating a database and publishing an event without coordinating recovery from partial failure | The data changes but its event is lost, or an event describes an uncommitted change. |
| Processing redelivered messages without idempotency | The same operation can charge, reserve, or notify twice. |
| Assuming a global event order across Kafka partitions | Consumers can apply dependent updates in the wrong order. |
| Accessing another service's private tables directly | Changes bypass its business rules and couple consumers to its schema. |
| Chaining synchronous calls without timeouts or an overall deadline | A slow dependency can hold resources throughout the call chain. |
| Retrying at several layers without bounded attempts and backoff | Retries multiply load during an outage. |
| Changing API or event contracts incompatibly while consumers still use the old contract | Independently deployed services fail to communicate. |
