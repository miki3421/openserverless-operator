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
import unittest
from contextlib import ExitStack
from unittest.mock import patch
from openserverless import couchdb
from openserverless.couchdb_util import CouchDB
from unittest.mock import Mock
from openserverless.couchdb_profile import PROFILE, preflight, validate_existing

class CouchDBAdvancedTests(unittest.TestCase):
    def current(self):
        return {"spec":{"template":{"spec":{"containers":[{"name":"couchdb","image":PROFILE["image"],"volumeMounts":[{"name":"couchdb-pvc","mountPath":PROFILE["volume_mount"]}]}]}}}}
    def test_fresh_install(self):
        validate_existing(None,{"items":[]})
    def test_current_profile_can_repeat(self):
        validate_existing(self.current(),{"items":[]})
    def test_legacy_image_refused(self):
        existing=self.current();existing["spec"]["template"]["spec"]["containers"][0]["image"]="apache/couchdb:2.3"
        with self.assertRaisesRegex(RuntimeError,"explicit migration"):
            validate_existing(existing,{"items":[]})
    def test_wrong_layout_refused(self):
        existing=self.current();existing["spec"]["template"]["spec"]["containers"][0]["volumeMounts"][0]["mountPath"]="/other"
        with self.assertRaisesRegex(RuntimeError,"volume layout"):
            validate_existing(existing,{"items":[]})
    def test_orphaned_couchdb_pvc_refused(self):
        with self.assertRaisesRegex(RuntimeError,"automatic volume reuse"):
            validate_existing(None,{"items":[{"metadata":{"name":"couchdb-pvc-couchdb-0"}}]})
        validate_existing(None,{"items":[{"metadata":{"name":"unrelated"}}]})
    def test_preflight_only_reads_and_propagates_api_errors(self):
        calls=[]
        def read(*args):
            calls.append(args);return "" if args[1]=="statefulset" else '{"items":[]}'
        preflight(read)
        self.assertTrue(all(a[0]=="get" for a in calls))
        with self.assertRaisesRegex(RuntimeError,"Forbidden"):
            preflight(lambda *a: (_ for _ in ()).throw(RuntimeError("Forbidden")))
    def test_legacy_rejection_precedes_mutation(self):
        with patch.object(couchdb.cfg,"get",return_value="k3s"),patch.object(couchdb.couchdb_profile,"preflight",side_effect=RuntimeError("explicit migration")),patch.object(couchdb.kus,"secretLiteral") as mutation:
            with self.assertRaisesRegex(RuntimeError,"explicit migration"):
                couchdb.create()
            mutation.assert_not_called()
    def test_unvalidated_openshift_image_is_not_deployed(self):
        with patch.object(couchdb.cfg,"get",return_value="openshift"),patch.object(couchdb.kube,"kubectl") as api,patch.object(couchdb.kus,"secretLiteral") as mutation:
            with self.assertRaisesRegex(RuntimeError,"validated OpenShift image"):
                couchdb.create()
            api.assert_not_called()
            mutation.assert_not_called()
    def test_task_catalogue_matches_profile(self):
        catalog=Path(__file__).resolve().parents[2]/"oplugins/opsroot.json"
        if not catalog.exists():
            self.skipTest("task sibling not mounted in this build test")
        self.assertEqual(json.loads(catalog.read_text())["config"]["images"]["couchdb"],PROFILE["image"])
    def test_initializer_does_not_log_credentials(self):
        secret="synthetic-password-must-not-be-logged"
        with ExitStack() as stack:
            stack.enter_context(patch.dict(couchdb.os.environ,{"OPENSERVERLESS_CONFIG":json.dumps({"couchdb":{"admin":{"password":secret}}})}))
            stack.enter_context(patch.object(couchdb.cfg,"configure"))
            stack.enter_context(patch.object(couchdb.cfg,"getall",return_value={"couchdb.admin.password":secret}))
            stack.enter_context(patch.object(couchdb.cfg,"get",return_value=secret))
            stack.enter_context(patch.object(couchdb.util,"wait_for_pod_ready"))
            stack.enter_context(patch.object(couchdb.openserverless.couchdb_util,"CouchDB"))
            for name in ["init_system","init_subjects","init_activations","init_actions","add_initial_subjects","init_users_metadata","init_compactions_config"]:
                stack.enter_context(patch.object(couchdb,name,return_value=True))
            with self.assertLogs(level="INFO") as captured:
                self.assertFalse(couchdb.init())
            self.assertNotIn(secret,"\n".join(captured.output))
    def test_single_node_setup_accepts_declaratively_enabled_server(self):
        db=CouchDB()
        db.db_session=Mock()
        db.db_session.get.return_value.status_code=200
        db.db_session.get.return_value.json.return_value={"state":"single_node_enabled"}
        self.assertTrue(db.configure_single_node())
        db.db_session.post.assert_not_called()
    def test_single_node_setup_does_not_hide_authentication_errors(self):
        db=CouchDB()
        db.db_session=Mock()
        db.db_session.get.return_value.status_code=401
        db.db_session.post.return_value.status_code=401
        self.assertFalse(db.configure_single_node())

if __name__ == "__main__":
    unittest.main()
