# ADV Operator SQLite qualification — 2026-11-09

The Operator image installs the qualified SQLite CVE-2025-7458 runtime-library
backport from the digest-pinned Bookworm package carrier.  No Operator source
caller of SQLite was found; this is defense in depth for a privileged control
plane process and does not claim an exploitable application path.

The exact-parent overlay preserves the deployed image except for
`libsqlite3-0` and the source/test receipt.  Its qualified release is
`ops-advanced-operator:20261109-sqlite-r1` with manifest
`sha256:deb6d770a3e139a58bddf39c71fa1e656a59ecaed272e2ce9c00d156960a8ff6`.
The same installation is present in the canonical Dockerfile.  Exact package
delta, OCI configuration, `dpkg --audit`, SQLite controls/regression vectors,
application import and a complete canonical build passed.

Trivy's raw occurrence remains visible because the custom Debian version is
not in its vendor version mapping.  The retained source commit, patch,
checksums and tests under `/usr/share/ops-security/sqlite` are the remediation
evidence.

