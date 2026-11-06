# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
# Keep Tika's 3.x API and original non-root Java entrypoint.
FROM apache/tika:3.3.3.0-full@sha256:6ef30abd65df4b6c478907904099d0245bdb0e0686f94fb9bc7493558442bb74
USER 0
RUN apt-get update \
 && apt-get install -y --no-install-recommends --only-upgrade \
      apt=3.2.1ubuntu0.1 libapt-pkg7.0=3.2.1ubuntu0.1 libbrotli1=1.2.0-3ubuntu0.1 \
 && echo '7c893d09a9ecb877dee7ccb3523865c9811ced7b35500e5b3f0e8b11d89f306d  /usr/bin/pebble' | sha256sum -c - \
 && rm /usr/bin/pebble \
 && rm -rf /var/lib/apt/lists/* \
 && echo '4559c1fa6ca08390a800036438607e31a6763aba0ea11b928c3fce72315ecf0f  /tika-server-standard-3.3.3.jar' | sha256sum -c -
# Pebble is an unowned base-image helper. Tika starts Java directly and never
# invokes it; it is not part of the parser or OCR contract qualified here.
USER 35002:35002
LABEL org.opencontainers.image.version="3.3.3-adv.1" \
      org.opencontainers.image.source="https://github.com/apache/tika"
