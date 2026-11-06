# ADV cert-manager qualification

The packaged manifest now uses cert-manager 1.21.3 instead of 1.10.1. The
matching task previously installed 1.11.0; both sources now contain the same
manifest and four digest-pinned upstream image indexes. The release receipt
records the upstream asset hash and the hash after these four substitutions.
The upstream Kubernetes support window is 1.33–1.36; this batch was tested on
AMD64 K3s 1.36.4+k3s1. Other architectures were not qualified.

Bootstrap uses server-side apply for the larger CRDs, without force-conflicts.
API errors propagate instead of being interpreted as absence. Partial existing
installations are refused. The operator preserves an existing cert-manager
service; it does not silently upgrade it. The task also refuses a different
existing installation and verifies an identical installation without applying
changes. Readiness requires three ready deployments and a successful admission
server dry run. Pod readiness alone was insufficient during a cainjector leader
lease transition; the new bounded admission check covers this case.

## Validation

- 197 operator unit tests and 10 Bun task tests passed, including absence,
  authorization failure, partial-installation refusal, catalog/manifest integrity,
  rollout failure and admission failure.
- A separate VM with its own writable disk passed a real `ops util cert-manager`
  fresh installation and repeat invocation. Eleven synthetic checks covered a
  self-signed issuer, CA, leaf certificate, chain/SAN/key verification, manually
  triggered renewal, key rotation, invalid-duration rejection, restart stability
  and owned-namespace cleanup.
- The same VM rehearsed the specific empty 1.11.0 → 1.21.3 transition. The six
  CRD UIDs and their sole stored version `v1` were preserved. Direct SSA across
  the old client-side field ownership failed and is retained as failed evidence.
  The reviewed lab conversion instead validated both batches server-side,
  replaced only the six definitions with current resource versions, and used
  client-side apply for the remaining public resources. No delete/recreate or
  force-conflicts was used for that transition. Eleven certificate checks passed
  afterwards. This is not a general certificate migration procedure.
- ADV's 46 original objects were checked against their original 1.11.0 applied
  manifest. All six certificate resource kinds were empty before this same
  bounded transition. Eleven live certificate checks, twelve Alpha/Beta checks,
  two streaming checks and the real OPS repeat command passed. All 32 non-Job
  application pods were ready. The baseline was not changed.

The default cached OPS kubeconfig on ADV failed the target-identity check, so
the command was tested with an explicit private context from the current K3s
instance. The unrelated cached context was preserved.

## Image and scanner evidence

The incremental operator image is
`localhost/ops-advanced-operator:20261106-certmanager-r2`:

- Manifest: `sha256:4b7d268ffcb34bd92be34383287026139bca5eb5fa791851c0686cf82f3aed8b`
- Configuration: `sha256:1dc415fe940b7d5d481b9825639fdf65b14bb91ed7750187e1b6391d680f03a8`

Comparison of 195 existing code/deployment/tool members found only the intended
Python helper and cert-manager manifest changes, plus the new release receipt.
Every imported image layer was verified. The normal operator build includes
the canonical sources; `tools/certmanager/Dockerfile.lab` is the bounded lab
reconstruction. Images remain locally imported, not published.

With the same Trivy database, the three running upstream cert-manager images
changed from 14 Critical, 197 High, 230 Medium, 19 Low and 26 Unknown findings to
1 High, 2 Medium and 9 Unknown. The new solver has 2 Unknown findings. The three
remaining rated findings are in controller dependency `go-jose/v4` 4.1.4:
CVE-2026-116585, CVE-2026-116586 and CVE-2026-116587. They are not suppressed or
claimed fixed. Scanner matches are not proof of exploitable deployment paths.

External ACME issuance, DNS-provider credentials, existing certificate migration,
ARM64, production HA and independent review remain unqualified. The old private
lab manifests, operator image and task catalog were retained for reviewed
rollback. Rollback must be reassessed if new certificate resources are created.

References: [release](https://github.com/cert-manager/cert-manager/releases/tag/v1.21.3),
[supported versions](https://cert-manager.io/docs/releases/),
[upgrade guidance](https://cert-manager.io/docs/installation/upgrade/).
