# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
# this module wraps kubectl
import json
from pathlib import Path

PROFILE = json.loads((Path(__file__).parent / "files/postgres-profile.json").read_text())


def validate_existing(existing, pvcs):
    """Refuse implicit major upgrades and reuse of orphaned database volumes."""
    if existing:
        spec = existing.get("spec", {})
        if (spec.get("image") != PROFILE["image"] or
                spec.get("database", {}).get("volumeMount") != PROFILE["volume_mount"]):
            raise RuntimeError("OPS Advanced PostgreSQL 18 requires an explicit migration of the existing database; image or data layout differs. No PostgreSQL resources were changed.")
    elif any(item["metadata"]["name"].startswith("postgres-db-openserverless-postgres-")
             for item in pvcs.get("items", [])):
        raise RuntimeError("Existing PostgreSQL PVCs found without their Kubegres resource. Restore or migrate them explicitly; automatic volume reuse is refused.")


def preflight(kubectl):
    # Do not swallow authorization, connectivity or decoding errors.
    crd = kubectl("get", "crd", "kubegres.kubegres.reactive-tech.io",
                  "--ignore-not-found", "-o", "json", namespace=None)
    existing = None
    if crd.strip():
        raw = kubectl("get", "kubegres", "openserverless-postgres",
                      "--ignore-not-found", "-o", "json")
        existing = json.loads(raw) if raw.strip() else None
    pvcs = json.loads(kubectl("get", "pvc", "-o", "json"))
    validate_existing(existing, pvcs)
