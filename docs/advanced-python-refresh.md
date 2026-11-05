# Advanced Python dependency refresh — 2026-11-05

This is an AMD64 Advanced laboratory qualification, unpublished and independent of the Apache 0.9.0 baseline. Existing datastore versions, volume compatibility guards and tenant credentials are preserved.

Poetry remains installed because cron.sh, quota.sh and dbinit.sh use `poetry run`. Application dependencies come from poetry.lock. Poetry 2.3.3 and pip 26.3 are pinned for the refresh; both installed Python environments are checked. Build cache ownership is explicit, so the non-root install does not fail trying to create `/opt/.cache`.

Application source and deploy.tar are byte-identical to the qualified base. The refresh deliberately preserves the current kubectl, kustomize, wsk and task binaries; their Go dependency findings require a separate compatibility-qualified batch.

167 unit tests run on both images: 158 PASS, 2 FAIL and 7 ERROR, no skips. The exact nine failures match before/after: eight Milvus fixture/rendering cases and one legacy FerretDB fixture against native Mongo. The entire suite is not green. The initial incorrectly mounted test harness and the first build cache-permission failure are retained in private evidence, separately from final test results.

Trivy at a fixed database: 20 Critical / 276 High before; 16 Critical / 246 High after. Residual findings include Debian, Go tools and one High attributed to setuptools 70.3.0 in pip's vendored SBOM. It is not suppressed or declared fixed. The updated application Python packages no longer match the original High/Critical findings.

## Qualified incremental build

The regular Dockerfile consumes the same updated lockfile. The incremental recipe preserves the previously deployed application and deployment assets while installing locked dependencies and available Debian updates.

Base image: `localhost/ops-advanced-security-aligned:20261105-r3`

Required local base manifest: `sha256:b2bba73f4e026f6b45937613b9d5f39c29ebe9a62643e18ef6f1394ec51c9e68`

Verify `docker image inspect --format '{{.Id}}'` against that manifest before using `Dockerfile.advanced-refresh`; a mutable tag alone is not provenance. The qualified candidate manifest is `sha256:ca260c836f5568d9070d93da780a85069334793d24ff0f6e9c105c987b70e630`. Debian update availability can change a later build; retain its package manifest and rescan/retest it rather than assuming byte reproducibility.

```sh
docker build -f Dockerfile.advanced-refresh --build-arg BASE_IMAGE=localhost/ops-advanced-security-aligned:20261105-r3 -t localhost/ops-advanced-operator:20261105-r4 .
```

The scan database was updated at 2026-11-05T19:01:45Z, SHA256 `2ce29bda5b2483d84da3f4365398addcf03a7decf5c79e29dc73db8f0fa454ed`. Both old and new images were scanned using that same database with no exclusions or ignore policy. Counts deduplicate vulnerability ID, package name and installed version per image.

ARM64, fresh installations and production are not qualified by this incremental laboratory run. No source was pushed or published.
