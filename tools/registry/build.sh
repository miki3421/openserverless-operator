#!/usr/bin/env bash
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
set -euo pipefail
if [[ $# != 2 ]]; then
  echo 'Usage: tools/registry/build.sh EXISTING_WORK_DIRECTORY EXISTING_GO_CACHE' >&2
  exit 2
fi
work=$(realpath "$1"); cache=$(realpath "$2")
test -d "$work" && test -d "$cache" && test ! -e "$work/source"
archive="$work/upstream.tar.gz"
if [[ ! -f "$archive" ]]; then
  curl --fail --location --proto '=https' --tlsv1.2 \
    https://codeload.github.com/distribution/distribution/tar.gz/3220848f15d9279c66a41aa0a257469e6aede1e9 -o "$archive"
fi
printf '%s  %s\n' ccd8840029d2e6e1cd7c648d8fbb4f2389c73ce5f4ec5552c2e8308883b281cb "$archive" | sha256sum -c -
printf '%s  %s\n' 8a3586332c3bd56c26931727f2076eb55fc813c6040c7f1fc5f648bc8fc9a4df tools/registry/dependencies.patch | sha256sum -c -
mkdir "$work/source" "$work/out"
tar -xzf "$archive" --strip-components=1 --no-same-owner -C "$work/source"
patch --batch --fuzz=0 -d "$work/source" -p1 < tools/registry/dependencies.patch
docker run --rm --pull=never --read-only --cap-drop ALL --security-opt no-new-privileges \
  --memory 10g --cpus 4 --pids-limit 1024 --tmpfs /tmp:rw,exec,nosuid,size=4g \
  -v "$work/source:/src:ro" -v "$work/out:/out" -v "$cache:/cache" \
  -e GOPATH=/cache/gopath -e GOMODCACHE=/cache/mod -e GOCACHE=/cache/build \
  -e GOTOOLCHAIN=local -e GOMAXPROCS=4 -e CGO_ENABLED=0 -w /src \
  golang:1.27.3-bookworm@sha256:b08bc99468058b6d8a0ada17fa54996b910d5e82a7e95bee0372e3bd98415258 \
  sh -ec 'go mod verify
    go build -mod=readonly -trimpath -ldflags "-s -w -X github.com/distribution/distribution/v3/version.version=3.1.2-adv.1 -X github.com/distribution/distribution/v3/version.revision=3220848f15d9279c66a41aa0a257469e6aede1e9+8a358633 -X github.com/distribution/distribution/v3/version.mainpkg=github.com/distribution/distribution/v3" -o /out/registry ./cmd/registry
    go version -m /out/registry > /out/buildinfo.txt'
sha256sum "$work/out/registry"
chmod 0755 "$work/out/registry"
cp tools/registry/Dockerfile "$work/out/Dockerfile"
docker build --pull=false -t localhost/ops-advanced-registry:3.1.2-20261106-r1 "$work/out"
