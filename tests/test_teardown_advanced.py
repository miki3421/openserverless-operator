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
import unittest
from unittest.mock import patch
from openserverless import kube, seaweedfs_ingress as ingress

class TeardownTests(unittest.TestCase):
    def test_missing_ingress_and_middleware_are_successful_retries(self):
        with patch.object(ingress.kube, "kubectl", return_value="") as command, patch.object(ingress.util, "get_traefik_middleware_resource", return_value="middleware.traefik.io"):
            self.assertEqual(ingress.delete_seaweedfs_ingress("k3s", "openserverless", "traefik", "seaweedfs-s3"), "")
            self.assertEqual(command.call_count, 2)
            self.assertTrue(all("--ignore-not-found" in call.args for call in command.call_args_list))

    def test_cleanup_preserves_output_from_both_resources(self):
        with patch.object(ingress.kube, "kubectl", side_effect=["ingress deleted\n", "middleware deleted\n"]), patch.object(ingress.util, "get_traefik_middleware_resource", return_value="middleware.traefik.io"):
            self.assertEqual(ingress.delete_seaweedfs_ingress("k3s", "openserverless", "traefik", "seaweedfs-s3"), "ingress deleted\nmiddleware deleted\n")

    def test_forbidden_cleanup_reaches_operator_for_retry(self):
        with patch.object(ingress.kube, "kubectl", side_effect=RuntimeError("Forbidden")):
            with self.assertRaisesRegex(RuntimeError, "Forbidden"):
                ingress.delete_seaweedfs_ingress("k3s", "openserverless", "nginx", "seaweedfs-s3")

    def test_openshift_route_deletion_is_idempotent(self):
        with patch.object(ingress.kube, "kubectl", return_value="") as command:
            self.assertEqual(ingress.delete_seaweedfs_ingress("openshift", "openserverless", "", "seaweedfs-s3"), "")
            self.assertEqual(command.call_args.args[1], "route")
            self.assertIn("--ignore-not-found", command.call_args.args)

    def test_generic_manifest_delete_is_idempotent_but_propagates_errors(self):
        with patch.object(kube, "kubectl", return_value="") as command:
            self.assertEqual(kube.delete({"kind":"ConfigMap","metadata":{"name":"gone"}}), "")
            self.assertIn("--ignore-not-found", command.call_args.args)
        with patch.object(kube, "kubectl", side_effect=RuntimeError("Forbidden")):
            with self.assertRaisesRegex(RuntimeError, "Forbidden"):
                kube.delete({"kind":"ConfigMap","metadata":{"name":"gone"}})

if __name__ == "__main__":
    unittest.main()
