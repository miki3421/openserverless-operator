<!--
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
-->

# Integrated PostgreSQL 18 development profile

OPS Advanced uses PostgreSQL 18.6 and pgvector 0.8.6, pinned by the OCI index digest in `openserverless/files/postgres-profile.json`. Both database manifests and the task image catalogue must match this profile. Kubegres remains 1.18 for this increment: it was validated with PostgreSQL 18 in the lab; updating the controller is a separate change.

The database volume mounts at `/var/lib/postgresql`; Kubegres explicitly sets PGDATA to `/var/lib/postgresql/pgdata`. New PostgreSQL 18 clusters enable data checksums. This layout must not be applied to existing PostgreSQL 16 volumes.

`postgres_profile.preflight()` executes before PostgreSQL reconciliation. It refuses differing database images/layouts and orphaned PostgreSQL PVCs. It does not convert data, implement migration, or constitute a rollback system. Read errors fail closed. The guard also intentionally refuses an unreviewed future digest change.

## Build for an isolated lab

Run from this operator repository. Images are development artifacts, not published releases.

```sh
docker build -f docker/postgres-backup/Dockerfile   -t miki3421/ops-advanced-pg-dumper:18.6-pgvector0.8.6-dev   docker/postgres-backup

docker build   --build-arg OPERATOR_IMAGE_DEFAULT=miki3421/ops-advanced-operator   --build-arg OPERATOR_TAG_DEFAULT=postgres18-dev   -t miki3421/ops-advanced-operator:postgres18-dev .

kind load docker-image --name ops-advanced-pg18   miki3421/ops-advanced-pg-dumper:18.6-pgvector0.8.6-dev   miki3421/ops-advanced-operator:postgres18-dev
```

The Advanced task catalogue references these images. They must be built and loaded locally (or explicitly published to your registry) before deployment. No image publication is performed automatically. The OCI PostgreSQL image has amd64 and arm64 manifests; runtime tests were amd64 only.

## Backup behavior

The backup container is built from the same pinned PostgreSQL 18 image with cron/gzip added. It connects to the primary service so one-replica configurations also have a backup source. Scheduling and retention are inherited; the shell environment is written to a private file instead of embedding credentials in crontab.

`pg_dumpall --clean --if-exists` uses pipefail and a temporary `.partial` file; only a successful dump/compression publishes the final archive. A restore must use an isolated target and a bootstrap administrative database/user that the dump will not drop. Dumps contain credentials and application data and must be protected. Automated PostgreSQL 16-to-18 migration is not included in this increment.

## Validation (2026-09-17)

Eight unit tests cover legacy-image refusal, layout mismatch, orphaned PVCs, repeat installation, read failures, manifest/profile agreement and failed-backup cleanup.

```sh
python -m unittest discover -s tests -p test_postgres_advanced.py -v
```

These tests require the existing Python dependencies, including PyYAML and Jinja2.

An isolated Kind v1.33.1 cluster in the retained KVM VM passed:

- PostgreSQL 18.6 startup with pgvector 0.8.6 and checksums enabled.
- Operator image `postgres_operator.create()` reconciliation and backup deployment.
- Two database pods, streaming replication and explicit replica promotion with data preserved.
- JSONB data, vector HNSW index, nearest-neighbor query and role grants.
- Operator `create_db_user()` and pg8000 application connection as a non-superuser.
- Logical single-database dump/restore and complete pg_dumpall restore on an independent PostgreSQL 18 instance, including data, vector extension and role privileges.
- Eight unit tests; read-only rejection of the existing host PostgreSQL 16 deployment.

This is not yet validation of the complete OPS application suite, FerretDB, automated major-version migration, automatic failover under node failure, performance gains, or ARM64 execution. See the root Advanced roadmap for those gates.

## Integrated K3s validation (2026-10-08)

A fresh full OPS Advanced installation on the retained `ops-advanced-rc7` VM runs this PostgreSQL 18.6/pgvector 0.8.6 profile with two database pods and checksums enabled. FerretDB, JavaScript/Python database actions and HTTP SSO mock tests passed. Two non-superusers wrote and queried JSONB/vector data in their own schemas; cross-database connections and an invalid password were refused. Both replicas retained the row counts and checksums across a primary-pod restart. Repeated setup preserved PVC identity, tenant data and the existing default user's credentials. See the root `ADVANCED.md` report and `advanced/lab/` scripts for evidence and limitations. This does not establish automatic failover on node loss, ARM64 compatibility or production migration safety.
