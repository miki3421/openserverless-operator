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
import json
import unittest
from unittest.mock import Mock, patch, MagicMock
from openserverless import redis


def data(persistent=True):
    return {'name':'redis','size':10,'storageClass':'local-path','redis_password':'synthetic',
            'namespace':'openserverless','password':'synthetic-user','prefix':'openserverless:',
            'persistence':persistent,'maxmemory':'1000mb','affinity':False,'tolerations':False}


class RedisRenderTest(unittest.TestCase):
    def test_native_layout_and_authenticated_probes(self):
        for enabled in (True,False):
            s=redis._build_spec(data(enabled));sts,cm,svc=s['items'];pod=sts['spec']['template']['spec'];c=pod['containers'][0]
            self.assertEqual(c['image'],redis.IMAGE)
            self.assertFalse(pod['automountServiceAccountToken'])
            self.assertEqual(pod['securityContext']['runAsUser'],999)
            self.assertTrue(c['securityContext']['readOnlyRootFilesystem'])
            self.assertIn('--user',c['readinessProbe']['exec']['command'])
            self.assertEqual(sts['spec']['volumeClaimTemplates'][0]['metadata']['name'],'redis-data-v810')
            self.assertEqual(svc['spec']['type'],'ClusterIP')
            self.assertNotIn('synthetic',json.dumps(s))
            self.assertIn('aclfile /data/users.acl',cm['data']['redis-conf'])
            self.assertIn('appendonly '+('yes' if enabled else 'no'),cm['data']['redis-conf'])

    def test_config_injection_rejected(self):
        d=data();d['maxmemory']='1gb\nuser injected on nopass +@all ~*'
        with self.assertRaises(ValueError):redis._build_spec(d)


class RedisGuardTest(unittest.TestCase):
    def setUp(self):
        self.data=data();self.spec=redis._build_spec(self.data)
        self.secret={'data':{'password':base64.b64encode(b'synthetic').decode()}}

    def test_identical_cluster_accepted(self):
        with patch.object(redis,'_json',side_effect=[self.spec['items'][0],self.secret,self.spec['items'][1]]):
            redis._check_storage_compatibility(self.spec,self.data)

    def test_existing_image_or_layout_rejected(self):
        for change in ('image','layout','claim'):
            current=copy.deepcopy(self.spec['items'][0])
            if change=='image':current['spec']['template']['spec']['containers'][0]['image']='legacy:8.2.1'
            elif change=='layout':current['metadata']['labels']={}
            else:current['spec']['volumeClaimTemplates'][0]['metadata']['name']='old-data'
            with patch.object(redis,'_json',side_effect=[current,self.secret]):
                with self.assertRaises(RuntimeError):redis._check_storage_compatibility(self.spec,self.data)

    def test_orphan_volume_rejected(self):
        with patch.object(redis,'_json',side_effect=[None,None]),patch.object(redis.kube,'kubectl',return_value=json.dumps({'items':[{'metadata':{'name':'redis-data-v810-redis-0'}}]})):
            with self.assertRaisesRegex(RuntimeError,'without its StatefulSet'):redis._check_storage_compatibility(self.spec,self.data)

    def test_missing_configuration_or_credentials_rejected(self):
        for secret,cm in [(None,self.spec['items'][1]),(self.secret,None)]:
            with patch.object(redis,'_json',side_effect=[self.spec['items'][0],secret,cm]):
                with self.assertRaisesRegex(RuntimeError,'refusing reset'):redis._check_storage_compatibility(self.spec,self.data)

    def test_persistence_mode_change_rejected(self):
        with patch.object(redis,'_json',side_effect=[self.spec['items'][0],self.secret,redis._build_spec(data(False))['items'][1]]):
            with self.assertRaisesRegex(RuntimeError,'persistence differs'):redis._check_storage_compatibility(self.spec,self.data)

    def test_password_rotation_rejected(self):
        d=data();d['redis_password']='changed'
        with patch.object(redis,'_json',side_effect=[self.spec['items'][0],self.secret,self.spec['items'][1]]):
            with self.assertRaisesRegex(RuntimeError,'password rotation'):redis._check_storage_compatibility(self.spec,d)

    def test_guard_precedes_key_and_account_mutations(self):
        with patch.object(redis.util,'get_redis_config_data',return_value=data()),patch.object(redis,'_check_storage_compatibility',side_effect=RuntimeError('refused')),patch.object(redis,'_ensure_security') as security,patch.object(redis.kube,'apply') as apply:
            with self.assertRaises(RuntimeError):redis.create()
            security.assert_not_called();apply.assert_not_called()


