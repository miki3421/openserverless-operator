# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Disposable synthetic qualification, run only on the existing ADV lab VM.

No existing namespace, database or credential is used. The candidate image
must already be imported; all pods use imagePullPolicy Never. Evidence is
written outside the repository by the caller. Namespace deletion is UID-bound.
"""
import copy
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
import uuid

IMAGE = 'localhost/ops-advanced-operator:20261106-retention-r1'
COUCH = 'docker.io/apache/couchdb:3.5.2@sha256:c703989c0a370a1a6b179785bcb5b2b5501347bb0219d02d597d0a9107eb8d28'

FIXTURE = r'''
import json,os,sys,time,requests
s=requests.Session();s.trust_env=False;s.auth=('synthetic-admin',os.environ['PASSWORD'])
base='http://couchdb:5984';db='openserverless_activations';day=86400000
def req(method,path,**kw):
 r=s.request(method,base+path,timeout=30,**kw);r.raise_for_status();return r.json()
if sys.argv[1]=='seed':
 req('POST','/_cluster_setup',json={'action':'enable_single_node','bind_address':'0.0.0.0','port':5984})
 for name in [db,'openserverless_whisks','openserverless_subjects']:req('PUT','/'+name)
 req('PUT','/_users/org.couchdb.user:synthetic-controller',json={'name':'synthetic-controller','type':'user','roles':[],'password':os.environ['PASSWORD']})
 req('PUT','/'+db+'/_security',json={'admins':{'names':['synthetic-admin'],'roles':[]},'members':{'names':['synthetic-controller'],'roles':[]}})
 req('PUT','/'+db+'/_design/activations',json={'views':{'byDate':{'map':'function(doc){if(doc.activationId!==undefined){emit(doc.start,[doc._id,doc._rev]);}}'}}})
 now=int(time.time()*1000)
 docs=[{'_id':t+'/old-'+str(i),'activationId':'old-'+str(i),'start':now-31*day,'logs':['synthetic'],'response':{'result':{'fixture':True}}} for t in ['alpha','beta'] for i in range(205)]
 docs += [{'_id':'alpha/recent','activationId':'recent','start':now-29*day,'logs':['keep']},{'_id':'beta/recent','activationId':'recent','start':now-29*day,'logs':['keep']},{'_id':'other','start':1,'payload':'keep'}]
 results=req('POST','/'+db+'/_bulk_docs',json={'docs':docs})
 if any(x.get('error') for x in results):raise RuntimeError('Fixture bulk insertion failed')
 for name in ['openserverless_whisks','openserverless_subjects']:req('PUT','/'+name+'/sentinel',json={'activationId':'sentinel','start':1})
 print(json.dumps({'seededExpired':410,'retainedFixtures':3}))
else:
 rows=req('GET','/'+db+'/_all_docs')['rows']; ids={r['id'] for r in rows}
 if ids != {'_design/activations','alpha/recent','beta/recent','other'}:raise RuntimeError('Unexpected retained documents')
 for tenant in ['alpha','beta']:
  if req('GET','/'+db+'/'+tenant+'%2Frecent')['logs']!=['keep']:raise RuntimeError('Recent logs changed')
 for name in ['openserverless_whisks','openserverless_subjects']:req('GET','/'+name+'/sentinel')
 print(json.dumps({'expiredDeleted':410,'recentPreserved':True,'otherDatabasesPreserved':True}))
'''


def main():
    compile(FIXTURE, '<synthetic-fixture>', 'exec')
    if subprocess.check_output(['hostname'], text=True).strip() != 'ops-advanced-lab':
        raise SystemExit('This fixture is restricted to the authorized ADV VM')
    run = uuid.uuid4().hex[:10]
    namespace = 'ops-retention-qual-' + run
    evidence = Path.home() / 'ops-retention-evidence' / run
    evidence.mkdir(parents=True, mode=0o700)
    os.chmod(evidence, 0o700)
    uid = None

    def k(*args, obj=None):
        command = ['sudo', 'k3s', 'kubectl', '-n', namespace, *args]
        result = subprocess.run(command, input=json.dumps(obj) if obj is not None else None,
                                text=True, capture_output=True, timeout=90)
        if result.returncode:
            raise RuntimeError('Kubernetes fixture operation failed: ' + ' '.join(args[:3]))
        return result.stdout

    def apply(*items):
        k('apply', '-f', '-', obj={'apiVersion': 'v1', 'kind': 'List', 'items': list(items)})

    def get(kind, name):
        return json.loads(k('get', kind, name, '-o', 'json'))

    def wait_pod(name):
        for _ in range(90):
            pod = get('pod', name)
            if pod['status'].get('phase') == 'Succeeded': return
            if pod['status'].get('phase') == 'Failed': raise RuntimeError('Fixture pod failed: ' + name)
            time.sleep(2)
        raise RuntimeError('Fixture pod timed out: ' + name)

    def helper(mode):
        name = 'fixture-' + mode
        apply({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name}, 'spec': {
            'automountServiceAccountToken': False, 'restartPolicy': 'Never', 'activeDeadlineSeconds': 180,
            'securityContext': {'runAsNonRoot': True, 'runAsUser': 1001, 'runAsGroup': 1001},
            'containers': [{'name': 'fixture', 'image': IMAGE, 'imagePullPolicy': 'Never',
                'command': ['/home/openserverless/.venv/bin/python', '-B', '-c', FIXTURE, mode],
                'env': [{'name': 'PASSWORD', 'valueFrom': {'secretKeyRef': {'name': 'fixture-auth', 'key': 'password'}}}],
                'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
                'resources': {'requests': {'cpu': '50m', 'memory': '64Mi'}, 'limits': {'cpu': '500m', 'memory': '256Mi'}},
            }],
        }})
        wait_pod(name)
        (evidence / (mode + '.log')).write_text(k('logs', name))

    try:
        # create, never apply/adopt an existing namespace
        k('create', '-f', '-', obj={'apiVersion': 'v1', 'kind': 'Namespace',
                                  'metadata': {'name': namespace, 'labels': {'ops-retention-test': run}}})
        uid = get('namespace', namespace)['metadata']['uid']
        password = secrets.token_hex(24)
        apply({'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'fixture-auth'}, 'stringData': {'password': password}},
              {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy', 'metadata': {'name': 'isolated-fixture'}, 'spec': {
                  'podSelector': {}, 'policyTypes': ['Ingress', 'Egress'],
                  'ingress': [{'from': [{'podSelector': {}}], 'ports': [{'port': 5984}]}],
                  'egress': [{'to': [{'podSelector': {}}], 'ports': [{'port': 5984}]}, {'ports': [{'port': 53, 'protocol': 'UDP'}, {'port': 53, 'protocol': 'TCP'}]}],
              }},
              {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': 'couchdb'}, 'spec': {'selector': {'fixture': 'couchdb'}, 'ports': [{'port': 5984}]}},
              {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': 'couchdb', 'labels': {'fixture': 'couchdb'}}, 'spec': {
                  'automountServiceAccountToken': False, 'restartPolicy': 'Never',
                  'securityContext': {'fsGroup': 5984},
                  'volumes': [{'name': 'data', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '128Mi'}}],
                  'containers': [{'name': 'couchdb', 'image': COUCH, 'imagePullPolicy': 'Never',
                     'env': [{'name': 'COUCHDB_USER', 'value': 'synthetic-admin'}, {'name': 'COUCHDB_PASSWORD', 'valueFrom': {'secretKeyRef': {'name': 'fixture-auth', 'key': 'password'}}}, {'name': 'ERL_FLAGS', 'value': '+S 2:2'}],
                     'ports': [{'containerPort': 5984}], 'volumeMounts': [{'name': 'data', 'mountPath': '/opt/couchdb/data'}],
                     'readinessProbe': {'httpGet': {'path': '/', 'port': 5984}, 'periodSeconds': 2},
                     'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'}, 'limits': {'cpu': '1', 'memory': '512Mi'}},
                  }],
              }})
        k('wait', '--for=condition=Ready', 'pod/couchdb', '--timeout=80s')
        helper('seed')
        # Render using the real module in the exact imported candidate image.
        code = "import json;from openserverless import config;from openserverless.activation_retention import resources;config.configure(json.loads(input()));print(json.dumps(resources()))"
        # The seeded helper has terminated, so use a separate short rendering pod.
        render_config = {'operator': {'image': IMAGE.rsplit(':', 1)[0], 'tag': IMAGE.rsplit(':', 1)[1]},
                         'couchdb': {'activation-retention-days': 30, 'controller': {'user': 'synthetic-controller', 'password': password}}}
        # Only synthetic credentials are present; keep their rendered output private.
        render = copy.deepcopy(get('pod', 'fixture-seed'))
        render = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': 'fixture-render'}, 'spec': render['spec']}
        for field in ['nodeName', 'serviceAccount', 'serviceAccountName']: render['spec'].pop(field, None)
        container = render['spec']['containers'][0]
        container['command'] = ['/home/openserverless/.venv/bin/python', '-B', '-c', code.replace('input()', 'os.environ["CONFIG"]')]
        container['command'][-1] = 'import os;' + container['command'][-1]
        container['env'] = [{'name': 'CONFIG', 'value': json.dumps(render_config)},
                            {'name': 'OPERATOR_IMAGE', 'value': IMAGE.rsplit(':', 1)[0]},
                            {'name': 'OPERATOR_TAG', 'value': IMAGE.rsplit(':', 1)[1]}]
        apply(render); wait_pod('fixture-render')
        items = json.loads(k('logs', 'fixture-render'))
        for item in items: item['metadata']['namespace'] = namespace
        cron = items[1]; cron['spec']['schedule'] = '* * * * *'
        cron['spec']['jobTemplate']['spec']['template']['spec']['containers'][0]['imagePullPolicy'] = 'Never'
        apply(*items)
        cron_uid = get('cronjob', 'activation-cleanup')['metadata']['uid']
        scheduled = None
        for _ in range(100):
            jobs = json.loads(k('get', 'jobs', '-o', 'json'))['items']
            owned = [j for j in jobs if any(r.get('uid') == cron_uid for r in j['metadata'].get('ownerReferences', []))]
            if any(j.get('status', {}).get('failed') for j in owned): raise RuntimeError('Scheduled cleanup failed')
            successful = [j for j in owned if j.get('status', {}).get('succeeded') == 1]
            if successful: scheduled = successful[0]; break
            time.sleep(2)
        if scheduled is None: raise RuntimeError('No completed scheduled cleanup')
        k('patch', 'cronjob', 'activation-cleanup', '--type=merge', '-p', '{"spec":{"suspend":true}}')
        log = k('logs', 'job/' + scheduled['metadata']['name'])
        (evidence / 'scheduled.log').write_text(log)
        result = json.loads(log.strip().splitlines()[-1])
        if result.get('deleted') != 410 or result.get('status') != 'complete': raise RuntimeError('Wrong cleanup result')
        helper('verify')
        # Repeat exactly the job template, not an action invocation.
        repeat = {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'name': 'cleanup-repeat'}, 'spec': cron['spec']['jobTemplate']['spec']}
        apply(repeat)
        k('wait', '--for=condition=complete', 'job/cleanup-repeat', '--timeout=80s')
        repeat_log = k('logs', 'job/cleanup-repeat'); (evidence / 'repeat.log').write_text(repeat_log)
        if json.loads(repeat_log.strip().splitlines()[-1]).get('deleted') != 0: raise RuntimeError('Repeat not empty')
        pods = json.loads(k('get', 'pods', '-o', 'json'))['items']
        result = {'status': 'PASS', 'namespace': namespace, 'namespaceUID': uid, 'cronjobUID': cron_uid,
                  'automaticScheduledJob': scheduled['metadata']['name'], 'deleted': 410, 'repeatDeleted': 0,
                  'imageIDs': sorted({c['imageID'] for p in pods for c in p.get('status', {}).get('containerStatuses', [])}),
                  'fixtureOnly': True, 'originalPeriod': '17 3 * * * UTC', 'testPeriod': '* * * * * UTC'}
        (evidence / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result), flush=True)
    except Exception:
        if uid:
            (evidence / 'pods.json').write_text(k('get', 'pods', '-o', 'json'))
            for pod in json.loads(k('get', 'pods', '-o', 'json'))['items']:
                try: (evidence / (pod['metadata']['name'] + '.log')).write_text(k('logs', pod['metadata']['name']))
                except Exception: pass
        raise
    finally:
        if uid:
            actual = get('namespace', namespace)
            if actual['metadata']['uid'] != uid or actual['metadata']['labels'].get('ops-retention-test') != run:
                raise RuntimeError('Refusing namespace cleanup: identity changed')
            k('delete', 'namespace', namespace, '--wait=false')
            print(json.dumps({'cleanupRequested': namespace, 'evidence': str(evidence)}), flush=True)


if __name__ == '__main__': main()
