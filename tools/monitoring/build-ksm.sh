#!/usr/bin/env bash
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
# Run from the operator root. Only the explicit work/cache directories are written.
set -euo pipefail
if [[ $# != 2 ]]; then
  echo 'Usage: tools/monitoring/build-ksm.sh EXISTING_WORK_DIRECTORY EXISTING_GO_CACHE' >&2
  exit 2
fi
work=$(realpath "$1")
cache=$(realpath "$2")
test -d "$work" && test -d "$cache"
test ! -e "$work/source"
source_commit=4ffeda2ef866b0fef372849825a803296483b336
archive="$work/upstream.tar.gz"
if [[ ! -f "$archive" ]]; then
  curl --fail --location --proto '=https' --tlsv1.2 \
    "https://codeload.github.com/kubernetes/kube-state-metrics/tar.gz/$source_commit" -o "$archive"
fi
printf '%s  %s\n' fffdaa8e6bbb0e2e9b2d07886bd70ef2f4b6507901ffb0de678b5486b6a46eba "$archive" | sha256sum -c -
printf '%s  %s\n' bc457a6285130915ad974a914e77b82c61f98500258132112c58ddd4dd62febb tools/monitoring/ksm-dependencies.patch | sha256sum -c -
mkdir "$work/source" "$work/out"
tar -xzf "$archive" --strip-components=1 --no-same-owner -C "$work/source"
patch --batch --fuzz=0 -d "$work/source" -p1 < tools/monitoring/ksm-dependencies.patch
docker run --rm --pull=never --read-only --cap-drop ALL --security-opt no-new-privileges \
  --memory 10g --cpus 4 --pids-limit 1024 --tmpfs /tmp:rw,exec,nosuid,size=4g \
  -v "$work/source:/src:ro" -v "$work/out:/out" -v "$cache:/cache" \
  -e GOPATH=/cache/gopath -e GOMODCACHE=/cache/mod -e GOCACHE=/cache/build \
  -e GOTOOLCHAIN=local -e GOMAXPROCS=4 -e CGO_ENABLED=0 -w /src \
  golang:1.27.3-bookworm@sha256:b08bc99468058b6d8a0ada17fa54996b910d5e82a7e95bee0372e3bd98415258 \
  sh -ec 'go mod verify
    go build -mod=readonly -trimpath -ldflags "-s -w -X github.com/prometheus/common/version.Version=2.20.0-adv.1 -X github.com/prometheus/common/version.Revision=4ffeda2ef866b0fef372849825a803296483b336+bc457a62 -X github.com/prometheus/common/version.Branch=advanced -X github.com/prometheus/common/version.BuildUser=OPS -X github.com/prometheus/common/version.BuildDate=2026-11-06 -X k8s.io/kube-state-metrics/v2/pkg/app.ClientGoVersion=v0.36.3" -o /out/kube-state-metrics .
    go version -m /out/kube-state-metrics > /out/buildinfo.txt'
sha256sum "$work/out/kube-state-metrics"
chmod 0755 "$work/out/kube-state-metrics"
cp tools/monitoring/Dockerfile.ksm "$work/out/Dockerfile"
docker build --pull=false -t localhost/ops-advanced-kube-state-metrics:2.20.0-20261106-r1 "$work/out"