class RedisTenantTest(unittest.TestCase):
    def test_scripting_is_explicit_and_preserves_the_key_boundary(self):
        for readonly in (False,True):
            rules=redis._rules('tenant:',readonly)
            self.assertIn('~tenant:*',rules)
            self.assertIn('&tenant:*',rules)
            self.assertIn('-@scripting',rules)
            self.assertNotIn('+@scripting',rules)
            for cmd in ['+eval','+evalsha','+eval_ro','+evalsha_ro','+script|load','+script|exists']:
                self.assertIn(cmd,rules)
            self.assertNotIn('+@all',rules)
            if readonly:self.assertNotIn('+@write',rules)

    def test_prefix_glob_injection_rejected(self):
        for value in ['*','foo:*','foo?','foo[ab]','foo\\','a\nb','']:
            with self.assertRaises(ValueError):redis._prefix(value)
        self.assertEqual(redis._prefix('valid'),'valid:')

    def test_admin_identity_cannot_be_tenant(self):
        for user in ['ops-admin','default','x\non','']:
            with self.assertRaises(ValueError):redis._username(user)

    def test_prefix_overlap_and_prefix_change_rejected(self):
        client=Mock();client.acl_users.return_value=['ops-admin','default','other'];client.acl_getuser.return_value={'keys':['~team:*']}
        for prefix in ['team:','team:nested:']:
            with self.assertRaisesRegex(RuntimeError,'overlaps'):redis._assert_prefix_available(client,'new',prefix)
        with self.assertRaisesRegex(RuntimeError,'prefix change'):redis._assert_prefix_available(client,'other','different:')
        redis._assert_prefix_available(client,'new','independent:')

    def test_unmanaged_acl_selectors_rejected(self):
        client=Mock();client.acl_users.return_value=['other'];client.acl_getuser.return_value={'keys':['~other:*'],'selectors':[{}]}
        with self.assertRaisesRegex(RuntimeError,'selectors'):redis._assert_prefix_available(client,'new','new:')

    def test_metadata_quotes_credentials(self):
        from urllib.parse import urlsplit,unquote
        url=redis._connection_metadata('user','p/a@ss:word')['REDIS_URL'];parts=urlsplit(url)
        self.assertEqual(parts.hostname,'redis');self.assertEqual(unquote(parts.password),'p/a@ss:word')

    def test_acl_save_failure_does_not_leave_new_grant(self):
        client=Mock();client.__enter__=Mock(return_value=client);client.__exit__=Mock(return_value=False)
        client.lock.return_value=MagicMock();client.scan_iter.return_value=iter([]);client.acl_users.return_value=[];client.acl_getuser.return_value=None;client.acl_save.side_effect=redis.redislib.ResponseError('disk failed')
        with patch.object(redis,'admin_client',return_value=client):
            with self.assertRaisesRegex(RuntimeError,'persist'):redis.provision_user('test','secret','test:')
        client.acl_deluser.assert_called_once_with('test')

    def test_secret_apply_failure_redacted(self):
        with patch.object(redis,'_json',return_value=None),patch.object(redis.subprocess,'run',return_value=Mock(returncode=1,stderr='private',stdout='private')):
            with self.assertRaisesRegex(RuntimeError,'Could not apply Redis credentials'):redis._ensure_security(data())


class RedisQuotaTest(unittest.TestCase):
    def test_mib_boundary_is_exact(self):
        from openserverless import quota_checker as q
        client=Mock();client.calculate_prefix_allocated_size.return_value=1024*1024-1
        user={'metadata':{'name':'test'},'spec':{'namespace':'test','redis':{'quota':1}}}
        with patch.object(q,'annotate') as annotate:
            q.check_redis_quota(client,[user])
            client.set_prefix_readonly.assert_not_called();annotate.assert_not_called()
            client.calculate_prefix_allocated_size.return_value=1024*1024
            q.check_redis_quota(client,[user])
            client.set_prefix_readonly.assert_called_once_with('test','test:')
            annotate.assert_called_once_with('test','redis_db_quota_reached=true')

    def test_unavailable_measurement_does_not_change_permissions(self):
        from openserverless import quota_checker as q
        client=Mock();client.calculate_prefix_allocated_size.return_value=None
        user={'metadata':{'name':'test'},'spec':{'namespace':'test','redis':{'prefix':'test','quota':1}}}
        with patch.object(q,'annotate') as annotate:
            with self.assertRaisesRegex(RuntimeError,'measure'):q.check_redis_quota(client,[user])
            client.set_prefix_readonly.assert_not_called();client.set_prefix_all.assert_not_called();annotate.assert_not_called()


class RedisOrphanTest(unittest.TestCase):
    def test_deleted_identity_does_not_implicitly_inherit_retained_data(self):
        client=MagicMock();client.__enter__.return_value=client
        client.acl_users.return_value=[];client.acl_getuser.return_value=None
        client.scan_iter.return_value=iter(['tenant:old-private-data'])
        with patch.object(redis,'admin_client',return_value=client):
            with self.assertRaisesRegex(RuntimeError,'Orphaned Redis tenant data'):
                redis.provision_user('tenant','new-password','tenant:')
        client.execute_command.assert_not_called()
