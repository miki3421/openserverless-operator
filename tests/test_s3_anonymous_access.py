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

import json
import unittest
from unittest.mock import Mock, patch

from openserverless import seaweedfs_deploy


class AnonymousS3AccessTest(unittest.TestCase):
    @patch.object(seaweedfs_deploy.kube, "kubectl")
    def test_reconcile_legacy_grant_preserves_only_public_buckets(self, kubectl):
        kubectl.return_value = json.dumps({"items": [
            {"spec": {"object-storage": {"data": {"enabled": True, "bucket": "alpha-data"},
                                          "route": {"enabled": True, "bucket": "alpha-web"}}}},
            {"spec": {"object-storage": {"data": {"enabled": True, "bucket": "beta-data"},
                                          "route": {"enabled": False, "bucket": "beta-web"}}}},
        ]})
        client = Mock()
        client.delete_user.return_value = True
        client.add_anonymous_access.return_value = True
        seaweedfs_deploy.reconcile_anonymous_web_access(client)
        client.delete_user.assert_called_once_with("anonymous")
        client.add_anonymous_access.assert_called_once_with({"openserverless-web", "alpha-web"})

    @patch.object(seaweedfs_deploy.kube, "kubectl")
    def test_rejects_bucket_used_for_both_data_and_web(self, kubectl):
        kubectl.return_value = json.dumps({"items": [
            {"spec": {"object-storage": {"data": {"enabled": True, "bucket": "shared"},
                                          "route": {"enabled": True, "bucket": "shared"}}}},
        ]})
        client = Mock()
        client.delete_user.return_value = True
        client.add_anonymous_access.return_value = True
        with self.assertRaisesRegex(ValueError, "overlap"):
            seaweedfs_deploy.reconcile_anonymous_web_access(client)
        client.delete_user.assert_called_once_with("anonymous")
        client.add_anonymous_access.assert_called_once_with({"openserverless-web"})

    @patch.object(seaweedfs_deploy.SeaweedfsClient, "__init__", return_value=None)
    @patch.object(seaweedfs_deploy.kube, "kubectl")
    def test_new_route_cannot_expose_another_tenants_private_bucket(self, kubectl, client_init):
        kubectl.return_value = json.dumps({"items": [
            {"spec": {"object-storage": {"data": {"enabled": True, "bucket": "alpha-data"}}}},
        ]})
        config = Mock()
        config.get.side_effect = lambda key: {
            "namespace": "beta", "object-storage.password": "unused",
            "object-storage.route.enabled": True,
            "object-storage.route.bucket": "alpha-data",
            "object-storage.data.enabled": False,
        }.get(key)
        with self.assertRaisesRegex(ValueError, "overlap"):
            seaweedfs_deploy.create_ow_storage({}, config, Mock())
        client_init.assert_called_once_with()

    @patch.object(seaweedfs_deploy.SeaweedfsClient, "__init__", return_value=None)
    @patch.object(seaweedfs_deploy.kube, "kubectl")
    def test_new_data_cannot_reuse_public_bucket(self, kubectl, client_init):
        kubectl.return_value = json.dumps({"items": [
            {"spec": {"object-storage": {"route": {"enabled": True, "bucket": "alpha-web"}}}},
        ]})
        config = Mock()
        config.get.side_effect = lambda key: {
            "namespace": "beta", "object-storage.password": "unused",
            "object-storage.route.enabled": False,
            "object-storage.data.enabled": True,
            "object-storage.data.bucket": "alpha-web",
        }.get(key)
        with self.assertRaisesRegex(ValueError, "overlap"):
            seaweedfs_deploy.create_ow_storage({}, config, Mock())
        client_init.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
