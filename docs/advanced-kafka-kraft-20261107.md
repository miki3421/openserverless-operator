# ADV Kafka 4.4.0 fresh-component qualification

Kafka 4.4.0 / KRaft is the selected **fresh-installation** profile. The existing
ADV installation still runs its qualified Kafka 2.3.1 / ZooKeeper broker. No
legacy volume, offsets or application definitions were modified by this batch.
The baseline was not changed. Kafka's use of KRaft does not remove OpenWhisk's
separate ZooKeeper requirement.

## Source and image contracts

- Broker: `docker.io/miki3421/ops-advanced-kafka:4.4.0-20261107-r4@sha256:4cca13390dc2e488b2e44e2f56da9fa32e01a37430cb15eea734f5db3e34d5dd`.
- Operator: `docker.io/miki3421/ops-advanced-operator:20261107-kafka-kraft-r1@sha256:eabe50cd9e00b8dd58eaed26e0ea8da3727f327063f39d09f35f36bc07645c14`.
- JVM: Temurin 25.0.4.1+1 from the immutable official Kafka parent. JMX exporter
  1.7.0, LZ4 1.11.4 and Snappy 1.1.10.12 have bound download checksums.
- Both images were published on the personal Docker Hub account and their
  manifest/configuration identities and Linux AMD64 platform checked through
  anonymous registry metadata. The fresh K3s trial downloaded the broker.
- The new `kafka_profile.py` checks the current StatefulSet and PVCs before
  rendering/applying anything. It refuses unknown/legacy layouts, orphaned
  claims and missing/unverified storage behind an existing workload.
- Canonical profile, deploy manifest, runtime template and task catalog select
  the same image. KRaft gets its own `kafka-kraft-pvc-data`; runtime formatting
  is allowed only for empty storage. Persisted identity is reused after restart.
- The operator image binds the canonical code/templates. Only four Kafka
  members in the 148-member parent deploy archive change; the Nginx correction
  and all other deployment members remain intact.

## Observed checks

The existing isolated component VM supplied a separate writable disk and an
initially absent OPS namespace. No ZooKeeper pod was installed there. The real
provider renderer produced the manifests; the trial verified empty target
state, applied NetworkPolicy before workloads, then installed the fresh broker.

- All 240 Python operator unit tests passed with complete writable source and
  the paired task catalog. Four task tests passed with 61 assertions.
- The final image passed seven real startup-refusal checks on synthetic tmpfs
  layouts; no formatter was invoked for those refused cases. The readiness
  command failed when no broker was running, as required.
- The exact current controller and invoker client jars each passed 35 local
  fixture checks: five compression modes, payloads/order, explicit commits and
  offset resume. The same two 35-check suites passed against the fresh K3s
  broker. These are client API checks, not full action-activation acceptance.
- A synthetic three-record fixture committed offset 2. After deleting/recreating
  the Kafka pod, its StatefulSet/PVC identities and metadata bytes were unchanged;
  the consumer resumed exactly the third record. Four seed and three resume
  assertions passed. This is graceful recreation, not a power-loss durability
  or general exactly-once guarantee.
- The fresh broker was ready, non-root UID 1000, and the quorum reported leader
  0 with one voter. The canonical real read-only preflight accepted that deployed
  profile and its bound PVC.
- Kafka broker/controller metrics were readable from the Prometheus-labelled
  fixture. Authorized Kafka-labelled controls reached both 9092 and 9093 before
  and after an action-labelled fixture was denied on both ports. Neither public
  listeners nor remote JMX management ports were introduced.

## Scanner evidence

Trivy 0.76.2 with the retained 2026-11-06 database found no advisory matches in
the final image's Alpine or Java inventory: 0 Critical, High, Medium and Low.
The first unpatched 4.4 candidate had 1 Critical / 10 High / 3 Medium / 1 Low;
targeted Alpine and compression-library updates removed those same-database
matches. This is not proof of vulnerability absence or a security certification.
The legacy 2.3.1 image was absent from the host cache, so no new controlled
old-broker comparison was claimed. The six-image OPS summary excludes Kafka.

## Retained intermediate failures

- A local volume initially lacked Kubernetes-equivalent ownership; only the
  owned synthetic fixture volume was provisioned for UID 1000.
- The first startup wrapper included Java diagnostic stdout in its generated
  cluster ID. Its restart refusal exposed the error. The corrected wrapper
  accepts exactly one UUID line; the malformed synthetic volume was not repaired
  or used to obtain a successful qualification. Do not use the earlier r1 image.
- A noexec Docker tmpfs blocked compression JNI loading. Explicit executable
  temporary memory reproduced the qualified Kubernetes filesystem contract;
  the failed codec attempts remain separate evidence.
- The operator build initially copied root-owned temporary files it could not
  remove as UID 1001. Correct COPY ownership fixed the build recipe.
- A negative network fixture initially required a DROP timeout. K3s actively
  rejected the disallowed connection. The corrected differential test accepts
  that refusal only with successful DNS and matching authorized controls before
  and after. No NetworkPolicy was weakened to pass the test.

## Migration backlog and limitations

The paired task's `docs/advanced-upgrade-backlog.md` tracks Kafka alongside
PostgreSQL and MongoDB. A future explicit `ops upgrade` route must qualify staged
broker/metadata transitions, ZooKeeper-to-KRaft conversion, identities,
topics/configuration, offsets, pending activations, consistent custody and
recovery. Updating the operator on a legacy Kafka installation is deliberately
refused; changing a tag is not that migration.

This batch qualifies a fresh **component**, not a complete OPS installation.
The existing ADV broker remains 2.3.1. New full-platform Alpha/Beta acceptance,
ARM64, multi-node HA, sustained load, power loss and production migration are
not qualified. Earlier Alpha/Beta results are preserved as their own batches.
The adjacent JSON receipt records the closed qualification and scanner identity.

References: [Kafka releases](https://kafka.apache.org/community/downloads/),
[KRaft upgrade boundary](https://kafka.apache.org/40/getting-started/upgrade/),
[client compatibility](https://kafka.apache.org/44/getting-started/compatibility/),
[supported Java](https://kafka.apache.org/44/operations/java-version/),
[JMX exporter releases](https://github.com/prometheus/jmx_exporter/releases).
