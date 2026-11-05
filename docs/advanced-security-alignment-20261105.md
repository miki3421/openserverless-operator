# Advanced security alignment, 2026-11-05

Align the existing Advanced operator with the applicable Apache 0.9.0
SeaweedFS isolation and IAM resume changes, anchored at public operator
93fc94ac2f69a1f52bd36e09e76344023460260b. Preserve Advanced backend versions,
volume compatibility guards and existing Redis ACL persistence/signal handling.
The parent commit records the previously deployed Advanced image source.

The filer stays private through a NetworkPolicy and ClusterIP Service. Resume
normalizes and verifies anonymous grants before reconciling ingress. SeaweedFS
4.48 reads authoritative IAM through `s3.configure` without `-apply`; its IAM
service replaces the old identity.json readback. Reject malformed/ambiguous
configuration, unexpected grants and attached anonymous policies. Native command
failures remain errors without exposing captured output.

MongoDB keyfile initialization now replaces a retained read-only destination
before restoring mode0400. This fixes a reproduced restart failure without
changing key material or persistent database files.

Local image: `localhost/ops-advanced-security-aligned:20261105-r2`, built offline
as an overlay on `localhost/ops-advanced-operator-integrated:20261026-local`.
The nine changed Python production files were hash-compared with the deployed
operator. deploy.tar preserves the prior members, replacing only the SeaweedFS
Service manifest. These local images require explicit import and are not a
published installation release.

Validation:

- IAM13/13, datastore security25/25, non-root Mongo keyfile repeat1/1 PASS.
- Full suite167:156PASS,2FAIL,7ERROR,2SKIP. The nine failing cases also fail on
  the imported pre-alignment source (Advanced Milvus fixtures and one
  FerretDB/native-Mongo fixture). Do not claim the full suite green.
- Mongo rollout and operator resume/update succeeded in the existing AMD64 lab.
- Alpha/Beta functional/persistence/authorization12PASS; streaming2PASS;
  direct five-backend read isolation10PASS, both directions. Cross-tenant writes
  were not tested.
- Controller OPTIONS validation is a separate fixed image: anonymous/invalid
  GET and OPTIONS401, authorized requests200; synthetic action cleaned up.

Private runtime evidence and scanner results remain outside this repository.
No tenant credential or application-source changes; no Apache push or PR.
Baseline component versions were not modified. This is not ARM64, migration or
whole-product vulnerability acceptance.
