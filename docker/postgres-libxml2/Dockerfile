# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
# Reuse the source-qualified Bookworm backport without replacing PostgreSQL.
ARG SECURITY_PACKAGES_IMAGE=docker.io/miki3421/ops-advanced-bookworm-security:20261109-r1@sha256:65c238b1d8e0463e350c1bb815fcbd830f175f0991247a6b59b9ca226e39c582
FROM ${SECURITY_PACKAGES_IMAGE} AS security-packages

FROM docker.io/pgvector/pgvector:0.8.6-pg18-bookworm@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a
COPY --from=security-packages /out/libxml2/ /tmp/ops-libxml2/
RUN set -eu; \
    test "$(dpkg-query -W -f='${Version}' libxml2)" = '2.9.14+dfsg-1.3~deb12u6'; \
    cd /tmp/ops-libxml2; sha256sum -c SHA256SUMS; \
    dpkg -i libxml2_2.9.14+dfsg-1.3~deb12u6+0ops1_amd64.deb; \
    test "$(dpkg-query -W -f='${Version}' libxml2)" = '2.9.14+dfsg-1.3~deb12u6+0ops1'; \
    mkdir -p /usr/share/ops-security/libxml2; \
    cp SOURCE SHA256SUMS patched-source.sha256 libxml2-cve-2026-6653.patch \
       copyright changelog test-build.log runtest.log remaining-tests.log \
       test-summary.txt ./*.dsc /usr/share/ops-security/libxml2/; \
    rm -r /tmp/ops-libxml2
