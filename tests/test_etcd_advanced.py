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
import base64
import json
import pathlib
import subprocess
import unittest
from unittest.mock import patch, Mock

from openserverless import etcd, etcd_tls


def data():
    return {'name':'openserverless-etcd','namespace':'openserverless','container':'openserverless-etcd',
        'root_password':'synthetic','etcd_replicas':3,'etcd_auto_compaction_retention':'1',
        'etcd_quota_backend_bytes':2147483648,'container_cpu_req':'250m',
        'container_mem_req':'256Mi','container_mem_lim':'384Mi','size':5,
        'storageClass':'local-path','affinity':False,'tolerations':False}


class EtcdRenderTest(unittest.TestCase):
    def test_singleton_and_quorum_cluster_require_tls_auth_and_fresh_storage(self):
        for count in (1,3,5):
            d=data();d['etcd_replicas']=count;spec=etcd._build_spec(d)
            sts=spec['items'][0];pod=sts['spec']['template']['spec'];c=pod['containers'][0]
            self.assertEqual(sts['spec']['replicas'],count)
            self.assertEqual(c['image'],etcd.IMAGE)
            self.assertNotIn('bitnami',json.dumps(spec))
            self.assertIn('--client-cert-auth=true',c['args'])
            self.assertIn('--peer-client-cert-auth=true',c['args'])
            self.assertIn('--tls-min-version=TLS1.3',c['args'])
            self.assertFalse(pod['automountServiceAccountToken'])
            self.assertTrue(c['securityContext']['readOnlyRootFilesystem'])
            self.assertEqual(sts['spec']['volumeClaimTemplates'][0]['metadata']['name'],'etcd-data-v37')
            self.assertEqual(c['startupProbe']['exec']['command'],['/usr/local/bin/ops-etcd-health'])
            self.assertFalse(any('synthetic' in a for a in c['args']))
            self.assertTrue(all(i['spec']['type']=='ClusterIP' for i in spec['items'] if i['kind']=='Service'))
            self.assertEqual(len([i for i in spec['items'] if i['kind']=='PodDisruptionBudget']),int(count>1))

    def test_even_membership_refused(self):
        d=data();d['etcd_replicas']=2
        with self.assertRaises(ValueError):etcd._build_spec(d)


class EtcdGuardTest(unittest.TestCase):
    def setUp(self):
        self.data=data();self.spec=etcd._build_spec(self.data)
        self.sts=copy.deepcopy(self.spec['items'][0])

    @patch.object(etcd,'_json')
    def test_identical_cluster_accepted(self,get):
        get.side_effect=[self.sts,{'data':{'rootPassword':base64.b64encode(b'synthetic').decode()}}]
        etcd._check_storage_compatibility(self.spec,self.data)

    @patch.object(etcd,'_json')
    def test_legacy_image_rejected(self,get):
        self.sts['spec']['template']['spec']['containers'][0]['image']='bitnami/etcd:3.5.17'
        get.return_value=self.sts
        with self.assertRaisesRegex(RuntimeError,'image differs'):etcd._check_storage_compatibility(self.spec,self.data)

    @patch.object(etcd,'_json')
    def test_membership_change_rejected(self,get):
        self.sts['spec']['replicas']=1;get.return_value=self.sts
        with self.assertRaisesRegex(RuntimeError,'membership or volume'):etcd._check_storage_compatibility(self.spec,self.data)

    @patch.object(etcd,'_json')
    def test_volume_identity_change_rejected(self,get):
        self.sts['spec']['volumeClaimTemplates'][0]['metadata']['name']='old-volume';get.return_value=self.sts
        with self.assertRaisesRegex(RuntimeError,'membership or volume'):etcd._check_storage_compatibility(self.spec,self.data)

    @patch.object(etcd,'_json',return_value=None)
    @patch.object(etcd.kube,'kubectl')
    def test_orphan_volume_rejected(self,kube,get):
        kube.return_value=json.dumps({'items':[{'metadata':{'name':'etcd-data-v37-openserverless-etcd-0'}}]})
        with self.assertRaisesRegex(RuntimeError,'without its StatefulSet'):etcd._check_storage_compatibility(self.spec,self.data)

    @patch.object(etcd,'_json')
    def test_password_rotation_rejected(self,get):
        get.side_effect=[self.sts,{'data':{'rootPassword':'different'}}]
        with self.assertRaisesRegex(RuntimeError,'credentials differ'):etcd._check_storage_compatibility(self.spec,self.data)

    @patch.object(etcd,'_json')
    def test_missing_root_credentials_are_not_reset(self,get):
        get.side_effect=[self.sts,None]
        with self.assertRaisesRegex(RuntimeError,'credentials are missing'):etcd._check_storage_compatibility(self.spec,self.data)

    @patch.object(etcd,'_ensure_security')
    @patch.object(etcd,'_check_storage_compatibility',side_effect=RuntimeError('refused'))
    @patch.object(etcd.util,'get_etcd_config_data',side_effect=data)
    def test_guard_precedes_key_or_account_mutation(self,config,guard,security):
        with self.assertRaisesRegex(RuntimeError,'refused'):etcd.create()
        security.assert_not_called()


