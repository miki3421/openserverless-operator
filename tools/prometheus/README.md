# Advanced Prometheus candidate

This AMD64 candidate uses Prometheus 3.16.0 at source revision
`e2e28adeee2c5883d000fc82aca6008cfbf7e96d`, its exact official release UI,
and Go 1.27.3. The only selected dependency change in either binary is
`golang.org/x/net` 0.61.0 to 0.62.0. Source, UI, compiler and base image
identities are pinned in the build files.

From the operator repository root, with the pinned compiler and base image
already available locally, supply an empty build directory and a Go cache:

```sh
tools/prometheus/build.sh /absolute/build-directory /absolute/go-cache
```

The script downloads the two public source archives if absent, verifies their
checksums, builds both binaries and creates the local candidate image. It does
not publish images or modify a cluster. The build's Go dependency resolution
requires network access unless the cache is complete. Output directories must
have enough space for source, compiler cache, binaries and the image build.

`Dockerfile.operator` packages the matching operator modules and deployment
archive on the qualified local parent. `opsroot.json` in the matching task
branch must name that operator image and the same Prometheus image digest.

Qualification covers a fresh isolated AMD64 installation, the production
configuration and 70 rules, self/Kubernetes scrapes, the official UI bytes,
configuration reload, synthetic rules, persisted samples across a Deployment
restart, and monitoring/action network boundaries. The operator's real create,
repeat-create and delete paths were also exercised on that empty installation.

Existing Prometheus 2.x volumes, orphaned claims and changed storage layouts are
refused before monitoring mutations. This candidate does not authorize their
reuse, a downgrade, or a whole-platform upgrade. ARM64, legacy TSDB migration,
hard-crash recovery and controller/Kafka/Alertmanager integration remain
separate qualifications. Scanner results are dependency evidence, not proof
of remote exploitability or a claim that the entire platform is secure.
