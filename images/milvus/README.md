# Milvus zero-grace read latency fix

This is an OPS Advanced derivative of the pinned upstream Milvus 3.0.2 image.
It is not an official Milvus release. The lab image has not been published.

With `common.gracefulTime: 0`, Bounded reads calculate the same guarantee
timestamp as Strong reads. Upstream 3.0.2 still skips fresh timestamp allocation
for Bounded requests, and its local WAL MVCC acceleration only handles Strong
requests. This can introduce unnecessary waits for periodic visibility updates.

`zero-grace.patch` uses the existing Strong execution path for that exact case,
in both search and query, including collection-default consistency. It does not
alter positive-grace Bounded, Session, Eventually, Customized, legacy explicit
timestamps or iterator acceleration rules. Authentication, authorization, native
libraries, storage formats and WAL durability are unchanged.

## Build and test

From this directory, on Linux amd64:

```sh
docker build --target test -t ops-milvus-zero-grace-tests .
docker build -t miki3421/ops-advanced-milvus:3.0.2-zero-grace-20261016 .
```

The recipe pins the upstream runtime image, Go toolchain image and source/header
revisions. Go dependencies use the upstream module files. Ubuntu development
packages are fetched during the build; the recipe does not promise bit-identical
images across different package-repository snapshots. The final image only
replaces `/milvus/bin/milvus`; development tools and sources remain in build stages.

The test target runs focused timestamp-allocation regressions plus the existing
upstream search/query timestamp-allocation tests. The `test` build tag is required
by upstream test helpers; it is not used for the production binary.

## Readiness and data safety

The full/slim operator manifests separately require both `/healthz` and completed
collection recovery before initial readiness. Successful recovery is cached by
PID 1 start time on the existing tools `emptyDir`. A container restart invalidates
the old marker, and a health failure clears it. Once recovery succeeds, temporary
DDL loading does not remove a healthy standalone instance from its Service.
This is a startup recovery gate, not an assertion that every later query succeeds.

The image/WAL/metadata/PVC compatibility guard remains enabled. Switching the lab
from stock 3.0.2 to this same-format derivative is an explicit operator action;
it does not authorize an automatic upgrade of old 2.5.2 volumes or metadata.
Keep the old image available for recovery. Application code and credentials do
not need to change.

See the parent project's `docs/MILVUS_302_VALIDATION.md` for lab results and the
exact imported image digest. Importing a locally rebuilt image can produce a
different digest; update its local manifest/catalog references only after testing.
For a local-only K3s import, register the digest-only repository reference as
well as the tag: the CRI normalizes `repository:tag@sha256:...` to
`repository@sha256:...`. A local alias containing both tag and digest alone is
insufficient and can trigger an attempted registry pull. This does not require
publishing the image.
