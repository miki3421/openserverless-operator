# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
FROM docker.io/apache/kafka:4.4.0@sha256:76af3d0116abbb33b4cd964af410d4921c33d1f663e7a95d5453a9c35a84869b
USER root
RUN apk upgrade --no-cache brotli-libs libexpat musl musl-utils \
 && test -f /opt/kafka/libs/lz4-java-1.11.2.jar \
 && test -f /opt/kafka/libs/snappy-java-1.1.10.7.jar \
 && rm /opt/kafka/libs/lz4-java-1.11.2.jar /opt/kafka/libs/snappy-java-1.1.10.7.jar
ADD --checksum=sha256:58c8e0b813960d2a248e050c353baea73139b48a2ffc55382d42c69707e17325 https://repo.maven.apache.org/maven2/at/yawk/lz4/lz4-java/1.11.4/lz4-java-1.11.4.jar /opt/kafka/libs/lz4-java-1.11.4.jar
ADD --checksum=sha256:1c03ce77b3f2e926c25a952612e372404d80bc88c6c8052bede964804fabd732 https://repo.maven.apache.org/maven2/org/xerial/snappy/snappy-java/1.1.10.12/snappy-java-1.1.10.12.jar /opt/kafka/libs/snappy-java-1.1.10.12.jar
ADD --checksum=sha256:0ad843261c567d1924a017d542e14c40f2afff523d940c25ee000742fde4b3a9 https://github.com/prometheus/jmx_exporter/releases/download/1.7.0/jmx_prometheus_javaagent-1.7.0.jar /opt/ops-kafka/jmx.jar
COPY tools/kafka-kraft/start.sh tools/kafka-kraft/ready.sh tools/kafka-kraft/log4j2.properties /opt/ops-kafka/
COPY enterprise-util/kafka/jmx/prometheus-jmx-agent-config.yml /opt/ops-kafka/jmx.yml
USER root
RUN chmod 0555 /opt/ops-kafka/*.sh && chmod 0444 /opt/ops-kafka/jmx.jar /opt/ops-kafka/*.properties /opt/ops-kafka/*.yml /opt/kafka/libs/lz4-java-1.11.4.jar /opt/kafka/libs/snappy-java-1.1.10.12.jar
USER 1000:1000
ENV KAFKA_HEAP_OPTS="-Xms256m -Xmx512m" \
    KAFKA_LOG4J_OPTS="-Dlog4j2.configurationFile=/opt/ops-kafka/log4j2.properties" \
    KAFKA_JMX_OPTS="-Dcom.sun.management.jmxremote=false" \
    LOG_DIR=/tmp/ops-kafka-logs
EXPOSE 9092 9093 9071
ENTRYPOINT ["/opt/ops-kafka/start.sh"]
CMD []
