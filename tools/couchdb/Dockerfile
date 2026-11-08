# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
# Keep the qualified CouchDB/Erlang payload, entrypoint and data format intact.
FROM docker.io/apache/couchdb:3.5.2@sha256:c703989c0a370a1a6b179785bcb5b2b5501347bb0219d02d597d0a9107eb8d28
RUN set -eux; \
    test "$(dpkg --print-architecture)" = amd64; \
    apt-get update; \
    apt-get install --yes --no-install-recommends --only-upgrade \
      libbrotli1=1.1.0-2+deb13u1 perl-base=5.40.1-6+deb13u1; \
    test "$(dpkg-query -W -f='${Version}' libbrotli1)" = 1.1.0-2+deb13u1; \
    test "$(dpkg-query -W -f='${Version}' perl-base)" = 5.40.1-6+deb13u1; \
    rm -rf /var/lib/apt/lists/*
