# ADV monitoring collectors and reload helper

This batch changes kube-state-metrics and Prometheus's configuration reloader.
It preserves Prometheus 2.43.1, Alertmanager 0.25.0, their configuration and data
volumes, the selected 27 Kubernetes resources, existing RBAC and NetworkPolicies.
It is an AMD64 laboratory qualification, not a Prometheus storage migration or
an ARM64 release qualification.

## Source and images

Kube-state-metrics moves from 2.8.0 to the upstream 2.20.0 source commit
`4ffeda2ef866b0fef372849825a803296483b336`. The source archive SHA256 is
`fffdaa8e6bbb0e2e9b2d07886bd70ef2f4b6507901ffb0de678b5486b6a46eba`.
`tools/monitoring/ksm-dependencies.patch` changes only `go.mod` and `go.sum`;
its SHA256 is `bc457a6285130915ad974a914e77b82c61f98500258132112c58ddd4dd62febb`.
It updates CEL 0.29.0, OpenTelemetry 1.45.0, gRPC 1.83.2 and x/net 0.60.0,
including their required transitive versions (x/crypto 0.57.0, x/text 0.42.0).
The Kubernetes client remains 0.36.3; application source is unchanged.

`tools/monitoring/build-ksm.sh` verifies the source archive and patch, applies it
without fuzz, verifies module checksums and builds with pinned Go 1.27.3. It
requires existing work/cache directories and a fresh `source` subdirectory.
The patch independently reproduced all 329 files of the tested candidate.
The tested binary SHA256 is
`2fbe48513bec4fb8420875ce582c09d8ed5fccbd1f58bc9b4acb0ab0de306b66`.
The resulting local image is
`localhost/ops-advanced-kube-state-metrics:2.20.0-20261106-r1`, manifest
`sha256:754b48553d8ae0a9df39443d60d7de2ea40ec131ccdb92ff511414b255470ed8`.

The reloader moves from 0.65.1 to the official 0.95.1 AMD64 artifact, pinned to
manifest `sha256:15f6aec87a20e25f33371ebf9f37bfba33d783f8f7c7dcc3448720c1a5397c2c`
and configuration `sha256:f3885962b30781abdbdcfad7b460b4e536190efe9b5ea93c92e9e80d90e748cc`.
The release index `sha256:39bdbade0c64f7c38bb6d18d6f602b14ad489bc189b709dc9266b49e6647aac1`
contains five different manifests all declared `linux/amd64`, plus attestation
entries. A local export contains only the selected child; importing that index
failed on another missing child, even with an explicit platform. Do not treat
the ambiguous index as a qualified multiarch pin. The selected child's rootfs
diff IDs were checked against the exact Docker image used in compatibility tests.
Containerd's canonical untagged digest alias must also resolve through CRI.

The incremental operator image is
`localhost/ops-advanced-operator:20261106-monitoring-r2`, manifest
`sha256:2489f67984623ab7c5b13a8f7b984d6f2c7bd1e2d23394716ce388c49858e4b7`.
Its deployment archive preserves all 116 file members and changes exactly the
two monitoring deployment manifests. The normal operator build packages the
same source manifests. These local images have not been published to a registry.

## Local and isolated checks

Both upstream and patched KSM passed the unit scope defined by upstream's
`test-unit` target with `-race`: 146 top-level tests / 349 including subtests,
16 tested packages and seven packages without test files. The upstream separate
e2e target was not counted in these totals. All 197 operator unit tests passed.

`tests/reloader_compat.py` passed six checks on both old and new images: initial
POST, projected ConfigMap symlink rotation, unchanged-content suppression, HTTP
503 retry, recovery to the latest configuration and process/request contract.
It uses an owned internal Docker network and removes its containers and network.

`tests/ksm_live.py` passed twelve checks on both old and new images in a separate
K3s 1.36.4 VM. It uses the actual resource flag and ClusterRole rules, confirms
denial of Secret listing, pod exec, node proxy and mutations, verifies eight
synthetic metric families, all 27 resource observations without list/watch
errors, and health. New client-go uses WatchList rather than a separate initial
List request; both successful paths are recorded. Owned resources are removed
with UID checks, and the test refuses a cluster containing OpenServerless.

OPS's shipped rule referencing `kube_pod_container_status_restarts_total` remains
covered. Arbitrary external dashboards are not qualified: upstream changed some
metric semantics across releases, including Pod reason and legacy Endpoint
address metrics. The existing explicit Endpoint selection remains supported;
no EndpointSlice or Secret permission was added.

## Scanner comparison and limits

With the same Trivy 0.76.2 database snapshot from 6 November 2026:

| Image | Critical | High | Medium | Low | Unknown |
|---|---:|---:|---:|---:|---:|
| KSM 2.8.0 | 4 | 66 | 77 | 4 | 9 |
| Official KSM 2.20.0 | 0 | 10 | 17 | 4 | 3 |
| Patched KSM 2.20.0 | 0 | 0 | 0 | 0 | 3 |
| Reloader 0.65.1 | 3 | 32 | 55 | 4 | 1 |
| Reloader 0.95.1 | 0 | 0 | 0 | 0 | 1 |

Remaining Unknown matches are GO-2026-5932 (x/crypto/OpenPGP) in both images and
CVE-2026-78661 / CVE-2026-97045 (x/net HTML) in KSM. They remain in raw reports;
no suppression, exploitability conclusion or full-suite security PASS is implied.

Preserved unsuccessful attempts include an undersized build tmpfs, a read-only
Go checksum-cache location, incompatible explicitly requested transitive minima,
two initial fixture mistakes (automatic Endpoints creation and `OK` casing),
the WatchList fixture contract adjustment, legacy Docker's unsupported COPY
chmod option, the ambiguous image-index imports and a missing CRI digest alias.
These were corrected and rerun; they are not relabelled as product regressions.

Source references: [KSM 2.20.0](https://github.com/kubernetes/kube-state-metrics/releases/tag/v2.20.0),
[Prometheus Operator 0.95.1](https://github.com/prometheus-operator/prometheus-operator/releases/tag/v0.95.1).

## ADV rollout

The native K3s context and node UID were checked before mutation. Each imported
manifest, image configuration and layer was SHA256-verified before patching;
resource UID/version preconditions guarded the three image changes. Their
rollouts passed. The operator version environment and guest catalog were aligned.
The original object and catalog snapshots remain privately in the guest under
`/home/ubuntu/ops-monitoring-20261106`.

The final comparison confirmed exactly the intended image/version changes and
unchanged PVC/PV specifications and identities, monitoring configurations, RBAC,
NetworkPolicies and Alertmanager deployment. Prometheus remained 2.43.1, reported
successful configuration reload and returned 55 KSM restart-metric series.
All twelve Alpha/Beta regression checks and both streaming checks passed.
Baseline was not changed.

Detailed raw scans, tests, unsuccessful attempts, image proofs and operational
receipts are retained under the operator's Codex Security evidence directory,
`artifacts/monitoring-go-20261106`. Private operational snapshots remain outside
Git. This work did not include a new full platform installation, production
upgrade, arbitrary dashboard validation or an independent security review.
