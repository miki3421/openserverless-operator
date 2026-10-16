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
# this module wraps the minio S3 client SDK using admin credentials
# to perform various operations

import subprocess
import unittest
from unittest.mock import Mock, patch
from openserverless.seaweedfs_util import SeaweedfsClient


class SeaweedShellTest(unittest.TestCase):
    def setUp(self):
        self.client = SeaweedfsClient.__new__(SeaweedfsClient)
        self.client.pod_name = "seaweedfs-0"

    @patch("openserverless.seaweedfs_util.subprocess.run")
    def test_success_without_stdout(self, run):
        for stderr in ("", "updated quota for bucket test\n"):
            run.return_value = Mock(returncode=0, stdout="", stderr=stderr)
            self.assertTrue(self.client._exec_weed_command("s3.bucket.quota -name test"))
        self.assertEqual(run.call_args.kwargs["input"], "s3.bucket.quota -name test\n")

    @patch("openserverless.seaweedfs_util.subprocess.run")
    def test_failure_never_returns_success_or_credentials(self, run):
        for code, stderr in ((1, "secret"), (0, "error: secret\n"),
                             (0, "unknown command: secret\n")):
            run.return_value = Mock(returncode=code, stdout="secret", stderr=stderr)
            with self.assertRaisesRegex(RuntimeError, "^SeaweedFS administration command failed$"):
                self.client._exec_weed_command("s3.configure -user=test")

    @patch("openserverless.seaweedfs_util.subprocess.run")
    def test_timeout_fails_closed(self, run):
        run.side_effect = subprocess.TimeoutExpired("kubectl", 60)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.client.remove_anonymous_access("test-web")

    @patch("openserverless.seaweedfs_util.subprocess.run")
    def test_command_separators_are_rejected(self, run):
        for command in ("a\nb", "a\rb", "a;b"):
            with self.assertRaises(ValueError):
                self.client._exec_weed_command(command)
        run.assert_not_called()


class SeaweedStorageGuardTest(unittest.TestCase):
    def setUp(self):
        self.spec = {'items': [{'kind': 'StatefulSet', 'metadata': {'name': 'seaweedfs'},
                     'spec': {'template': {'spec': {'containers': [{'image': 'candidate@sha256:test'}],
                     'volumes': [{'persistentVolumeClaim': {'claimName': 'seaweedfs-pvc'}}]}}}}]}

    @patch('openserverless.seaweedfs_deploy.kube.kubectl')
    def test_fresh_storage_allowed(self, command):
        from openserverless.seaweedfs_deploy import _check_storage_compatibility
        command.return_value = ''
        _check_storage_compatibility(self.spec)
        self.assertEqual(command.call_count, 2)

    @patch('openserverless.seaweedfs_deploy.kube.kubectl')
    def test_matching_image_allowed(self, command):
        import json
        from openserverless.seaweedfs_deploy import _check_storage_compatibility
        command.return_value = json.dumps(self.spec['items'][0])
        _check_storage_compatibility(self.spec)

    @patch('openserverless.seaweedfs_deploy.kube.kubectl')
    def test_old_image_refused(self, command):
        import json, copy
        from openserverless.seaweedfs_deploy import _check_storage_compatibility
        old = copy.deepcopy(self.spec['items'][0])
        old['spec']['template']['spec']['containers'][0]['image'] = 'seaweedfs:3.97'
        command.return_value = json.dumps(old)
        with self.assertRaisesRegex(RuntimeError, 'automatic data upgrades are disabled'):
            _check_storage_compatibility(self.spec)

    @patch('openserverless.seaweedfs_deploy.kube.kubectl')
    def test_orphan_volume_refused(self, command):
        from openserverless.seaweedfs_deploy import _check_storage_compatibility
        command.side_effect = ['', '{"metadata":{"name":"seaweedfs-pvc"}}']
        with self.assertRaisesRegex(RuntimeError, 'implicit data reuse is disabled'):
            _check_storage_compatibility(self.spec)
