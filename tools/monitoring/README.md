# ADV kube-state-metrics build

`build-ksm.sh WORK_DIRECTORY GO_CACHE_DIRECTORY` rebuilds kube-state-metrics
2.20.0 from the pinned source archive with Go 1.27.3. Both directories must
already exist and the work directory must not contain `source`. The compiler
image must already be available locally. The script verifies the upstream
archive and both dependency patches before building; Go modules may be fetched
into the explicit cache directory.

The original `ksm-dependencies.patch` remains unchanged. `ksm-xnet.patch`
updates `golang.org/x/net` to 0.62.0 and its selected companion modules. The
candidate retains the same 101 runtime modules; six versions change. This
addresses the two x/net findings in the November 6 database without adding a
scanner exclusion. The remaining GO-2026-5932 module match concerns OpenPGP:
the exact candidate's compiled package closure contains no OpenPGP package.
This disposition is specific to the verified binary, not the whole module.

The resulting image is
`localhost/ops-advanced-kube-state-metrics:2.20.0-20261106-r2`.
Its qualified AMD64 manifest is
`sha256:f42762c8e0481058ba74a998cf5ed27e8e06be1c0729b2763946ff47db6f0b3e`;
binary SHA256 is
`19b79422651d0aa2eebb4e7f99fab5e9da1a8ccbe3ce67455e68c628c629ec04`.

## Operator packaging

Build either Dockerfile from the operator repository root with `--pull=false`.
They have distinct purposes and must not be interchanged:

- `Dockerfile.operator.ksm-fresh` extends the fresh-install candidate with
  Prometheus 3.16 and Alertmanager 0.34.2. Their existing-volume guards remain.
  The task catalog selects this image for a new qualified installation.
- `Dockerfile.operator.ksm-existing-adv` extends the currently installed ADV
  operator. It updates only the stateless kube-state-metrics manifest, retaining
  the running Prometheus 2.43.1 and Alertmanager 0.25.0 and their data. This is
  a lab rollout artifact, not a general monitoring migration path.

`refresh_ksm_archive.py` checks the parent manifest hash and preserves every
other archive member. Both qualified images preserve all 148 archive members
apart from the intended manifest and all parent operator source files.

## Qualification and limits

The November 6 AMD64 comparison passed 145 top-level Go tests (348 including
subtests) on both dependency sets across 15 selected packages with the race
detector. Six packages have no tests; this is not the entire upstream suite.
Operator unit tests: 217 passed. Isolated Kubernetes: 12 metric/RBAC checks and
24 Prometheus integration checks passed, with owned test resources removed.

The compatible image was then applied to ADV only. Post-rollout verification
confirmed retained PVC/PV identities, RBAC, network policies, configuration and
the existing Prometheus/Alertmanager deployments. Alpha/Beta passed 12 security
workflow checks and two streaming checks. The baseline was not changed.

ARM64, the full platform installation and migration of existing monitoring
volumes are not qualified by these tests. Raw evidence, failed intermediate
attempts and scanner output are retained separately from the repository.
