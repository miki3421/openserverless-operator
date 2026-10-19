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
#
# Deploys a standalone ferretdb relying on postgres db
#

"""Execute provisioning functions with Kubernetes boundaries mocked, no cluster."""
import ast
import base64
import copy
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
import urllib

ROOT = Path(__file__).resolve().parents[2]


def functions(filename, names, namespace):
    tree = ast.parse((ROOT / 'openserverless' / filename).read_text())
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            selected.append(node)
    code = ast.Module(body=selected, type_ignores=[])
    exec(compile(code, filename, 'exec'), namespace)
    return namespace


def validators():
    ns = functions('util.py', ['validate_namespace', 'validate_database_name'], {'re': re})
    return SimpleNamespace(**ns)


class MongoProvisioningTests(unittest.TestCase):
    def mongo(self, metadata_ok=True):
        util = validators()
        util.get_postgres_config_data = Mock(return_value={})
        util.get_pod_name_by_selector = Mock(return_value='postgres-primary')
        calls = []
        render_ns = functions('postgres_operator.py', ['render_postgres_script'], {'util': util, 'base64': base64, 'os': SimpleNamespace(path=os.path, chmod=lambda *args: None),
            'ntp': SimpleNamespace(spool_template=lambda template, out, data: out)})
        def render(namespace, template, data):
            calls.append((namespace, template, copy.deepcopy(data)))
            return render_ns['render_postgres_script'](namespace, template, data)
        postgres = SimpleNamespace(render_postgres_script=render, exec_psql_command=Mock(return_value='CREATE DATABASE'))
        ns = functions('ferretdb.py', ['create_db_user', 'delete_db_user'], {'util':util,'postgres':postgres,
            'logging':logging,'UserConfig':object,'UserMetadata':object,'_add_mdb_user_metadata':Mock(return_value=metadata_ok)})
        config = {'namespace':'fixtureuser','mongodb.database':'fixturedb','mongodb.password':'synthetic'}
        ucfg = SimpleNamespace(get=config.get)
        return ns, ucfg, calls, postgres

    def test_mongo_retains_postgres_suffix_without_passing_it_as_kube_namespace(self):
        ns, user, calls, postgres = self.mongo()
        self.assertTrue(ns['create_db_user'](user, object()))
        self.assertEqual([x[0] for x in calls], ['fixtureuser','fixtureuser'])
        self.assertEqual(calls[0][2]['database'],'fixturedb_ferretdb')
        self.assertEqual(calls[0][2]['username'],'fixtureuser_ferretdb')
        postgres.exec_psql_command.assert_called_once()

    def test_delete_uses_the_same_safe_namespace_and_existing_postgres_names(self):
        ns, _, calls, postgres = self.mongo()
        self.assertTrue(ns['delete_db_user']('fixtureuser','fixturedb'))
        self.assertEqual(calls[0][0], 'fixtureuser')
        self.assertEqual(calls[0][2]['database'],'fixturedb_ferretdb')
        postgres.exec_psql_command.assert_called_once()

    def test_missing_connection_metadata_prevents_success(self):
        ns, user, _, _ = self.mongo(metadata_ok=False)
        self.assertIsNone(ns['create_db_user'](user, object()))

    def test_invalid_database_is_rejected_before_any_sql_execution(self):
        ns, user, _, postgres = self.mongo()
        user.get=lambda k: {'namespace':'fixtureuser','mongodb.database':'x;id','mongodb.password':'synthetic'}.get(k)
        self.assertIsNone(ns['create_db_user'](user,object()))
        postgres.exec_psql_command.assert_not_called()

    def test_real_kube_namespace_validation_is_not_weakened(self):
        self.assertTrue(validators().validate_namespace('fixtureuser'))
        self.assertFalse(validators().validate_namespace('fixtureuser_ferretdb'))


