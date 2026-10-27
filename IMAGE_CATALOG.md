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

# Platform image catalog

The matching OPS task branch copies `opsroot.json`'s `config.images`, including
explicit `IMAGES_*` local configuration overrides, into `Whisk.spec.images`.
This field is the effective, persistent catalog for operator-managed images.
Release versions are selected in the task catalog. The operator contains slot
names, not a second catalog of release versions.

The resolver applies the catalog after Kustomize rendering and before applying
resources. It covers core services, init containers, CronJobs, PostgreSQL's
Kubegres image, PostgreSQL backup, monitoring and Pulsar. Fresh ingress-nginx and
cert-manager deployments use the same resolver. Kind/EKS ingress and OpenShift
CouchDB have separate keys to preserve their existing image variants.

New catalogs take precedence over `controller.image` and `invoker.image`; the
matching tasks keep those legacy fields synchronized. Explicit overrides must
be set in the effective catalog. References may contain a registry port, a tag,
a SHA-256 digest, or a tag and digest. Missing entries for enabled components,
invalid references, and unmapped managed container slots are errors.

An existing Whisk without `spec.images` retains the legacy templates and image
selection. Their literal defaults remain only for this compatibility path.
Removing those defaults requires a separate deprecation policy. Application
images, user workflow images and OpenWhisk action runtimes remain governed by
their existing manifests (`runtimes.json` for action runtimes).

On operator resume and Whisk update, only image fields of existing resources
directly owned by that Whisk are reconciled. Patches test `resourceVersion` and
only differing images are changed. PVCs, credentials, user resources and
immutable completed Jobs are not recreated. Existing cluster ingress and
cert-manager installations are not adopted. The operator's own image and the
separately deployed Admin API, Streamer and Tika are managed by their OPS tasks.

This feature is not a database migration engine. An image reference must remain
compatible with the existing data format. Major-version migrations require an
explicit upgrade plan and tested backup/restore procedure.

Run the regression suite with the operator's Python dependencies installed:

```sh
python -m unittest discover -s tests -p 'test_*.py' -v
```

Use the companion task branch `feature/centralize-images-0.9.0` and build an
operator containing this implementation before enabling its catalog. Existing
published Apache images do not yet implement this field.

Local validation on the existing K3s baseline (2026-10-27): 143 Python regression
tests passed, including 17 catalog tests. A catalog-only Nginx reference change
updated only the static StatefulSet and was reverted. Repeated reconciliation
was a no-op, all 55 inventoried resource UIDs were preserved, and all 15
Alpha/Beta application checks passed. Both tenants could still read their
pre-update synthetic records. This was an in-place baseline test, not a fresh
installation or an Advanced-profile migration.
