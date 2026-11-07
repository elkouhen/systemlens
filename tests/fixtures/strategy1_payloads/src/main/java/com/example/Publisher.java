package com.example;

class Publisher {
    KafkaService kafkaService;
    KafkaProperties kafkaProperties;

    void viaLocal() {
        Object event = new OrderCreated();
        kafkaService.envoyerMessageKafka(kafkaProperties.getTopics().getOrdersCreated(), event);
    }

    void viaParameter(OrderCreated created) {
        kafkaService.envoyerMessageKafka(kafkaProperties.getTopics().getOrdersCreated(), created);
    }
}
