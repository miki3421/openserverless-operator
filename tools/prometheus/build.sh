#!/usr/bin/env bash
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
# Run from the operator root; all derived files stay in the supplied directories.
set -euo pipefail
if [[ $# != 2 || $(uname -m) != x86_64 ]]; then
  echo 'Usage (qualified AMD64 host): tools/prometheus/build.sh EXISTING_WORK_DIRECTORY EXISTING_GO_CACHE' >&2
  exit 2
fi
work=$(realpath "$1"); cache=$(realpath "$2")
test -d "$work" && test -d "$cache" && test ! -e "$work/source"
archive="$work/upstream.tar.gz"
ui="$work/ui.tar.gz"
if [[ ! -f "$archive" ]]; then
  curl --fail --location --proto '=https' --tlsv1.2 \
    https://codeload.github.com/prometheus/prometheus/tar.gz/e2e28adeee2c5883d000fc82aca6008cfbf7e96d -o "$archive"
fi
if [[ ! -f "$ui" ]]; then
  curl --fail --location --proto '=https' --tlsv1.2 \
    https://github.com/prometheus/prometheus/releases/download/v3.16.0/prometheus-web-ui-3.16.0.tar.gz -o "$ui"
fi
printf '%s  %s\n' 5691de55569c79ac40b5ddef0336cfd13f8b50ae85e541f401af16be45b9a270 "$archive" | sha256sum -c -
printf '%s  %s\n' 000a45231f6dd22a5703218aad3ee11da125c8a44d1824a27670416c07531001 "$ui" | sha256sum -c -
printf '%s  %s\n' bd53be42c0233a74eb28df1cd92b4c6b61bd524b9ddb293da8d700baf02d4836 tools/prometheus/dependencies.patch | sha256sum -c -
mkdir "$work/source" "$work/ui" "$work/out"
tar -xzf "$archive" --strip-components=1 --no-same-owner -C "$work/source"
tar -xzf "$ui" --no-same-owner -C "$work/ui"
patch --batch --fuzz=0 -d "$work/source" -p1 < tools/prometheus/dependencies.patch
(
  cd "$work/source"
  PREBUILT_ASSETS_STATIC_DIR="$work/ui/static" bash scripts/compress_assets.sh
)
# Upstream's workspace includes tooling modules. Resolve their checksum file
# before making the source read-only; keep workspace version selection intact.
docker run --rm --pull=never --read-only --cap-drop ALL --security-opt no-new-privileges \
  --memory 10g --cpus 4 --pids-limit 1024 --tmpfs /tmp:rw,exec,nosuid,size=2g \
  -v "$work/source:/src" -v "$cache:/cache" \
  -e GOPATH=/cache/gopath -e GOMODCACHE=/cache/mod -e GOCACHE=/cache/build \
  -e GOTOOLCHAIN=local -e GOMAXPROCS=4 -e CGO_ENABLED=0 -w /src \
  golang:1.27.3-bookworm@sha256:b08bc99468058b6d8a0ada17fa54996b910d5e82a7e95bee0372e3bd98415258 \
  sh -ec 'go list -mod=readonly -tags netgo,builtinassets -deps ./cmd/prometheus ./cmd/promtool >/dev/null; go mod verify'
docker run --rm --pull=never --read-only --cap-drop ALL --security-opt no-new-privileges \
  --memory 10g --cpus 4 --pids-limit 1024 --tmpfs /tmp:rw,exec,nosuid,size=4g \
  -v "$work/source:/src:ro" -v "$work/out:/out" -v "$cache:/cache" \
  -e GOPATH=/cache/gopath -e GOMODCACHE=/cache/mod -e GOCACHE=/cache/build \
  -e GOTOOLCHAIN=local -e GOMAXPROCS=4 -e CGO_ENABLED=0 -w /src \
  golang:1.27.3-bookworm@sha256:b08bc99468058b6d8a0ada17fa54996b910d5e82a7e95bee0372e3bd98415258 \
  sh -ec 'go mod verify
    go build -mod=readonly -trimpath -tags netgo,builtinassets -ldflags "-s -w -X github.com/prometheus/common/version.Version=3.16.0-adv.1 -X github.com/prometheus/common/version.Revision=e2e28adeee2c5883d000fc82aca6008cfbf7e96d+ops-xnet062 -X github.com/prometheus/common/version.Branch=advanced -X github.com/prometheus/common/version.BuildUser=OPS -X github.com/prometheus/common/version.BuildDate=20261106" -o /out/ ./cmd/prometheus ./cmd/promtool
    go mod verify
    go version -m /out/prometheus /out/promtool > /out/buildinfo.txt'
sha256sum "$work/out/prometheus" "$work/out/promtool"
chmod 0755 "$work/out/prometheus" "$work/out/promtool"
cp tools/prometheus/Dockerfile "$work/out/Dockerfile"
docker build --pull=false -t localhost/ops-advanced-prometheus:3.16.0-20261106-r1 "$work/out"
