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
"""Add MongoDB and Milvus to an existing WhiskUser through an OPS request."""

import json
import re
from uuid import UUID

from pymongo import MongoClient
import openserverless.config as cfg
import openserverless.couchdb_util as couchdb
import openserverless.ferretdb as mongodb
import openserverless.milvus_standalone as milvus
from openserverless.user_config import UserConfig
from openserverless.user_metadata import UserMetadata
from openserverless.userdb_util import USER_META_DBN


REQUEST_ANNOTATION = "openserverless.org/enable-services"
STATUS_KEY = "serviceEnablement"
SERVICE_KEYS = {
    "mongodb": {"MONGODB_URL"},
    "milvus": {"MILVUS_HOST", "MILVUS_PORT", "MILVUS_TOKEN", "MILVUS_DB_NAME"},
}


class RetryEnablement(Exception):
    """The same request may retry while a service or metadata is unavailable."""


def request_from(body):
    metadata = body.get("metadata", {})
    raw = metadata.get("annotations", {}).get(REQUEST_ANNOTATION)
    if not isinstance(raw, str) or len(raw) > 512 or metadata.get("deletionTimestamp"):
        return None
    try:
        value = json.loads(raw)
        request_id = value["requestId"]
        services = value["services"]
        if (set(value) == {"requestId", "tenantUid", "services"}
                and str(UUID(request_id)) == request_id
                and value["tenantUid"] and value["tenantUid"] == metadata["uid"]
                and isinstance(services, list) and 1 <= len(services) <= len(SERVICE_KEYS)
                and len(set(services)) == len(services) and set(services) <= SERVICE_KEYS.keys()):
            return value
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
    return None


def document_for(db, namespace):
    selector = {"selector": {"login": {"$eq": namespace}}}
    result = db.find_doc(USER_META_DBN, json.dumps(selector))
    docs = result.get("docs", []) if isinstance(result, dict) else []
    if len(docs) != 1 or docs[0].get("login") != namespace:
        raise RetryEnablement()
    doc = docs[0]
    if not doc.get("_id") or not doc.get("_rev") or not isinstance(doc.get("metadata"), list):
        raise RetryEnablement()
    return doc


def service_metadata(config, service):
    generated = UserMetadata(config)
    if service == "mongodb":
        result = mongodb.create_db_user(config, generated)
    else:
        result = milvus.create_ow_milvus(config, generated, resume=True)
    entries = generated.get_metadata()["metadata"]
    if not result or {item["key"] for item in entries if item["value"]} != SERVICE_KEYS[service]:
        raise RetryEnablement()
    if service == "mongodb":
        with MongoClient(entries[0]["value"], serverSelectionTimeoutMS=7000,
                         connectTimeoutMS=7000, socketTimeoutMS=7000) as client:
            client.get_default_database().list_collection_names()
    return entries


def reconcile(body, patch):
    request = request_from(body)
    if request is None:
        return
    status = body.get("status", {})
    current = status.get(STATUS_KEY, {})
    request_id = request["requestId"]
    if current.get("requestId") == request_id and current.get("state") in ("ready", "refused"):
        return
    spec = body.get("spec", {})
    namespace = body["metadata"]["name"]
    services = request["services"]
    result = {"requestId": request_id, "services": services}
    if (namespace != spec.get("namespace") or not re.fullmatch("[a-z0-9]{5,61}", namespace)
            or not any(condition.get("type") == "Ready" and condition.get("status") == "True"
                       for condition in status.get("conditions", []))):
        patch.status[STATUS_KEY] = {**result, "state": "refused", "reason": "TenantNotReady"}
        return
    for service in services:
        settings = spec.get(service, {})
        if (cfg.get("components." + service) is not True or settings.get("database") != namespace
                or (service == "mongodb" and len(namespace + "_ferretdb") > 63)
                or not isinstance(settings.get("password"), str) or not settings["password"]):
            patch.status[STATUS_KEY] = {**result, "state": "refused", "reason": "ServiceUnavailableOrUnscoped"}
            return

    config = UserConfig(spec)
    try:
        db = couchdb.CouchDB()
        doc = document_for(db, namespace)
        entries = doc["metadata"]
        metadata_keys = {item["key"] for item in entries}
        proof = doc.get("service_enablements", {})
        added = {}
        for service in services:
            keys = SERVICE_KEYS[service]
            recorded_uid = proof.get(service)
            if keys <= metadata_keys and (recorded_uid == request["tenantUid"] or spec[service]["enabled"] is True):
                continue
            if keys & metadata_keys or recorded_uid is not None:
                patch.status[STATUS_KEY] = {**result, "state": "refused", "reason": "ExistingServiceMetadataConflict"}
                return
            added[service] = service_metadata(config, service)
        if added:
            doc["metadata"] = entries + [entry for group in added.values() for entry in group]
            doc["service_enablements"] = {**proof, **dict.fromkeys(added, request["tenantUid"])}
            if not db.update_doc(USER_META_DBN, doc, refresh_revision=False):
                raise RetryEnablement()
    except Exception:
        # Backend errors can include credentials. Kopf receives a fixed error.
        raise RetryEnablement() from None

    for service in services:
        if spec[service]["enabled"] is not True:
            patch.spec[service] = {"enabled": True}
    patch.status[STATUS_KEY] = {**result, "state": "ready"}
