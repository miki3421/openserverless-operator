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
import hashlib
from pathlib import Path

PROFILE = json.loads((Path(__file__).parent / "files/couchdb-profile.json").read_text())
SETTINGS = """[couchdb]
single_node = true
[cluster]
n = 1
[query_server_config]
reduce_limit = false
[compactions]
openserverless_activations = [{db_fragmentation, "60%"}, {view_fragmentation, "60%"}]
openserverless_subjects = [{db_fragmentation, "60%"}, {view_fragmentation, "60%"}]
openserverless_whisks = [{db_fragmentation, "60%"}, {view_fragmentation, "60%"}]
"""
SETTINGS_SHA256 = hashlib.sha256(SETTINGS.encode()).hexdigest()

def validate_existing(existing, pvcs):
    if existing:
        containers = existing.get("spec",{}).get("template",{}).get("spec",{}).get("containers",[])
        couch = next((c for c in containers if c.get("name") == "couchdb"), {})
        mounts = couch.get("volumeMounts",[])
        if couch.get("image") != PROFILE["image"] or not any(m.get("name")=="couchdb-pvc" and m.get("mountPath")==PROFILE["volume_mount"] for m in mounts):
            raise RuntimeError("OPS Advanced CouchDB 3.5 requires an explicit migration of the existing database; image or volume layout differs. No CouchDB resources were changed.")
    elif any(p["metadata"]["name"].startswith("couchdb-pvc-couchdb-") for p in pvcs.get("items",[])):
        raise RuntimeError("Existing CouchDB PVCs found without their StatefulSet. Migrate or restore them explicitly; automatic volume reuse is refused.")

def preflight(kubectl):
    raw = kubectl("get", "statefulset", "couchdb", "--ignore-not-found", "-o", "json")
    pvcs = json.loads(kubectl("get", "pvc", "-o", "json"))
    validate_existing(json.loads(raw) if raw.strip() else None, pvcs)
