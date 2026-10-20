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

"""Explicitly scoped CouchDB lifecycle regression for the two disposable OPS labs.

Run inside the operator pod with --lab and mode setup, before, after or cleanup.
No credential or HTTP response body is printed. Never run this on production.
"""
import argparse
import base64
import copy
import json
import logging
import secrets
import subprocess
import time
import uuid
from unittest.mock import patch

import requests
from minio import Minio
from minio.error import S3Error
from openserverless import config as cfg, kube, couchdb
from openserverless.couchdb_util import CouchDB

TENANT = 'cdbsec20261020'
LABEL = 'couchdb-security-20261020'


def apply_private(spec):
    result = subprocess.run(['kubectl', '-n', 'openserverless', 'apply', '-f', '-'],
                            input=json.dumps(spec), text=True, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError('Could not apply synthetic lab resources')


def scheduler():
    name = 'cdb-security-scheduler'
    assert not kube.get('job/' + name)
    template = copy.deepcopy(kube.get('cronjob/cron-init')['spec']['jobTemplate']['spec'])
    image = kube.get('sts/openserverless-operator')['spec']['template']['spec']['containers'][0]['image']
    template['template']['spec']['containers'][0]['image'] = image
    template['backoffLimit'] = 0
    job = {'apiVersion': 'batch/v1', 'kind': 'Job',
           'metadata': {'name': name, 'namespace': 'openserverless', 'labels': {'ops-lab-validation': LABEL}},
           'spec': template}
    try:
        apply_private(job)
        kube.kubectl('wait', '--for=condition=complete', 'job/' + name, '--timeout=180s')
        print(json.dumps({'scheduler_complete': True, 'candidate_image': True}))
    finally:
        kube.kubectl('delete', 'job', name, '--ignore-not-found', '--wait=true', '--timeout=90s')


def bootstrap():
    """Run the candidate dbinit against an ephemeral, independent database."""
    rendered = []
    with patch.object(couchdb, 'apply_couchdb_spec', side_effect=lambda spec: rendered.append(spec) or True), \
         patch.object(couchdb.util, 'wait_for_pod_ready', return_value=True):
        assert couchdb.create()
    spec = rendered[0]
    init_secret = next(item for item in spec['items'] if item['kind'] == 'Secret' and item['metadata']['name'] == 'couchdb-init-config')
    original_job = next(item for item in spec['items'] if item['kind'] == 'Job')
    container = original_job['spec']['template']['spec']['containers'][0]
    assert container['env'] == [{'name': 'OPENSERVERLESS_CONFIG', 'valueFrom': {'secretKeyRef': {'name': 'couchdb-init-config', 'key': 'config'}}}]
    init_config = json.loads(init_secret['stringData']['config'])
    assert all(key.startswith(('couchdb.', 'openwhisk.namespaces.')) for key in init_config)
    # Apply the private service change without restarting CouchDB or touching data.
    old_service = kube.get('svc/couchdb-np')
    services = [item for item in spec['items'] if item['kind'] == 'Service']
    assert kube.apply({'apiVersion': 'v1', 'kind': 'List', 'items': services})
    service = kube.get('svc/couchdb-np')
    assert service['metadata']['uid'] == old_service['metadata']['uid']
    assert service['spec']['clusterIP'] == old_service['spec']['clusterIP']
    assert service['spec']['type'] == 'ClusterIP'
    assert all('nodePort' not in port for port in service['spec']['ports'])
    apply_private(init_secret)
    # Old completed bootstrap jobs retain literal credentials in their Pod specs
    # and logs. Remove only the completed job; do not rerun it over live databases.
    old_job = kube.get('job/couchdb-init')
    if old_job:
        assert old_job.get('status', {}).get('succeeded') == 1
        kube.kubectl('delete', 'job', 'couchdb-init', '--wait=true')
    name = 'cdb-security-bootstrap'
    for kind in ('pod', 'job', 'svc', 'secret'):
        assert not kube.get(kind + '/' + name)
    labels = {'ops-lab-validation': LABEL}
    metadata = {'name': name, 'namespace': 'openserverless', 'labels': labels}
    fresh = {'couchdb.host': name, 'couchdb.port': '5984'}
    for role in ('admin', 'controller', 'invoker'):
        fresh['couchdb.' + role + '.user'] = 'synthetic_' + role
        fresh['couchdb.' + role + '.password'] = secrets.token_hex(24)
    fresh['openwhisk.namespaces.whisk-system'] = str(uuid.uuid4()) + ':' + secrets.token_hex(32)
    secret = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': metadata,
              'stringData': {'config': json.dumps(fresh), 'username': fresh['couchdb.admin.user'], 'password': fresh['couchdb.admin.password']}}
    image = kube.get('sts/couchdb')['spec']['template']['spec']['containers'][0]['image']
    pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': metadata,
           'spec': {'restartPolicy': 'Never', 'containers': [{'name': 'couchdb', 'image': image,
                    'env': [{'name': key, 'valueFrom': {'secretKeyRef': {'name': name, 'key': value}}}
                            for key, value in [('COUCHDB_USER', 'username'), ('COUCHDB_PASSWORD', 'password')]],
                    'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'}, 'limits': {'memory': '1Gi'}},
                    'volumeMounts': [{'name': 'data', 'mountPath': '/opt/couchdb/data'}]}],
                    'volumes': [{'name': 'data', 'emptyDir': {}}]}}
    pod['metadata'] = copy.deepcopy(metadata)
    pod['metadata']['labels']['cdb-bootstrap-server'] = name
    svc = {'apiVersion': 'v1', 'kind': 'Service', 'metadata': metadata,
           'spec': {'selector': {'cdb-bootstrap-server': name}, 'ports': [{'port': 5984, 'targetPort': 5984}]}}
    job = copy.deepcopy(original_job)
    job['metadata'] = metadata
    job['spec']['backoffLimit'] = 0
    job['spec']['template']['metadata'] = {'labels': labels}
    template = job['spec']['template']['spec']
    template['initContainers'][0]['command'][2] = 'until wget -qO - http://' + name + ':5984; do sleep 2; done'
    template['containers'][0]['env'][0]['valueFrom']['secretKeyRef']['name'] = name
    # Kubernetes injects COUCHDB_SERVICE_HOST for the real service; the client
    # intentionally gives environment settings precedence over JSON configuration.
    template['containers'][0]['env'].extend([
        {'name': 'COUCHDB_SERVICE_HOST', 'value': name},
        {'name': 'COUCHDB_SERVICE_PORT', 'value': '5984'}])
    try:
        apply_private({'apiVersion': 'v1', 'kind': 'List', 'items': [secret, pod, svc, job]})
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            status = kube.get('job/' + name).get('status', {})
            if status.get('succeeded') == 1: break
            if any(item.get('type') == 'Failed' and item.get('status') == 'True' for item in status.get('conditions', [])):
                raise RuntimeError('Synthetic bootstrap job failed')
            time.sleep(2)
        else: raise RuntimeError('Synthetic bootstrap timed out')
        logs = kube.kubectl('logs', 'job/' + name, '-c', 'init-couchdb')
        assert all(value not in logs for key, value in fresh.items() if 'password' in key or key.startswith('openwhisk.namespaces.'))
        cfg.configure(fresh)
        with patch.dict('os.environ', {'COUCHDB_SERVICE_HOST': name, 'COUCHDB_SERVICE_PORT': '5984'}):
            db = CouchDB()
        assert db.get_doc('subjects', 'whisk-system')['subject'] == 'whisk-system'
        assert db.check_db('users_metadata')
        assert db.db_session.get(db.db_base + 'users_metadata/_security').json()['members']['names'] == [fresh['couchdb.admin.user']]
        assert requests.get(db.db_url + '/_all_dbs', auth=('whisk_admin', 'some_passw0rd'), timeout=10).status_code == 401
        print(json.dumps({'bootstrap_complete': True, 'minimal_secret': True, 'logs_without_credentials': True,
                          'default_password_rejected': True, 'couchdb_service_private': True}))
    finally:
        kube.kubectl('delete', 'pod,job,svc,secret', name, '--ignore-not-found', '--wait=true', '--timeout=120s')


