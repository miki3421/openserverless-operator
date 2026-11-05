#!/bin/sh
set -eu
export GOTOOLCHAIN=local CGO_ENABLED=0 GOWORK=off
test "$(go env GOVERSION)" = go1.27.3 || { echo "Go 1.27.3 is required" >&2; exit 1; }
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
out=${1:?output directory required}
mkdir -p "$out"
out=$(CDPATH= cd -- "$out" && pwd)
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT HUP INT TERM
GOMODCACHE=$(go env GOMODCACHE)
export GOMODCACHE

# Download immutable Go module versions and verify source archive + go.mod sums.
fetch() {
  module=$1; version=$2; sum=$3; modsum=$4; name=$5
  go mod download -json "$module@$version" > "$scratch/receipt.json"
  grep -F "\"Sum\": \"$sum\"" "$scratch/receipt.json" >/dev/null
  grep -F "\"GoModSum\": \"$modsum\"" "$scratch/receipt.json" >/dev/null
  cp -a "$GOMODCACHE/$module@$version" "$scratch/$name"
  chmod -R u+w "$scratch/$name"
}
fetch 'sigs.k8s.io/kustomize/kustomize/v5' 'v5.8.3' 'h1:SJXKgCEb4Ru+8PrhwQ+ORAASc2rPWBneHjh4fKTXPWY=' 'h1:goZZjpeux35r4u4E3BPtetYBYcj6Vi5xQtpABCPF9Nk=' 'kustomize'
fetch 'github.com/go-task/task/v3' 'v3.54.0' 'h1:xDgD9BNHIDnHhemB3gumYb+ozUoa6oCFMIz44GGosj8=' 'h1:oOHzpL1r0bga9Xq1dC/8kucE1Y4fxSMTlKKFpNi6mFc=' 'task'
fetch 'github.com/apache/openwhisk-cli' 'v0.0.0-20210326003826-7c47ef1dbad5' 'h1:n+vlRWZxFVArds8OemiSnDs392VFApXhPMO563Q4gtM=' 'h1:gWtJgsIx6n8yb3aUz6iRuSn8LiEZLbrmRJiE/DtQnFI=' 'wsk'
fetch 'github.com/jteeuwen/go-bindata' 'v3.0.7+incompatible' 'h1:91Uy4d9SYVr1kyTJ15wJsog+esAZZl7JmEfTkwmhJts=' 'h1:JVvhzYOiGBnFSYRyV00iY8q7/0PThjIYav1p9h5dmKs=' 'bindata'

for name in kustomize task wsk; do
  cp "$root/$name/go.mod" "$root/$name/go.sum" "$scratch/$name/"
done
cp -a "$root/kubectl" "$scratch/kubectl"
cd "$scratch/kubectl"
go build -p 4 -mod=readonly -trimpath -buildvcs=false -ldflags '-s -w -X k8s.io/client-go/pkg/version.gitVersion=v1.36.6-adv.1 -X k8s.io/component-base/version.gitVersion=v1.36.6-adv.1 -X k8s.io/component-base/version.gitMajor=1 -X k8s.io/component-base/version.gitMinor=36 -X k8s.io/component-base/version.gitCommit=1b2171b110bb2a2542e1322035c6583ec86eb976 -X k8s.io/component-base/version.gitTreeState=dirty' -o "$out/kubectl" .
cd "$scratch/kustomize"
go build -p 4 -mod=readonly -trimpath -buildvcs=false -ldflags '-s -w -X sigs.k8s.io/kustomize/api/provenance.version=v5.8.3-adv.1' -o "$out/kustomize" .
cd "$scratch/task"
go build -p 4 -mod=readonly -trimpath -buildvcs=false -ldflags '-s -w -X github.com/go-task/task/v3/internal/version.version=3.54.0-adv.1' -o "$out/task" ./cmd/task
cd "$scratch/bindata"
printf 'module github.com/jteeuwen/go-bindata\n\ngo 1.27.0\n' > go.mod
go build -p 4 -mod=readonly -trimpath -buildvcs=false -o "$scratch/go-bindata" ./go-bindata
cd "$scratch/wsk"
"$scratch/go-bindata" -pkg wski18n -o wski18n/i18n_resources.go wski18n/resources
go build -p 4 -mod=readonly -trimpath -buildvcs=false -ldflags '-s -w -X main.CLI_BUILD_TIME=1.2.0-adv.1' -o "$out/wsk" .
for tool in kubectl kustomize wsk task; do
  go version -m "$out/$tool"
done
