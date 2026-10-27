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

"""Catalog routing, legacy behavior and image-only live reconciliation."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import yaml
from jinja2 import Template
import openserverless.config as cfg
from openserverless import image_catalog as images

ROOT = Path(__file__).resolve().parents[1]


def workload(name='redis', kind='StatefulSet', container='redis', image='redis:old', uid='fixture-owner'):
    return {'apiVersion': 'apps/v1', 'kind': kind,
            'metadata': {'name': name, 'resourceVersion': '123',
                         'ownerReferences': [{'uid': uid}]},
            'spec': {'replicas': 2, 'template': {'spec': {'containers': [
                {'name': container, 'image': image, 'env': [{'name': 'KEEP', 'value': 'unchanged'}]}]}}}}


class ImageCatalogTests(unittest.TestCase):
    def tearDown(self):
        cfg.clean()

    def configure(self, values):
        cfg.configure({'images': values})

    def test_legacy_manifests_and_user_images_are_unchanged(self):
        cfg.clean()
        original = workload()
        self.assertEqual(images.resolve('redis', original), original)
        self.assertEqual(images.render_yaml('redis', 'literal: unchanged\n'), 'literal: unchanged\n')
        self.configure({'redis': 'redis:new'})
        self.assertEqual(images.resolve('runtimes', original), original)
        self.assertEqual(images.resolve('workflows', original), original)

    def test_catalog_does_not_mutate_input_or_other_fields(self):
        self.configure({'redis': 'registry.local:5443/redis:release@sha256:' + 'a' * 64})
        original = workload()
        expected = deepcopy(original)
        expected['spec']['template']['spec']['containers'][0]['image'] = images.catalog()['redis']
        self.assertEqual(images.resolve('redis', original), expected)
        self.assertEqual(original['spec']['template']['spec']['containers'][0]['image'], 'redis:old')

    def test_invalid_references_fail_before_render(self):
        for value in ('redis', '', 'https://registry/redis:1', 'redis:1\nother:2', 'redis@sha256:no', 42):
            with self.subTest(value=value):
                self.configure({'redis': value})
                with self.assertRaises(ValueError):
                    images.catalog()
        self.configure({})
        with self.assertRaises(ValueError):
            images.catalog()

    def test_missing_and_unmapped_slots_do_not_fall_back_silently(self):
        self.configure({'operator': 'operator:test'})
        with self.assertRaisesRegex(ValueError, 'Missing spec.images.redis'):
            images.resolve('redis', workload())
        self.configure({'redis': 'redis:test'})
        with self.assertRaisesRegex(ValueError, 'Unmapped image slot'):
            images.resolve('redis', workload(container='new-sidecar'))

    def test_enabled_components_are_validated_before_deployment(self):
        self.configure({'redis': 'redis:test'})
        cfg.put('components.redis', True)
        images.validate_for_spec()
        cfg.put('components.kafka', True)
        with self.assertRaisesRegex(ValueError, 'busybox_kafka, kafka, volume_exporter'):
            images.validate_for_spec()

    def test_init_containers_and_kubegres_use_distinct_keys(self):
        values = {'controller': 'controller:test', 'busybox': 'busybox:test', 'pgvector': 'postgres:test'}
        self.configure(values)
        resource = workload('controller', container='controller')
        resource['spec']['template']['spec']['initContainers'] = [{'name': 'check-kafka', 'image': 'old:1'}]
        result = images.resolve('openwhisk-enterprise', resource)
        self.assertEqual(result['spec']['template']['spec']['initContainers'][0]['image'], values['busybox'])
        postgres = {'kind': 'Kubegres', 'metadata': {'name': 'openserverless-postgres'}, 'spec': {'image': 'old:1'}}
        self.assertEqual(images.resolve('postgres-operator-deploy', postgres)['spec']['image'], values['pgvector'])

    def test_cronjob_and_couchdb_job_use_the_selected_operator(self):
        self.configure({'operator': 'localhost/operator:test', 'busybox': 'busybox:test'})
        text = (ROOT / 'openserverless/templates/couchdb-init.yaml').read_text()
        job = yaml.safe_load(Template(text).render(image='legacy:1', config='{}'))
        result = images.resolve('couchdb', job)
        self.assertEqual(result['spec']['template']['spec']['containers'][0]['image'], 'localhost/operator:test')
        cron = next(item for item in yaml.safe_load_all((ROOT / 'deploy/scheduler/cron.yaml').read_text()) if item)
        result = images.resolve('scheduler', cron)
        self.assertEqual(result['spec']['jobTemplate']['spec']['template']['spec']['containers'][0]['image'],
                         'localhost/operator:test')

    def test_runtime_specific_ingress_and_openshift_couchdb(self):
        self.configure({'ingress': 'ingress:cloud', 'ingress_kind': 'ingress:kind',
                        'couchdb': 'couchdb:normal', 'couchdb_openshift': 'couchdb:openshift'})
        cfg.put('openserverless.kube', 'kind')
        controller = workload('ingress-nginx-controller', 'Deployment', 'controller')
        result = images.resolve('ingress-nginx', controller)
        self.assertEqual(result['spec']['template']['spec']['containers'][0]['image'], 'ingress:kind')
        cfg.put('openserverless.kube', 'openshift')
        result = images.resolve('couchdb', workload('couchdb', container='couchdb'))
        self.assertEqual(result['spec']['template']['spec']['containers'][0]['image'], 'couchdb:openshift')

    def test_actual_kustomize_creation_uses_catalog_but_delete_build_is_legacy(self):
        from openserverless import kustomize
        self.configure({'redis': 'registry.local:5443/redis:test'})
        with tempfile.TemporaryDirectory() as directory:
            shutil.copytree(ROOT / 'deploy/redis', Path(directory) / 'deploy/redis')
            previous = os.getcwd()
            try:
                os.chdir(directory)
                resource = next(item for item in kustomize.kustom_list('redis')['items']
                                if item['kind'] == 'StatefulSet')
                self.assertEqual(resource['spec']['template']['spec']['containers'][0]['image'],
                                 'registry.local:5443/redis:test')
                self.assertIn('docker.io/bitnamilegacy/redis:8.2.1', kustomize.build('redis'))
            finally:
                os.chdir(previous)

    def test_controller_and_invoker_templates_keep_full_digest_reference(self):
        from openserverless import enterprise_util, util
        reference = 'registry.local:5443/image:release@sha256:' + 'a' * 64
        self.configure({'controller': reference, 'invoker': reference})
        for key, build in [('controller', util.get_controller_image_data),
                           ('invoker', enterprise_util.get_invoker_image_data)]:
            data = {}
            build(data)
            self.assertEqual(data[key + '_image'] + ':' + data[key + '_tag'], reference)

    def test_cert_manager_creation_resolves_catalog_and_preserves_legacy_command(self):
        from openserverless import certmanager
        self.configure({key: key + ':fixture' for key in
                        ['cert_manager', 'cert_manager_cainjector', 'cert_manager_webhook']})
        with patch.object(certmanager.kube, 'get', return_value=None), \
             patch.object(certmanager.kube, 'kubectl', return_value='created') as ctl, \
             patch.object(certmanager, 'wait_for_cm_ready'):
            certmanager.create()
        self.assertEqual(ctl.call_args.args, ('apply', '-f', '-'))
        workloads = [row for row in yaml.safe_load_all(ctl.call_args.kwargs['input'])
                     if isinstance(row, dict) and row.get('kind') == 'Deployment']
        self.assertEqual({row['spec']['template']['spec']['containers'][0]['image'] for row in workloads},
                         set(images.catalog().values()))
        cfg.clean()
        with patch.object(certmanager.kube, 'get', return_value=None), \
             patch.object(certmanager.kube, 'kubectl', return_value='created') as ctl, \
             patch.object(certmanager, 'wait_for_cm_ready'):
            certmanager.create()
        self.assertEqual(ctl.call_args.args, ('apply', '-f', 'deploy/cert-manager/cert-manager.yaml'))

    def test_fresh_kind_ingress_resolves_controller_and_admission_images(self):
        from openserverless import ingress
        self.configure({'ingress_kind': 'controller:fixture', 'ingress_admission_kind': 'admission:fixture'})
        cfg.put('openserverless.kube', 'kind')
        with patch.object(ingress.kube, 'get', return_value=None), \
             patch.object(ingress.kube, 'kubectl', return_value='created') as ctl, \
             patch.object(ingress, 'wait_for_ingress_ready'):
            ingress.create()
        self.assertEqual(ctl.call_args.args, ('apply', '-f', '-'))
        resources = [row for row in yaml.safe_load_all(ctl.call_args.kwargs['input']) if isinstance(row, dict)]
        resolved = [parent[field] for row in resources
                    for parent, field, _, _ in images.image_slots('ingress-nginx', row)]
        self.assertEqual(sorted(resolved), ['admission:fixture', 'admission:fixture', 'controller:fixture'])

    def test_all_image_slots_in_managed_base_manifests_are_mapped(self):
        checked = 0
        for group in images.SLOTS:
            for path in (ROOT / 'deploy' / group).glob('*.yaml'):
                if path.name.startswith('_'):
                    continue
                for resource in yaml.safe_load_all(path.read_text()):
                    if not isinstance(resource, dict):
                        continue
                    for parent, field, key, _ in images.image_slots(group, resource):
                        self.assertTrue(key, str(path))
                        checked += 1
        self.assertGreater(checked, 55)

    def test_reconcile_patches_only_changed_images_on_owned_resources(self):
        self.configure({'redis': 'redis:new'})
        owned = workload()
        foreign = workload(uid='someone-else')
        unknown = workload(name='tenant-action')
        with patch('openserverless.kube.kubectl', return_value=json.dumps({'items': [owned, foreign, unknown]})) as ctl:
            images.reconcile({'metadata': {'uid': 'fixture-owner'}})
        self.assertEqual(ctl.call_count, 2)
        args = ctl.call_args.args
        self.assertEqual(args[:3], ('patch', 'StatefulSet/redis', '--type=json'))
        patch_body = json.loads(args[4])
        self.assertEqual(patch_body, [
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': '123'},
            {'op': 'replace', 'path': '/spec/template/spec/containers/0/image', 'value': 'redis:new'}])

    def test_reconcile_is_noop_when_images_match(self):
        self.configure({'redis': 'redis:old'})
        with patch('openserverless.kube.kubectl', return_value=json.dumps({'items': [workload()]})) as ctl:
            images.reconcile({'metadata': {'uid': 'fixture-owner'}})
        self.assertEqual(ctl.call_count, 1)

    def test_all_resources_are_validated_before_the_first_patch(self):
        self.configure({'redis': 'redis:new'})
        postgres = workload('openserverless-mongodb', container='ferretdb')
        with patch('openserverless.kube.kubectl', return_value=json.dumps({'items': [workload(), postgres]})) as ctl:
            with self.assertRaisesRegex(ValueError, 'Missing spec.images.ferretdb'):
                images.reconcile({'metadata': {'uid': 'fixture-owner'}})
        self.assertEqual(ctl.call_count, 1)

    def test_owner_lookup_failure_cannot_change_ownerless_workloads(self):
        self.configure({'redis': 'redis:new'})
        with patch('openserverless.kube.kubectl') as ctl:
            with self.assertRaises(RuntimeError):
                images.reconcile(None)
        ctl.assert_not_called()


if __name__ == '__main__':
    unittest.main()
