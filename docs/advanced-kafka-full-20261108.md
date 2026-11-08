# ADV Kafka 4.4.0 — Alpha/Beta application qualification

On 2026-11-08 a new single-node AMD64 ADV laboratory exercised the full
Controller/Invoker profile with Kafka 4.4.0 / KRaft, HTTP ingress, monitoring,
and PostgreSQL, native MongoDB, Redis, SeaweedFS and Milvus. PostgreSQL and etcd
used one replica each. Baseline and the existing ADV installation were unchanged;
the latter still uses Kafka 2.3.1. This does not implement that legacy migration.

The paired [JSON receipt](advanced-kafka-full-20261108.json) records immutable
images, source identities, successful batches and retained failures. Earlier
[component qualification](advanced-kafka-kraft-20261107.md) remains separate.
No new broker or operator image was built, and no new Trivy scan was run.

## Installation and minimum task correction

The initially reused component-only VM had disabled Traefik. The first setup
attempt stopped at its missing middleware CRDs, before Kafka creation. The
canonical uninstaller then exposed a separate partial-installation deletion
failure (`whisk_delete`, `TypeError` on absent state). Logs were preserved and
the owned temporary K3s control plane was discarded with its official uninstaller;
no finalizer was manually removed. Partial-installation deletion hardening remains
open and is not presented as a Kafka regression.

A fresh K3s installation had a new node UID, ready Traefik/CRDs, no OPS namespace
and no PVCs. `ops setup cluster` from the pinned personal task source completed.
Alpha and Beta received new credentials and were deployed with direct
`ops ide login` and `ops ide deploy` commands, using the unchanged application
source from the previous qualification. No application data was restored.

The full-profile Streamer task selected the public API hostname without a scheme.
The Streamer defaults a bare hostname to HTTPS, although this profile serves HTTP.
Its initial transport suite had **4 PASS / 20 FAIL**, mostly sanitized 502 replies.
The existing ADV already used the working private Controller endpoint.

The minimum task change selects `http://controller:3233` for both full and slim
profiles, matching the canonical controller Service and the existing template
fallback. This removes the dependency on public ingress/TLS for internal calls;
it does not change Streamer authentication or network guards. The real canonical
task was rerun and its live pod environment verified, followed by **24/24**
streaming checks. No broker, runtime or application code changed to pass them.

The complete installation was not repeated from zero after this task correction;
the correction was rendered and deployed through the real task on the fresh
installation. Keep that distinction when describing unattended installer coverage.

## Real application and broker evidence

Before the broker restart and again after client recovery:

- 12 application/access checks passed, including owner workflows and persisted
  reads through all five real backends, and anonymous/invalid/peer denial cases.
- 10 direct backend read-isolation checks passed, both directions, after positive
  owner-fixture checks. Backend write isolation was not tested.
- 24 streaming checks passed: GET/POST, both routes, concurrent tenant correlation
  and negative authorization cases.
- Two scheduled-heartbeat checks passed through distinct Redis observations and
  exact successful dispatches from completed jobs owned by the current CronJob.
  Activation history remains unavailable; its storage was not qualified.

Each steady-state batch therefore contains **48 PASS**. Frontend indexes, two
assets per tenant and the JSON-format health endpoint were also verified.
An early harness request expected JSON from the unsuffixed health web route;
that route returned 204. The appropriate `health/status.json` route returned 200.
This was a test assumption correction, not an application or Kafka patch.

Four normal blocking Python/Node activations across Alpha/Beta were additionally
correlated by exact activation ID in both `invoker0` and `completed0`, before
restart and after recovery: **eight successful correlated activations** in total.
The diagnostic consumer used bounded explicit partition/offset reads and disabled
auto-commit; it did not advance the OpenWhisk consumer groups.

Kafka pod recreation preserved StatefulSet/PVC identities and exact KRaft metadata,
cluster and directory IDs. Ten additional reads verified the original synthetic
data after restart without exercising/reseeding those fixtures. Controller and
Invoker recovered without a manual restart.

## Availability findings retained

Broker readiness did not immediately mean the OpenWhisk invocation path had
recovered. The first post-readiness request received **503**, with the Controller
logging `No invokers available`. One scheduled job also failed to dispatch both
synthetic tenant ticks with 503. These observations were not relabeled PASS.

Successful original-fixture reads completed within an observed upper bound of
41.209 seconds after the broker-readiness witness; this is not an exact recovery
latency, SLA or controlled comparison with Kafka 2.3.1. Subsequent correlated
activations, application/streaming suites and new scheduled heartbeats passed.

The single-broker profile does **not** qualify uninterrupted availability,
catch-up of missed schedules, duplicate-free execution, power-loss durability,
HA, ARM64 or production migration. The existing cron containment settings were
not loosened and no automatic retries were added to mask the interruption.

An optional extra tenant-specific TCP probe stopped because initialized action
pods cannot reliably be selected by their original names after prewarm reuse.
It produced no new network-boundary PASS; previous component-boundary evidence
remains separate. Independent review was not performed.

Raw logs, Kafka records, credentials and operational snapshots remain private
outside Git. Only sanitized results are committed. All persistent pods were Ready
at the final inventory; historical failed-job evidence was retained separately.
