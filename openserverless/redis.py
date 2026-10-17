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
import hashlib
import json
import logging
import re
import subprocess
import urllib.parse

import kopf
import redis as redislib
import yaml

import openserverless.config as cfg
import openserverless.kube as kube
import openserverless.template as ntp
import openserverless.util as util
import openserverless.openwhisk as openwhisk
import openserverless.operator_util as operator_util
from openserverless.redis_acl import tenant_rules
from openserverless.user_config import UserConfig
from openserverless.user_metadata import UserMetadata

IMAGE = 'docker.io/library/redis:8.10.2@sha256:2c2dff791878316e3b083188be0d578423b24813cdda5de0e80ea4f7b3e6bfd6'
SERVICE_NAME = 'redis'
ADMIN_USER = 'ops-admin'
LAYOUT = 'v810'


def _json(kind, name):
    raw = kube.kubectl('get', kind, name, '--ignore-not-found', '-o', 'json')
    return json.loads(raw) if raw.strip() else None


def _build_spec(data):
    data = dict(data)
    data['image'] = IMAGE
    data['persistence'] = bool(data['persistence'])
    data['maxmemory'] = str(data['maxmemory'])
    if not re.fullmatch(r'[0-9]+(?:[kKmMgG][bB]?)?', data['maxmemory']):
        raise ValueError('Invalid Redis memory limit')
    sts = yaml.safe_load(ntp.expand_template('redis-set.yaml', data))
    if data.get('affinity') or data.get('tolerations'):
        scheduling = yaml.safe_load(ntp.expand_template('affinity-tolerance-sts-core-attach.yaml', data))
        sts['spec']['template']['spec'].update(scheduling['spec']['template']['spec'])
    cm = yaml.safe_load(ntp.expand_template('redis-conf.yaml', data))
    svc = {'apiVersion':'v1', 'kind':'Service',
           'metadata':{'name':data['name'],'namespace':'openserverless'},
           'spec':{'type':'ClusterIP','selector':{'name':data['name']},
                   'ports':[{'name':'redis','port':6379,'targetPort':'redis'}]}}
    return {'apiVersion':'v1','kind':'List','items':[sts,cm,svc]}


def _check_storage_compatibility(spec, data):
    wanted = spec['items'][0]
    actual = _json('sts', data['name'])
    secret = _json('secret', data['name']+'-admin-v810')
    if actual:
        a = actual['spec']; w = wanted['spec']
        if a['template']['spec']['containers'][0]['image'] != IMAGE:
            raise RuntimeError('Existing Redis image differs; implicit data upgrades are disabled')
        if actual['metadata'].get('labels',{}).get('ops.openserverless.org/redis-layout') != LAYOUT:
            raise RuntimeError('Existing Redis layout differs; implicit data reuse is disabled')
        def claims(s):
            return [(c['metadata']['name'], c['spec'].get('storageClassName'),
                     c['spec']['resources']['requests']['storage']) for c in s['volumeClaimTemplates']]
        if a['replicas'] != 1 or claims(a) != claims(w):
            raise RuntimeError('Existing Redis volume differs; implicit data reuse is disabled')
        cm = _json('cm', data['name']+'-cm')
        if not cm or not secret:
            raise RuntimeError('Existing Redis configuration or credentials are missing; refusing reset')
        wanted_cm = spec['items'][1]
        def storage_directives(config):
            return [line.strip() for line in config.splitlines()
                    if line.strip().startswith(('dir ', 'aclfile ', 'appendonly ', 'appenddirname ', 'dbfilename ', 'save '))]
        if storage_directives(cm['data']['redis-conf']) != storage_directives(wanted_cm['data']['redis-conf']):
            raise RuntimeError('Existing Redis persistence differs; implicit changes are disabled')
    else:
        claims = json.loads(kube.kubectl('get','pvc','-o','json'))['items']
        if any(p['metadata']['name'].startswith('redis-data-v810-'+data['name']+'-') for p in claims):
            raise RuntimeError('Existing Redis PVC without its StatefulSet; implicit data reuse is disabled')
    if secret:
        expected = base64.b64encode(data['redis_password'].encode()).decode()
        if secret.get('data',{}).get('password') != expected:
            raise RuntimeError('Existing Redis credentials differ; implicit password rotation is disabled')


