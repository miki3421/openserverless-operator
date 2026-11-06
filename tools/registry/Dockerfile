# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
# build.sh binds the source, dependency patch and Go compiler.
FROM docker.io/library/registry:3.1.2@sha256:38907510915c3f1604c7e83d6ab602ab3020d092f73bdaed8696773693ff768c
COPY registry /bin/registry
LABEL org.opencontainers.image.source="https://github.com/distribution/distribution" \
      org.opencontainers.image.revision="3220848f15d9279c66a41aa0a257469e6aede1e9+8a358633" \
      org.opencontainers.image.version="3.1.2-adv.1"
