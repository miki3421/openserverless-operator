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

import base64
import json
import logging
import contextlib
import re
import time

import kopf
import requests
import yaml

import openserverless.config as cfg
import openserverless.kube as kube
import openserverless.template as ntp
import openserverless.util as util
import openserverless.operator_util as operator_util
from openserverless import etcd_tls

IMAGE = 'docker.io/miki3421/ops-advanced-etcd:3.7.2-20261016@sha256:02969ce1a6172cfd0d4363224e6fc8e9de3051bc43dd384ae82af5ffbca5e3ea'
LAYOUT = 'v37'


def _json(kind, name):
    raw = kube.kubectl('get', kind, name, '--ignore-not-found', '-o', 'json')
    return json.loads(raw) if raw.strip() else None


def _build_spec(data):
    data = dict(data)
    data.setdefault('image', IMAGE)
    replicas = int(data['etcd_replicas'])
    if replicas not in (1, 3, 5):
        raise ValueError('etcd requires one, three or five members')
    data['etc_initial_cluster'] = ','.join(
        f"{data['name']}-{i}=https://{data['name']}-{i}.{data['name']}-headless.{data['namespace']}.svc.cluster.local:2380"
        for i in range(replicas))
    retention = str(data['etcd_auto_compaction_retention'])
    data['etcd_auto_compaction_retention'] = retention+'h' if retention.isdigit() else retention
    deployment = yaml.safe_load(ntp.expand_template('etcd-sts.yaml', data))
    if data.get('affinity') or data.get('tolerations'):
        scheduling = yaml.safe_load(ntp.expand_template('affinity-tolerance-sts-core-attach.yaml', data))
        deployment['spec']['template']['spec'].update(scheduling['spec']['template']['spec'])
    items = [deployment]
    for headless in (False, True):
        name = data['name'] + ('-headless' if headless else '')
        spec = {'type':'ClusterIP', 'selector':{'app.kubernetes.io/name':data['name']},
                'ports':[{'name':'client','port':2379,'targetPort':'client'},
                         {'name':'peer','port':2380,'targetPort':'peer'}]}
        if headless: spec.update({'clusterIP':'None','publishNotReadyAddresses':True})
        items.append({'apiVersion':'v1','kind':'Service',
            'metadata':{'name':name,'namespace':data['namespace']}, 'spec':spec})
    if replicas > 1:
        items.append({'apiVersion':'policy/v1','kind':'PodDisruptionBudget',
            'metadata':{'name':data['name'],'namespace':data['namespace']},
            'spec':{'minAvailable':replicas//2+1,
                    'selector':{'matchLabels':{'app.kubernetes.io/name':data['name']}}}})
    return {'apiVersion':'v1','kind':'List','items':items}


def _check_storage_compatibility(spec, data):
    wanted = spec['items'][0]
    actual = _json('statefulset', data['name'])
    if actual:
        a = actual['spec']; w = wanted['spec']
        if a['template']['spec']['containers'][0]['image'] != w['template']['spec']['containers'][0]['image']:
            raise RuntimeError('Existing etcd image differs; implicit data upgrades are disabled')
        if actual['metadata'].get('labels',{}).get('ops.openserverless.org/etcd-layout') != LAYOUT:
            raise RuntimeError('Existing etcd layout differs; implicit data reuse is disabled')
        if (a['replicas'] != w['replicas'] or a['volumeClaimTemplates'] != w['volumeClaimTemplates']):
            # Kubernetes defaults fields such as volumeMode, so compare claim identities below.
            def claims(s):
                return [(v['metadata']['name'], v['spec'].get('storageClassName'),
                         v['spec']['resources']['requests']['storage']) for v in s['volumeClaimTemplates']]
            if a['replicas'] != w['replicas'] or claims(a) != claims(w):
                raise RuntimeError('Existing etcd membership or volume differs; review it explicitly')
        def identity(s):
            return [v for v in s['template']['spec']['containers'][0]['args']
                    if v.startswith(('--data-dir=', '--initial-cluster=', '--initial-cluster-token=',
                                     '--listen-peer-urls=', '--peer-client-cert-auth=', '--client-cert-auth='))]
        if identity(a) != identity(w):
            raise RuntimeError('Existing etcd cluster identity differs; implicit data reuse is disabled')
    else:
        claims = json.loads(kube.kubectl('get','pvc','-o','json'))['items']
        if any(p['metadata']['name'].startswith('etcd-data-v37-'+data['name']+'-') for p in claims):
            raise RuntimeError('Existing etcd 3.7 PVC without its StatefulSet; implicit data reuse is disabled')
    root = _json('secret', data['name']+'-secret')
    if actual and not root:
        raise RuntimeError('Existing etcd credentials are missing; refusing implicit reset')
    if root and root.get('data',{}).get('rootPassword') != util.b64_encode(data['root_password']):
        raise RuntimeError('Existing etcd credentials differ; implicit password rotation is disabled')


