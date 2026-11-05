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
"""Persistent installation credentials. Never infer live SQL password rotation."""
import base64
import json
import secrets
import subprocess

from openserverless import config as cfg

SECRET_NAME = "postgres-openserverless-secret"
FIELDS = {
    "superUserPassword": ("postgres.admin.password", "postgres_root_password"),
    "replicationUserPassword": (None, "postgres_root_replica_password"),
    "openserverlessUserPassword": ("postgres.openserverless.password", "postgres_openserverless_password"),
}
LEGACY_PASSWORDS = {"0therPa55", "0therPa55sd", "s0meP@ass3"}


def _run(*args, input=None):
    result = subprocess.run(["kubectl", "-n", "openserverless", *args],
                            input=input, text=True, capture_output=True, timeout=60)
    if result.returncode:
        # Neither kubectl stderr nor its Secret payload belongs in logs.
        raise RuntimeError("Could not access PostgreSQL credentials")
    return result.stdout


def _validate(value):
    if not isinstance(value, str) or not value or any(c in value for c in "\x00\r\n"):
        raise ValueError("Invalid PostgreSQL installation credential")
    if value in LEGACY_PASSWORDS:
        raise ValueError("Legacy PostgreSQL password detected; rotate SQL roles and Secret explicitly")
    return value


def credentials(create=False, owner=None):
    raw = _run("get", "secret", SECRET_NAME, "--ignore-not-found", "-o", "json")
    if raw.strip():
        data = json.loads(raw)["data"]
        values = {}
        for key, (setting, output) in FIELDS.items():
            value = _validate(base64.b64decode(data[key], validate=True).decode("utf-8"))
            configured = cfg.get(setting) if setting else None
            if configured and configured != value:
                raise ValueError("PostgreSQL configuration differs from its Secret; rotate credentials explicitly")
            values[output] = value
        return values
    if not create:
        raise RuntimeError("PostgreSQL credentials Secret is missing; refusing implicit rotation")
    # Existing data must never be paired with randomly invented passwords.
    for kind, name in [("kubegres", "openserverless-postgres"), ("pvc", None)]:
        args = ["get", kind]
        args += [name, "--ignore-not-found"] if name else ["-l", "app=openserverless-postgres"]
        current = _run(*args, "-o", "json")
        if current.strip() and (name or json.loads(current).get("items")):
            raise RuntimeError("Existing PostgreSQL resources have no credentials Secret; restore it explicitly")
    data = {}
    for key, (setting, _) in FIELDS.items():
        data[key] = _validate(cfg.get(setting) if setting and cfg.get(setting) else secrets.token_urlsafe(36))
    resource = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
                "metadata": {"name": SECRET_NAME, "namespace": "openserverless"},
                "stringData": data}
    if owner:
        import kopf
        kopf.append_owner_reference([resource], owner)
    # create is atomic: another reconciliation cannot overwrite a winning Secret.
    _run("create", "-f", "-", input=json.dumps(resource))
    return {output: data[key] for key, (_, output) in FIELDS.items()}


def apply_resources(spec):
    """Apply password-bearing resources without the generic verbose wrapper."""
    return _run("apply", "-f", "-", input=json.dumps(spec))
