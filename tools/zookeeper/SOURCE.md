# ZooKeeper source and qualification scope

The ADV invoker contains Curator 5.9.0 and the ZooKeeper 3.9.7 client. A fresh
instance-id allocation on the old ZooKeeper 3.4.14 server fails with
`KeeperException.UnimplementedException` in the SharedCount/container-parent
path. Reusing a previously assigned invoker id takes a different code path and
does not qualify this first-boot operation.

The server uses the official Apache ZooKeeper 3.9.7 binary archive:

- https://downloads.apache.org/zookeeper/zookeeper-3.9.7/apache-zookeeper-3.9.7-bin.tar.gz
- Official SHA512: `69ce6bf0b6df9a9ecdf080bdc15758eef97ef176a88f1408214f2327fe6e5349fedf738031977e6fc2f52cb636b45779653389a6bfda75be9c82dd3e72f24f25`.
- Derived SHA256, also enforced by Docker ADD: `d87db41fc53cf06966497489be6ee604b9bbd5f53871183a252371db75f7617c`.
- Eclipse Temurin Java 17 base manifest: `sha256:8ee89ca3f9eb6a931a2b2e8b2901a4af3554383f8077558e3631d49bb58cff1d`.

The runtime retains the archive's LICENSE and NOTICE. The base OS step upgrades
apt and libapt beyond the stated fixed-version floor; ZooKeeper and the JRE
payloads are preserved. The r3 dependency changes are described below.
The server runs as uid/gid 1001, with bounded heap,
a read-only root filesystem in qualification, separate data/log volumes, and
the administrative HTTP server explicitly disabled by the supplied config.

CounterProbe uses the actual invoker's dependency jars. The legacy-server
failure and candidate fresh-counter success are separate evidence. A controlled
graceful container recreation also preserved the counter and mapping. This
does not establish power-loss durability or multi-server failover.

The earlier r2 image retained five High, 33 Medium and seven Low fixed-database
Trivy matches. The r3 server-only recipe removes seven optional Jetty jars and
replaces JLine 3.30.14 with 3.30.15 (jdk8 aggregate), and Snappy 1.1.10.5 with
1.1.10.12. Docker ADD verifies exact SHA256 identities; dependency license
notices and the original Apache LICENSE/NOTICE are retained. The same database
reports zero High/Critical, 28 Medium and seven Low matches in r3, without ignores.

The normal NIO protocol and CLI remain supported. Admin HTTP, controller command
HTTP and the optional Jetty-based PrometheusMetricsProvider are unavailable in
this server-only image. They were not selected by the qualified ADV config.
ZooKeeper's AdminServerFactory explicitly supports omitting Jetty; this does
not imply that arbitrary optional ZooKeeper configurations are compatible.
The cluster's separate Prometheus installation is unchanged.

DependencyProbe checks absence of Jetty classes, disabled AdminServer, bounded
JLine regex matching, Snappy malformed typed-input rejection, round trips and
compressed snapshot streams. SnapshotProbe interchanges a bounded Snappy stream
between r2 and r3. Actual-client counter tests also cover r2-created native
snapshot/transaction data, r3 first allocation, CLI reads and graceful restart
with retained data, under non-root/read-only/container resource controls.

These close the five package matches, not a demonstrated Internet exploit.
Admin HTTP is disabled; JLine grep is not called by the normal client protocol;
Snappy is used for local snapshot files. An attacker-controlled remote path to
the vulnerable operations was not demonstrated in this profile. Broader security
analysis, ARM64, power-loss durability and production qualification remain open.

The operator profile rejects legacy images, orphaned PVCs and changed data/log
mount layouts. In addition to empty installations and matching repeats, it
permits the one exact digest-qualified r2 image as an explicitly tested source
for the dependency-only r3 patch. Tag-only references, altered digests and
changed layouts are refused. This is not a general volume migration mechanism
or an automatic ZooKeeper 3.4 data migration.

## Qualified r3 image and ADV patch

- Server: `docker.io/miki3421/ops-advanced-zookeeper:3.9.7-20261107-r3`,
  manifest `sha256:137adefa2395428d951f0846ec5a442e98e0d574d7d4efe78bd4fcee1bea4718`.
- Matching operator: `docker.io/miki3421/ops-advanced-operator:20261107-zookeeper-java-r1`,
  manifest `sha256:5ad34632f257ab25ce4fe6851757da72d88c9bb2ba9a7adaf1eedd988794d1ef`.
- Both remote manifest bodies and digests were verified without login. The
  task catalog and all operator-owned job templates select the matching image.
- Seven focused operator profile tests and six task tests passed. Ten isolated
  compile/dependency/snapshot/CLI/counter checks passed. Relative to the previous
  operator archive, only the ZooKeeper StatefulSet member changed; 147 other
  members were byte-preserved.
- On ADV AMD64, the server pod was recreated and the invoker was restarted.
  Both existing PVC identities and the invoker assignment counter were retained.
  All 30 persistent workload controllers were ready after the patch.
- Alpha/Beta: 12 application/auth, 10 direct backend read-isolation, 24 stream
  transport/auth/correlation checks and two scheduled-heartbeat checks passed.
  Both HTML frontends and matching JavaScript assets passed HTTP/MIME checks.
  Cron verification correlates successful scheduler dispatches with Redis
  heartbeats; activation history remained unavailable.

One legacy-image cron job received HTTP 503 during the planned restart of the
single invoker. Its failure evidence was retained before removing the closed
job; subsequent scheduled executions passed. This is not a claim of zero-downtime
maintenance, high availability or recovery of a missed execution. The baseline
was left stopped and unchanged. Temporary isolated containers, network and data
volumes were removed; private operational evidence stays outside Git.

These are focused author-run checks, not a whole-suite or independent review.
The fixed scanner database was updated on 2026-11-06, SHA256
`112895ef5c65cfe71007fc6d1ba8df0b6652412cd5c869f95e3d4723b4912508`.
Counts describe this server image with that database, not all ADV components.

Primary references:
- https://curator.apache.org/docs/breaking-changes/
- https://zookeeper.apache.org/releases/
- https://github.com/apache/zookeeper/blob/release-3.9.7/zookeeper-server/src/main/java/org/apache/zookeeper/server/admin/AdminServerFactory.java
- https://github.com/jetty/jetty.project/security/advisories/GHSA-355h-qmc2-wpwf
- https://github.com/jetty/jetty.project/security/advisories/GHSA-2fvj-hgj9-j2gr
- https://github.com/jline/jline3/pull/2018
- https://github.com/xerial/snappy-java/releases/tag/v1.1.10.12
