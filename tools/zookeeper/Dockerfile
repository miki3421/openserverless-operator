# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
FROM eclipse-temurin:17-jre-noble@sha256:8ee89ca3f9eb6a931a2b2e8b2901a4af3554383f8077558e3631d49bb58cff1d
RUN apt-get update \
 && apt-get install -y --only-upgrade --no-install-recommends apt libapt-pkg6.0t64 \
 && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' apt)" ge 2.8.4ubuntu0.1 \
 && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libapt-pkg6.0t64)" ge 2.8.4ubuntu0.1 \
 && rm -rf /var/lib/apt/lists/*
ADD --checksum=sha256:d87db41fc53cf06966497489be6ee604b9bbd5f53871183a252371db75f7617c https://downloads.apache.org/zookeeper/zookeeper-3.9.7/apache-zookeeper-3.9.7-bin.tar.gz /tmp/zookeeper.tar.gz
RUN mkdir -p /opt/zookeeper /data /datalog /conf \
 && tar -xzf /tmp/zookeeper.tar.gz -C /opt/zookeeper --strip-components=1 \
 && rm /tmp/zookeeper.tar.gz \
 && for module in server http util io servlet security util-ajax; do \
      test -f "/opt/zookeeper/lib/jetty-${module}-9.4.58.v20250814.jar" \
      && rm "/opt/zookeeper/lib/jetty-${module}-9.4.58.v20250814.jar" || exit 1; \
    done \
 && test -f /opt/zookeeper/lib/jline-3.30.14-jdk8.jar \
 && test -f /opt/zookeeper/lib/snappy-java-1.1.10.5.jar \
 && rm /opt/zookeeper/lib/jline-3.30.14-jdk8.jar /opt/zookeeper/lib/snappy-java-1.1.10.5.jar \
 && chown 1001:1001 /data /datalog /conf
# Server-only profile: AdminServer HTTP, controller command HTTP and the optional
# Jetty Prometheus provider are unavailable. The normal NIO client and CLI remain.
# Retain upstream LICENSE/NOTICE and all dependency license notices.
ADD --checksum=sha256:73f6137bad794beefed4680dc7b6052ab2931e4a1e7fd5395c3478e7e08e6684 https://repo.maven.apache.org/maven2/org/jline/jline/3.30.15/jline-3.30.15-jdk8.jar /opt/zookeeper/lib/jline-3.30.15-jdk8.jar
ADD --checksum=sha256:1c03ce77b3f2e926c25a952612e372404d80bc88c6c8052bede964804fabd732 https://repo.maven.apache.org/maven2/org/xerial/snappy/snappy-java/1.1.10.12/snappy-java-1.1.10.12.jar /opt/zookeeper/lib/snappy-java-1.1.10.12.jar
RUN chmod 0444 /opt/zookeeper/lib/jline-3.30.15-jdk8.jar /opt/zookeeper/lib/snappy-java-1.1.10.12.jar
ENV PATH=/opt/zookeeper/bin:$PATH ZOOCFGDIR=/conf ZOO_LOG_DIR=/tmp JVMFLAGS="-Xms128m -Xmx512m"
USER 1001:1001
EXPOSE 2181
ENTRYPOINT ["zkServer.sh", "start-foreground"]
