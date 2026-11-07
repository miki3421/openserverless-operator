# ADV fresh installation from Docker Hub

Status: clean AMD64 laboratory installation and application checks completed on
2026-11-07. This is a personal ADV candidate, not an Apache or production release.

## Immutable inputs

- Task repository: `miki3421/openserverless-task-custom`, branch
  `fix/advanced-zookeeper-20261107`, deployment source `9a63b91`.
- Operator repository: `miki3421/openserverless-operator`, same branch,
  implementation `701612c`, registry receipt `19e0196`.
- Selected operator:
  `docker.io/miki3421/ops-advanced-operator:20261107-zookeeper-r1`, manifest
  `sha256:3e593815523645853b2b0a79c24698da12dff46166fa07304186684077bba9a6`.
- ZooKeeper:
  `docker.io/miki3421/ops-advanced-zookeeper:3.9.7-20261107-r2`, manifest
  `sha256:8a6aba58c5455cdf8673f12de4818fec0fd2811ce388e06bcf9dbb6ac3e20a8c`.
- All 19 custom image inputs and remote manifest identities are recorded in
  `tools/registry-release/release-images.json`. Anonymous manifest access was
  verified. No write credential was installed in Kubernetes.

## Fresh-start fixes

The rehearsal uninstalled K3s from the authorized ADV VM, recreated its data,
and installed K3s `v1.36.4+k3s1`. No ADV image archive was imported into the new
containerd cache. The baseline was left stopped and unchanged.

An empty installation exposed integration dependencies hidden by earlier caches
and initialized volumes:

1. The Whisk template omitted required SeaweedFS `console-enabled`. It now
   explicitly selects `false`; the administrative filer console stays private.
2. The operator's inherited job-image environment selected an old local image.
   Task rendering now derives job image/tag from the selected operator catalog
   reference, including digest and registry-port forms.
3. The task Role lacked the canonical operator's namespaced NetworkPolicy
   permissions. The matching namespaced rule now permits reconciliation.
4. Several qualified images and embedded manifests still referenced local
   labels. The release receipt, task/runtime catalogs, monitoring profiles and
   embedded references now select published personal Docker Hub packages.
   The Milvus catalog digest was aligned with the actual qualified payload.
5. Curator 5.9.0/ZooKeeper client 3.9.7 could not allocate a fresh invoker id
   against server 3.4.14. The new server 3.9.7 profile supports that operation;
   it refuses legacy/orphaned volumes and unqualified existing layouts.
   See `tools/zookeeper/SOURCE.md` for source identities and limits.

## Verification and retained failures

The final clean installation completed the initial root login, hello action
invocation and default-user provisioning. The observed infrastructure inventory
had 44 pods, all Running/Ready or Succeeded, and no `localhost/` image references.
Scheduler and initialization jobs used the selected published operator.

Alpha and Beta were provisioned sequentially with fresh synthetic credentials,
then deployed with direct `ops ide login` / `ops ide deploy` commands. Their
frontends returned HTTP 200. Application acceptance uses the live backends:

- 12/12 application/security checks: owner writes/readback on PostgreSQL,
  FerretDB, Redis, SeaweedFS/S3 and Milvus; anonymous, invalid and peer identity
  rejection; tenant input rejection.
- 10/10 direct backend read-isolation checks, both directions, after positive
  owner fixture verification. No generic network failure counted as isolation.
- 24/24 streaming checks: GET/POST on web/action routes, concurrent request and
  tenant correlation, anonymous/invalid/peer credential rejection.
- Scheduled heartbeat evidence is recorded separately from activation-history
  availability: 2/2 tenants PASS, with two distinct current-CronJob-owned,
  completed scheduler dispatches matched to their Redis heartbeats. Activation
  history is NOT_AVAILABLE, not a successful activation-storage test.

The 30-day activation cleanup CronJob is present with the selected operator
image and daily schedule. Its destructive cleanup was not invoked in this
fresh-install acceptance. The closed, credential-free receipt is
`docs/advanced-fresh-dockerhub-20261107.json`. The application overlay is existing
unpublished fixture work; its exact member hashes are recorded in that receipt.

The test-only headless streamer Service resolves the real streamer pod address;
it is a synthetic fixture dependency, not a replacement platform Service.

Earlier failure evidence is retained privately rather than overwritten. It
includes the missing PATH prerequisite, failed original bootstrap candidates,
one contaminated attempt with surviving child task processes, the old-ZooKeeper
fresh-counter failure and a stale Beta builder fixture referencing an absent
internal image. Derived application workspaces were regenerated from canonical
packages; the existing token-aware streaming fixture was then synchronized.
The local observer initially lacked database drivers, and an attempted probe
against an expiring action container could not attach. Installing clients in
the observer's isolated virtual environment using the runtime's pinned
constraints enabled the direct read-only probes. Those prerequisite failures
are not backend isolation successes or production defects.

Focused local source checks remain separate: task 15 PASS; operator 27 PASS,
2 SKIP; streaming fixture 14 PASS. These are not whole-suite results.
The actual invoker dependency jars reproduced the legacy counter failure and
passed fresh-counter and graceful-recreation persistence checks on the final
server image. Temporary qualification containers, networks and volumes were
removed after retaining their evidence.

## Open gates

This qualifies the native K3s installation plus OPS platform setup on one AMD64
lab node. It does not qualify the complete SSH orchestration of
`ops cloud k3s create`, existing-volume migration, rollback, ARM64, multi-node
failover, power-loss durability, representative load or independent security
review. Direct write-isolation testing remains NOT_RUN.

The ZooKeeper image scan still reports five Java High, 33 Medium and seven Low
matches, with zero Critical. Disabling the administrative HTTP server is not
a blanket dismissal of Jetty, JLine or Snappy findings. Tika, remaining Debian
and Java dependencies, and Akka/Kafka modernization retain their own backlog.
Successful fresh installation does not establish an enterprise security release.

Operational logs, credentials, synthetic client bindings and earlier failure
material remain outside Git. Only sanitized evidence belongs in this repository.
