# Advanced Go tool qualification — 2026-11-05

Execution: single agent, existing AMD64 Advanced K3s v1.36.4+k3s1 laboratory. No baseline changes or publication. This is a bounded tool refresh, not whole-platform security acceptance.

## Artifact

The canonical build script produced kubectl v1.36.6-adv.1, kustomize v5.8.3-adv.1, wsk 1.2.0-adv.1 and task 3.54.0-adv.1 using Go 1.27.3. A second offline build of the final source reproduced all four binaries byte for byte.

The incremental qualification image is `localhost/ops-advanced-operator:20261105-r5`:

- OCI manifest: `sha256:b3a3684e2b38e0661be47c3bab02d865ae4b9f99585b3c69d5e6b155b20b0200`.
- Image configuration: `sha256:0414009bddabbc718b64c9dbd6fbc4893d63d9483c8f63e7bab9d7c14c5411ad`.
- The live pod image identity and all four live binary hashes match the qualified artifact.
- All 82 compared operator source/configuration files, including Python code, run.sh, deploy.tar and Python dependency locks, match the previous image.

The full main Dockerfile was updated to use the same canonical tool builder, but a complete fresh image build/install was not qualified. ARM64 and production were not qualified.

## Compatibility evidence

- Four CLI compatibility tests passed on both the base and candidate: kubectl JSON/JSONPath/version metadata, kustomize rendering, task includes/variables/failure propagation, and wsk authentication/result handling.
- Full local suites: 171 tests each; 162 PASS, 2 FAIL, 7 ERROR, no skips. The same nine unsuccessful cases occur on both images: eight Milvus fixture/render cases and one FerretDB provisioning fixture case. The whole suite is not green.
- Real operator rendering: 16 generated deployment groups, 59 Kubernetes objects, canonically identical with old and new kustomize. This comparison did not apply manifests.
- Live CLI checks: server discovery, a synthetic ConfigMap apply/read/UID-bound patch, and synthetic Alpha/Beta action creation, owner invocation and peer denial (403) passed. Synthetic resources were removed.
- Operator resume completed for the controller and the three existing users; newly scheduled cron and quota jobs completed with the candidate image. Active pods were ready. Historical failed jobs were retained as earlier evidence.
- Alpha/Beta application/security checks: 12 PASS. Streaming delivery: 2 PASS. These bounded checks do not qualify all backend isolation, SSO, load, restore or independent security gates.

## Trivy comparison

The same vulnerability database was used for both exact image artifacts: SHA256 `2ce29bda5b2483d84da3f4365398addcf03a7decf5c79e29dc73db8f0fa454ed`, updated 2026-11-05T19:01:45Z. Counts are unique `(VulnerabilityID, PkgName, InstalledVersion)` tuples per image; no suppressions or exclusions were introduced.

| Scope | Before Critical / High | After Critical / High |
| --- | --- | --- |
| Four Go tools | 14 / 197 | 0 / 0 |
| Entire operator image | 16 / 246 | 2 / 49 |

Remaining whole-image Critical/High findings belong to Debian (2/48) and Python (0/1). The Go tools still contain one Medium and four distinct Unknown findings:

- wsk: CVE-2022-29526 (Medium, x/sys faccessat), CVE-2026-39824 (Unknown, x/sys Windows API).
- kubectl and task: CVE-2026-78661 and CVE-2026-97045 (Unknown, x/net).
- task: GO-2026-5932 (Unknown, x/crypto/openpgp).

These remaining scanner findings were preserved; reachability and exploitability have not been established by this batch. Zero Critical/High is not a claim of no vulnerabilities.

## Evidence and recovery

Raw scanner results, both full test logs, render comparison, live checks, source hashes, build reproduction, pre-change manifests and private job logs are retained outside Git in the operator's qualification records. Initial unsuccessful build/scanner/fixture attempts remain separate from final evidence.

The previous r4 image remains available locally. A reviewed rollback must restore the operator StatefulSet image and image environment variables, both cron job templates and the task image catalog together. This batch did not change datastore formats or volumes. No blanket image/cache prune was performed.
