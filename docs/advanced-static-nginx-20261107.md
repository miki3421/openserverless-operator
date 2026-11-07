# ADV static Nginx template correction — 2026-11-07

Fresh static-provider creation could select Nginx 1.24 despite the deploy manifest
and task catalog pinning 1.30.6. `storage_static.create()` applies the runtime
template after reading the deploy tree; that template still contained 1.24.
Updating only the deploy archive therefore did not survive rendering.

The minimum source correction updates
`openserverless/templates/nginx-static-sts.yaml` to the already qualified image.
The regression in `tests/test_static_nginx_render.py` calls the real provider
creation path and Kustomize against disposable files, with Kubernetes writes and
readiness mocked. It fails with the old template and passes with the correction,
checking the resulting image, container ports and volume configuration.

## Immutable images

| Component | Published reference |
| --- | --- |
| Static Nginx | `docker.io/nginx/nginx-unprivileged:1.30.6@sha256:e17bfe33eecc8fd86ca9f12c665f21270b0d88b09a0f13bfdede3fcd795f5fda` |
| Operator | `docker.io/miki3421/ops-advanced-operator:20261107-static-template-r1@sha256:3eedc771131c01ba26b62f42fccccf29ca62f9dd1e16531e125f1272cdd375e1` |

Both references are qualified for Linux AMD64. The operator configuration digest
is `sha256:13cb515fd33edccc6bbc08f9fba273750ea4da50050d2da1fa40d7684a304a60`.
Anonymous registry metadata retrieval verified the manifest and configuration
hashes and architecture; ADV pulled and ran these immutable references.

`tools/static-nginx/Dockerfile.operator` adds one template layer to the immutable
qualified ZooKeeper/Java operator parent. Its 77 parent layers remain identical;
the deployment archive and installed dependencies are unchanged. Reproduce from
this repository root with:

```sh
docker build -f tools/static-nginx/Dockerfile.operator \
  -t miki3421/ops-advanced-operator:20261107-static-template-r1 .
```

The paired personal task catalog selects the new operator. Its static Nginx pin
was already correct. The running operator's `OPERATOR_IMAGE` and `OPERATOR_TAG`
also identify this candidate for newly generated maintenance jobs.

## Local validation

- The old-template regression failed as expected; the corrected test passed.
- All 228 operator unit tests passed with a complete writable disposable source
  copy and the paired task catalog at the expected sibling `oplugins` path.
- All four task image-selection tests passed (60 assertions).
- All 20 local HTTP compatibility checks passed across the old and new static
  images using synthetic storage and the actual rendered proxy configuration.
  The test removes its owned Docker resources afterward.

Earlier harness attempts mixed image-installed modules with a read-only source
tree, or omitted required source members/catalog placement. Those attempts and
their failures/skips are retained separately; they are not product regressions
or additional successful tests. The complete-source execution resolved them.

## ADV validation

The operator and static StatefulSets were rolled out selectively. An operator
restart does not re-render the static provider, so the existing static workload
received the same pinned image explicitly; the real-renderer regression covers
subsequent provider creation. No general cluster upgrade, database migration or
application redeployment was performed.

- The live process reports Nginx 1.30.6, passes `nginx -t`, and runs as UID 101.
- The static StatefulSet UID and full spec except its image are unchanged;
  ConfigMap data, Service spec/UID and volume declarations are preserved.
- Both frontend indexes and four JS/CSS assets are byte-identical before/after,
  with correct MIME types and responses matching the deployed application files.
- Alpha/Beta: 12 functional/access checks, 10 backend read-isolation checks,
  24 streaming checks and two scheduled-heartbeat checks passed: 48 total.
- All 22 persistent controllers are ready. Other persistent container specs
  are unchanged by the static rollout.
- The 30-day activation-cleanup job follows the new operator pin. Existing
  cron-init and quota-checker templates retain the qualified parent image:
  their executable code/dependencies are identical and were not changed in
  this batch. The scheduler heartbeat qualification uses successful dispatches
  from completed jobs owned by the current CronJob and Redis heartbeats;
  activation history remains unavailable and is not claimed as verified.

## Scope and rollback

This qualifies the existing AMD64 ADV lab and source rendering. It does not
qualify ARM64, production load, backend write isolation or a fresh full-cluster
installation. No new Trivy scan was run: previous advisory counts retain their
original image/database provenance. Nginx is outside the six-image comparison
total, and changing a template does not remove operator package advisories.

The baseline was not changed. Small redacted results are retained beside this
document; private snapshots, clients and detailed operational evidence remain
outside Git. To roll back this batch, restore the previous static image and
operator image/environment/catalog pins together; no data rollback is needed.
