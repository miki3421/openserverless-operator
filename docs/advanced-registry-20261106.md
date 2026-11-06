# ADV registry 3.1.2 qualification

The AMD64 lab registry uses Distribution 3.1.2 with the existing filesystem
backend and htpasswd authentication. No existing repository, tag, credential,
PVC, service or network policy is recreated. This is not an S3, token-provider,
multi-node or ARM64 qualification.

## Reproducible inputs

- Upstream: `distribution/distribution@3220848f15d9279c66a41aa0a257469e6aede1e9`.
- Source archive SHA256: `ccd8840029d2e6e1cd7c648d8fbb4f2389c73ce5f4ec5552c2e8308883b281cb`.
- `tools/registry/dependencies.patch` SHA256:
  `8a3586332c3bd56c26931727f2076eb55fc813c6040c7f1fc5f648bc8fc9a4df`.
- Go 1.27.3, go-jose 4.1.6, compress 1.18.7, OpenTelemetry exporters 1.45.0,
  x/net 0.62.0 and the required x/crypto 0.58.0 dependency closure.
- Only go.mod and go.sum change in the 4,599-file upstream source tree.
  The independently reconstructed build tree matches the tested tree exactly.
- Registry binary SHA256:
  `e90b3a38ae44bea173326b923b2ab6afbc3f0d5b600d3558c0d4429a94f4af4e`.
- Lab image: `localhost/ops-advanced-registry:3.1.2-20261106-r1`, manifest
  `sha256:75a843bf4b74c23c6776dbabbbd674b1ba5bc4ff1a1b7af3bce5d31a2b866351`.
- Operator: `localhost/ops-advanced-operator:20261106-registry-r1`, manifest
  `sha256:ce213f0ac64b5da1c7ba36631c1a4aae98df4af8804c30e217aa1b8ec443c09f`.
  Its only changed deployment archive member is `registry/02-registry-sts.yaml`;
  all 196 compared application, tool and deployment files retain their expected
  content. The parent is the qualified monitoring operator.

Run `tools/registry/build.sh WORK_DIRECTORY GO_CACHE` from the repository root.
Both directories must already exist; the source output directory must not.
The script verifies source and patch hashes and binds the compiler image.
Dependencies are resolved in the builder; no host Docker socket, cloud
credentials or operational configuration is mounted. The build explicitly uses
`-mod=readonly`, not the old vendored dependency tree.

## Validation and retained failures

- `tests/registry_compat.py`: 30 passing checks against old 3.0.0 and the
  candidate, including auth failures, chunked uploads, manifest and blob digests,
  pagination, four concurrent uploads, restart and old-version readback of
  candidate writes on synthetic storage.
- `tests/registry_k8s.py`: 14 passing checks on a separately identified K3s
  1.36.4 VM, including a new PVC, synthetic Secret, pod replacement, exact
  manifest/blob readback and unchanged PVC/PV identity. The owned namespace and
  storage were removed.
- Operator unit suite: 197 PASS.
- ADV: both image rollouts pass; exact spec comparisons permit only the image
  and operator tag changes. All PVC/PV specs and identities, network policies,
  services and registry Secret hashes remain unchanged. The real registry
  catalog was empty: live authentication was verified, while populated data
  compatibility is demonstrated by the synthetic tests above.
- Alpha/Beta: 12 application/security checks and 2 streaming checks PASS.

The upstream race suite is **not wholly green**. With the first two fixture
corrections, upstream records 573 PASS / 24 SKIP / 2 FAIL including subtests;
the dependency candidate records 571 PASS / 24 SKIP / 4 FAIL. The parent
in-memory suite failure is included in those counts.

`tools/registry/test-fixtures.patch` changes tests only: install shared router
handlers before parallel requests; replace an external Google health probe with
a local HTTP fixture; give rejected HTTP requests independent bytes.Reader
cursors. The two failing HTTP tests (`TestBlobAPI`, `TestBlobDelete`) then pass
with `-race` on both source variants. No production Go file includes this patch.

`TestInMemoryDriverSuite/TestDeleteOnlyDeletesSubpaths` intermittently fails on
both the original and candidate source. A deterministic `/repeat/repeat` case
fails on both. `dir.find` recurses with the same last path component when no
slash remains, which can choose the repeated child instead of its parent. The
example/testing in-memory driver is not used by ADV's filesystem registry.
The failure is retained, not removed or counted as a successful test.

Earlier network-disabled external HTTP failures, a missing test-container
`patch` executable and the initial five-minute suite timeout are also retained.
Cloud backends requiring credentials remain skipped. Corrected local HTTP tests
do not qualify public TLS/CA behavior. The jose update adds an RSA JWK size bound;
custom external token issuers need their own compatibility tests.

## Scanner comparison

Same Trivy 0.76.2 database, no ignore rules: old image 4 Critical / 68 High /
94 Medium / 23 Low / 6 Unknown; official 3.1.2 7 High / 15 Medium / 4 Low /
3 Unknown; candidate 1 Unknown (`GO-2026-5932`, x/crypto OpenPGP).
Zero rated matches is not proof of absence of vulnerabilities.

Images are local lab artifacts; source publication does not publish container
images or qualify fresh multi-architecture distribution. Keep the old registry
image available until the lab acceptance record has been retained. Do not run
garbage collection as part of this image refresh.