def _ensure_security(data, owner=None):
    name = data['name']+'-admin-v810'
    existing = _json('secret', name)
    if existing:
        if not all(existing.get('data',{}).get(k) for k in ['password','users.acl']):
            raise RuntimeError('Incomplete Redis bootstrap credentials; refusing reset')
        return
    password = data['redis_password']
    if not password:
        raise ValueError('Redis administrator password is required')
    digest = hashlib.sha256(password.encode()).hexdigest()
    acl = 'user default off resetpass resetkeys resetchannels -@all\n'
    acl += f'user {ADMIN_USER} on #{digest} ~* &* +@all\n'
    secret = {'apiVersion':'v1','kind':'Secret','type':'Opaque',
              'metadata':{'name':name,'namespace':'openserverless'},
              'data':{k:base64.b64encode(v.encode()).decode() for k,v in
                      {'password':password,'users.acl':acl}.items()}}
    if owner: kopf.append_owner_reference([secret],owner)
    result = subprocess.run(['kubectl','-n','openserverless','apply','-f','-'],
                            input=json.dumps(secret),text=True,capture_output=True,timeout=30)
    if result.returncode:
        raise RuntimeError('Could not apply Redis credentials')


def admin_client(password=None):
    secret = _json('secret', SERVICE_NAME+'-admin-v810')
    if not secret: raise RuntimeError('Redis administrator credentials are missing')
    stored = base64.b64decode(secret['data']['password']).decode()
    if password is not None and password != stored:
        raise RuntimeError('Redis administrator credentials do not match')
    return redislib.Redis(host=SERVICE_NAME,port=6379,username=ADMIN_USER,password=stored,
                         decode_responses=True,socket_timeout=10,socket_connect_timeout=5)


