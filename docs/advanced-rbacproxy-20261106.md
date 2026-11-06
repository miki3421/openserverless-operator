# ADV Kubegres metrics proxy qualification

The existing metrics sidecar used kube-rbac-proxy 0.14.1 with old Go and
Kubernetes dependencies. This change selects upstream 0.23.1, compiled with
Go 1.27.3, while preserving the proxy arguments, TLS endpoint, localhost
upstream, service account, RBAC and the Kubegres manager itself.

Upstream release and compatibility history:
<https://github.com/kube-rbac-proxy/kube-rbac-proxy/releases/tag/v0.23.1> and
<https://github.com/kube-rbac-proxy/kube-rbac-proxy/blob/v0.23.1/CHANGELOG.md>.
The actual candidate accepts the existing logging flags. No ignored paths,
anonymous access, authorization bypass or new permanent binding is added.

## Bound images

- Proxy: `quay.io/brancz/kube-rbac-proxy:v0.23.1`
- AMD64 manifest: `sha256:bb7c6fbfbb00cc2892e97fd102b8f01eb19eef09ce216385a7cf68ea7a9e7fac`
- Configuration: `sha256:57fc683c5da025b8aaebb3e7c65aa0ca86696a7a6ccb90603b289114daa65dcf`
- Lab operator: `localhost/ops-advanced-operator:20261106-rbacproxy-r1`
- Operator manifest: `sha256:faf1695a71f093683472dfe410de2cd7928bc9331536a195ceb182f17330f37c`
- Operator configuration: `sha256:1873501c7e2f522d618cf7641f0bba2cf60b9f270289b2931a28315f306b3861`

The canonical manifest is `deploy/postgres-operator/kubegres-operator.yaml`.
The incremental lab Dockerfile updates only its hash-bound deploy archive
member. Comparison of 195 source/tool/archive members found only that member
changed; the manager image remains Kubegres 1.18. Source fixes from earlier
retention and static-proxy batches remain included.

## Verification

`tests/rbacproxy_compat.py` uses two cached real proxy binaries and an owned
synthetic TokenReview/SubjectAccessReview server on an internal Docker network.
It uses disposable fixture certificates and tokens, never a real kubeconfig.
It verifies anonymous and invalid authentication, an authenticated user without
permission, an authorized metric reader, wrong verb/path, spoofed identity
headers and reviewer failure. Every denied request is also checked for absence
at the upstream, so HTTP status alone is not the oracle.

```sh
python3 tests/rbacproxy_compat.py \
  --base registry.k8s.io/kubebuilder/kube-rbac-proxy@sha256:928e64203edad8f1bba23593c7be04f0f8410c6e4feb98d9e9c2d00a8ff59048 \
  --candidate quay.io/brancz/kube-rbac-proxy@sha256:bb7c6fbfbb00cc2892e97fd102b8f01eb19eef09ce216385a7cf68ea7a9e7fac \
  --fixture-image localhost/ops-advanced-nodejs:22-20261106-json-r2
```

- Eighteen synthetic checks passed, nine on each image.
- All 190 operator Python tests passed using the bounded writable test copy
  documented in `advanced-fixture-reconciliation-20261106.md`.
- Seven real ADV authentication/authorization checks passed before and after
  rollout: 401 anonymous/invalid/spoofed, 403 unprivileged/wrong verb/wrong path,
  and 200 with actual manager metrics for the authorized reader. Temporary
  service accounts and their sole `get /metrics` binding were removed.
- Twelve Alpha/Beta functional/access checks passed. Both changed workloads
  retained their UIDs and became ready; the final deployment comparison permits
  only the image and operator tag changes. PostgreSQL StatefulSets and all
  existing PVC specifications/identities were unchanged. Retention remained
  enabled at 30 days with the new operator image.
- With identical Trivy databases, old-image findings were 5 Critical / 70 High /
  79 Medium / 4 Low / 10 Unknown; the candidate has 3 Unknown and none in the
  other severity categories. Unknown findings remain visible and unresolved.

Initial fixture runs exposed a non-root directory permission error, an incorrect
test mount/exec layout, and incomplete trust of the proxy's generated certificate
chain. Corrected runs retain the real guards and use the exact proxy certificate
obtained over authenticated Kubernetes port-forwarding. Original failed logs
are preserved. The source-only Docker export initially omitted an untagged
reference; the subsequent named export was verified by manifest and config
hash before deployment. No failed run is counted as PASS.

This qualifies the AMD64 ADV metrics integration, not all Kubegres dependencies,
ARM64, production or a fresh installation. No workload data, permanent access
grant or baseline configuration changed. Rollback snapshots are private, outside
Git. Restore the old proxy and paired operator/catalog to roll back this batch.
