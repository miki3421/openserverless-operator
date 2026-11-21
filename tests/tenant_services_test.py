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
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import openserverless.tenant_services as enable
from openserverless.milvus_admin_client import MilvusAdminClient
from openserverless.user_handlers import whisk_user_enable_services


class EnablementTests(unittest.TestCase):
    def setUp(self):
        self.uid = str(uuid4())
        self.request = {"tenantUid": self.uid, "requestId": str(uuid4()), "services": ["mongodb", "milvus"]}
        self.body = {
            "metadata": {"name": "addonly", "uid": self.uid, "annotations": {
                enable.REQUEST_ANNOTATION: json.dumps(self.request)}},
            "spec": {"namespace": "addonly", "password": "existing-password", "auth": "existing-api-key",
                     "redis": {"enabled": True, "password": "existing-redis"},
                     **{name: {"enabled": False, "database": "addonly", "password": "service-secret"}
                        for name in self.request["services"]}},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
        self.doc = {"_id": "fixture", "_rev": "4-fixture", "login": "addonly", "password": "existing-hash",
                    "password_timestamp": "original-time", "metadata": [{"key": "REDIS_URL", "value": "redis-fixture"}],
                    "quota": [{"key": "REDIS", "value": "12"}]}
        self.db = Mock()
        self.db.find_doc.side_effect = lambda *args: {"docs": [copy.deepcopy(self.doc)]}
        self.db.update_doc.return_value = True
        self.patch = SimpleNamespace(spec={}, status={})
        for target, replacement in (
            (enable.cfg, {"get": Mock(return_value=True)}),
            (enable.couchdb, {"CouchDB": Mock(return_value=self.db)}),
            (enable, {"service_metadata": Mock(side_effect=self.provider)}),
        ):
            context = patch.multiple(target, **replacement)
            context.start()
            self.addCleanup(context.stop)

    def provider(self, config, service):
        return [{"key": name, "value": "new-service-value"} for name in sorted(enable.SERVICE_KEYS[service])]

    def test_updates_only_selected_flags_and_merges_existing_couchdb_revision(self):
        whisk_user_enable_services(self.body, self.patch)
        self.assertEqual(self.patch.spec, {name: {"enabled": True} for name in self.request["services"]})
        self.assertEqual(self.patch.status[enable.STATUS_KEY], {
            "state": "ready", "requestId": self.request["requestId"], "services": self.request["services"],
        })
        old, updated = self.doc, self.db.update_doc.call_args.args[1]
        self.assertEqual(self.db.update_doc.call_args.kwargs, {"refresh_revision": False})
        self.assertEqual({k: updated[k] for k in old if k != "metadata"}, {k: old[k] for k in old if k != "metadata"})
        self.assertEqual(updated["metadata"][0], old["metadata"][0])
        self.assertEqual(set(e["key"] for e in updated["metadata"][1:]), set.union(*enable.SERVICE_KEYS.values()))
        self.assertNotIn("service-secret", json.dumps(self.patch.status))

    def test_recovers_after_metadata_update_before_spec_acknowledgement(self):
        enable.reconcile(self.body, self.patch)
        self.doc = self.db.update_doc.call_args.args[1]
        self.db.update_doc.reset_mock()
        enable.service_metadata.reset_mock()
        enable.reconcile(self.body, SimpleNamespace(spec={}, status={}))
        enable.service_metadata.assert_not_called()
        self.db.update_doc.assert_not_called()

    def test_rejects_terminating_foreign_or_unscoped_target_without_external_calls(self):
        original = copy.deepcopy(self.body)
        for metadata in ({"uid": str(uuid4())}, {"deletionTimestamp": "terminating"}):
            self.body["metadata"].update(metadata)
            enable.reconcile(self.body, self.patch)
            self.assertEqual(self.patch.spec, {})
            self.body = copy.deepcopy(original)
        self.body["spec"]["milvus"]["database"] = "neighbor"
        enable.reconcile(self.body, self.patch)
        self.assertEqual(self.patch.status[enable.STATUS_KEY]["state"], "refused")
        enable.service_metadata.assert_not_called()
        self.db.update_doc.assert_not_called()

    def test_unavailable_backend_and_revision_conflict_leave_spec_and_status_pending(self):
        enable.service_metadata.side_effect = [self.provider(None, "mongodb"), enable.RetryEnablement()]
        with self.assertRaises(enable.RetryEnablement):
            enable.reconcile(self.body, self.patch)
        self.db.update_doc.assert_not_called()
        enable.service_metadata.side_effect = self.provider
        self.db.update_doc.return_value = False
        with self.assertRaises(enable.RetryEnablement):
            enable.reconcile(self.body, self.patch)
        self.assertEqual((self.patch.spec, self.patch.status), ({}, {}))


class MilvusResumeTests(unittest.TestCase):
    def test_resumes_scoped_grants_without_replacing_database_account_or_role(self):
        existing = Mock()
        existing.list_users.return_value = ["addonly"]
        existing.list_databases.return_value = ["default", "addonly"]
        existing.list_roles.return_value = ["addonly_role"]
        tenant = Mock()
        tenant.list_collections.return_value = []
        with patch("openserverless.milvus_admin_client.MilvusClient", side_effect=[existing, existing, tenant]):
            admin = MilvusAdminClient()
            self.assertTrue(admin.setup_user("addonly", "saved-secret", "addonly", resume=True))
        for attr in ("create_user", "create_database", "create_role", "drop_database", "update_password"):
            getattr(existing, attr).assert_not_called()
        existing.grant_role.assert_called_once_with(user_name="addonly", role_name="addonly_role", db_name="addonly")
        tenant.list_collections.assert_called_once()


if __name__ == "__main__":
    unittest.main()
