# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Explicit KSM qualification in an isolated single-node component cluster.

Uses the repository's exact production resource selection and RBAC rules.
Creates only labelled synthetic resources; records metrics, never tokens.
"""
import argparse
import json
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
import urllib.request
import uuid
import yaml


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kubectl', required=True)
    p.add_argument('--kubeconfig', required=True)
    p.add_argument('--node-uid', required=True)
    p.add_argument('--image', required=True)
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    ns = 'ops-ksm-test-' + uuid.uuid4().hex[:12]
    owned = []
    checks = []

    def ctl(*words, obj=None, fail=True):
        r = subprocess.run([args.kubectl, '--kubeconfig', args.kubeconfig,
                            '--request-timeout=30s', *words], input=json.dumps(obj) if obj else None,
                           text=True, capture_output=True, timeout=150)
        if fail and r.returncode:
            raise RuntimeError(f'kubectl {words[0]} failed: {r.stderr[:500]}')
        return r

    def target():
        nodes = json.loads(ctl('get', 'nodes', '-o', 'json').stdout)['items']
        if len(nodes) != 1 or nodes[0]['metadata']['uid'] != args.node_uid:
            raise RuntimeError('Isolated target identity mismatch')
        if ctl('get', 'ns', 'openserverless', fail=False).returncode == 0:
            raise RuntimeError('Refuse a platform cluster; use the isolated component lab')

    def create(kind, name, spec=None, api='v1', namespaced=True, extra=None):
        target()
        meta = {'name': name, 'labels': {'ops-qualification': ns}}
        if namespaced: meta['namespace'] = ns
        obj = {'apiVersion': api, 'kind': kind, 'metadata': meta}
        if spec is not None: obj['spec'] = spec
        obj.update(extra or {})
        made = json.loads(ctl('create', '-f', '-', '-o', 'json', obj=obj).stdout)
        if not namespaced: owned.append((kind, name, made['metadata']['uid']))
        return made

    def passed(name):
        checks.append({'check': name, 'status': 'PASS'})
        print(json.dumps(checks[-1]), flush=True)

    def sample(text, metric, labels, value=None):
        for line in text.splitlines():
            if not line.startswith(metric + '{'): continue
            if all(f'{k}="{v}"' in line for k, v in labels.items()):
                if value is None or float(line.rsplit(' ', 1)[1]) == value: return True
        return False

    try:
        target()
        create('Namespace', ns, namespaced=False)
        create('ServiceAccount', 'reader')
        roles = list(yaml.safe_load_all((root / 'deploy/openserverless-permissions/openserverless-monitoring-01-roles.yaml').read_text()))
        role = next(x for x in roles if x['metadata']['name'] == 'openserverless-prometheus-kube-state-metrics-cr')
        create('ClusterRole', ns, api='rbac.authorization.k8s.io/v1', namespaced=False, extra={'rules': role['rules']})
        create('ClusterRoleBinding', ns, api='rbac.authorization.k8s.io/v1', namespaced=False, extra={
            'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole', 'name': ns},
            'subjects': [{'kind': 'ServiceAccount', 'name': 'reader', 'namespace': ns}]})
        for verb, resource in [('list', 'secrets'), ('create', 'pods/exec'), ('get', 'nodes/proxy'), ('create', 'deployments')]:
            result = ctl('auth', 'can-i', verb, resource, '--as=system:serviceaccount:' + ns + ':reader', '-n', ns, fail=False)
            if result.returncode != 1 or result.stdout.strip() != 'no':
                raise RuntimeError('Unexpected KSM permissions')
        passed('existing_rbac_forbids_secrets_exec_proxy_and_mutation')
        template = yaml.safe_load((root / 'deploy/monitoring/kubestate-03-dep.yaml').read_text())
        container = template['spec']['template']['spec']['containers'][0]
        create('Pod', 'reader', {'serviceAccountName': 'reader', 'containers': [{
            'name': 'ksm', 'image': args.image, 'imagePullPolicy': 'Never', 'args': container['args'],
            'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                'capabilities': {'drop': ['ALL']}}}]})
        create('ConfigMap', 'fixture', extra={'data': {'public': 'synthetic'}})
        create('PersistentVolumeClaim', 'pending', {'accessModes': ['ReadWriteOnce'], 'storageClassName': '',
               'resources': {'requests': {'storage': '1Mi'}}})
        # No selector: the Endpoint controller must not race the owned fixture.
        create('Service', 'fixture', {'ports': [{'port': 8080}]})
        create('Endpoints', 'fixture', extra={'subsets': [{'addresses': [{'ip': '192.0.2.10'}], 'ports': [{'port': 8080}]}]})
        create('Deployment', 'idle', {'replicas': 0, 'selector': {'matchLabels': {'fixture': 'idle'}},
               'template': {'metadata': {'labels': {'fixture': 'idle'}}, 'spec': {'containers': [{'name': 'idle', 'image': args.image}]}}}, api='apps/v1')
        create('CronJob', 'suspended', {'schedule': '* * * * *', 'suspend': True,
               'jobTemplate': {'spec': {'template': {'spec': {'restartPolicy': 'Never', 'containers': [{'name': 'idle', 'image': args.image}]}}}}}, api='batch/v1')
        ctl('wait', 'pod/reader', '-n', ns, '--for=condition=Ready', '--timeout=120s')
        passed('reader_ready_with_exact_production_resource_selection')
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); telemetry_port = s.getsockname()[1]
        with tempfile.TemporaryFile() as log:
            forward = subprocess.Popen([args.kubectl, '--kubeconfig', args.kubeconfig, '-n', ns,
                                        'port-forward', '--address=127.0.0.1', 'pod/reader',
                                        f'{port}:8080', f'{telemetry_port}:8081'], stdout=log, stderr=log)
            try:
                def fetch(local, path):
                    with urllib.request.urlopen(f'http://127.0.0.1:{local}/{path}', timeout=5) as response:
                        return response.read().decode()
                deadline = time.monotonic() + 60
                while True:
                    try:
                        metrics = fetch(port, 'metrics'); telemetry = fetch(telemetry_port, 'metrics')
                        if sample(metrics, 'kube_cronjob_spec_suspend', {'namespace': ns, 'cronjob': 'suspended'}, 1): break
                    except (OSError, urllib.error.URLError): pass
                    if time.monotonic() >= deadline: raise RuntimeError('Metric fixture not observed')
                    time.sleep(.5)
                for metric, labels, value in [
                    ('kube_pod_container_status_restarts_total', {'namespace': ns, 'pod': 'reader', 'container': 'ksm'}, 0),
                    ('kube_pod_status_phase', {'namespace': ns, 'pod': 'reader', 'phase': 'Running'}, 1),
                    ('kube_deployment_spec_replicas', {'namespace': ns, 'deployment': 'idle'}, 0),
                    ('kube_persistentvolumeclaim_status_phase', {'namespace': ns, 'persistentvolumeclaim': 'pending', 'phase': 'Pending'}, 1),
                    ('kube_service_info', {'namespace': ns, 'service': 'fixture'}, 1),
                    ('kube_configmap_info', {'namespace': ns, 'configmap': 'fixture'}, 1),
                    ('kube_endpoint_info', {'namespace': ns, 'endpoint': 'fixture'}, 1),
                    ('kube_cronjob_spec_suspend', {'namespace': ns, 'cronjob': 'suspended'}, 1),
                ]:
                    if not sample(metrics, metric, labels, value): raise RuntimeError('Missing metric: ' + metric)
                    passed(metric)
                successful = set()
                listed, watched = set(), set()
                for line in telemetry.splitlines():
                    if not line.startswith(('kube_state_metrics_list_total{', 'kube_state_metrics_watch_total{')): continue
                    if 'result="error"' in line and float(line.rsplit(' ', 1)[1]) != 0:
                        raise RuntimeError('Resource list/watch errors: ' + line)
                    if 'result="success"' in line and float(line.rsplit(' ', 1)[1]) > 0:
                        resource = re.search(r'resource="([^"]+)"', line).group(1)
                        successful.add(resource)
                        (listed if line.startswith('kube_state_metrics_list_total{') else watched).add(resource)
                # New client-go uses WatchList for the initial enumeration; it
                # legitimately bypasses List(). Both protocols remain instrumented.
                print(json.dumps({'successfulResourceLists': sorted(listed),
                                  'successfulResourceWatches': sorted(watched)}), flush=True)
                if len(successful) != 27: raise RuntimeError(f'Expected 27 observed resource types, got {len(successful)}')
                passed('all_27_resources_observed_without_list_watch_errors')
                if fetch(port, 'healthz') != 'OK': raise RuntimeError('Health endpoint mismatch')
                passed('health_endpoint')
            finally:
                forward.terminate(); forward.wait(timeout=10)
    finally:
        for kind, name, uid in reversed(owned):
            target()
            current = json.loads(ctl('get', kind, name, '-o', 'json').stdout)
            if current['metadata']['uid'] != uid: raise RuntimeError('Cleanup UID mismatch')
            ctl('delete', kind, name, '--wait=true', '--timeout=120s')
    print(json.dumps({'image': args.image, 'passed': len(checks), 'ownedResourcesRemoved': True}))


if __name__ == '__main__': main()
