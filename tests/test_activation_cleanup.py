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

import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from openserverless.activation_cleanup import (
    BATCH_SIZE, Client, CleanupError, DAY_MS, cleanup, retention_days,
)
from openserverless import activation_retention as retention, config as cfg


def row(name="alpha/old", timestamp=1, rev="1-abc"):
    return {"id": name, "key": timestamp, "value": [name, rev]}


class CleanupTests(unittest.TestCase):
    def run_cleanup(self, client, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return cleanup(client, 30, now_ms=31 * DAY_MS, **kwargs)

    def test_pages_are_requeried_without_skip_or_log_content(self):
        client = Mock()
        page = [row(f"alpha/{i}") for i in range(BATCH_SIZE)]
        client.request.side_effect = [
            {"rows": page}, [{"id": r["id"], "ok": True} for r in page],
            {"rows": [row("beta/old")]}, [{"id": "beta/old", "ok": True}], {"rows": []},
        ]
        self.assertEqual(self.run_cleanup(client)["deleted"], BATCH_SIZE + 1)
        for call in client.request.call_args_list:
            if call.args[0] == "GET":
                self.assertEqual(call.kwargs["params"]["inclusive_end"], "false")
                self.assertNotIn("skip", call.kwargs["params"])
                self.assertNotIn("include_docs", call.kwargs["params"])
            else:
                self.assertEqual(call.args[1], "/_bulk_docs")
                for document in call.kwargs["json"]["docs"]:
                    self.assertEqual(set(document), {"_id", "_rev", "_deleted"})

    def test_recent_boundary_design_and_malformed_rows_never_delete(self):
        invalid = [row(timestamp=DAY_MS), row(timestamp=DAY_MS+1), row(timestamp=True),
                   row(timestamp="1"), row(timestamp=-1), row("_design/activations"),
                   row(rev=""), {**row(), "value": ["different", "1-abc"]}, None]
        for item in invalid:
            client = Mock(); client.request.return_value = {"rows": [item]}
            with self.subTest(item=item), self.assertRaises(CleanupError):
                self.run_cleanup(client)
            self.assertEqual(client.request.call_count, 1)

    def test_duplicate_rows_and_oversized_page_refused(self):
        for rows in ([row(), row()], [row(str(i)) for i in range(BATCH_SIZE+1)]):
            client = Mock(); client.request.return_value = {"rows": rows}
            with self.assertRaises(CleanupError): self.run_cleanup(client)
            self.assertEqual(client.request.call_count, 1)

    def test_conflict_partial_missing_and_duplicate_bulk_results_fail(self):
        for results in ([], [{"id": "alpha/old", "error": "conflict"}],
                        [{"id": "different", "ok": True}], [{"id": "alpha/old"}]):
            client = Mock(); client.request.side_effect = [{"rows": [row()]}, results]
            with self.assertRaises(CleanupError): self.run_cleanup(client)
            self.assertEqual(client.request.call_count, 2)

    def test_timeout_and_database_failure_propagate(self):
        client = Mock(); client.request.side_effect = CleanupError("CouchDB cleanup HTTP 403")
        with self.assertRaisesRegex(CleanupError, "HTTP 403"): self.run_cleanup(client)

    def test_batch_limit_is_not_reported_as_complete(self):
        client = Mock(); client.request.side_effect = [
            {"rows": [row()]}, [{"id": "alpha/old", "ok": True}], {"rows": [row("beta/old")]},
        ]
        with self.assertRaisesRegex(CleanupError, "backlog remains"):
            self.run_cleanup(client, max_batches=1)

    def test_empty_repeat_and_exact_limit_are_successful(self):
        client = Mock(); client.request.return_value = {"rows": []}
        self.assertEqual(self.run_cleanup(client)["deleted"], 0)
        self.assertEqual(self.run_cleanup(client)["deleted"], 0)
        client.request.side_effect = [{"rows": [row()]}, [{"id": "alpha/old", "ok": True}], {"rows": []}]
        self.assertEqual(self.run_cleanup(client, max_batches=1)["status"], "complete")

    def test_disabled_does_not_connect(self):
        client = Mock()
        self.assertEqual(cleanup(client, 0), {"status": "disabled", "deleted": 0})
        client.request.assert_not_called()

    def test_days_validation(self):
        for bad in (-1, True, False, None, "", "01", "30.0", 30.0, "1e2", "30\n", 36501):
            with self.subTest(bad=bad), self.assertRaises(ValueError): retention_days(bad)
        for good in (0, 1, "30", 36500): self.assertEqual(retention_days(good), int(good))

    def test_client_binds_database_and_does_not_follow_redirect_or_proxy(self):
        client = Client("couchdb", 5984, "synthetic", "secret")
        self.assertEqual(client.base, "http://couchdb:5984/openserverless_activations")
        self.assertFalse(client.session.trust_env)
        response = Mock(status_code=302)
        response.__enter__ = Mock(return_value=response); response.__exit__ = Mock(return_value=False)
        with patch.object(client.session, "request", return_value=response) as request:
            with self.assertRaisesRegex(CleanupError, "HTTP 302"):
                client.request("GET", "/_design/activations/_view/byDate")
            self.assertFalse(request.call_args.kwargs["allow_redirects"])
            self.assertEqual(request.call_args.kwargs["timeout"], (5, 30))
        client.session.close()


class RetentionResourcesTests(unittest.TestCase):
    def setUp(self):
        cfg.clean()
        cfg.put("components.couchdb", True)
        cfg.put("operator.reference", "example/operator:test@sha256:" + "a" * 64)
        cfg.put("couchdb.controller.user", "controller")
        cfg.put("couchdb.controller.password", "synthetic-secret")

    def tearDown(self):
        cfg.clean()

    def test_job_is_bounded_and_only_references_controller_secret(self):
        cfg.put(retention.FIELD, 30)
        secret, job = retention.resources()
        spec = job["spec"]; pod = spec["jobTemplate"]["spec"]["template"]["spec"]
        self.assertFalse(spec["suspend"])
        self.assertEqual(spec["concurrencyPolicy"], "Forbid")
        self.assertEqual(spec["timeZone"], "Etc/UTC")
        self.assertEqual(spec["jobTemplate"]["spec"]["activeDeadlineSeconds"], 600)
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(pod["containers"][0]["command"][-2:], ["--days", "30"])
        self.assertNotIn("synthetic-secret", str(job))
        self.assertEqual(secret["stringData"]["user"], "controller")
        self.assertEqual(pod["containers"][0]["image"],
                         "example/operator:test@sha256:" + "a" * 64)

    def test_mutable_operator_reference_is_rejected(self):
        cfg.put("operator.reference", "example/operator:latest")
        with self.assertRaisesRegex(ValueError, "digest-qualified"):
            retention.resources()

    def test_old_cr_missing_field_and_explicit_zero_are_suspended(self):
        self.assertTrue(retention.resources()[1]["spec"]["suspend"])
        cfg.put(retention.FIELD, 0)
        self.assertTrue(retention.resources()[1]["spec"]["suspend"])
        self.assertEqual(retention.resources()[1]["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["command"][-1], "0")

    def test_policy_and_secret_reconcile_on_resume_without_recreating_database(self):
        cfg.put(retention.FIELD, 45)
        with patch("openserverless.couchdb.apply_couchdb_spec") as apply, patch("openserverless.couchdb.create") as create:
            retention.reconcile()
        self.assertEqual(apply.call_args.args[0]["items"][1]["kind"], "CronJob")
        create.assert_not_called()

    def test_invalid_configuration_precedes_apply(self):
        cfg.put(retention.FIELD, -1)
        with patch("openserverless.couchdb.apply_couchdb_spec") as apply:
            with self.assertRaises(ValueError): retention.reconcile()
        apply.assert_not_called()

    def test_disabling_couchdb_removes_only_the_cleanup_resources(self):
        cfg.put("components.couchdb", False)
        with patch("openserverless.kube.delete") as delete:
            retention.reconcile()
        self.assertEqual([(x["kind"], x["metadata"]["name"]) for x in delete.call_args.args[0]["items"]],
                         [("CronJob", "activation-cleanup"), ("Secret", "activation-cleanup-auth")])

    def test_owner_reference_is_attached_to_job_and_secret(self):
        owner = {"apiVersion": "openserverless.org/v1", "kind": "Whisk",
                 "metadata": {"name": "controller", "namespace": "openserverless", "uid": "synthetic-uid"}}
        with patch("openserverless.couchdb.apply_couchdb_spec") as apply:
            retention.reconcile(owner)
        for item in apply.call_args.args[0]["items"]:
            self.assertEqual(item["metadata"]["ownerReferences"][0]["uid"], "synthetic-uid")

    def test_resume_and_update_handlers_reconcile_the_policy(self):
        from openserverless import main
        with patch.object(main.operator_util, "config_from_spec"), \
             patch.object(main.operator_util, "whisk_post_resume"), \
             patch.object(main.kube, "get", return_value={"metadata": {"name": "controller"}}), \
             patch.object(main.activation_retention, "reconcile") as reconcile, \
             patch.object(main.messaging_security, "reconcile"), \
             patch.object(main.monitoring, "reconcile"), \
             patch.object(main.seaweedfs, "reconcile"), \
             patch.object(main.patcher, "patch"):
            main.whisk_resume({}, {}, "controller")
            main.whisk_update({}, {}, "openserverless", [], "controller")
        self.assertEqual(reconcile.call_count, 2)


if __name__ == "__main__":
    unittest.main()
