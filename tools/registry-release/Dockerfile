# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
# Registry-only layer over the qualified fresh-install AMD64 candidate.
FROM localhost/ops-advanced-operator:20261106-ksm-xnet-fresh-r1@sha256:86ecae31b1dd76fa7a06c4f1e3f62ca7bd74c84c61a96f80fff517e3d5d0243c
ENV OPERATOR_IMAGE=docker.io/miki3421/ops-advanced-operator
ENV OPERATOR_TAG=20261107-dockerhub-r1
COPY --chown=1001:1001 openserverless/files/prometheus-profile.json openserverless/files/alertmanager-profile.json /home/openserverless/openserverless/files/
COPY --chown=1001:1001 openserverless/templates/kafka-001-sts.yaml /home/openserverless/openserverless/templates/
COPY --chown=1001:1001 deploy/monitoring/prometheus-03-dep.yaml deploy/monitoring/kubestate-03-dep.yaml /home/openserverless/deploy/monitoring/
COPY --chown=1001:1001 deploy/alert-manager/alertmanager-03-dep.yaml /home/openserverless/deploy/alert-manager/
COPY --chown=1001:1001 deploy/registry/02-registry-sts.yaml /home/openserverless/deploy/registry/
COPY --chown=1001:1001 deploy/kafka/kafka-001-sts.yaml /home/openserverless/deploy/kafka/
COPY --chown=1001:1001 tools/registry-release/refresh_archive.py tools/registry-release/archive-inputs.json /tmp/
RUN python -B /tmp/refresh_archive.py && rm /tmp/refresh_archive.py /tmp/archive-inputs.json
