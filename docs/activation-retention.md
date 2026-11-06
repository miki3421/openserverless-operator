# Activation retention

New OPS installations explicitly configure `spec.couchdb.activation-retention-days: 30`.
The operator creates the system `activation-cleanup` CronJob in `openserverless`,
using its own matching image. Requires Kubernetes 1.27 or later, including K3s.
The task and operator changes must be shipped together; an older operator does
not implement this field.

At 03:17 UTC each day it deletes **whole activation documents** whose `start`
timestamp is strictly older than the configured number of 24-hour days. Logs,
results and activation metadata disappear together. Recent records and the exact
cutoff remain. A separate log-only job at the same threshold would be redundant.
This does not delete action definitions, user accounts or application databases.

The existing `activations/byDate` view is queried in batches of 200. Every row and
bulk result is checked. Revision conflicts, authorization failures, timeouts and
an unfinished backlog produce a failed Job, not a success report. Each run uses
one fixed cutoff, at most 1,000 batches and a 600-second Kubernetes deadline.
It always rereads the first page: offset pagination after deletions would skip
records. Partial runs are safely retried by the next daily Job. Large backlogs
can take multiple runs; a failed or suspended job does not enforce the age limit.

`concurrencyPolicy: Forbid` avoids overlapping scheduled runs of this CronJob.
Revision checks make duplicate attempts safe; this is not an exactly-once
scheduler guarantee. One successful and two failed Job histories are retained.
The pod runs without a Kubernetes token or elevated privileges and receives only
the existing CouchDB controller database-member credentials through a Secret.
It does not receive the CouchDB server administrator credential. Logs contain
counts and fixed errors, not activation content, document IDs or credentials.

## Installation and existing clusters

With the matching tasks:

```sh
ops config activations --days=30
```

This changes local installation configuration only; normal setup includes it in
the Whisk resource. Omitting the command on a fresh install still selects 30.
Use another integer up to 36,500 days, or `--days=0` to suspend future runs.
Changing local configuration alone does not reconfigure a running cluster.

For an existing cluster, explicitly set the same field in its Whisk spec after
reviewing the desired retention period. The operator reconciles it on update and
resume without recreating CouchDB. A legacy CR with the field missing, or a value
of zero, produces a suspended cleanup job: an operator-only upgrade must not
silently enable historical deletion. There is deliberately no CRD default that
would retroactively add the policy to existing resources. Removing the field
also suspends future runs; an already active Job may finish its current run.

Inspect `kubectl -n openserverless get cronjob activation-cleanup` and the related
Job status/logs. Operator image rebuild and deployment are needed before enabling
this in an existing installation; changing tasks alone is insufficient.

## Disk and retention limits

This uses normal CouchDB `_deleted` tombstones, **not** the `_purge` API.
Compaction can reclaim obsolete document bodies and revisions, including large
logs; it does not necessarily shrink the file immediately and needs free disk
space. Tombstone/index metadata and independent backup copies have their own
lifetime. Disabling the job cannot recover already deleted activations.

## Local qualification

Run `python -B -m unittest discover -s tests -p 'test_activation_cleanup.py'`
with the operator dependencies installed. The integration test
`python tests/activation_retention_integration.py` creates and removes its own
disposable Docker network and CouchDB, with memory-backed data, no published
port and no pull. It requires cached `apache/couchdb:3.5.2` and local Docker.
It never accepts an existing database or cluster as a test target.

The synthetic integration deletes 410 expired Alpha/Beta records over three
pages using a database member credential. A repeated run deletes zero. Recent
and cutoff records, design documents, non-activation records, and sentinels in
the action/account databases remain. This is local CouchDB qualification, not
evidence of a scheduled run in a live Kubernetes cluster.

On 2026-11-06, single-agent local qualification passed 17 focused retention tests
and 28 existing CouchDB regression tests (one additional catalogue-sibling check
was skipped because that sibling was not mounted). Two Bun configuration tests
passed in the task repository. Actual OPS configuration and template rendering
verified 30, 45 and 0 days; invalid input left configuration unchanged. The
CronJob was accepted by ADV K3s with server-side dry-run only. No retention job
was installed or executed on ADV or baseline. This does not qualify automatic
scheduling, a release image, or other CPU architectures.

## Subsequent ADV qualification

The operator was then qualified and installed on the authorized AMD64 ADV K3s
lab. Image `localhost/ops-advanced-operator:20261106-retention-r1` has manifest
`sha256:8ed6aa73e13cfb305b1cde0052f27dfc06574873f4316ceb7e65a3acaf305db4`
and configuration
`sha256:517e4d3658dfd6cf9b51212b83b885c7a7fa9ccb6c3c95aece64784e562ac4e5`.
The incremental lab recipe is `tools/activation-retention/Dockerfile.lab`, based
on the previously qualified r5 manifest
`sha256:b3a3684e2b38e0661be47c3bab02d865ae4b9f99585b3c69d5e6b155b20b0200`.
Check that exact base before building. The normal Dockerfile includes the new
modules and manifests for full builds; a fresh full image build is a separate
qualification. No image was published to a registry by this batch.

The candidate retains all base layers. Comparison of 197 source/tool/archive
members found only the expected four Python additions/changes and two deployment
manifest changes. The four previously refreshed Go binaries remain identical.
All 17 focused tests pass against the packaged candidate modules.

`tests/activation_retention_cluster.py` uses an owned, temporary namespace with
memory-backed CouchDB and isolated networking. A real CronJob dispatch deleted
410 expired records; recent records and other databases survived. Repeating its
Job template deleted zero. For this bounded test only, the schedule was every
minute; the deployed system policy remains daily at 03:17 UTC. The test namespace
was removed by verified UID. An initial fixture string syntax error occurred
before seeding; its evidence was retained, and the corrected fixture now compiles
its embedded script before creating resources.

The actual ADV database preview found zero records older than 30 days among
8,569 live documents. An operator-only rollout first created a suspended job for
the unchanged legacy Whisk, proving that rollout alone did not enable deletion.
After explicitly setting 30 days, the generated job was enabled, and a one-off
run completed with zero deletions. The policy survived another operator restart.
Alpha/Beta application, persistence and API authorization checks passed 12/12.
The baseline was not contacted or modified. Dependencies, broad suite failures,
ARM64 and production qualification retain their earlier limits; no whole-suite
or whole-platform security acceptance is claimed.

References: [Kubernetes CronJobs](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/),
[CouchDB bulk deletion](https://docs.couchdb.org/en/stable/api/database/bulk-api.html),
[CouchDB compaction](https://docs.couchdb.org/en/stable/maintenance/compaction.html).
