# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
# Preserve the qualified PostgreSQL/pgvector/libxml2 image and its data format.
ARG BASE_IMAGE=docker.io/miki3421/ops-advanced-postgres@sha256:eece1d6ad7208838a4eaa56beef57bc82db2e7ff17b7fcce23dbcf29740cf5b9
FROM ${BASE_IMAGE}

# Inputs are verified against Debian's signed bookworm-security index before
# the build. There is no network access or package resolution during the build.
COPY SHA256SUMS preserved.sha256 SOURCE.json /tmp/adv-perl/
COPY vendor/ /tmp/adv-perl/
RUN set -eu; \
    test "$(dpkg --print-architecture)" = amd64; \
    cd /tmp/adv-perl; \
    sha256sum -c SHA256SUMS; \
    sha256sum -c preserved.sha256; \
    for package in perl perl-base perl-modules-5.36 libperl5.36; do \
        test "$(dpkg-query -W -f='${Version}' "$package")" = '5.36.0-7+deb12u3'; \
    done; \
    dpkg -i perl-base_5.36.0-7+deb12u4_amd64.deb \
        perl-modules-5.36_5.36.0-7+deb12u4_all.deb \
        libperl5.36_5.36.0-7+deb12u4_amd64.deb \
        perl_5.36.0-7+deb12u4_amd64.deb; \
    for package in perl perl-base perl-modules-5.36 libperl5.36; do \
        test "$(dpkg-query -W -f='${Version}' "$package")" = '5.36.0-7+deb12u4'; \
    done; \
    test -z "$(dpkg --audit)"; \
    sha256sum -c preserved.sha256; \
    mkdir -p /usr/share/ops-security/perl; \
    cp SHA256SUMS preserved.sha256 SOURCE.json /usr/share/ops-security/perl/; \
    rm -r /tmp/adv-perl
