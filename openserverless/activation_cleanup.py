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

"""Bounded, revision-checked deletion of expired activation documents only."""
import argparse
import json
import os
import re
import sys
import time

import requests

DAY_MS = 86_400_000
DATABASE = "openserverless_activations"
BATCH_SIZE = 200
MAX_BATCHES = 1000


def retention_days(value):
    if isinstance(value, bool) or not re.fullmatch(r"0|[1-9][0-9]{0,4}", str(value)):
        raise ValueError("Activation retention must be an integer from 0 to 36500 days")
    days = int(value)
    if days > 36500:
        raise ValueError("Activation retention must be an integer from 0 to 36500 days")
    return days


class CleanupError(RuntimeError):
    """Only fixed, credential-free messages may leave the worker."""


class Client:
    def __init__(self, host, port, username, password):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]*", host):
            raise ValueError("Invalid CouchDB host")
        if not str(port).isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError("Invalid CouchDB port")
        if not username or not password:
            raise ValueError("CouchDB cleanup credentials are required")
        self.base = f"http://{host}:{int(port)}/{DATABASE}"
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.auth = (username, password)

    def request(self, method, path, **kwargs):
        try:
            with self.session.request(method, self.base + path, timeout=(5, 30),
                                      allow_redirects=False, stream=True, **kwargs) as response:
                if response.status_code not in (200, 201):
                    raise CleanupError(f"CouchDB cleanup HTTP {response.status_code}")
                body = bytearray()
                for chunk in response.iter_content(65536):
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        raise CleanupError("CouchDB cleanup response exceeds its bound")
                return json.loads(body)
        except (requests.RequestException, ValueError):
            raise CleanupError("CouchDB cleanup request failed") from None


def expired_rows(client, cutoff):
    result = client.request("GET", "/_design/activations/_view/byDate", params={
        "startkey": "0", "endkey": str(cutoff), "inclusive_end": "false",
        "limit": str(BATCH_SIZE), "reduce": "false", "update": "true",
    })
    rows = result.get("rows") if isinstance(result, dict) else None
    if not isinstance(rows, list) or len(rows) > BATCH_SIZE:
        raise CleanupError("Invalid activation cleanup view response")
    documents, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise CleanupError("Invalid activation cleanup row")
        timestamp, value, doc_id = row.get("key"), row.get("value"), row.get("id")
        if (type(timestamp) is not int or not 0 <= timestamp < cutoff
                or not isinstance(value, list) or len(value) != 2
                or not isinstance(doc_id, str) or not doc_id or doc_id.startswith("_")
                or value[0] != doc_id or doc_id in seen
                or not isinstance(value[1], str) or not re.fullmatch(r"[1-9][0-9]*-[0-9a-f]+", value[1])):
            raise CleanupError("Invalid activation cleanup row")
        seen.add(doc_id)
        documents.append({"_id": doc_id, "_rev": value[1], "_deleted": True})
    return documents


def cleanup(client, days, now_ms=None, max_batches=MAX_BATCHES):
    days = retention_days(days)
    if days == 0:
        return {"status": "disabled", "deleted": 0}
    cutoff = (int(time.time() * 1000) if now_ms is None else now_ms) - days * DAY_MS
    deleted = 0
    # Re-query the first page: deleting rows makes offset/skip pagination unsafe.
    for batch in range(max_batches + 1):
        documents = expired_rows(client, cutoff)
        if not documents:
            return {"status": "complete", "days": days, "deleted": deleted, "cutoffMs": cutoff}
        if batch == max_batches:
            raise CleanupError("Activation cleanup batch limit reached; backlog remains")
        results = client.request("POST", "/_bulk_docs", json={"docs": documents})
        if not isinstance(results, list) or len(results) != len(documents):
            raise CleanupError("Incomplete activation cleanup bulk response")
        expected = {doc["_id"] for doc in documents}
        succeeded = set()
        for result in results:
            if (not isinstance(result, dict) or result.get("id") not in expected
                    or result["id"] in succeeded or result.get("error")
                    or result.get("ok") is not True):
                raise CleanupError("Activation cleanup bulk failure; retry at the next run")
            succeeded.add(result["id"])
        deleted += len(succeeded)
        print(json.dumps({"status": "progress", "deleted": deleted}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=retention_days, required=True)
    args = parser.parse_args()
    try:
        if args.days == 0:
            print(json.dumps({"status": "disabled", "deleted": 0}))
            return 0
        client = Client(os.environ.get("COUCHDB_HOST", "couchdb"),
                        os.environ.get("COUCHDB_PORT", "5984"),
                        os.environ.get("COUCHDB_USERNAME"), os.environ.get("COUCHDB_PASSWORD"))
        try:
            print(json.dumps(cleanup(client, args.days)))
        finally:
            client.session.close()
        return 0
    except (CleanupError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