def _ensure_security(data, owner=None):
    name=data['name']; ns=data['namespace']
    server=_json('secret', name+'-server-tls-v37')
    client=_json('secret', name+'-client-tls-v37')
    if bool(server) != bool(client):
        raise RuntimeError('Incomplete etcd TLS material; refusing implicit CA rotation')
    if server:
        if (not all(server.get('data',{}).get(k) for k in ['ca.crt','tls.crt','tls.key','jwt.key']) or
                not all(client.get('data',{}).get(k) for k in ['ca.crt','tls.crt','tls.key']) or
                server['data']['ca.crt'] != client['data']['ca.crt']):
            raise RuntimeError('Invalid etcd TLS material; refusing implicit CA rotation')
    else:
        if _json('statefulset',name):
            raise RuntimeError('Existing etcd TLS material is missing; refusing implicit CA rotation')
        values=etcd_tls.generate_security(name,ns)
        resources=[]
        for suffix, material in zip(['server','client'],values):
            resources.append({'apiVersion':'v1','kind':'Secret','type':'Opaque',
                'metadata':{'name':name+'-'+suffix+'-tls-v37','namespace':ns},
                'data':{k:util.b64_encode(v) for k,v in material.items()}})
        if owner: kopf.append_owner_reference(resources,owner)
        etcd_tls.apply_private({'apiVersion':'v1','kind':'List','items':resources},ns)
        client=resources[1]
    root={'apiVersion':'v1','kind':'Secret','type':'Opaque',
          'metadata':{'name':name+'-secret','namespace':ns},
          'data':{'rootPassword':util.b64_encode(data['root_password'])}}
    if owner: kopf.append_owner_reference([root],owner)
    etcd_tls.apply_private(root,ns)
    return client['data']


def _post(session, endpoint, path, body, allowed=()):
    for attempt in range(2):
        try:
            r=session.post(endpoint+'/v3/'+path,json=body,timeout=8)
            result=r.json()
        except (requests.RequestException,ValueError):
            raise RuntimeError('etcd administration request failed') from None
        error=result.get('error') or result.get('message','')
        credentials=getattr(session,'_ops_etcd_credentials',None)
        if attempt==0 and credentials and error in (
                'etcdserver: revision of auth store is old', 'etcdserver: invalid auth token'):
            # Auth-store updates can invalidate a JWT between admin operations.
            # Refresh only this authenticated session, never substitute root for a tenant.
            try:
                auth=session.post(endpoint+'/v3/auth/authenticate',json=credentials,timeout=8)
                token=auth.json().get('token') if auth.ok else None
            except (requests.RequestException,ValueError): token=None
            if not token: raise RuntimeError('Could not renew etcd administration authentication')
            session.headers['Authorization']=token
            continue
        if error in allowed: return result
        if not r.ok or error:
            raise RuntimeError('etcd administration request was rejected')
        return result


