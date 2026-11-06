#!/usr/bin/env bash
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
set -euo pipefail
if [[ $# != 2 || $(uname -m) != x86_64 ]]; then
  echo 'Usage (qualified AMD64 host): tools/alertmanager/build.sh EXISTING_WORK_DIRECTORY EXISTING_GO_CACHE' >&2
  exit 2
fi
work=$(realpath "$1"); cache=$(realpath "$2")
test -d "$work" && test -d "$cache" && test ! -e "$work/source"
archive="$work/upstream.tar.gz"; ui="$work/ui.tar.gz"
if [[ ! -f "$archive" ]]; then
  curl --fail --location --proto '=https' --tlsv1.2 \
    https://codeload.github.com/prometheus/alertmanager/tar.gz/e8349048265ccc0871c3d0d668b05eacc68baf50 -o "$archive"
fi
if [[ ! -f "$ui" ]]; then
  curl --fail --location --proto '=https' --tlsv1.2 \
    https://github.com/prometheus/alertmanager/releases/download/v0.34.2/alertmanager-web-ui-0.34.2.tar.gz -o "$ui"
fi
printf '%s  %s\n' 81e69f87954aedb557ed67d9ef0218a1c0c9b9e0c3184c9ddffe07bd247239fc "$archive" | sha256sum -c -
printf '%s  %s\n' 3d6b13c5af094724e0bed8f757399e08af44d40d6c6cd87392a1f8ee6ed1e457 "$ui" | sha256sum -c -
printf '%s  %s\n' fc80ab27ce2863aaab92586d99a2cefe91863df91a203dba89974dea2fc9f4ab tools/alertmanager/dependencies.patch | sha256sum -c -
mkdir "$work/source" "$work/out"
tar -xzf "$archive" --strip-components=1 --no-same-owner -C "$work/source"
tar -xzf "$ui" --no-same-owner -C "$work/source/ui/app"
patch --batch --fuzz=0 -d "$work/source" -p1 < tools/alertmanager/dependencies.patch
docker run --rm --pull=never --read-only --cap-drop ALL --security-opt no-new-privileges \
  --memory 8g --cpus 4 --pids-limit 1024 --tmpfs /tmp:rw,exec,nosuid,size=4g \
  -v "$work/source:/src:ro" -v "$work/out:/out" -v "$cache:/cache" \
  -e GOPATH=/cache/gopath -e GOMODCACHE=/cache/mod -e GOCACHE=/cache/build \
  -e GOTOOLCHAIN=local -e GOMAXPROCS=4 -e CGO_ENABLED=0 -w /src \
  golang:1.27.3-bookworm@sha256:b08bc99468058b6d8a0ada17fa54996b910d5e82a7e95bee0372e3bd98415258 \
  sh -ec 'go mod verify
    go build -mod=readonly -trimpath -tags netgo -ldflags "-s -w -X github.com/prometheus/common/version.Version=0.34.2-adv.1 -X github.com/prometheus/common/version.Revision=e8349048265ccc0871c3d0d668b05eacc68baf50+ops-xnet062 -X github.com/prometheus/common/version.Branch=advanced -X github.com/prometheus/common/version.BuildUser=OPS -X github.com/prometheus/common/version.BuildDate=20261106" -o /out/ ./cmd/alertmanager ./cmd/amtool
    go mod verify
    go version -m /out/alertmanager /out/amtool > /out/buildinfo.txt'
sha256sum "$work/out/alertmanager" "$work/out/amtool"
chmod 0755 "$work/out/alertmanager" "$work/out/amtool"
cp tools/alertmanager/Dockerfile "$work/out/Dockerfile"
docker build --pull=false -t localhost/ops-advanced-alertmanager:0.34.2-20261106-r1 "$work/out"
