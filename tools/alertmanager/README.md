# Advanced Alertmanager candidate

This fresh-install AMD64 candidate uses Alertmanager 0.34.2 at source revision
`e8349048265ccc0871c3d0d668b05eacc68baf50` and the exact official release UI.
It is compiled with Go 1.27.3. The minimal x/net 0.62.0 dependency closure also
updates x/crypto, x/sync, x/sys and x/text in the two binaries; x/tools changes
in the source module graph but is not linked into either binary.

From the operator root, with the pinned compiler and base image already local:

```sh
tools/alertmanager/build.sh /absolute/empty-build-directory /absolute/go-cache
```

The script verifies source, UI and dependency patch checksums. Dependency
resolution needs network access unless the supplied cache is complete.
It builds local images only. `Dockerfile.operator` packages the matching source
and replaces exactly one member of the qualified parent's deployment archive.

Keep the default restriction on cross-origin requests. API v1 was removed
upstream; API v2 is the supported interface. OPS discard, Slack, Gmail and
combined templates are checked in UTF-8 strict mode with synthetic values and
network access disabled. No external notification is sent by qualification.

Existing 0.25.x deployments, orphaned claims and changed storage layouts are
refused before monitoring mutations. Silences and the notification log require
an explicit migration plan before old volumes can be reused. This guard is
also exercised read-only against the existing ADV installation.

The isolated Kubernetes fixture verifies Prometheus-to-Alertmanager-to-local-
webhook delivery, silencing, restart persistence and deduplication, UI bytes,
default CORS behavior and action/monitoring network boundaries. Its ownership
guard requires an absent namespace on an explicitly pinned node. Images must
be preloaded with their canonical repository@digest aliases, because CRI
normalizes tag@digest references. Existing ADV volumes, ARM64, hard crashes,
external notification delivery and full-platform upgrades are not qualified.
