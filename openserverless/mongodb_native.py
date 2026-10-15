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

"""Native MongoDB deployment and OPS metadata; PostgreSQL is independent."""
import base64
import json
import logging
import os
import secrets
import subprocess
import kopf
from pymongo.errors import PyMongoError
import openserverless.config as cfg
import openserverless.kube as kube
import openserverless.operator_util as operator_util
from openserverless.mongodb_client import NativeMongo, tenant_names, tenant_uri
from openserverless.mongodb_profile import PROFILE, NAME, SERVICE, resources, preflight

def _namespace():
    return os.environ.get("OPS_MONGODB_NAMESPACE","openserverless")
def _host():
    return f"{SERVICE}.{_namespace()}.svc.cluster.local"
def _secret(existing_required=False,owner=None):
    raw=kube.kubectl("get","secret",PROFILE["secret"],"--ignore-not-found","-o","json",namespace=_namespace(),debugresult=False)
    if raw.strip():
        data=json.loads(raw)["data"]
        try:
            credentials={k:base64.b64decode(data[k],validate=True).decode() for k in ["username","password","keyfile","system-password"]}
        except (KeyError,ValueError,UnicodeError):
            raise RuntimeError("Native MongoDB credentials are incomplete or invalid") from None
        if any(not value for value in credentials.values()):
            raise RuntimeError("Native MongoDB credentials cannot be empty")
        return credentials
    if existing_required:
        raise RuntimeError("Existing MongoDB credentials are missing; refusing to rotate credentials for existing data")
    credentials={"username":"ops-admin","password":secrets.token_hex(32),"keyfile":base64.b64encode(secrets.token_bytes(96)).decode(),"system-password":secrets.token_hex(32)}
    obj={"apiVersion":"v1","kind":"Secret","metadata":{"name":PROFILE["secret"],"namespace":_namespace()},"type":"Opaque","stringData":credentials}
    if owner:
        kopf.append_owner_reference(obj,owner)
    # Do not use the generic kubectl error logger: its input contains credentials.
    result=subprocess.run(["kubectl","-n",_namespace(),"create","-f","-"],input=json.dumps(obj).encode(),capture_output=True)
    if result.returncode:
        raise RuntimeError(f"Unable to create native MongoDB credentials (kubectl exit {result.returncode})")
    return credentials
def admin_client():
    user=os.environ.get("MONGODB_ADMIN_USER")
    password=os.environ.get("MONGODB_ADMIN_PASSWORD")
    if not user or not password:
        credentials=_secret(existing_required=True)
        user,password=credentials["username"],credentials["password"]
    return NativeMongo(_host(),user,password,PROFILE["replica_set"])
def _operation(name,fn):
    client=admin_client()
    try:
        return fn(client)
    except PyMongoError as exc:
        raise RuntimeError(f"Native MongoDB {name} failed (code {getattr(exc,'code',None)})") from None
    finally:
        client.close()
def create(owner=None):
    if cfg.get("openserverless.kube")=="openshift":
        raise RuntimeError("Native MongoDB needs a validated OpenShift image before deployment")
    if str(cfg.get("openserverless.hostpath")).lower()=="true":
        raise RuntimeError("Native MongoDB requires a validated PVC storage class; legacy static hostpath volumes are not adopted")
    existing=preflight(kube.kubectl,_namespace())
    credentials=_secret(existing,owner)
    spec=resources(_namespace(),cfg.get("mongodb.volume-size") or 10,cfg.get("openserverless.storageclass"),str(cfg.get("openserverless.affinity")).lower()=="true",str(cfg.get("openserverless.tolerations")).lower()=="true")
    if owner:
        kopf.append_owner_reference(spec["items"],owner)
    kube.apply(spec,namespace=_namespace())
    client=NativeMongo(_host(),credentials["username"],credentials["password"],PROFILE["replica_set"])
    try:
        member=f"{NAME}-0.{_host()}"
        client.initialize(member)
        client.provision("openserverless",credentials["system-password"],"openserverless")
    except PyMongoError as exc:
        raise RuntimeError(f"Native MongoDB setup failed (code {getattr(exc,'code',None)})") from None
    finally:
        client.close()
    uri=tenant_uri(_host(),"openserverless",credentials["system-password"],"openserverless",PROFILE["replica_set"])
    result=subprocess.run(["kubectl","-n",_namespace(),"patch","cm/config","--type=merge","--patch-file=/dev/stdin"],
                          input=json.dumps({"metadata":{"annotations":{"mongodb_url":uri}}}).encode(),capture_output=True)
    if result.returncode:
        raise RuntimeError(f"Unable to annotate MongoDB system metadata (kubectl exit {result.returncode})")
    return True
def create_db_user(ucfg,user_metadata):
    user,db=tenant_names(ucfg.get("namespace"),ucfg.get("mongodb.database"))
    password=ucfg.get("mongodb.password")
    _operation("tenant provisioning",lambda client:client.provision(user,password,db))
    user_metadata.add_metadata("MONGODB_URL",tenant_uri(_host(),user,password,db,PROFILE["replica_set"]))
    return True
def delete_db_user(namespace,database):
    user,db=tenant_names(namespace,database)
    raw=kube.kubectl("get","sts",NAME,"--ignore-not-found","-o","json",namespace=_namespace())
    if not raw.strip():
        return True  # Component already gone; do not recreate data or credentials.
    return _operation("tenant removal",lambda client:client.remove(user,db))
def delete(owner=None):
    # Volume claims remain for explicit future migration/restore handling.
    return kube.kubectl("delete","sts/"+NAME,"svc/"+SERVICE,"cm/"+NAME+"-probe","--ignore-not-found",namespace=_namespace())
def patch(status,action,owner=None):
    try:
        result=create(owner) if action=="create" else delete(owner)
        operator_util.patch_operator_status(status,"ferretdb","on" if action=="create" else "off")
        return result
    except Exception:
        operator_util.patch_operator_status(status,"ferretdb","error")
        raise
