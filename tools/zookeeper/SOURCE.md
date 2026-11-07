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

The runtime retains the archive's LICENSE and NOTICE. The final recipe upgrades
only apt and libapt beyond the stated fixed-version floor; ZooKeeper and Java
payloads are preserved. The server runs as uid/gid 1001, with bounded heap,
a read-only root filesystem in qualification, separate data/log volumes, and
the administrative HTTP server explicitly disabled by the supplied config.

CounterProbe uses the actual invoker's dependency jars. The legacy-server
failure and candidate fresh-counter success are separate evidence. A controlled
graceful container recreation also preserved the counter and mapping. This
does not establish power-loss durability or multi-server failover.

The final image's fixed-database Trivy result retains five High, 33 Medium and
seven Low package matches, including Jetty, JLine and Snappy Java dependencies.
The administrative HTTP endpoint is disabled in the tested profile; this is not
a blanket exclusion of those Java findings. No ignore file is used. Broader
security analysis, ARM64 and production qualification remain open.

The operator profile rejects legacy images, orphaned PVCs and changed data/log
mount layouts. A new installation must use empty volumes; this recipe does not
provide an automatic ZooKeeper 3.4 data migration.

Primary references:
- https://curator.apache.org/docs/breaking-changes/
- https://zookeeper.apache.org/releases/
