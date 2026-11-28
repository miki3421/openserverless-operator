# PostgreSQL Perl security overlay

This AMD64 overlay updates only the four installed Perl packages from
`5.36.0-7+deb12u3` to Debian's `5.36.0-7+deb12u4`. The vendor update addresses
CVE-2026-13221, CVE-2026-42496 and CVE-2026-8376. The last issue requires a
32-bit Perl build; the existing AMD64 occurrence is not a demonstrated
32-bit exposure. Package remediation and application exploitability are
separate conclusions.

The parent is the exact already-qualified ADV PostgreSQL image. The build
checks that PostgreSQL, pgvector, the qualified libxml2 backport and startup
script remain byte-identical. It preserves the inherited image configuration
and does not initialize, migrate or connect to a database.

Prepare `vendor/` with exactly the four named `.deb` inputs. Verify them against
the SHA256 entries in Debian's signed `bookworm-security` index, using the
published security archive key, and retain that verification receipt.
`SOURCE.json` records the signed-index provenance and input hashes; package
files and private build/qualification state stay outside Git.

Build with network access disabled. The default parent is immutable. For an
offline build, an imported local parent tag may be supplied through
`BASE_IMAGE` only after every rootfs diff ID, the complete effective container
configuration and platform have been compared against that exact manifest.
Docker may reserialize the imported config: record both original and local
config digests explicitly, without calling the latter the original OCI image.
Retain the manifest-to-config/rootfs binding; a similarly named image is
insufficient.

Qualification must compare the complete installed-package delta and inherited
image configuration, exercise normal Perl and security regression cases, and
verify PostgreSQL/pgvector with retained synthetic data across a restart on an
isolated volume. Scan the exact candidate with the retained current database.
No live rollout or release is implied by this recipe. Target rollout remains
an Agent-executed operation with a recoverable original image identity.

The explicit opt-in native scripts are
`tests/native_postgres_perl_contract.py` (JSON output path) and
`tests/native_postgres_perl_data.py` (JSON output path). They use the recorded
local parent/candidate tags, no network, and task-owned synthetic storage.
They do not connect to an existing database. Their exact retained qualification
receipts belong to the ADV Critical artifact collection.
