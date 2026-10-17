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

import copy
import json
import unittest
import pathlib
import tempfile
import subprocess
import sys
import os
from unittest.mock import patch

import yaml
from openserverless import milvus_standalone as milvus


class MilvusStorageTest(unittest.TestCase):
    def setUp(self):
        config = {'etcd': {'rootPath': 'milvus-v3'}, 'minio': {'bucketName': 'vectors',
                  'rootPath': 'milvus-v3'}, 'mq': {'type': 'woodpecker'}}
        self.deployment = {'kind': 'Deployment', 'metadata': {'name': 'openserverless-milvus-standalone'},
            'spec': {'template': {'spec': {'containers': [{'image': 'milvus:v3@sha256:test'}],
                'volumes': [{'persistentVolumeClaim': {'claimName': 'openserverless-milvus'}}]}}}}
        self.cm = {'kind': 'ConfigMap', 'metadata': {'name': 'openserverless-milvus'},
                   'data': {'default.yaml': yaml.safe_dump(config)}}
        self.spec = {'items': [self.deployment, self.cm]}

    @patch.object(milvus.kube, 'kubectl')
    def test_fresh_install(self, command):
        command.return_value = ''
        milvus._check_storage_compatibility(self.spec)
        self.assertEqual(command.call_count, 3)

    @patch.object(milvus.kube, 'kubectl')
    def test_same_install_can_be_reconciled(self, command):
        command.side_effect = [json.dumps(self.deployment), json.dumps(self.cm)]
        milvus._check_storage_compatibility(self.spec)

    @patch.object(milvus.kube, 'kubectl')
    def test_engine_upgrade_refused(self, command):
        old = copy.deepcopy(self.deployment)
        old['spec']['template']['spec']['containers'][0]['image'] = 'milvus:v2.5.2'
        command.side_effect = [json.dumps(old), json.dumps(self.cm)]
        with self.assertRaisesRegex(RuntimeError, 'image differs'):
            milvus._check_storage_compatibility(self.spec)

    @patch.object(milvus.kube, 'kubectl')
    def test_wal_and_metadata_changes_refused(self, command):
        for section, key, value in [('mq', 'type', 'rocksmq'), ('etcd', 'rootPath', 'milvus'),
                                    ('minio', 'rootPath', 'files'), ('etcd', 'ssl', {'enabled': True}),
                                    ('etcd', 'endpoints', ['new-cluster:2379'])]:
            old = copy.deepcopy(self.cm)
            config = yaml.safe_load(old['data']['default.yaml'])
            config[section][key] = value
            old['data']['default.yaml'] = yaml.safe_dump(config)
            command.side_effect = [json.dumps(self.deployment), json.dumps(old)]
            with self.assertRaisesRegex(RuntimeError, 'storage or WAL differs'):
                milvus._check_storage_compatibility(self.spec)

    @patch.object(milvus.kube, 'kubectl')
    def test_orphaned_persistent_state_refused(self, command):
        for output in [('', json.dumps(self.cm)), ('', '', '{}')]:
            command.side_effect = output
            with self.assertRaisesRegex(RuntimeError, 'implicit .* reuse is disabled'):
                milvus._check_storage_compatibility(self.spec)

    @patch.object(milvus.kube, 'kubectl')
    def test_wal_backend_change_refused(self, command):
        old = copy.deepcopy(self.cm)
        config = yaml.safe_load(old['data']['default.yaml'])
        config['woodpecker'] = {'storage': {'type': 'minio'}}
        old['data']['default.yaml'] = yaml.safe_dump(config)
        command.side_effect = [json.dumps(self.deployment), json.dumps(old)]
        with self.assertRaisesRegex(RuntimeError, 'storage or WAL differs'):
            milvus._check_storage_compatibility(self.spec)

    @patch.object(milvus.kube, 'kubectl')
    def test_volume_change_refused(self, command):
        old = copy.deepcopy(self.deployment)
        old['spec']['template']['spec']['volumes'][0]['persistentVolumeClaim']['claimName'] = 'old-volume'
        command.side_effect = [json.dumps(old), json.dumps(self.cm)]
        with self.assertRaisesRegex(RuntimeError, 'volume differs'):
            milvus._check_storage_compatibility(self.spec)

    @patch.object(milvus, 'create_milvus_accounts')
    @patch.object(milvus, '_check_storage_compatibility', side_effect=RuntimeError('refused'))
    @patch.object(milvus, '_build_spec')
    @patch.object(milvus.util, 'get_milvus_config_data', return_value={'slim': False})
    def test_guard_precedes_all_account_mutations(self, config, build, guard, accounts):
        with self.assertRaisesRegex(RuntimeError, 'refused'):
            milvus.create()
        accounts.assert_not_called()


