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
import hashlib
import json
import logging
import time
from pathlib import Path
import openserverless.kube as kube
import openserverless.config as cfg


def absent(kind, *names, namespace=None):
    # Authentication, transport and API errors must not be mistaken for absence.
    return not kube.kubectl("get", kind, *names, "--ignore-not-found", "-o", "name",
                            namespace=namespace, timeout=60).strip()


def wait_for_admission(timeout=300):
    deadline = time.monotonic() + timeout
    probe = {"apiVersion": "cert-manager.io/v1", "kind": "Issuer",
             "metadata": {"generateName": "ops-admission-probe-", "namespace": "cert-manager"},
             "spec": {"selfSigned": {}}}
    while True:
        try:
            return kube.kubectl("create", "--dry-run=server", "--request-timeout=10s",
                                "-f", "-", "-o", "name", namespace=None,
                                input=json.dumps(probe), timeout=15)
        except Exception as error:
            if time.monotonic() >= deadline:
                raise RuntimeError("cert-manager admission is not ready") from error
            time.sleep(2)

def create(owner=None):
    logging.info("*** Configuring certificate manager")
    if not absent("service", "cert-manager", namespace="cert-manager"):
        # Preserve externally installed certificate managers, including older versions.
        return "certificate manager is already installed...skipping setup"
    crds = ("certificates.cert-manager.io", "certificaterequests.cert-manager.io",
            "issuers.cert-manager.io", "clusterissuers.cert-manager.io",
            "orders.acme.cert-manager.io", "challenges.acme.cert-manager.io")
    if not absent("namespace", "cert-manager") or not absent("crd", *crds):
        raise RuntimeError("Incomplete cert-manager installation requires reviewed recovery; refusing overwrite")
    version = json.loads(kube.kubectl("version", "-o", "json", namespace=None, timeout=60))["serverVersion"]
    if version["major"] != "1" or version["minor"].rstrip("+") not in ("33", "34", "35", "36"):
        raise RuntimeError("This cert-manager candidate supports Kubernetes 1.33–1.36")
    spec = "deploy/cert-manager/cert-manager.yaml"
    release = json.loads(Path("deploy/cert-manager/release.json").read_text())
    if hashlib.sha256(Path(spec).read_bytes()).hexdigest() != release["manifestSha256"]:
        raise RuntimeError("cert-manager manifest integrity mismatch")
    res = kube.kubectl("apply", "--server-side", "--field-manager=ops-cert-manager",
                       "-f", spec, namespace=None, timeout=180)
    cfg.put("state.cm.spec", spec)
    for name in ("cert-manager", "cert-manager-cainjector", "cert-manager-webhook"):
        kube.kubectl("rollout", "status", f"deployment/{name}", "--timeout=300s",
                     namespace="cert-manager", timeout=330)
    wait_for_admission()
    return res

def delete():
    spec = cfg.get("state.cm.spec")
    res = False
    if spec:
        res = kube.kubectl("delete", "-f", spec, namespace=None)
        return res