def _bootstrap_auth(data, material, timeout=120):
    endpoint=f"https://{data['name']}-0.{data['name']}-headless.{data['namespace']}.svc.cluster.local:2379"
    with etcd_tls.gateway(endpoint,material) as session:
        deadline=time.monotonic()+timeout
        while True:
            try:
                r=session.get(endpoint+'/readyz',timeout=3)
                if r.ok: break
            except requests.RequestException: pass
            if time.monotonic()>deadline: raise RuntimeError('etcd quorum did not become available')
            time.sleep(1)
        credentials={'name':'root','password':data['root_password']}
        session._ops_etcd_credentials=credentials
        r=session.post(endpoint+'/v3/auth/authenticate',json=credentials,timeout=8)
        result=r.json()
        if result.get('token'):
            session.headers['Authorization']=result['token']
        elif (result.get('error') or result.get('message')) == 'etcdserver: authentication is not enabled':
            _post(session,endpoint,'auth/user/add',credentials,
                  allowed=('etcdserver: user name already exists',))
            _post(session,endpoint,'auth/role/add',{'name':'root'},
                  allowed=('etcdserver: role name already exists',))
            _post(session,endpoint,'auth/user/grant',{'user':'root','role':'root'})
            _post(session,endpoint,'auth/enable',{})
            token=_post(session,endpoint,'auth/authenticate',credentials)['token']
            session.headers['Authorization']=token
        else:
            raise RuntimeError('etcd root authentication failed; credentials were not reset')
        if not _post(session,endpoint,'auth/status',{}).get('enabled'):
            raise RuntimeError('etcd authentication did not become enabled')


def create(owner=None):
    data=util.get_etcd_config_data();spec=_build_spec(data)
    _check_storage_compatibility(spec,data)
    material=_ensure_security(data,owner)
    if owner: kopf.append_owner_reference(spec['items'],owner)
    else: cfg.put('state.etcd.spec',spec)
    res=kube.apply(spec)
    _bootstrap_auth(data,material)
    wait_for_etcd_ready()
    return res


def wait_for_etcd_ready():
    kube.kubectl('rollout','status','statefulset/openserverless-etcd','--timeout=240s')


def delete(owner=None):
    if owner:
        data=util.get_etcd_config_data();spec=_build_spec(data)
    else:
        spec=cfg.get('state.etcd.spec');data=util.get_etcd_config_data()
    res=kube.delete(spec) if spec else False
    for suffix in ['secret','server-tls-v37','client-tls-v37']:
        kube.kubectl('delete','secret',data['name']+'-'+suffix,'--ignore-not-found')
    return res


def patch(status, action, owner=None):
    try:
        msg=create(owner) if action=='create' else delete(owner)
        operator_util.patch_operator_status(status,'etcd','on' if action=='create' else 'off')
        logging.info('etcd component reconciliation completed')
    except Exception:
        operator_util.patch_operator_status(status,'etcd','error')
        raise


@contextlib.contextmanager
def _admin():
    data=util.get_etcd_config_data()
    endpoint=f"https://{data['name']}-0.{data['name']}-headless.{data['namespace']}.svc.cluster.local:2379"
    material=_json('secret',data['name']+'-client-tls-v37')['data']
    with etcd_tls.gateway(endpoint,material) as session:
        session._ops_etcd_credentials={'name':'root','password':data['root_password']}
        session.headers['Authorization']=_post(session,endpoint,'auth/authenticate',
            {'name':'root','password':data['root_password']})['token']
        yield session,endpoint


def create_etcd_user(username, password, prefix):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',username) or username=='root' or not prefix.strip('/'):
        raise ValueError('Invalid etcd user or key prefix')
    try:
        with _admin() as (session,endpoint):
            result=_post(session,endpoint,'auth/user/add',{'name':username,'password':password},
                         allowed=('etcdserver: user name already exists',))
            if result.get('error') or result.get('message'):
                _post(session,endpoint,'auth/authenticate',{'name':username,'password':password})
            role=username+'_role'
            _post(session,endpoint,'auth/role/add',{'name':role},
                  allowed=('etcdserver: role name already exists',))
            key=(prefix.rstrip('/')+'/').encode()
            end=bytearray(key);end[-1]+=1
            _post(session,endpoint,'auth/role/grant',{'name':role,'perm':{'permType':'READWRITE',
                'key':base64.b64encode(key).decode(),'range_end':base64.b64encode(end).decode()}})
            _post(session,endpoint,'auth/user/grant',{'user':username,'role':role})
        return True
    except RuntimeError:
        logging.error('Could not provision the etcd user')
        return False


def delete_db_user(username):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',username) or username=='root':
        raise ValueError('Invalid etcd user')
    try:
        with _admin() as (session,endpoint):
            _post(session,endpoint,'auth/user/delete',{'name':username},
                  allowed=('etcdserver: user name not found',))
            _post(session,endpoint,'auth/role/delete',{'role':username+'_role'},
                  allowed=('etcdserver: role name not found',))
        return True
    except RuntimeError:
        logging.error('Could not remove the etcd user')
        return False