class EtcdSecurityTest(unittest.TestCase):
    def test_new_installations_have_distinct_keys_and_client_has_no_cn_identity(self):
        server,client=etcd_tls.generate_security('test-etcd','openserverless')
        second,_=etcd_tls.generate_security('test-etcd','openserverless')
        self.assertNotEqual(server['jwt.key'],second['jwt.key'])
        self.assertNotEqual(server['ca.crt'],second['ca.crt'])
        self.assertEqual(server['ca.crt'],client['ca.crt'])
        self.assertNotIn('jwt.key',client)
        result=subprocess.run(['openssl','x509','-noout','-subject'],input=client['tls.crt'],text=True,capture_output=True,check=True)
        self.assertNotIn('CN =',result.stdout)

    def test_administration_forbids_plain_http(self):
        with self.assertRaises(ValueError):
            with etcd_tls.gateway('http://localhost:2379',{}):pass

    @patch.object(etcd_tls,'apply_private')
    @patch.object(etcd,'_json')
    def test_incomplete_ca_is_not_regenerated(self,get,apply):
        get.side_effect=[{'data':{'ca.crt':'old'}},None]
        with self.assertRaisesRegex(RuntimeError,'Incomplete'):etcd._ensure_security(data())
        apply.assert_not_called()

    @patch.object(etcd_tls,'gateway')
    def test_invalid_root_password_is_not_reset(self,gateway):
        session=gateway.return_value.__enter__.return_value
        session.get.return_value.ok=True
        session.post.return_value.json.return_value={'error':'etcdserver: authentication failed, invalid user ID or password'}
        with self.assertRaisesRegex(RuntimeError,'credentials were not reset'):etcd._bootstrap_auth(data(),{})
        self.assertEqual(session.post.call_count,1)

    @patch.object(etcd_tls,'subprocess')
    def test_secret_apply_error_does_not_expose_payload(self,subprocess):
        subprocess.run.return_value.returncode=1
        with self.assertRaisesRegex(RuntimeError,'Could not apply etcd security resources') as ctx:
            etcd_tls.apply_private({'data':{'key':'secret-value'}},'openserverless')
        self.assertNotIn('secret-value',str(ctx.exception))


class EtcdAuthenticationRefreshTest(unittest.TestCase):
    def response(self,ok,body):
        r=Mock();r.ok=ok;r.json.return_value=body;return r

    def test_stale_authenticated_session_is_renewed_once(self):
        s=Mock();s.headers={};s._ops_etcd_credentials={'name':'root','password':'synthetic'}
        s.post.side_effect=[self.response(False,{'message':'etcdserver: revision of auth store is old'}),
            self.response(True,{'token':'renewed'}),self.response(True,{'header':{}})]
        etcd._post(s,'https://etcd','kv/put',{})
        self.assertEqual(s.post.call_count,3)
        self.assertEqual(s.headers['Authorization'],'renewed')

    def test_tenant_without_refresh_credentials_is_not_elevated(self):
        s=Mock();s._ops_etcd_credentials=None
        s.post.return_value=self.response(False,{'message':'etcdserver: revision of auth store is old'})
        with self.assertRaises(RuntimeError):etcd._post(s,'https://etcd','kv/put',{})
        self.assertEqual(s.post.call_count,1)

    def test_permission_failure_is_not_retried(self):
        s=Mock();s._ops_etcd_credentials={'name':'root','password':'synthetic'}
        s.post.return_value=self.response(False,{'message':'etcdserver: permission denied'})
        with self.assertRaises(RuntimeError):etcd._post(s,'https://etcd','kv/put',{})
        self.assertEqual(s.post.call_count,1)


if __name__=='__main__':unittest.main()
