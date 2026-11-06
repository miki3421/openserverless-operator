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

"""Synthetic-only retention qualification in a new, disposable local CouchDB.

Requires an already cached apache/couchdb:3.5.2 image and Docker. Creates its
own internal network and memory-backed data, without publishing any port. No
existing container, volume, database or cluster is accepted as input.
"""
import contextlib
import io
import ipaddress
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openserverless.activation_cleanup import Client, DATABASE, DAY_MS, cleanup


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def main():
    name = "ops-retention-test-" + uuid.uuid4().hex[:12]
    network = name + "-net"
    password = secrets.token_hex(24)
    docker("network", "create", "--internal", network)
    session = requests.Session(); session.trust_env = False
    session.auth = ("synthetic-admin", password)
    started = False
    try:
        docker("run", "-d", "--rm", "--pull=never", "--name", name,
               "--network", network, "--memory", "512m", "--pids-limit", "256",
               "--tmpfs", "/opt/couchdb/data:rw,uid=5984,gid=5984,size=128m",
               "-e", "ERL_FLAGS=+S 2:2", "-e", "COUCHDB_USER=synthetic-admin",
               "-e", "COUCHDB_PASSWORD=" + password, "apache/couchdb:3.5.2")
        started = True
        networks = json.loads(docker("inspect", "--format", "{{json .NetworkSettings.Networks}}", name))
        address = networks[network]["IPAddress"]
        if not ipaddress.ip_address(address).is_private: raise RuntimeError("Unexpected test binding")
        port = 5984; base = f"http://{address}:{port}"

        def request(method, path, **kwargs):
            response = session.request(method, base + path, timeout=10, **kwargs)
            response.raise_for_status()
            return response.json()

        for attempt in range(60):
            try:
                if session.get(base + "/", timeout=1).status_code == 200: break
            except requests.RequestException: pass
            time.sleep(0.5)
        else: raise RuntimeError("Synthetic CouchDB did not start")

        request("POST", "/_cluster_setup", json={"action": "enable_single_node", "bind_address": "0.0.0.0", "port": 5984})
        for db in (DATABASE, "openserverless_whisks", "openserverless_subjects"):
            request("PUT", "/" + db)
        request("PUT", "/_users/org.couchdb.user:synthetic-controller", json={
            "_id": "org.couchdb.user:synthetic-controller", "name": "synthetic-controller",
            "type": "user", "roles": [], "password": password,
        })
        request("PUT", f"/{DATABASE}/_security", json={
            "admins": {"names": ["synthetic-admin"], "roles": []},
            "members": {"names": ["synthetic-controller"], "roles": []},
        })
        design = json.loads((Path(__file__).resolve().parents[1] / "openserverless/files/activations_design_document_for_activations_db.json").read_text())
        request("PUT", f"/{DATABASE}/_design/activations", json=design)
        now = int(time.time() * 1000); cutoff = now - 30 * DAY_MS
        old = [{"_id": f"{tenant}/old-{i}", "activationId": f"old-{i}", "start": cutoff-1,
                "logs": ["synthetic"], "response": {"result": {"synthetic": True}}}
               for tenant in ("alpha", "beta") for i in range(205)]
        keep = [{"_id": "alpha/boundary", "activationId": "boundary", "start": cutoff, "logs": ["keep"]},
                {"_id": "beta/recent", "activationId": "recent", "start": cutoff+1, "logs": ["keep"]},
                {"_id": "alpha/nonactivation", "start": 1, "payload": "keep"}]
        request("POST", f"/{DATABASE}/_bulk_docs", json={"docs": old + keep})
        for db in ("openserverless_whisks", "openserverless_subjects"):
            request("PUT", f"/{db}/sentinel", json={"activationId": "sentinel", "start": 1})
        client = Client(address, port, "synthetic-controller", password)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                first = cleanup(client, 30, now_ms=now)
                repeat = cleanup(client, 30, now_ms=now)
        finally: client.session.close()
        if first["deleted"] != 410 or repeat["deleted"] != 0:
            raise RuntimeError("Pagination or repeated-run check failed")
        for doc in old:
            if session.get(base + f"/{DATABASE}/" + doc["_id"].replace("/", "%2F"), timeout=10).status_code != 404:
                raise RuntimeError("Expired activation survived")
        for doc in keep:
            actual = request("GET", f"/{DATABASE}/" + doc["_id"].replace("/", "%2F"))
            if any(actual.get(k) != v for k, v in doc.items()): raise RuntimeError("Retained document changed")
        request("GET", f"/{DATABASE}/_design/activations")
        for db in ("openserverless_whisks", "openserverless_subjects"):
            request("GET", f"/{db}/sentinel")
        print(json.dumps({"status": "PASS", "syntheticOnly": True, "deleted": 410,
                          "repeatDeleted": 0, "boundaryAndRecentPreserved": True,
                          "designAndNonActivationPreserved": True, "otherDatabasesPreserved": True,
                          "credentialRole": "database member, not server admin"}))
    finally:
        session.close()
        if started: docker("rm", "-f", name)
        docker("network", "rm", network)


if __name__ == "__main__":
    main()
