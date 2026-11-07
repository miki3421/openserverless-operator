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
 && chown 1001:1001 /data /datalog /conf
ENV PATH=/opt/zookeeper/bin:$PATH ZOOCFGDIR=/conf ZOO_LOG_DIR=/tmp JVMFLAGS="-Xms128m -Xmx512m"
USER 1001:1001
EXPOSE 2181
ENTRYPOINT ["zkServer.sh", "start-foreground"]