class ReadyTests(unittest.TestCase):
    def ready(self, mongo_result=True, metadata_result=True):
        cfg=SimpleNamespace(get=lambda k:k=='components.mongodb')
        spec={'namespace':'fixtureuser','auth':'synthetic','mongodb.enabled':True}
        user=SimpleNamespace(get=spec.get)
        metadata=Mock()
        ns=functions('user_handlers.py',['whisk_user_create'],{'cfg':cfg,'get_ucfg':lambda _:user,
            'UserMetadata':lambda _:metadata,'datetime':datetime,'logging':logging,
            'kube':SimpleNamespace(get=lambda _:{}),'cdb':SimpleNamespace(create_ow_user=lambda *_:True),
            'endpoint':SimpleNamespace(create_ow_api_endpoint=lambda *_:'applied'),
            'mdb':SimpleNamespace(create_db_user=lambda *_:mongo_result),
            'userdb':SimpleNamespace(save_user_metadata=lambda _:metadata_result)})
        patch=SimpleNamespace(status={})
        result=ns['whisk_user_create'](spec,'fixtureuser',patch)
        return result,patch.status['conditions'][-1]

    def test_successful_provisioning_remains_ready(self):
        _,condition=self.ready()
        self.assertEqual(condition['status'],'True')

    def test_failed_mongo_is_not_ready(self):
        _,condition=self.ready(mongo_result=None)
        self.assertEqual(condition['status'],'False')
        self.assertIn('mongodb',condition['message'])

    def test_failed_metadata_persistence_is_not_ready(self):
        _,condition=self.ready(metadata_result=False)
        self.assertEqual(condition['status'],'False')
        self.assertIn('user_metadata',condition['message'])


class SqlTests(unittest.TestCase):
    def test_batch_sql_has_private_password_files_no_tty_and_strict_error_exit(self):
        import tempfile
        for function in ['exec_psql_command','exec_psql_command_in_db']:
            with self.subTest(function=function), tempfile.TemporaryDirectory() as folder:
                script=Path(folder,'owner.sql');password=Path(folder,'owner.pgpass')
                script.touch();password.touch();calls=[]
                def kubectl(*args):
                    calls.append(args)
                    return 'CREATE DATABASE' if args[0]=='exec' and 'psql ' in args[-1] else ''
                ns=functions('postgres_operator.py',[function,'_cleanup_psql_files'],{'os':os,'logging':logging,
                    'util':validators(),'kube':SimpleNamespace(kubectl=kubectl)})
                args=['ownerdb','postgres-primary',str(script),str(password)] if function.endswith('_in_db') else ['postgres-primary',str(script),str(password)]
                self.assertTrue(ns[function](*args))
                commands=[c[-1] for c in calls if c[0]=='exec' and 'psql ' in c[-1]]
                self.assertIn('ON_ERROR_STOP=1',commands[0])
                self.assertIn(str(password),commands[0])
                self.assertNotIn('-it',[arg for call in calls for arg in call])
                self.assertFalse(script.exists());self.assertFalse(password.exists())
                self.assertIn(('exec','postgres-primary','--','rm','-f',str(script),str(password)),calls)

    def test_sql_failure_propagates_and_cleans_temporary_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            script=Path(folder,'owner.sql');password=Path(folder,'owner.pgpass');script.touch();password.touch()
            def kubectl(*args):
                if args[0]=='exec' and 'psql ' in args[-1]:raise RuntimeError('SQL failed')
                return ''
            ns=functions('postgres_operator.py',['exec_psql_command','_cleanup_psql_files'],{'os':os,'logging':logging,'kube':SimpleNamespace(kubectl=kubectl)})
            with self.assertRaisesRegex(RuntimeError,'SQL failed'):ns['exec_psql_command']('postgres-primary',str(script),str(password))
            self.assertFalse(script.exists());self.assertFalse(password.exists())


class KubernetesResultTests(unittest.TestCase):
    def test_another_worker_cannot_replace_this_commands_result(self):
        ns={'logging':logging,'json':json,'mocker':SimpleNamespace(invoke=lambda *_:None)}
        class Result:
            stdout=b'own-result';stderr=b''
            reads=0
            @property
            def returncode(self):
                self.reads+=1
                if self.reads>1:ns['output']='another-workers-output'
                return 0
        ns['subprocess']=SimpleNamespace(run=lambda *_args,**_kwargs:Result())
        functions('kube.py',['kubectl'],ns)
        self.assertEqual(ns['kubectl']('get','pod'),'own-result')


if __name__ == '__main__': unittest.main()
