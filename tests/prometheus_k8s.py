# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Fresh-storage qualification, only on an explicitly identified isolated node.

Requires an absent openserverless namespace and preloaded images. Creates owned
monitoring resources, tests them and removes them. Does not migrate old TSDB data.
"""
import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import socket
import re
import subprocess
import sys
import tempfile
import tarfile
import time
import urllib.parse
import urllib.request
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from openserverless.prometheus_profile import PROFILE, preflight


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubectl', required=True)
    parser.add_argument('--kubeconfig', required=True)
    parser.add_argument('--node-uid', required=True)
    parser.add_argument('--ui-archive', help='Optional exact official 3.16.0 UI archive; verifies served JavaScript bytes')
    args = parser.parse_args()
    ns = 'openserverless'
    owned = []
    checks = []
    command = [args.kubectl, '--kubeconfig', args.kubeconfig, '--request-timeout=30s']
    ui_members = {}
    if args.ui_archive:
        raw = Path(args.ui_archive).read_bytes()
        if hashlib.sha256(raw).hexdigest() != '000a45231f6dd22a5703218aad3ee11da125c8a44d1824a27670416c07531001':
            raise RuntimeError('Unexpected UI archive')
        with tarfile.open(args.ui_archive) as archive:
            ui_members = {m.name: hashlib.sha256(archive.extractfile(m).read()).hexdigest()
                          for m in archive if m.isfile()}

    def k(*argv, obj=None):
        run = subprocess.run(command + list(argv), input=json.dumps(obj) if obj else None,
                             text=True, capture_output=True, timeout=240)
        if run.returncode:
            raise RuntimeError('kubectl ' + argv[0] + ' failed: ' + run.stderr[:1000])
        return run.stdout

    def get(kind, name=None, namespace=None):
        return json.loads(k('get', kind, *([name] if name else []),
                            *(['-n', namespace] if namespace else []), '-o', 'json'))

    def identity():
        nodes = get('nodes')['items']
        if len(nodes) != 1 or nodes[0]['metadata']['uid'] != args.node_uid:
            raise RuntimeError('Unexpected isolated node identity')

    def check(ok, name):
        if not ok:
            raise RuntimeError(name)
        checks.append(name)
        print(json.dumps({'check': name, 'status': 'PASS'}), flush=True)

    def create(obj):
        identity()
        k('create', '-f', '-', obj=obj)
        meta = obj['metadata']
        current = get(obj['kind'], meta['name'], meta.get('namespace'))
        owned.append((obj['kind'], meta['name'], meta.get('namespace'), current['metadata']['uid']))
        return current

    def pod():
        items = json.loads(k('get', 'pods', '-n', ns, '-l', 'app.kubernetes.io/name=prometheus', '-o', 'json'))['items']
        return next(p for p in items if p['metadata']['labels'].get('app.kubernetes.io/component') == 'server')

    def rollout():
        k('rollout', 'status', 'deployment/' + PROFILE['deployment'], '-n', ns, '--timeout=180s')

    @contextlib.contextmanager
    def forward():
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        with tempfile.TemporaryFile() as log:
            proc = subprocess.Popen(command + ['-n', ns, 'port-forward', '--address=127.0.0.1',
                                    'deployment/' + PROFILE['deployment'], str(port) + ':9999'], stdout=log, stderr=log)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def request(path):
                with opener.open('http://127.0.0.1:' + str(port) + path, timeout=10) as response:
                    return response.read()
            try:
                await_result(lambda: request('/-/ready') == b'Prometheus Server is Ready.\n', 30)
                yield request
            finally:
                proc.terminate()
                proc.wait(timeout=10)

    def await_result(fn, timeout=120):
        deadline = time.monotonic() + timeout
        while True:
            try:
                result = fn()
                if result:
                    return result
            except (OSError, ValueError, KeyError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('Timed out waiting for ' + fn.__name__)
            time.sleep(2)

    def query(request, expression, at=None):
        params = {'query': expression}
        if at is not None:
            params['time'] = str(at)
        data = json.loads(request('/api/v1/query?' + urllib.parse.urlencode(params)))
        if data['status'] != 'success':
            raise RuntimeError('PromQL query failed')
        return data['data']['result']

    identity()
    if k('get', 'namespace', ns, '--ignore-not-found', '-o', 'name').strip():
        raise RuntimeError('This test requires an absent openserverless namespace')
    try:
        create({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': ns, 'labels': {'ops.synthetic': 'prometheus-qualification'}}})
        preflight(lambda *a: k(*a, '-n', ns))
        check(True, 'fresh_profile_preflight')
        for path in ['deploy/openserverless-permissions/openserverless-monitoring-00-sa.yaml',
                     'deploy/openserverless-permissions/openserverless-monitoring-01-roles.yaml',
                     'deploy/monitoring/monitoring-00-networkpolicy.yaml']:
            for obj in yaml.safe_load_all((ROOT / path).read_text()):
                create(obj)
        create({'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim', 'metadata': {'name': PROFILE['claim'], 'namespace': ns},
                'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '1Gi'}}}})
        cm = yaml.safe_load((ROOT / 'deploy/monitoring/prometheus-02-cm.yaml').read_text())
        # Keep production config/rules intact for promtool before adding fixtures.
        create(cm)
        for name in ['kubestate-03-dep.yaml', 'kubestate-04-svc.yaml', 'prometheus-04-svc.yaml', 'prometheus-03-dep.yaml']:
            obj = yaml.safe_load((ROOT / 'deploy/monitoring' / name).read_text())
            if obj['kind'] == 'Deployment':
                for container in obj['spec']['template']['spec']['containers']:
                    container['imagePullPolicy'] = 'Never'
                obj['spec']['template']['spec']['securityContext'] = {'fsGroup': 65534, 'runAsUser': 65534, 'runAsNonRoot': True}
            create(obj)
        rollout()
        preflight(lambda *a: k(*a, '-n', ns))
        check(True, 'same_profile_repeat_preflight')
        current = pod()
        output = k('exec', '-n', ns, current['metadata']['name'], '-c', 'prometheus-server', '--',
                   '/bin/promtool', 'check', 'config', '/etc/config/prometheus.yml')
        print(output, flush=True)
        check('SUCCESS' in output, 'production_configuration_and_rules')
        pvc = get('pvc', PROFILE['claim'], ns)
        volume = pvc['spec']['volumeName']
        pv_uid = get('pv', volume)['metadata']['uid']
        with forward() as request:
            build = json.loads(request('/api/v1/status/buildinfo'))['data']
            check(build['version'] == PROFILE['expected_version'], 'running_version')
            html = request('/query')
            scripts = re.findall(rb'<script[^>]+src="([^"]+)"', html)
            check(b'<html' in html and bool(scripts), 'web_interface_html')
            for uri in scripts:
                path = urllib.parse.urlsplit(uri.decode())
                if path.netloc or path.scheme:
                    raise RuntimeError('Unexpected external UI script')
                asset_path = '/' + path.path.lstrip('./')
                body = request(asset_path)
                check(len(body) > 100, 'web_javascript_served_' + asset_path)
                if ui_members:
                    expected = ui_members['static/mantine-ui' + asset_path]
                    check(hashlib.sha256(body).hexdigest() == expected, 'official_ui_bytes_preserved_' + asset_path)
            await_result(lambda: len([r for r in query(request, 'up') if r['metric']['job'] in
                         ('prometheus', 'kube-state-metrics', 'kubelet', 'cadvisor') and r['value'][1] == '1']) == 4)
            check(True, 'self_ksm_kubelet_cadvisor_scrapes')
            check(bool(query(request, 'kube_node_info')), 'kubernetes_metric_query')
            check(bool(json.loads(request('/api/v1/rules'))['data']['groups']), 'production_rules_loaded')
            # The isolated VM has no controller/Kafka/Alertmanager. Report, do not hide, these missing targets.
            targets = json.loads(request('/api/v1/targets'))['data']['activeTargets']
            print(json.dumps({'isolated_targets': [{'job': t['labels']['job'], 'health': t['health'],
                                                    'error': t['lastError']} for t in targets]}), flush=True)
            config = yaml.safe_load(cm['data']['prometheus.yml'])
            config['global'].update({'scrape_interval': '5s', 'scrape_timeout': '4s', 'evaluation_interval': '5s',
                                     'external_labels': {'qualification_generation': '2'}})
            config['rule_files'].append('/etc/config/qualification.yml')
            cm['data']['qualification.yml'] = yaml.safe_dump({'groups': [{'name': 'qualification', 'rules': [
                {'record': 'ops_qualification_record', 'expr': 'vector(7)'},
                {'alert': 'OpsQualificationAlert', 'expr': 'vector(1)', 'labels': {'synthetic': 'true'}}]}]})
            cm['data']['prometheus.yml'] = yaml.safe_dump(config)
            identity()
            k('apply', '-f', '-', obj=cm)
            await_result(lambda: 'qualification_generation: "2"' in json.loads(request('/api/v1/status/config'))['data']['yaml'])
            check(True, 'configmap_reloader_observed_change')
            await_result(lambda: bool(query(request, 'ops_qualification_record == 7')))
            check(True, 'recording_rule_evaluated')
            await_result(lambda: bool(query(request, 'ALERTS{alertname="OpsQualificationAlert",alertstate="firing"}')))
            check(True, 'synthetic_alert_fires')
            good = cm['data']['prometheus.yml']
            cm['data']['prometheus.yml'] = 'scrape_configs: invalid-scalar\n'
            identity()
            k('apply', '-f', '-', obj=cm)
            await_result(lambda: bool(query(request, 'prometheus_config_last_reload_successful == 0')))
            check('qualification_generation: "2"' in json.loads(request('/api/v1/status/config'))['data']['yaml'], 'invalid_reload_preserves_valid_config')
            cm['data']['prometheus.yml'] = good
            identity()
            k('apply', '-f', '-', obj=cm)
            await_result(lambda: bool(query(request, 'prometheus_config_last_reload_successful == 1')))
            check(True, 'valid_config_recovers_after_failed_reload')
            sample_time = time.time() - 1
            before = query(request, 'ops_qualification_record', sample_time)
            check(bool(before), 'persistent_sample_created')

        identity()
        old_uid = current['metadata']['uid']
        # Honor the production Recreate strategy; deleting a pod directly can
        # start its replacement while the old process still owns the TSDB lock.
        k('rollout', 'restart', 'deployment/' + PROFILE['deployment'], '-n', ns)
        rollout()
        check(pod()['metadata']['uid'] != old_uid, 'pod_replaced')
        restarted = pod()
        for container in restarted['status']['containerStatuses']:
            if container['restartCount']:
                print(k('logs', restarted['metadata']['name'], '-n', ns, '-c', container['name'], '--previous'), flush=True)
        check(all(c['restartCount'] == 0 for c in restarted['status']['containerStatuses']), 'recreate_starts_without_container_retries')
        check(get('pvc', PROFILE['claim'], ns)['metadata']['uid'] == pvc['metadata']['uid']
              and get('pv', volume)['metadata']['uid'] == pv_uid, 'pvc_pv_identity_preserved')
        with forward() as request:
            check(query(request, 'ops_qualification_record', sample_time) == before, 'historical_sample_survives_restart')
        prometheus_ip = pod()['status']['podIP']
        for action in [False, True]:
            name = 'probe-action' if action else 'probe-monitor'
            labels = {'app.kubernetes.io/name': 'prometheus', 'app.kubernetes.io/instance': 'openserverless-prometheus'}
            if action:
                labels['user-action-pod'] = 'true'
            create({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'namespace': ns, 'labels': labels},
                    'spec': {'automountServiceAccountToken': False, 'restartPolicy': 'Never', 'containers': [{
                        'name': 'probe', 'image': PROFILE['image'], 'imagePullPolicy': 'Never',
                        'command': ['/bin/sh', '-c', 'sleep 600']} ]}})
            k('wait', '--for=condition=Ready', 'pod/' + name, '-n', ns, '--timeout=60s')
            target = 'http://openserverless-prometheus-kube-state-metrics:8080/metrics'
            result = k('exec', name, '-n', ns, '--', 'sh', '-c',
                       'if wget -T 3 -q -O /dev/null "$1"; then echo ALLOWED; else echo DENIED; fi', 'probe', target).strip()
            check(result == ('DENIED' if action else 'ALLOWED'), name + '_ksm_boundary')
            result = k('exec', name, '-n', ns, '--', 'sh', '-c',
                       'if wget -T 3 -q -O /dev/null "$1"; then echo ALLOWED; else echo DENIED; fi',
                       'probe', 'http://' + prometheus_ip + ':9999/-/ready').strip()
            check(result == 'DENIED', name + '_prometheus_denied')
        print(json.dumps({'status': 'PASS', 'checks': len(checks), 'scope': 'fresh isolated AMD64 component only',
                          'legacy_tsdb_migration': 'NOT_RUN', 'controller_kafka_alert_delivery': 'NOT_RUN'}), flush=True)
    finally:
        # Keep resources if target identity changed. Never clean an unrelated object.
        identity()
        for kind, name, namespace, uid in reversed(owned):
            current = get(kind, name, namespace)
            if current['metadata']['uid'] != uid:
                raise RuntimeError('Owned resource identity changed; cleanup refused')
            k('delete', kind, name, *(['-n', namespace] if namespace else []), '--wait=true', '--timeout=120s')
        print(json.dumps({'cleanup': 'PASS', 'owned_resources_removed': len(owned)}), flush=True)


if __name__ == '__main__':
    main()
