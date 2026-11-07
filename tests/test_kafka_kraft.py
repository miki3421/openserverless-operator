# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import yaml
from openserverless import kafka, kafka_profile

ROOT = Path(__file__).resolve().parents[1]


class KafkaKraftTests(unittest.TestCase):
    def current(self):
        return yaml.safe_load((ROOT / 'deploy/kafka/kafka-001-sts.yaml').read_text())

    def claims(self):
        return {'items': [{'metadata': {'name': kafka_profile.PROFILE['claim'],
                                       'labels': {'app': 'kafka', 'openserverless.org/kafka-profile': kafka_profile.PROFILE['mode']}},
                           'status': {'phase': 'Bound'}}]}

    def test_empty_install(self):
        kafka_profile.validate_existing(None, {'items': []})

    def test_qualified_restart(self):
        kafka_profile.validate_existing(self.current(), self.claims())

    def test_existing_workload_without_verified_storage_is_refused(self):
        for pvcs in [{'items': []}, {'items': [{'metadata': {'name': kafka_profile.PROFILE['claim']},
                                                'status': {'phase': 'Pending'}}]}]:
            with self.subTest(pvcs=pvcs), self.assertRaisesRegex(RuntimeError, 'explicit recovery'):
                kafka_profile.validate_existing(self.current(), pvcs)

    def test_orphaned_current_and_legacy_volumes_are_refused(self):
        for name in ['kafka-pvc-data', kafka_profile.PROFILE['claim'], 'foreign-kafka-claim']:
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'explicit recovery'):
                kafka_profile.validate_existing(None, {'items': [{'metadata': {'name': name, 'labels': {'app': 'kafka'}}}]})

    def test_other_component_volume_is_not_kafka(self):
        kafka_profile.validate_existing(None, {'items': [{'metadata': {'name': 'postgres', 'labels': {'app': 'postgres'}}}]})

    def test_legacy_and_unrecognized_images_are_refused(self):
        for image in ['ghcr.io/nuvolaris/kafka:2.3.1.2311011621', 'apache/kafka:4.4.0']:
            state = self.current()
            state['spec']['template']['spec']['containers'][0]['image'] = image
            with self.subTest(image=image), self.assertRaisesRegex(RuntimeError, 'explicit migration'):
                kafka_profile.validate_existing(state, self.claims())

    def test_topology_and_mount_contracts_are_checked(self):
        original = self.current()
        mutations = [
            lambda x: x['spec'].update(replicas=2),
            lambda x: x['spec']['template']['metadata']['labels'].pop('openserverless.org/kafka-profile'),
            lambda x: x['spec']['template']['spec']['securityContext'].update(fsGroup=0),
            lambda x: x['spec']['template']['spec']['containers'][0]['securityContext'].update(runAsUser=0),
            lambda x: x['spec']['template']['spec']['volumes'][0]['persistentVolumeClaim'].update(claimName='kafka-pvc-data'),
            lambda x: x['spec']['template']['spec']['containers'][0]['volumeMounts'][0].update(subPath='legacy'),
            lambda x: x['spec']['template']['spec']['containers'][0]['volumeMounts'][0].update(readOnly=True),
        ]
        for i, mutate in enumerate(mutations):
            state = copy.deepcopy(original)
            mutate(state)
            with self.subTest(case=i), self.assertRaisesRegex(RuntimeError, 'explicit migration'):
                kafka_profile.validate_existing(state, self.claims())

    def test_legacy_volume_next_to_current_profile_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'explicit migration'):
            pvcs = self.claims()
            pvcs['items'].append({'metadata': {'name': 'kafka-pvc-data'}})
            kafka_profile.validate_existing(self.current(), pvcs)

    def test_api_denial_is_propagated(self):
        with patch.object(kafka.kube, 'kubectl', side_effect=RuntimeError('forbidden')), \
             patch.object(kafka.cfg_util, 'get_kafka_config_data') as config, \
             patch.object(kafka.security, 'apply') as apply:
            with self.assertRaisesRegex(RuntimeError, 'forbidden'):
                kafka.create()
            config.assert_not_called()
            apply.assert_not_called()

    def test_legacy_refusal_precedes_render_and_apply(self):
        state = self.current()
        state['spec']['template']['spec']['containers'][0]['image'] = 'ghcr.io/nuvolaris/kafka:2.3.1.2311011621'
        with patch.object(kafka.kube, 'kubectl', side_effect=[json.dumps(state), '{"items":[]}']), \
             patch.object(kafka.kus, 'patchTemplates') as render, \
             patch.object(kafka.security, 'apply') as apply:
            with self.assertRaisesRegex(RuntimeError, 'explicit migration'):
                kafka.create()
            render.assert_not_called()
            apply.assert_not_called()

    def test_real_create_renders_kraft_and_matching_pin(self):
        data = {'name': 'kafka', 'image': kafka_profile.PROFILE['image'],
                'claim': kafka_profile.PROFILE['claim'], 'data_volume_size': 1,
                'storage_class': 'synthetic', 'affinity': False, 'tolerations': False}
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / 'deploy/kafka', root / 'deploy/kafka')
            shutil.copytree(ROOT / 'openserverless/templates', root / 'openserverless/templates')
            try:
                os.chdir(root)
                with patch.object(kafka.kube, 'kubectl', side_effect=['', '{"items":[]}']), \
                     patch.object(kafka.cfg_util, 'get_kafka_config_data', return_value=data), \
                     patch.object(kafka.security, 'apply', side_effect=lambda spec, _: spec), \
                     patch.object(kafka.util, 'wait_for_pod_ready'), \
                     patch.object(kafka, 'get_kafka_connect_data', return_value='kafka:9092'), \
                     patch.object(kafka.cfg, 'put'):
                    result = kafka.create()
            finally:
                os.chdir(previous)
        sts = next(x for x in result['items'] if x['kind'] == 'StatefulSet')
        server = sts['spec']['template']['spec']['containers'][0]
        self.assertEqual(server['image'], kafka_profile.PROFILE['image'])
        self.assertEqual(sts['spec']['template']['spec']['volumes'][0]['persistentVolumeClaim']['claimName'], data['claim'])
        self.assertEqual(server['command'], ['/opt/ops-kafka/start.sh'])
        self.assertNotIn('initContainers', sts['spec']['template']['spec'])
        self.assertNotIn('zookeeper', json.dumps(sts).lower())
        self.assertNotIn('--version', json.dumps(server['readinessProbe']))
        service = next(x for x in result['items'] if x['kind'] == 'Service')
        self.assertTrue(service['spec']['publishNotReadyAddresses'])
        self.assertNotIn(9093, [p['port'] for p in service['spec']['ports']])
        policy = next(x for x in result['items'] if x['kind'] == 'NetworkPolicy')
        rule = next(x for x in policy['spec']['ingress'] if x['ports'][0]['port'] == 9093)
        self.assertEqual(rule['from'][0]['podSelector']['matchLabels'], {'name': 'kafka'})
        self.assertIn({'key': 'user-action-pod', 'operator': 'DoesNotExist'}, rule['from'][0]['podSelector']['matchExpressions'])

    def test_deploy_pin_matches_profile_and_catalog(self):
        self.assertEqual(self.current()['spec']['template']['spec']['containers'][0]['image'], kafka_profile.PROFILE['image'])
        catalog = ROOT.parent / 'oplugins/opsroot.json'
        if not catalog.exists():
            self.skipTest('paired task catalog not mounted')
        self.assertEqual(json.loads(catalog.read_text())['config']['images']['kafka'], kafka_profile.PROFILE['image'])
