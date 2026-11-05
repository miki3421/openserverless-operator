# Qualified Advanced operator tools

This directory builds only the four operator CLIs. It does not install or upgrade a cluster. No task, operator, API or datastore source behavior is patched by this refresh.

| Tool | Upstream source | Local version |
| --- | --- | --- |
| kubectl | Kubernetes v1.36.6 entrypoint; k8s.io modules v0.36.6 | v1.36.6-adv.1 |
| kustomize | sigs.k8s.io/kustomize/kustomize/v5 v5.8.3 | v5.8.3-adv.1 |
| wsk | Apache OpenWhisk CLI 1.2.0, commit 7c47ef1dbad5 (Go pseudo-version recorded in sources.json) | 1.2.0-adv.1 |
| task | github.com/go-task/task/v3 v3.54.0 | 3.54.0-adv.1 |

All four binaries use Go 1.27.3 with CGO disabled and a Go 1.27 module directive, preserving current standard-library defaults rather than opting into legacy runtime compatibility defaults. Local suffixes distinguish these builds from official binaries.

kubectl's main.go is copied from Kubernetes commit 1b2171b110bb2a2542e1322035c6583ec86eb976, cmd/kubectl/kubectl.go. Its dependencies use the published staging modules, avoiding a copy of the Kubernetes monorepo. Client metadata reports upstream source provenance with gitTreeState=dirty to reflect the local dependency rebuild. The zero build timestamp is intentional for reproducibility. Both version components remain numeric.

The dependency locks raise golang.org/x/net to v0.60.0 and golang.org/x/text to v0.42.0 for kubectl, and go-jose/v4 to v4.1.6 plus x/net to v0.60.0 for task. Wsk retains its upstream CLI/client behavior. Its translations are generated with the pinned go-bindata v3.0.7 source before compiling; no Gradle or unpinned go get step is required.

## Build

Use Go 1.27.3, or the pinned builder in Dockerfile.go-tools. Run from any directory:

```sh
/path/to/operator/tools/go-tools/build.sh /absolute/output-directory
```

build.sh verifies upstream module archive and go.mod checksums before copying sources, overlays the committed go.mod/go.sum locks and builds with -mod=readonly. It neither upgrades dependencies nor disables checksum verification. Module downloads require network for an empty cache; a fully populated verified module cache supports an offline build. Temporary source copies are removed by the script. The caller owns the output and Go caches.

The regular operator Dockerfile and Dockerfile.go-tools use this same build script. Dockerfile.go-tools preserves an independently verified existing operator base. Dockerfile.go-tools-qualification accepts only already-built, independently hashed outputs as its build context; it is the incremental recipe used in the Advanced laboratory.

## Qualification boundaries

See [QUALIFICATION.md](QUALIFICATION.md) for the exact deployed artifact, tests, scanner comparison and remaining limitations.

The validated base is localhost/ops-advanced-operator:20261105-r4, OCI manifest sha256:ca260c836f5568d9070d93da780a85069334793d24ff0f6e9c105c987b70e630. Verify that identity before rebuilding an incremental image. The new image tag is localhost/ops-advanced-operator:20261105-r5. Runtime qualification is AMD64 against the existing Advanced K3s 1.36 lab, not ARM64, a fresh installation or production. kubectl must remain within Kubernetes' supported client/server minor-version skew; this image is not an implicit support claim for legacy clusters.

The compatibility tests exercise actual installed CLIs using synthetic inputs: kubectl client JSON/JSONPath and version metadata; kustomize patch, image, namespace, generator and private-service behavior; task includes/variables/error propagation; wsk HTTP authentication and denial behavior. Existing operator tests are retained, including their known fixture failures. No failed or skipped coverage is relabeled as PASS.

Upstream references:
- https://github.com/kubernetes/kubernetes/blob/v1.36.6/cmd/kubectl/kubectl.go
- https://github.com/kubernetes-sigs/kustomize/releases/tag/kustomize%2Fv5.8.3
- https://github.com/apache/openwhisk-cli/releases/tag/1.2.0
- https://github.com/go-task/task/releases/tag/v3.54.0
- https://kubernetes.io/releases/version-skew-policy/
