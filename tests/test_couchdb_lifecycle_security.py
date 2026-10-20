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

import base64
import copy
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

from openserverless import config as cfg, couchdb
from openserverless.couchdb_util import CouchDB

ROOT = Path(__file__).resolve().parents[1]


def load(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SubjectDatabase(CouchDB):
    def __init__(self):
        self.subject = {'subject': 'tenant', 'namespaces': [{'name': 'tenant', 'uuid': 'uuid', 'key': 'key'}]}
        self.metadata = {'login': 'tenant', 'password': 'hash', 'email': 'tenant@example.test',
                         'env': [{'key': 'AUTH', 'value': 'uuid:key'}]}
        self.writes = []
        self.read_metadata = 0

    def find_doc(self, database, selector):
        if database == 'subjects':
            return {'docs': [self.subject] if self.subject else []}
        self.read_metadata += 1
        return {'docs': [copy.deepcopy(self.metadata)]}

    def update_doc(self, database, document):
        self.writes.append(copy.deepcopy(document))
        return True


class TenantRevocationTests(unittest.TestCase):
    def setUp(self):
        self.db = SubjectDatabase()
        self.args = {'couchdb_host': 'couchdb', 'couchdb_user': 'admin',
                     'couchdb_password': 'synthetic-only', 'login': 'tenant',
                     'password': 'synthetic-only', '__ow_headers': {'authorization': 'uuid:key'}}

    def test_credential_lookup_block_unblock_and_delete(self):
        for state, allowed in [('active', True), ('blocked', False), ('unblocked', True), ('deleted', False)]:
            if state == 'deleted':
                self.db.subject = None
            else:
                self.db.subject['blocked'] = state == 'blocked'
            with self.subTest(state=state):
                self.assertEqual(allowed, bool(self.db.find_active_subject(uuid='uuid', key='key')))
                self.assertEqual(allowed, bool(self.db.find_active_subject(subject='tenant')))

    def test_alias_namespace_retains_access_only_when_subject_is_active(self):
        self.db.subject['namespaces'].append({'name': 'alias', 'uuid': 'alias-uuid', 'key': 'alias-key'})
        self.assertTrue(self.db.find_active_subject(uuid='alias-uuid', key='alias-key'))
        self.db.subject['blocked'] = True
        self.assertIsNone(self.db.find_active_subject(uuid='alias-uuid', key='alias-key'))

    def test_wrong_rotated_or_other_tenant_credentials_are_rejected(self):
        for args in [dict(uuid='uuid', key='old-key'), dict(subject='victim'),
                     dict(uuid='uuid', key='key', subject='victim'), dict(uuid='uuid'), {},
                     dict(subject={'$ne': None})]:
            with self.subTest(args=args):
                self.assertIsNone(self.db.find_active_subject(**args))

    def test_truthy_and_malformed_block_states_fail_closed(self):
        for blocked in (True, 'true', 'false', 1, ['blocked']):
            self.db.subject['blocked'] = blocked
            self.assertIsNone(self.db.find_active_subject(uuid='uuid', key='key'))
        for response in (None, {}, {'docs': None}, {'docs': [None]}, {'docs': [{}]}):
            with patch.object(self.db, 'find_doc', return_value=response):
                self.assertIsNone(self.db.find_active_subject(subject='tenant'))

    def test_every_packaged_service_rejects_a_blocked_subject(self):
        # These six web actions execute their own authentication, under whisk-system.
        paths = [ROOT / 'actions/common/authorize.py', *ROOT.glob('actions/devel/*/common/authorize.py')]
        self.assertEqual(7, len(paths))
        common = types.ModuleType('common'); common.__path__ = []
        with patch.dict(sys.modules, {'common': common, 'common.util': types.ModuleType('common.util')}):
            for index, path in enumerate(paths):
                auth = load(path.relative_to(ROOT), 'authorize_' + str(index))
                helper = auth.Authorize.__new__(auth.Authorize); helper._db = self.db
                for token in ('uuid:key', 'Basic ' + base64.b64encode(b'uuid:key').decode(),
                              base64.b64encode(b'uuid:key').decode()):
                    with self.subTest(path=str(path), token_type=token.split(':')[0][:8]):
                        self.db.subject['blocked'] = False
                        self.assertEqual('tenant', helper.login(token)['login'])
                        self.db.subject['blocked'] = True
                        self.db.read_metadata = 0
                        with self.assertRaises(auth.AuthorizationError):
                            helper.login(token)
                        self.assertEqual(0, self.db.read_metadata)

    def test_download_denial_does_not_mask_auth_error_during_cleanup(self):
        common = types.ModuleType('common'); common.__path__ = []
        modules = {'common': common, 'common.util': types.ModuleType('common.util'),
                   'common.s3_util': types.ModuleType('common.s3_util')}
        authorization = types.ModuleType('common.authorize'); authorization.Authorize = Mock()
        authorization.Authorize.return_value.login.side_effect = ValueError('denied')
        modules['common.authorize'] = authorization
        with patch.dict(sys.modules, modules):
            download = load('actions/devel/download/__main__.py', 'system_download')
            response = download.main({**self.args, '__ow_path': '/tenant-data/file.txt',
                                      '__ow_headers': {'x-impersonate-auth': 'uuid:key'}})
        self.assertEqual(400, response['statusCode'])
        self.assertIn('denied', response['body'])

    def test_password_login_and_secrets_follow_subject_state(self):
        login = load('actions/login/__main__.py', 'system_login')
        secrets = load('actions/secrets/__main__.py', 'system_secrets')
        for state in ('active', 'blocked', 'unblocked', 'deleted'):
            writes_before = len(self.db.writes)
            if state == 'deleted': self.db.subject = None
            else: self.db.subject['blocked'] = state == 'blocked'
            allowed = state in ('active', 'unblocked')
            with patch.object(login.cu, 'CouchDB', return_value=self.db), \
                    patch.object(login.bu, 'verify_password', return_value=True):
                response = login.main(self.args)
                self.assertEqual(200 if allowed else 401, response['statusCode'])
            with patch.object(secrets.cu, 'CouchDB', return_value=self.db):
                response = secrets.main({**self.args, 'env': {'SYNTHETIC': 'value'}})
                self.assertEqual(200 if allowed else 401, response['statusCode'])
                if not allowed:
                    self.assertNotIn('env', response.get('body', {}))
            self.assertEqual(writes_before + int(allowed), len(self.db.writes))


class CouchDBProvisioningSecurityTests(unittest.TestCase):
    def setUp(self):
        cfg.clean()
        for role in ('admin', 'controller', 'invoker'):
            cfg.put('couchdb.' + role + '.user', role + '_user')
            cfg.put('couchdb.' + role + '.password', 'synthetic-' + role)
        cfg.put('openwhisk.namespaces.system', 'uuid:key')
        self.addCleanup(cfg.clean)

    def test_missing_passwords_never_use_a_fallback(self):
        for role in ('admin', 'controller', 'invoker'):
            key = 'couchdb.' + role + '.password'; old = cfg.get(key)
            cfg.put(key, '')
            with patch.object(couchdb.kube, 'kubectl', side_effect=lambda *args: '' if args[1] == 'statefulset' else '{"items":[]}'), patch.object(couchdb.kube, 'apply') as apply, self.assertRaisesRegex(ValueError, 'must be configured'):
                couchdb.create()
            apply.assert_not_called()
            cfg.put(key, old)
        cfg.put('couchdb.admin.password', '')
        with self.assertRaises(ValueError): CouchDB()

    def test_cron_uses_its_controller_credential_without_an_admin_password(self):
        from openserverless import actionexecutor
        from contextlib import ExitStack
        cfg.clean()
        settings = {'scheduler.schedule': '* * * * *', 'controller.protocol': 'http',
                    'controller.host': 'controller', 'controller.port': '3233',
                    'couchdb.controller.user': 'controller_user',
                    'couchdb.controller.password': 'synthetic-controller'}
        with ExitStack() as stack:
            stack.enter_context(patch.dict(actionexecutor.os.environ, {'SCHEDULER_CONFIG': json.dumps(settings)}, clear=True))
            factory = stack.enter_context(patch.object(actionexecutor.cu, 'CouchDB', wraps=CouchDB))
            stack.enter_context(patch.object(CouchDB, 'wait_db_ready', return_value=True))
            actions = stack.enter_context(patch.object(actionexecutor, 'get_cron_aware_actions', return_value=[]))
            actionexecutor.start()
        factory.assert_called_once_with(username='controller_user', password='synthetic-controller')
        client = actions.call_args.args[0]
        self.assertEqual(('controller_user', 'synthetic-controller'), (client.db_username, client.db_password))

    def test_system_action_deployment_refuses_missing_admin_password(self):
        from openserverless import whisk_actions_deployer
        cfg.put('couchdb.admin.password', '')
        with self.assertRaises(ValueError): whisk_actions_deployer.get_couchdb_inputs()

    def test_init_configuration_is_minimal_and_stored_in_a_secret_reference(self):
        cfg.put('postgres.password', 'unrelated-secret')
        cfg.put('operator.tag', 'test')
        cfg.put('state.previous.secret', 'old-secret')
        config = couchdb.initialization_config()
        self.assertNotIn('postgres.password', config)
        self.assertNotIn('state.previous.secret', config)
        self.assertEqual('uuid:key', config['openwhisk.namespaces.system'])
        import yaml
        template = (ROOT / 'openserverless/templates/couchdb-init.yaml').read_text()
        job = yaml.safe_load(couchdb.env.from_string(template).render(image='test-image'))
        env = job['spec']['template']['spec']['containers'][0]['env']
        self.assertEqual([{'name': 'OPENSERVERLESS_CONFIG', 'valueFrom': {'secretKeyRef': {
            'name': 'couchdb-init-config', 'key': 'config'}}}], env)
        self.assertNotIn('synthetic-admin', json.dumps(job))

    def test_initializer_does_not_log_secrets(self):
        names = ['init_system', 'init_subjects', 'init_activations', 'init_actions',
                 'add_initial_subjects', 'init_users_metadata', 'init_compactions_config']
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.dict(couchdb.os.environ, {'OPENSERVERLESS_CONFIG': json.dumps(couchdb.initialization_config())}))
            stack.enter_context(patch.object(couchdb.util, 'wait_for_pod_ready'))
            stack.enter_context(patch.object(couchdb.openserverless.couchdb_util, 'CouchDB'))
            for name in names: stack.enter_context(patch.object(couchdb, name, return_value=True))
            with self.assertLogs(level='INFO') as captured: self.assertFalse(couchdb.init())
        self.assertNotIn('synthetic-admin', '\n'.join(captured.output))
        self.assertNotIn('uuid:key', '\n'.join(captured.output))

    def test_secret_apply_errors_never_reach_the_logging_wrapper(self):
        secret = {'kind': 'Secret', 'stringData': {'config': 'synthetic-secret'}}
        service = {'kind': 'Service', 'metadata': {'name': 'couchdb'}}
        with patch.object(couchdb.subprocess, 'run', return_value=Mock(returncode=1, stderr='synthetic-secret')), \
                patch.object(couchdb.kube, 'apply') as apply:
            with self.assertRaisesRegex(RuntimeError, '^Could not apply private CouchDB bootstrap resources$'):
                couchdb.apply_couchdb_spec({'items': [secret, service]})
            apply.assert_not_called()
        with patch.object(couchdb.subprocess, 'run', return_value=Mock(returncode=0)), \
                patch.object(couchdb.kube, 'apply', return_value='applied') as apply:
            self.assertEqual('applied', couchdb.apply_couchdb_spec({'items': [secret, service]}))
            self.assertEqual([service], apply.call_args.args[0]['items'])

    def test_services_are_private(self):
        import yaml
        services = list(yaml.safe_load_all((ROOT / 'deploy/couchdb/couchdb-svc.yaml').read_text()))
        for service in services:
            self.assertEqual('ClusterIP', service['spec'].get('type', 'ClusterIP'))
            self.assertFalse(service['spec'].get('externalIPs'))
            self.assertTrue(all('nodePort' not in port for port in service['spec']['ports']))

    def test_json_structural_characters_cannot_redirect_a_metadata_write(self):
        for value in ['ordinary@example.test', '\", \"_id\": \"victim', 'quote\"\\\n\u2603', '<tag>']:
            db = Mock()
            data = {'login': 'tenant', 'password': 'hash', 'email': value,
                    'metadata': [{'key': 'APP_SETTING', 'value': value}],
                    'quota': [{'key': 'STORAGE', 'value': 12}]}
            self.assertTrue(couchdb.update_templated_doc(db, 'users_metadata', 'user_metadata.json', data))
            doc = db.update_doc.call_args.args[1]
            self.assertEqual('tenant', doc['_id'])
            self.assertEqual(value, doc['email'])
            self.assertEqual(value, doc['env'][0]['value'])
            self.assertEqual('12', doc['quota'][0]['value'])
            self.assertEqual(doc, json.loads(json.dumps(doc)))

    def test_subject_values_are_serialized_without_template_injection(self):
        db = Mock()
        data = {'name': 'tenant', 'uuid': 'uuid', 'key': '\", \"_id\": \"victim'}
        couchdb.update_templated_doc(db, 'subjects', 'subject.json', data)
        self.assertEqual('tenant', db.update_doc.call_args.args[1]['_id'])
        self.assertEqual(data['key'], db.update_doc.call_args.args[1]['namespaces'][0]['key'])


if __name__ == '__main__': unittest.main()
