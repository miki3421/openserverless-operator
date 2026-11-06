# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
# Keep upstream base, user, entrypoint and exact release UI; replace Go binaries.
FROM quay.io/prometheus/prometheus:v3.16.0@sha256:9332054523223e2ddc18d8101799d54a4a8d823b030c53216f063a6c117773cd
COPY prometheus promtool /bin/
LABEL org.opencontainers.image.revision="e2e28adeee2c5883d000fc82aca6008cfbf7e96d+ops-xnet062" \
      org.opencontainers.image.version="3.16.0-adv.1"
