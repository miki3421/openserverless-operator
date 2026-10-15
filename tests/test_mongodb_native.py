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
import hashlib
from pathlib import Path

import copy
import json
import logging
import unittest
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlparse, parse_qs, unquote
from pymongo.errors import OperationFailure
from openserverless.mongodb_profile import resources, validate_existing, preflight, PROFILE
from openserverless.mongodb_client import NativeMongo, tenant_names, tenant_uri
from openserverless import mongodb_native as native
from openserverless import quota_checker as quota
from openserverless.user_config import UserConfig
from openserverless.user_metadata import UserMetadata

class NativeMongoTests(unittest.TestCase):
    def current(self):
        return resources()["items"][2]
    def backend(self):
        with patch('openserverless.mongodb_client.MongoClient') as constructor:
            result=NativeMongo('host','admin','test-password')
        return result
    def test_fresh_and_repeated_install_guard(self):
        validate_existing(None,{"items":[]})
        validate_existing(self.current(),{"items":[]})
    def test_legacy_backend_and_orphaned_volumes_are_refused(self):
        old=self.current();old['spec']['template']['spec']['containers'][0]['name']='ferretdb'
        with self.assertRaisesRegex(RuntimeError,'fresh installation'): validate_existing(old,{"items":[]})
        with self.assertRaisesRegex(RuntimeError,'volume reuse'): validate_existing(None,{"items":[{"metadata":{"name":"openserverless-mongodb-pvc-openserverless-mongodb-0"}}]})
    def test_changed_layout_and_disabled_auth_are_refused(self):
        old=self.current();old['spec']['template']['spec']['containers'][0]['volumeMounts'][0]['mountPath']='/state'
        with self.assertRaisesRegex(RuntimeError,'layout'):validate_existing(old,{"items":[]})
        old=self.current();old['spec']['template']['spec']['containers'][0]['args'].remove('--auth')
        with self.assertRaisesRegex(RuntimeError,'authentication'):validate_existing(old,{"items":[]})
    def test_preflight_failure_cannot_rotate_or_apply(self):
        with patch.object(native.cfg,'get',return_value='k3s'),patch.object(native,'preflight',side_effect=RuntimeError('legacy')),patch.object(native,'_secret') as secret,patch.object(native.kube,'apply') as apply:
            with self.assertRaisesRegex(RuntimeError,'legacy'):native.create()
            secret.assert_not_called();apply.assert_not_called()
    def test_static_hostpath_is_refused_before_any_mutation(self):
        with patch.object(native.cfg,'get',side_effect=lambda key:'true' if key=='openserverless.hostpath' else None),patch.object(native,'_secret') as secret,patch.object(native.kube,'apply') as apply:
            with self.assertRaisesRegex(RuntimeError,'hostpath'):native.create()
            secret.assert_not_called();apply.assert_not_called()
    def test_missing_existing_credentials_cannot_be_regenerated(self):
        with patch.object(native.kube,'kubectl',return_value=''),patch.object(native.subprocess,'run') as create:
            with self.assertRaisesRegex(RuntimeError,'refusing to rotate'):native._secret(existing_required=True)
            create.assert_not_called()
    def test_secret_creation_errors_do_not_include_passwords_or_stderr(self):
        with patch.object(native.kube,'kubectl',return_value=''),patch.object(native.subprocess,'run',return_value=Mock(returncode=1,stderr=b'SECRET-MUST-NOT-APPEAR')):
            with self.assertRaises(RuntimeError) as caught:native._secret()
            self.assertNotIn('SECRET-MUST-NOT-APPEAR',str(caught.exception))
    def test_credentials_are_files_not_plain_environment_or_process_args(self):
        pod=self.current()['spec']['template']['spec'];container=pod['containers'][0]
        self.assertFalse(pod['automountServiceAccountToken'])
        self.assertEqual(container['securityContext']['runAsUser'],999)
        self.assertEqual(container['image'],PROFILE['image'])
        self.assertTrue(all(e['name'].endswith('_FILE') or e['name']=='HOME' for e in container['env']))
        self.assertIn('chmod 400',pod['initContainers'][0]['command'][-1])
    def test_scheduling_flags_remain_effective(self):
        pod=resources(affinity=True,tolerations=True)['items'][2]['spec']['template']['spec']
        self.assertEqual(pod['tolerations'][0]['value'],'core')
        self.assertEqual(pod['affinity']['podAntiAffinity']['requiredDuringSchedulingIgnoredDuringExecution'][0]['labelSelector']['matchLabels'],{'app':'openserverless-mongodb'})
    def test_default_and_custom_logical_names_are_preserved(self):
        self.assertEqual(tenant_names('tenant','tenant'),('tenant_ferretdb','tenant'))
        self.assertEqual(tenant_names('tenant','configured_db'),('tenant_ferretdb','configured_db'))
    def test_identifiers_and_uri_reserved_characters(self):
        for namespace,database in [('a','../admin'),('$external','a'),('a','x'*64),('a',None),('a','admin'),('a','openserverless')]:
            with self.assertRaises(ValueError):tenant_names(namespace,database)
        uri=urlparse(tenant_uri('host','tenant','p@:/?#%+','tenant_mongodb'))
        self.assertEqual(unquote(uri.password),'p@:/?#%+')
        self.assertEqual(parse_qs(uri.query)['authSource'],['tenant_mongodb'])
        self.assertEqual(parse_qs(uri.query)['authMechanism'],['SCRAM-SHA-256'])
    def test_role_is_bound_to_tenant_and_reconciliation_keeps_quota(self):
        client=self.backend(); db=client.client['tenant_mongodb']
        db.command.return_value={'users':[]}
        client.provision('tenant_mongodb','password','tenant_mongodb')
        self.assertEqual(db.command.call_args.kwargs['roles'],[{'role':'readWrite','db':'tenant_mongodb'}])
        db.command.return_value={'users':[{'user':'tenant_mongodb','roles':[{'role':'read','db':'tenant_mongodb'}]}]}
        client.provision('tenant_mongodb','new-password','tenant_mongodb')
        self.assertEqual(db.command.call_args.kwargs['roles'],[{'role':'read','db':'tenant_mongodb'}])
    def test_remove_revokes_user_before_dropping_database(self):
        client=self.backend();db=client.client['tenant_mongodb']
        db.command.return_value={'users':[{'user':'tenant_mongodb'}]}
        events=[];db.command.side_effect=lambda *a,**kw:events.append(a[0]) or {'users':[{'user':'tenant_mongodb'}]}
        client.client.drop_database.side_effect=lambda *a:events.append('dropDatabase')
        client.remove('tenant_mongodb','tenant_mongodb')
        self.assertEqual(events,['usersInfo','dropUser','dropDatabase'])
    def test_shared_database_is_not_deleted_and_repeated_removal_is_safe(self):
        client=self.backend();db=client.client['shared_mongodb']
        db.command.return_value={'users':[{'user':'owner_mongodb'},{'user':'peer_mongodb'}]}
        client.remove('owner_mongodb','shared_mongodb');client.client.drop_database.assert_not_called()
        db.command.return_value={'users':[]}
        self.assertTrue(client.remove('owner_mongodb','shared_mongodb'))
    def test_component_absent_deletion_is_a_noop_but_api_errors_propagate(self):
        with patch.object(native.kube,'kubectl',return_value=''),patch.object(native,'_operation') as mutate:
            self.assertTrue(native.delete_db_user('a','a'));mutate.assert_not_called()
        with patch.object(native.kube,'kubectl',side_effect=RuntimeError('Forbidden')):
            with self.assertRaisesRegex(RuntimeError,'Forbidden'):native.delete_db_user('a','a')
    def test_driver_errors_are_sanitized_and_client_is_closed(self):
        client=Mock()
        with patch.object(native,'admin_client',return_value=client):
            with self.assertRaises(RuntimeError) as caught:
                native._operation('tenant provisioning',lambda c:(_ for _ in ()).throw(OperationFailure('SECRET',13)))
            self.assertNotIn('SECRET',str(caught.exception));self.assertIn('13',str(caught.exception));client.close.assert_called_once()
    def test_quotas_use_mib_and_reconcile_after_interrupted_annotations(self):
        client=Mock();client.usage.side_effect=[1024*1024,1024*1024-1]
        user={'spec':{'namespace':'a','mongodb':{'enabled':True,'database':'a','quota':'1'}},'metadata':{'name':'a','annotations':{'ferret_db_quota_reached':'true'}}}
        with patch.object(quota,'annotate') as annotate:
            quota.check_mongo_quota(client,[user]);quota.check_mongo_quota(client,[user])
            self.assertEqual(client.set_readonly.call_args_list[0].args,('a_ferretdb','a',True))
            self.assertEqual(client.set_readonly.call_args_list[1].args,('a_ferretdb','a',False))
            self.assertEqual(annotate.call_args.args,('a','ferret_db_quota_reached=false'))
    def test_disabled_and_invalid_quotas_do_not_write_roles(self):
        client=Mock()
        for enabled,value in [(True,None),(False,'1')]:quota.check_mongo_quota(client,[{'spec':{'mongodb':{'enabled':enabled,'quota':value}}}])
        client.set_readonly.assert_not_called()
        with self.assertRaises(ValueError):quota.check_mongo_quota(client,[{'spec':{'mongodb':{'enabled':True,'quota':'0'}}}])
    def test_auto_quota_restores_writes_even_after_interrupted_annotation(self):
        client=Mock()
        user={'spec':{'namespace':'a','mongodb':{'enabled':True,'database':'a','quota':'auto'}},'metadata':{'name':'a'}}
        with patch.object(quota,'annotate') as annotate:
            quota.check_mongo_quota(client,[user])
            client.set_readonly.assert_called_once_with('a_ferretdb','a',False)
            client.usage.assert_not_called()
            annotate.assert_called_once_with('a','ferret_db_quota_reached=false')
    def test_scheduled_checker_includes_auto_tenants(self):
        user={'spec':{'namespace':'a','mongodb':{'enabled':True,'database':'a','quota':'auto'}},'metadata':{'name':'a'}}
        def users(path):
            return [user] if 'mongodb' in path else []
        with patch.dict('os.environ',{'MONGODB_ENABLED':'true'}),patch.object(quota,'get_wsk_users',side_effect=users),patch.object(native,'admin_client') as admin,patch.object(quota,'check_mongo_quota') as check:
            quota.start()
            check.assert_called_once_with(admin.return_value,[user])
            admin.return_value.close.assert_called_once()
    def test_metadata_and_configuration_debug_logs_do_not_disclose_credentials(self):
        config=UserConfig({'namespace':'tenant','password':'secret-login','mongodb':{'password':'secret-db'}})
        with self.assertLogs(level='DEBUG') as messages:
            config.dump_config();metadata=UserMetadata(config);metadata.add_metadata('MONGODB_URL','mongodb://user:secret-db@host/db');metadata.dump()
        self.assertNotIn('secret-db',' '.join(messages.output));self.assertNotIn('secret-login',' '.join(messages.output))
    def action_module(self):
        parent=types.ModuleType('common');parent.__path__=[]
        util=types.ModuleType('common.util');util.get_env_value=lambda data,key:'mongodb://tenant:password@host/custom_mongodb'
        command=types.ModuleType('common.command_data');command.CommandData=Mock()
        path=Path(__file__).resolve().parents[1]/'actions/devel/ferretdb/command/ferretbd.py'
        spec=importlib.util.spec_from_file_location('test_native_mongo_action',path);module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'common':parent,'common.util':util,'common.command_data':command}):spec.loader.exec_module(module)
        return module
    def test_command_api_uses_uri_database_instead_of_login(self):
        module=self.action_module()
        with patch.object(module,'MongoClient') as ctor:
            module.FerretDB({'login':'a_different_name'})._get_db()
            ctor.return_value.get_default_database.assert_called_once()
            ctor.return_value.__getitem__.assert_not_called()
    def test_replica_set_bson_reply_is_serializable_and_objectid_shape_is_preserved(self):
        from bson import ObjectId, Timestamp
        module=self.action_module();identifier=ObjectId()
        encoded=json.loads(json.dumps({'id':identifier,'operationTime':Timestamp(10,2)},cls=module.JSONEncoder))
        self.assertEqual(encoded['id'],str(identifier))
        self.assertEqual(encoded['operationTime'],{'$timestamp':{'t':10,'i':2}})

if __name__=='__main__':unittest.main()
