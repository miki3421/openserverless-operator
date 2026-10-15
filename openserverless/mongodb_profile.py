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

"""Pinned native MongoDB resources and preflight guards for fresh installs."""
import json
from pathlib import Path

PROFILE = json.loads((Path(__file__).parent / "files/mongodb-profile.json").read_text())
NAME = "openserverless-mongodb"
SERVICE = NAME + "-svc"
PROBE = """const fs=require('fs');
const user=fs.readFileSync('/run/mongodb-auth/username','utf8');
const password=fs.readFileSync('/run/mongodb-auth/password','utf8');
try {
 const c=new Mongo('mongodb://'+encodeURIComponent(user)+':'+encodeURIComponent(password)+'@127.0.0.1:27017/admin?directConnection=true&authMechanism=SCRAM-SHA-256');
 const result=c.getDB('admin').runCommand({hello:1});
 quit(result.isWritablePrimary ? 0 : 1);
} catch (_) { quit(1); }
"""

def validate_existing(existing, pvcs):
    if existing:
        pod = existing.get("spec", {}).get("template", {})
        container = next((c for c in pod.get("spec", {}).get("containers", []) if c.get("name") == "mongodb"), {})
        if container.get("image") != PROFILE["image"] or pod.get("metadata", {}).get("labels", {}).get("ops-advanced/backend") != PROFILE["backend"]:
            raise RuntimeError("Native MongoDB requires a fresh installation; an existing Mongo/FerretDB StatefulSet cannot be upgraded implicitly.")
        if not any(m.get("name") == NAME + "-pvc" and m.get("mountPath") == PROFILE["volume_mount"] for m in container.get("volumeMounts", [])):
            raise RuntimeError("Native MongoDB volume layout differs; automatic volume reuse is refused.")
        if "--auth" not in container.get("args", []) or "--replSet" not in container.get("args", []):
            raise RuntimeError("Native MongoDB authentication or replica-set settings differ.")
    elif any(p["metadata"]["name"].startswith(NAME + "-pvc-") for p in pvcs.get("items", [])):
        raise RuntimeError("Orphaned Mongo/FerretDB PVC found; automatic volume reuse is refused.")

def preflight(kubectl, namespace="openserverless"):
    raw = kubectl("get", "sts", NAME, "--ignore-not-found", "-o", "json", namespace=namespace)
    pvcs = json.loads(kubectl("get", "pvc", "-o", "json", namespace=namespace))
    validate_existing(json.loads(raw) if raw.strip() else None, pvcs)
    return bool(raw.strip())

def resources(namespace="openserverless", size=10, storage_class="local-path", affinity=False, tolerations=False):
    args = ["--bind_ip_all", "--auth", "--replSet", PROFILE["replica_set"], "--keyFile", "/run/mongodb-keyfile/keyfile",
            "--wiredTigerCacheSizeGB", PROFILE["wired_tiger_cache_gib"], "--setParameter", "authenticationMechanisms=SCRAM-SHA-256"]
    metadata = lambda name: {"name": name, "namespace": namespace}
    security = {"runAsUser":999,"runAsGroup":999,"runAsNonRoot":True,"allowPrivilegeEscalation":False,"capabilities":{"drop":["ALL"]},"seccompProfile":{"type":"RuntimeDefault"}}
    container = {"name":"mongodb","image":PROFILE["image"],"imagePullPolicy":"IfNotPresent","args":args,
                 "env":[{"name":"HOME","value":"/tmp"},{"name":"MONGO_INITDB_ROOT_USERNAME_FILE","value":"/run/mongodb-auth/username"},{"name":"MONGO_INITDB_ROOT_PASSWORD_FILE","value":"/run/mongodb-auth/password"}],
                 "ports":[{"containerPort":27017,"name":"mongodb"}],"securityContext":security,
                 "resources":{"requests":{"cpu":"100m","memory":"512Mi"},"limits":{"memory":"2Gi"}},
                 "volumeMounts":[{"name":NAME+"-pvc","mountPath":PROFILE["volume_mount"]},{"name":"auth","mountPath":"/run/mongodb-auth","readOnly":True},{"name":"keyfile","mountPath":"/run/mongodb-keyfile","readOnly":True},{"name":"probe","mountPath":"/run/mongodb-probe","readOnly":True}],
                 "startupProbe":{"tcpSocket":{"port":27017},"failureThreshold":60,"periodSeconds":5},
                 "readinessProbe":{"exec":{"command":["mongosh","--quiet","--nodb","/run/mongodb-probe/check.js"]},"periodSeconds":10,"timeoutSeconds":10},
                 "livenessProbe":{"tcpSocket":{"port":27017},"periodSeconds":10,"failureThreshold":6}}
    sts={"apiVersion":"apps/v1","kind":"StatefulSet","metadata":metadata(NAME),"spec":{"serviceName":SERVICE,"replicas":1,"selector":{"matchLabels":{"app":NAME}},
         "template":{"metadata":{"annotations":{"whisks.openserverless.org/annotate-version":"true"},"labels":{"app":NAME,"name":NAME,"ops-advanced/backend":PROFILE["backend"]}},"spec":{"automountServiceAccountToken":False,"securityContext":{"fsGroup":999},
                      "initContainers":[{"name":"keyfile","image":PROFILE["image"],"command":["sh","-c","cp /run/source/keyfile /run/target/keyfile && chmod 400 /run/target/keyfile"],"securityContext":security,"volumeMounts":[{"name":"auth","mountPath":"/run/source","readOnly":True},{"name":"keyfile","mountPath":"/run/target"}]}],
                      "containers":[container],"volumes":[{"name":"auth","secret":{"secretName":PROFILE["secret"],"defaultMode":288}},{"name":"keyfile","emptyDir":{}},{"name":"probe","configMap":{"name":NAME+"-probe"}}]}},
         "volumeClaimTemplates":[{"metadata":{"name":NAME+"-pvc"},"spec":{"accessModes":["ReadWriteOnce"],"storageClassName":storage_class,"resources":{"requests":{"storage":str(size)+"Gi"}}}}]}}
    pod=sts["spec"]["template"]["spec"]
    if affinity:
        pod["affinity"]={"nodeAffinity":{"preferredDuringSchedulingIgnoredDuringExecution":[
            {"weight":100,"preference":{"matchExpressions":[{"key":"openserverless-role","operator":"NotIn","values":["invoker"]}]}},
            {"weight":80,"preference":{"matchExpressions":[{"key":"openserverless-role","operator":"In","values":["core"]}]}}]},
            "podAntiAffinity":{"requiredDuringSchedulingIgnoredDuringExecution":[{"labelSelector":{"matchLabels":{"app":NAME}},"topologyKey":"kubernetes.io/hostname"}]}}
    if tolerations:
        pod["tolerations"]=[{"key":"openserverless-role","operator":"Equal","value":"core","effect":"NoSchedule"}]
    svc={"apiVersion":"v1","kind":"Service","metadata":metadata(SERVICE),"spec":{"clusterIP":"None","publishNotReadyAddresses":True,"selector":{"app":NAME},"ports":[{"name":"mongodb","port":27017,"targetPort":27017}]}}
    cm={"apiVersion":"v1","kind":"ConfigMap","metadata":metadata(NAME+"-probe"),"data":{"check.js":PROBE}}
    return {"apiVersion":"v1","kind":"List","items":[svc,cm,sts]}