def _username(username):
    if not isinstance(username,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',username) or username in ('default',ADMIN_USER):
        raise ValueError('Invalid Redis tenant name')
    return username


def _prefix(prefix):
    if not isinstance(prefix,str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,200}',prefix):
        raise ValueError('Invalid Redis tenant prefix')
    return prefix if prefix.endswith(':') else prefix+':'


def _rules(prefix, readonly=False):
    return tenant_rules(prefix, readonly)


def _assert_prefix_available(client, username, prefix):
    for user in client.acl_users():
        if user in ('default',ADMIN_USER): continue
        actual = client.acl_getuser(user)
        keys = actual.get('keys',[])
        if isinstance(keys,str): keys=keys.split()
        for key in keys:
            if not key.startswith('~') or not key.endswith(':*') or any(c in key[1:-1] for c in '*?[]\\'):
                raise RuntimeError('Unmanaged Redis key grant; refusing ambiguous tenant isolation')
            current = key[1:-1]
            if user == username:
                if current != prefix: raise RuntimeError('Redis tenant prefix change requires explicit migration')
            elif current.startswith(prefix) or prefix.startswith(current):
                raise RuntimeError('Redis tenant prefix overlaps another tenant')
        if actual.get('selectors'):
            raise RuntimeError('Unmanaged Redis ACL selectors; refusing ambiguous tenant isolation')


def provision_user(username, password, prefix, readonly=False):
    username=_username(username);prefix=_prefix(prefix)
    if not isinstance(password,str) or not password: raise ValueError('Redis tenant password is required')
    digest=hashlib.sha256(password.encode()).hexdigest()
    with admin_client() as client, client.lock('__ops_acl_lock__', timeout=120, blocking_timeout=15):
        _assert_prefix_available(client,username,prefix)
        old=client.acl_getuser(username)
        if old is None and next(client.scan_iter(match=prefix+'*', count=100), None) is not None:
            raise RuntimeError('Orphaned Redis tenant data; explicit cleanup is required before prefix reuse')
        changed_password=bool(old and old.get('passwords') != [digest])
        client.execute_command('ACL','SETUSER',username,'reset','on','#'+digest,*_rules(prefix,readonly))
        try: client.acl_save()
        except redislib.RedisError:
            # Never leave an unsaved tenant grant active after reporting failure.
            client.acl_deluser(username)
            raise RuntimeError('Could not persist Redis tenant ACL') from None
        if changed_password: client.execute_command('CLIENT','KILL','USER',username,'SKIPME','yes')
    return True


def set_user_readonly(username, prefix, readonly):
    username=_username(username);prefix=_prefix(prefix)
    with admin_client() as client, client.lock('__ops_acl_lock__', timeout=120, blocking_timeout=15):
        _assert_prefix_available(client,username,prefix)
        actual=client.acl_getuser(username)
        if not actual: raise RuntimeError('Redis tenant does not exist')
        # Keep password hashes, replace command/key/channel grants atomically.
        client.execute_command('ACL','SETUSER',username,*_rules(prefix,readonly))
        client.acl_save()
    return True


def create(owner=None):
    data=util.get_redis_config_data();spec=_build_spec(data)
    _check_storage_compatibility(spec,data)
    _ensure_security(data,owner)
    if owner: kopf.append_owner_reference(spec['items'],owner)
    else: cfg.put('state.redis.spec',spec)
    result=kube.apply(spec)
    wait_for_redis_ready()
    if not create_openserverless_db_user(data):
        raise RuntimeError('Could not provision the OpenServerless Redis account')
    return result


def wait_for_redis_ready():
    kube.kubectl('rollout','status','sts/'+SERVICE_NAME,'--timeout=180s')


def _connection_metadata(username, password):
    user=urllib.parse.quote(username,safe='');pwd=urllib.parse.quote(password,safe='')
    return {'REDIS_URL':f'redis://{user}:{pwd}@{SERVICE_NAME}:6379',
            'REDIS_ALT_URL':f'redis://{user}:{pwd}@{SERVICE_NAME}:6379',
            'REDIS_SERVICE':SERVICE_NAME,'REDIS_PORT':6379,
            'REDIS_PROVIDER':'redis','REDIS_PASSWORD':password}


def _add_redis_user_metadata(ucfg, user_metadata):
    for key,value in _connection_metadata(ucfg.get('namespace'),ucfg.get('redis.password')).items():
        user_metadata.add_metadata(key,value)


def create_openserverless_db_user(data):
    provision_user(data['namespace'],data['password'],data['prefix'])
    values=_connection_metadata(data['namespace'],data['password']);values['REDIS_PREFIX']=_prefix(data['prefix'])
    for key,value in values.items(): openwhisk.annotate(key.lower()+'='+str(value))
    return True


def restore_openserverless_db_user():
    return create_openserverless_db_user(util.get_redis_config_data())


def create_db_user(ucfg: UserConfig, user_metadata: UserMetadata, read_only_mode=False):
    namespace=ucfg.get('namespace');prefix=_prefix(ucfg.get('redis.prefix') or namespace)
    provision_user(namespace,ucfg.get('redis.password'),prefix,read_only_mode)
    user_metadata.add_metadata('REDIS_PREFIX',prefix)
    _add_redis_user_metadata(ucfg,user_metadata)
    return True


def delete_db_user(namespace):
    namespace=_username(namespace)
    with admin_client() as client, client.lock('__ops_acl_lock__', timeout=120, blocking_timeout=15):
        client.acl_deluser(namespace)
        client.acl_save()
    # Retain keys, matching the previous deletion contract; remove only credentials.
    return True


def delete(owner=None):
    data=util.get_redis_config_data();spec=_build_spec(data) if owner else cfg.get('state.redis.spec')
    # Volume claim retention keeps identities/data; the orphan guard refuses implicit reuse.
    return kube.delete(spec) if spec else False


def patch(status, action, owner=None):
    try:
        result=create(owner) if action=='create' else delete(owner)
        operator_util.patch_operator_status(status,'redis','on' if action=='create' else 'off')
        logging.info('Redis component reconciliation completed')
        return result
    except Exception:
        operator_util.patch_operator_status(status,'redis','error')
        raise
