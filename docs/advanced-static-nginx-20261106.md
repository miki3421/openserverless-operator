# ADV static Nginx qualification

Historical qualification: a later fresh deployment exposed an overriding runtime
template that still selected 1.24. See
[the 2026-11-07 correction](advanced-static-nginx-20261107.md) for the source fix,
published operator identity and subsequent live qualification. The earlier scan
and test results below remain historical evidence.

The static proxy previously used nginx-unprivileged 1.24 on Debian 11. The
AMD64 ADV candidate uses the upstream unprivileged 1.30.6 image on Debian 13,
with an immutable platform manifest. This is a standalone proxy change;
the Node action runtime remains on Debian 12 with its existing native ABI.

- Image: `docker.io/nginx/nginx-unprivileged:1.30.6`
- AMD64 manifest: `sha256:e17bfe33eecc8fd86ca9f12c665f21270b0d88b09a0f13bfdede3fcd795f5fda`
- Configuration: `sha256:22c1a0e48d112c33f5e38c2675ea93ec7be54919c8c2a312bcef6d4a98af8957`

Upstream moved these images from `nginxinc` to `nginx` starting with 1.30.6:
<https://github.com/nginx/docker-nginx-unprivileged>. The UID remains 101 and
the listener remains port 8080. The proxy configuration, service, ingress,
SeaweedFS content and access controls are unchanged.

## Implementation and reproduction

The canonical change is the image in
`deploy/nginx-static/nginx-static-sts.yaml`. The normal operator Dockerfile
packages this deploy tree. `tools/static-nginx/Dockerfile.lab` rebuilds the
already qualified lab operator incrementally; its helper verifies the original
archive member hash before replacing that member. Comparison of 195 relevant
files/archive members found only the static StatefulSet member changed.

The lab operator is `localhost/ops-advanced-operator:20261106-static-r1`, manifest
`sha256:3c4df87bcc67ffdef68c07cc43540d9afa7fa4912943d06d89071e10a6746ff9`,
configuration `sha256:4f86d1055fbb66fdf4432b7eb6763762bdd4fbb16d6ecf78cbd98472bfcaea14`.
It retains the qualified activation-retention implementation. This local
operator image has not been published to a registry.

`tests/static_nginx_compat.py` accepts an exported static ConfigMap and cached
base/candidate/fixture images. It creates its own internal Docker network and
containers, uses synthetic HTTP storage, and removes its resources afterward.
It has no pull operation and does not access application storage. Example:

```sh
python3 tests/static_nginx_compat.py --config /protected/static-config.json \
  --base nginxinc/nginx-unprivileged@sha256:f9dc6a4b27715a9cf0cecf0780c8b84df9a6eb31f18a67ba0d63e4f99cc5c8e9 \
  --candidate nginx/nginx-unprivileged@sha256:e17bfe33eecc8fd86ca9f12c665f21270b0d88b09a0f13bfdede3fcd795f5fda \
  --fixture-image localhost/ops-advanced-nodejs:22-20261106-json-r2
```

Use the actual rendered ConfigMap: its rewrite behavior differs from the raw
deploy placeholder. The test compares existing slash normalization rather than
inventing a new redirect contract.

## Results and limits

- 20 local HTTP checks passed across old and candidate images: two tenant
  indexes, HEAD, range, MIME, slash normalization, SPA fallback, 404, upstream
  denial and request-body forwarding.
- All 190 operator Python unit tests passed with the paired task catalog.
- On ADV, both workload identities survived rollout and became ready. The
  ConfigMap data and frontend response bytes were unchanged. Twelve Alpha/Beta
  functional/access checks and two streaming checks passed. The 30-day cleanup
  CronJob retained its policy and selected the new operator image.
- The final image check follows manifest-to-configuration digests and validates
  AMD64. Kubernetes reports a manifest reference for the upstream image and a
  configuration digest for the local operator; either is checked against the
  bound chain, never accepted by tag alone.
- Trivy using one database for both images changed from 18 Critical / 190 High
  to 1 Critical / 69 High. Residual Critical CVE-2026-6653 affects the reported
  libxml2 package and has no fixed version in that scan. This comparison is
  package evidence, not proof of exploitation or complete remediation.

Initial harness failures (internal-network port binding, a redirect assumption
that also failed on the old control, and manifest/configuration ID handling)
are retained separately from corrected results. A concurrent scanner cache-lock
failure was retried sequentially; it is not counted as a successful scan.

The baseline was not changed. ARM64, new full installations, production traffic,
load testing and complete vulnerability closure are not qualified by this work.
Private snapshots and evidence remain outside Git. A rollback restores the
previous static image and operator plus their matching task catalog pins.
