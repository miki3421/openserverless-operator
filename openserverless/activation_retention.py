# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements. See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Install and reconcile the activation retention job alongside CouchDB."""
import kopf

from openserverless import config as cfg
from openserverless.activation_cleanup import retention_days

FIELD = "couchdb.activation-retention-days"
NAME = "activation-cleanup"


def resources():
    # New task installations explicitly set 30. An old CR missing this field
    # must not silently acquire a destructive policy on an operator upgrade.
    days = retention_days(cfg.getall().get(FIELD, 0))
    image = cfg.operator_image_reference()
    credentials = {}
    for field in ("user", "password"):
        value = cfg.get(f"couchdb.controller.{field}", f"COUCHDB_CONTROLLER_{field.upper()}")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("CouchDB controller credentials are required for activation retention")
        credentials[field] = value
    secret = {
        "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
        "metadata": {"name": NAME + "-auth", "namespace": "openserverless"},
        "stringData": credentials,
    }
    job = {
        "apiVersion": "batch/v1", "kind": "CronJob",
        "metadata": {"name": NAME, "namespace": "openserverless"},
        "spec": {
            "schedule": "17 3 * * *", "timeZone": "Etc/UTC", "suspend": days == 0,
            "concurrencyPolicy": "Forbid", "startingDeadlineSeconds": 3600,
            "successfulJobsHistoryLimit": 1, "failedJobsHistoryLimit": 2,
            "jobTemplate": {"spec": {
                "activeDeadlineSeconds": 600, "backoffLimit": 0,
                "template": {"metadata": {"labels": {"name": NAME}}, "spec": {
                    "automountServiceAccountToken": False, "restartPolicy": "Never",
                    "securityContext": {"runAsNonRoot": True, "runAsUser": 1001,
                                        "runAsGroup": 1001, "seccompProfile": {"type": "RuntimeDefault"}},
                    "containers": [{
                        "name": NAME, "image": image, "imagePullPolicy": "IfNotPresent",
                        "command": ["/home/openserverless/.venv/bin/python", "-B", "-m",
                                    "openserverless.activation_cleanup", "--days", str(days)],
                        "securityContext": {"allowPrivilegeEscalation": False,
                                            "readOnlyRootFilesystem": True,
                                            "capabilities": {"drop": ["ALL"]}},
                        "resources": {"requests": {"cpu": "25m", "memory": "64Mi"},
                                      "limits": {"cpu": "250m", "memory": "256Mi"}},
                        "env": [
                            {"name": "COUCHDB_HOST", "value": str(cfg.get("couchdb.host") or "couchdb")},
                            {"name": "COUCHDB_PORT", "value": str(cfg.get("couchdb.port") or 5984)},
                            {"name": "COUCHDB_USERNAME", "valueFrom": {"secretKeyRef": {
                                "name": NAME + "-auth", "key": "user"}}},
                            {"name": "COUCHDB_PASSWORD", "valueFrom": {"secretKeyRef": {
                                "name": NAME + "-auth", "key": "password"}}},
                        ],
                    }],
                }},
            }},
        },
    }
    return [secret, job]


def reconcile(owner=None):
    if not cfg.get("components.couchdb"):
        from openserverless import kube
        return kube.delete({"apiVersion": "v1", "kind": "List", "items": [
            {"apiVersion": api, "kind": kind,
             "metadata": {"name": name, "namespace": "openserverless"}}
            for api, kind, name in (("batch/v1", "CronJob", NAME),
                                    ("v1", "Secret", NAME + "-auth"))
        ]})
    # Reuse the private Secret apply path; never log a failed Secret payload.
    from openserverless.couchdb import apply_couchdb_spec
    items = resources()
    if owner:
        kopf.append_owner_reference(items, owner)
    return apply_couchdb_spec({"apiVersion": "v1", "kind": "List", "items": items})
