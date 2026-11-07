# Fresh ADV Kafka / KRaft

This profile installs a **new** single broker/controller using Kafka 4.4.0.
It does not migrate Kafka 2.3.1 or adopt existing ZooKeeper-era storage.
ZooKeeper remains a separate OpenWhisk dependency; do not remove it from OPS.

The Dockerfile binds the official AMD64 image and the SHA256 of JMX exporter
1.7.0, LZ4 1.11.4 and Snappy 1.1.10.12. Targeted Alpine package upgrades remove
the fixed findings observed in the pinned upstream image. The qualification
receipt records the resulting immutable image and scanner/database identity.
Retain the Apache distribution's licenses and dependency notices.

From the operator repository root:

```sh
docker build -f tools/kafka-kraft/Dockerfile -t ops-kafka-candidate .
python3 tests/kafka_kraft_storage.py --image ops-kafka-candidate
```

The storage checks create only restricted, disposable containers with synthetic
tmpfs data, no network, and no image pull. Their failures must not invoke storage
formatting. The broker also refuses non-KRaft metadata, wrong node identities,
duplicate identity keys and foreign/symlinked layouts at runtime.

`start.sh` accepts only the supported `kafka-0` topology. On an empty mounted
volume it generates and validates one UUID and formats KRaft storage once. On
restart it validates and reuses the persisted identity. It never uses
`--ignore-formatted`, deletes data or starts a ZooKeeper-to-KRaft conversion.
The separate operator preflight refuses legacy/unknown StatefulSets, orphaned
PVCs, and an existing workload whose verified bound storage has disappeared.

The non-root process uses a read-only root filesystem. Kubernetes supplies the
writable data claim via fsGroup and a bounded `/tmp` emptyDir. Compression JNI
libraries need executable temporary mappings. An isolated Docker test must use
an explicit executable tmpfs (`/tmp:rw,exec,nosuid`); Docker's default noexec
tmpfs is not equivalent to the qualified Kubernetes emptyDir. No public ports
are needed for local fixture checks.

The 9092 internal endpoint and metrics on 9071/9888 remain. The controller
listener 9093 is allowed only between Kafka-labelled pods without the action
marker. The headless Service publishes not-ready addresses so the singleton
controller can resolve its own DNS during initial quorum startup. Readiness
performs a bounded broker API request rather than printing a CLI version.

`tests/KafkaKraftClientChecks.java` runs against the actual controller/invoker
client jars. In an owned disposable fixture, run it with Java source mode and
`-cp '/controller/lib/*'` or `-cp '/invoker/lib/*'`. Set
`-Dops.kafka.bootstrap=<owned-fixture-endpoint>` explicitly. `kafka` checks five
codecs and offset behavior using unique synthetic topics. `seed` creates the
fixed synthetic `ops-kraft-persistence` fixture and commits offset 2; after a
broker recreation, `resume` checks the pending third record and deletes it.
These modes mutate their fixture broker and must not target an installation
containing real workloads.

`tests/kafka_kraft_render.py` is an offline render fixture with configuration
and Kubernetes writes mocked. Its empty-state mock is not proof that a real
namespace or volume is empty. The operator unit suite separately verifies
render agreement and refusal before apply.

`Dockerfile.operator` refreshes the qualified parent operator. Its archive
helper verifies the original inventory and four parent member hashes before
replacing only Kafka deployment members. Build this image only after the
canonical profile/template/deploy pins identify the published qualified broker.

See `docs/advanced-kafka-kraft-20261107.md` for observed scope and limitations.