def run(mode):
    logging.disable(logging.CRITICAL)
    nodes = json.loads(kube.kubectl('get', 'nodes', '-o', 'json', namespace=''))
    assert all(n['metadata']['name'] in ('ops-baseline-clean-20261019', 'ops-advanced-lab') for n in nodes['items'])
    cfg.configure(kube.get('wsk/controller')['spec'])
    cfg.detect()
    if mode == 'bootstrap':
        bootstrap(); return
    if mode == 'scheduler':
        scheduler(); return
    client = CouchDB()
    resource = kube.get('wsku/' + TENANT)
    if mode == 'setup':
        if resource:
            assert resource['metadata'].get('labels', {}).get('ops-lab-validation') == LABEL
        else:
            password = secrets.token_hex(20)
            spec = {'namespace': TENANT, 'email': TENANT + '@example.test',
                    'password': password, 'auth': str(uuid.uuid4()) + ':' + secrets.token_hex(32),
                    'redis': {'enabled': True, 'prefix': TENANT, 'password': secrets.token_hex(20)},
                    'postgres': {'enabled': True, 'database': TENANT, 'password': secrets.token_hex(20)},
                    'mongodb': {'enabled': True, 'database': TENANT, 'password': secrets.token_hex(20)},
                    'object-storage': {'password': secrets.token_hex(20), 'quota': '100',
                                       'data': {'enabled': True, 'bucket': TENANT + '-data'},
                                       'route': {'enabled': False, 'bucket': TENANT + '-web'}},
                    'milvus': {'enabled': False, 'database': TENANT, 'password': secrets.token_hex(20)}}
            apply_private({'apiVersion': 'openserverless.org/v1', 'kind': 'WhiskUser',
                           'metadata': {'name': TENANT, 'namespace': 'openserverless',
                                        'labels': {'ops-lab-validation': LABEL}}, 'spec': spec})
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            resource = kube.get('wsku/' + TENANT)
            if any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in resource.get('status', {}).get('conditions', [])):
                print(json.dumps({'fixture': TENANT, 'ready': True})); return
            time.sleep(2)
        raise RuntimeError('Synthetic tenant did not become ready')
    assert resource and resource['metadata'].get('labels', {}).get('ops-lab-validation') == LABEL
    if mode == 'cleanup':
        kube.kubectl('delete', 'wsku', TENANT, '--wait=true', '--timeout=180s')
        assert client.get_doc('subjects', TENANT) is None
        assert client.get_doc('users_metadata', TENANT) is None
        print(json.dumps({'fixture_deleted': True})); return
    secret = resource['spec']
    auth = secret['auth']
    bearer = 'Basic ' + base64.b64encode(auth.encode()).decode()
    session = requests.Session(); session.trust_env = False
    prefix = 'http://controller:3233/api/v1/web/whisk-system/nuv/'
    api = 'http://openserverless-system-api:5000/system/api/v1/'
    checks = []
    env = {entry['key']: entry['value'] for entry in client.get_doc('users_metadata', TENANT)['env']}
    storage = Minio(env['S3_HOST'] + ':' + str(env['S3_PORT']),
                    access_key=env['S3_ACCESS_KEY'], secret_key=env['S3_SECRET_KEY'], secure=False)
    def object_exists(name):
        try:
            storage.stat_object(TENANT + '-data', name)
            return True
        except S3Error as error:
            if error.code in ('NoSuchKey', 'NoSuchObject'): return False
            raise
    def record(name, passed, details=None):
        checks.append({'check': name, 'passed': bool(passed), 'details': details})
    def request(method, url, **kwargs):
        return session.request(method, url, timeout=90, **kwargs)
    def block(value):
        subject = client.get_doc('subjects', TENANT)
        subject['blocked'] = value
        assert client.update_doc('subjects', subject)
    original = copy.deepcopy(client.get_doc('subjects', TENANT))
    try:
        for phase, blocked in [('active', False), ('blocked', True), ('unblocked', False)]:
            block(blocked)
            results = {}
            results['password_login'] = request('POST', prefix + 'login', json={'login': TENANT, 'password': secret['password']})
            # The supported IDE profile-sync request supplies an env object.
            # An empty object reads the profile without changing its values.
            results['secrets_read'] = request('POST', prefix + 'secrets', headers={'Authorization': auth}, json={'login': TENANT, 'env': {}})
            metadata_before = client.get_doc('users_metadata', TENANT)
            results['secrets_write'] = request('POST', prefix + 'secrets', headers={'Authorization': auth},
                                                json={'login': TENANT, 'env': {'CDB_SYNTHETIC_PHASE': phase}})
            if blocked and mode == 'after':
                record('blocked_secret_write_preserves_metadata', metadata_before == client.get_doc('users_metadata', TENANT))
            headers = {'x-impersonate-auth': bearer}
            results['redis'] = request('POST', prefix + 'redis', headers=headers, json={'command': 'SET ' + TENANT + ':check value'})
            results['postgres'] = request('POST', prefix + 'psql', headers=headers, json={'command': 'SELECT 1 AS probe'})
            results['mongodb'] = request('POST', prefix + 'ferretdb', headers=headers, json={'ping': 1})
            results['s3'] = request('POST', prefix + 's3', headers=headers, json={'command': 'ls', 'args': [TENANT + '-data']})
            object_name = mode + '-' + phase + '-probe.txt'
            if object_exists(object_name): storage.remove_object(TENANT + '-data', object_name)
            upload = request('PUT', prefix + 'devel_upload/' + TENANT + '-data/' + object_name,
                                        headers={**headers, 'Content-Type': 'application/octet-stream'}, data=b'synthetic-couchdb-check')
            expected_success = not blocked or mode == 'before'
            record(phase + '_upload_write_boundary', object_exists(object_name) == expected_success,
                   {'http': upload.status_code, 'stored': object_exists(object_name)})
            # The existing MinIO wrapper stores the object but then returns 400
            # because ObjectWriteResult has no _object_name. Verify the write,
            # rather than classifying this pre-existing response bug as a denial.
            if expected_success:
                results['download'] = request('GET', prefix + 'devel_download/' + TENANT + '-data/' + object_name, headers=headers)
                record(phase + '_download_content', results['download'].content == b'synthetic-couchdb-check')
            else:
                results['download'] = request('GET', prefix + 'devel_download/' + TENANT + '-data/before-active-probe.txt', headers=headers)
            results['admin_password_login'] = request('POST', api + 'auth', json={'login': TENANT, 'password': secret['password']})
            build = request('POST', api + 'build/start', headers={'Authorization': bearer},
                            json={'source': 'runtime:python', 'kind': 'python', 'target': TENANT + ':invalid;target'})
            for name, response in results.items():
                expected_success = not blocked or mode == 'before'
                record(phase + '_' + name, (200 <= response.status_code < 300) == expected_success,
                       {'http': response.status_code})
            expected_build = 401 if blocked and mode == 'after' else 400
            record(phase + '_builder_denies_revoked_before_validation', build.status_code == expected_build,
                   {'http': build.status_code})
    finally:
        current = client.get_doc('subjects', TENANT)
        original['_rev'] = current['_rev']
        assert client.update_doc('subjects', original)
    # Use only synthetic documents to exercise the privileged metadata serializer.
    victim = 'cdb-victim-20261020'; own = 'cdb-input-20261020'
    assert client.get_doc('users_metadata', victim) is None
    assert client.get_doc('users_metadata', own) is None
    try:
        assert client.update_doc('users_metadata', {'_id': victim, 'sentinel': 'unchanged'})
        injection = 'synthetic", "_id": "' + victim + '", "extra": "synthetic'
        couchdb.update_templated_doc(client, 'users_metadata', 'user_metadata.json', {
            'login': own, 'email': injection, 'password': 'synthetic-hash', 'metadata': [], 'quota': []})
        intact = client.get_doc('users_metadata', victim).get('sentinel') == 'unchanged'
        record('metadata_injection_cannot_change_other_document' if mode == 'after' else 'original_metadata_injection_reproduced',
               intact if mode == 'after' else not intact)
        if mode == 'after':
            record('structural_characters_round_trip', client.get_doc('users_metadata', own)['email'] == injection)
    finally:
        client.delete_doc('users_metadata', victim); client.delete_doc('users_metadata', own)
    output = {'mode': mode, 'checks': checks, 'passed': sum(c['passed'] for c in checks),
              'failed': [c['check'] for c in checks if not c['passed']]}
    print(json.dumps(output))
    if output['failed']: raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lab', action='store_true', required=True)
    parser.add_argument('mode', choices=('setup', 'before', 'after', 'cleanup', 'bootstrap', 'scheduler'))
    run(parser.parse_args().mode)