class MilvusRenderTest(unittest.TestCase):
    def test_full_and_slim_use_only_required_resources(self):
        data = {'slim': False, 'affinity': False, 'tolerations': False, 'replicas': 1,
                'storageClass': 'local-path', 'size': 10, 'milvus_etcd_username': 'root',
                'milvus_etcd_password': 'synthetic', 'milvus_etcd_root_password': 'test: #quoted',
                'milvus_s3_username': 'milvus', 'milvus_s3_password': 'test: #quoted',
                'milvus_root_password': 'test: #quoted', 'bucket_server_hostname': 'seaweedfs',
                'bucket_server_port': 9000, 'etcd_container': 'openserverless-etcd',
                'etcd_range': range(3), 'milvus_max_database_num': 64,
                'milvus_max_role_num': 100, 'milvus_max_user_num': 100}
        for slim in [False, True]:
            data['slim'] = slim
            spec = milvus._build_spec(data)
            self.assertEqual(len(spec['items']), 6)
            self.assertFalse(any('pulsar' in item['metadata']['name'] for item in spec['items']))
            deployment = next(item for item in spec['items'] if item['kind'] == 'Deployment')
            pod = deployment['spec']['template']['spec']
            self.assertFalse(pod['automountServiceAccountToken'])
            self.assertEqual(pod['securityContext']['fsGroup'],999)
            tls=next(v for v in pod['volumes'] if v['name']=='etcd-client-tls')
            self.assertEqual(tls['secret']['secretName'],'openserverless-etcd-client-tls-v37')
            self.assertEqual(tls['secret']['defaultMode'],0o440)
            self.assertNotIn('emptyDir', next(v for v in pod['volumes'] if v['name'] == 'milvus-data-disk'))
            cm = next(item for item in spec['items'] if item['kind'] == 'ConfigMap')
            config = yaml.safe_load(cm['data']['default.yaml'])
            self.assertEqual(config['mq']['type'], 'woodpecker')
            self.assertEqual(config['woodpecker']['storage']['type'], 'local')
            self.assertEqual(config['woodpecker']['logstore']['segmentSyncPolicy']['maxIntervalForLocalStorage'], '1ms')
            self.assertEqual(config['proxy']['timeTickInterval'], 10)
            self.assertEqual(config['minio']['secretAccessKey'], 'test: #quoted')
            self.assertEqual(config['etcd']['rootPath'], 'milvus-etcd-v37')
            self.assertTrue(config['etcd']['ssl']['enabled'])
            self.assertEqual(len(config['etcd']['endpoints']), 3)
            user = yaml.safe_load(cm['data']['user.yaml'])
            self.assertTrue(user['common']['security']['authorizationEnabled'])
            self.assertEqual(user['common']['gracefulTime'], 0)
            service = next(item for item in spec['items'] if item['kind'] == 'Service')
            self.assertEqual(service['spec']['type'], 'ClusterIP')


class MilvusReadinessTest(unittest.TestCase):
    def test_probe_requires_health_and_complete_recovery(self):
        root = pathlib.Path.cwd()
        for profile in ['milvus', 'milvus-slim']:
            deployment = yaml.safe_load((root / 'deploy' / profile / 'milvus.yaml').read_text())
            container = deployment['spec']['template']['spec']['containers'][0]
            probe = container['readinessProbe']
            self.assertEqual(probe['failureThreshold'], 1)
            self.assertEqual(container['livenessProbe']['httpGet']['path'], '/healthz')
            for health_code, ready_code, body, expected in [
                (0, 0, '{"state":"Ready"}', True),
                (0, 0, '{"state":"NotReady","resourceGroups":[{"state":"Ready"}]}', False),
                (0, 0, '{"state":"NotReady","reason":"replica is not query visible"}', False),
                (0, 0, 'invalid json', False),
                (0, 0, '', False),
                (22, 0, '{"state":"Ready"}', False),
                (0, 22, '{"state":"Ready"}', False),
                (0, 28, '', False),
            ]:
                with self.subTest(profile=profile, health=health_code, code=ready_code, body=body):
                    with tempfile.TemporaryDirectory() as directory:
                        fake = pathlib.Path(directory) / 'curl'
                        fake.write_text('#!' + sys.executable + '\nimport sys\n'
                            + 'health="/healthz" in sys.argv[-1]\n'
                            + 'print("OK" if health else ' + repr(body) + ')\n'
                            + 'sys.exit(' + str(health_code) + ' if health else ' + str(ready_code) + ')\n')
                        fake.chmod(0o755)
                        command = list(probe['exec']['command'])
                        command[-1] = command[-1].replace('/milvus/tools/ops-milvus-recovered', directory + '/marker')
                        result = subprocess.run(command,
                            env={**os.environ, 'PATH': directory + ':' + os.environ['PATH']}, capture_output=True, text=True)
                        self.assertEqual(result.returncode == 0, expected)

    def test_recovery_marker_is_bound_to_process_and_health(self):
        deployment = yaml.safe_load(pathlib.Path('deploy/milvus/milvus.yaml').read_text())
        command = deployment['spec']['template']['spec']['containers'][0]['readinessProbe']['exec']['command']
        with tempfile.TemporaryDirectory() as directory:
            control = pathlib.Path(directory) / 'control.json'
            marker = pathlib.Path(directory) / 'marker'
            fake = pathlib.Path(directory) / 'curl'
            fake.write_text('#!' + sys.executable + '\nimport json,sys\n'
                + 'x=json.load(open(' + repr(str(control)) + '))\n'
                + 'health="/healthz" in sys.argv[-1]\n'
                + 'print("OK" if health else x["body"])\n'
                + 'sys.exit(x["health"] if health else 0)\n')
            fake.chmod(0o755)
            command = list(command)
            command[-1] = command[-1].replace('/milvus/tools/ops-milvus-recovered', str(marker))
            def run(health, body):
                control.write_text(json.dumps({'health': health, 'body': body}))
                return subprocess.run(command, env={**os.environ, 'PATH': directory + ':' + os.environ['PATH']},
                                      capture_output=True).returncode == 0
            self.assertTrue(run(0, '{"state":"Ready"}'))
            generation = marker.read_text()
            self.assertTrue(generation.strip().isdigit())
            self.assertTrue(run(0, '{"state":"NotReady"}'))  # New collection loading is allowed.
            marker.write_text('old-process\n')
            self.assertFalse(run(0, '{"state":"NotReady"}'))
            self.assertTrue(run(0, '{"state":"Ready"}'))
            self.assertFalse(run(22, '{"state":"Ready"}'))
            self.assertFalse(marker.exists())
            self.assertFalse(run(0, '{"state":"NotReady"}'))


if __name__ == '__main__':
    unittest.main()
