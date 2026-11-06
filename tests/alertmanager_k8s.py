# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Fresh, UID-bound AMD64 qualification with a private synthetic webhook sink.

Requires an absent openserverless namespace and all images preloaded. Uses no
external notification recipients. Removes only its owned namespace afterwards.
"""
import argparse
import contextlib
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from openserverless.alertmanager_profile import PROFILE, preflight
from openserverless.prometheus_profile import PROFILE as PROMETHEUS

SINK = '''from http.server import BaseHTTPRequestHandler, HTTPServer
import json
records=[]
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self):
  self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(records).encode())
 def do_POST(self):
  n=int(self.headers.get('Content-Length','0'))
  if n>65536 or len(records)>100:
   self.send_response(413); self.end_headers(); return
  records.append(json.loads(self.rfile.read(n)))
  self.send_response(200); self.end_headers()
HTTPServer(('0.0.0.0',8080),Handler).serve_forever()
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubectl', required=True)
    parser.add_argument('--kubeconfig', required=True)
    parser.add_argument('--node-uid', required=True)
    parser.add_argument('--sink-image', required=True, help='Preloaded qualified image containing python3')
    parser.add_argument('--ui-archive', required=True)
    args = parser.parse_args()
    if hashlib.sha256(Path(args.ui_archive).read_bytes()).hexdigest() != '3d6b13c5af094724e0bed8f757399e08af44d40d6c6cd87392a1f8ee6ed1e457':
        raise RuntimeError('Unexpected official UI archive')
    ns = 'openserverless'
    owned_uid = None
    checks = []
    command = [args.kubectl, '--kubeconfig', args.kubeconfig, '--request-timeout=30s']

    def k(*argv, obj=None):
        r = subprocess.run(command + list(argv), input=json.dumps(obj) if obj is not None else None,
                           text=True, capture_output=True, timeout=240)
        if r.returncode:
            raise RuntimeError('kubectl ' + argv[0] + ': ' + r.stderr[:1000])
        return r.stdout

    def get(*argv): return json.loads(k('get', *argv, '-o', 'json'))

    def identity():
        nodes = get('nodes')['items']
        if len(nodes) != 1 or nodes[0]['metadata']['uid'] != args.node_uid:
            raise RuntimeError('Unexpected isolated node identity')
        if owned_uid and get('namespace', ns)['metadata']['uid'] != owned_uid:
            raise RuntimeError('Owned namespace identity changed')

    def create(obj):
        identity()
        return json.loads(k('create', '-f', '-', '-o', 'json', obj=obj))

    def check(ok, name):
        if not ok: raise RuntimeError(name)
        checks.append(name)
        print(json.dumps({'check': name, 'status': 'PASS'}), flush=True)

    def wait(fn, timeout=120):
        until = time.monotonic() + timeout
        while True:
            try:
                result = fn()
                if result: return result
            except (OSError, ValueError, KeyError): pass
            if time.monotonic() >= until: raise RuntimeError('Timeout: ' + fn.__name__)
            time.sleep(1)

    @contextlib.contextmanager
    def forward(resource, remote):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
        with tempfile.TemporaryFile() as log:
            proc = subprocess.Popen(command + ['-n', ns, 'port-forward', '--address=127.0.0.1', resource,
                                               str(port) + ':' + str(remote)], stdout=log, stderr=log)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def req(method, path, data=None, headers=None):
                h = {'Content-Type': 'application/json', **(headers or {})}
                body = json.dumps(data).encode() if data is not None else None
                try:
                    r = opener.open(urllib.request.Request('http://127.0.0.1:' + str(port) + path,
                                                          data=body, method=method, headers=h), timeout=10)
                except urllib.error.HTTPError as e: r = e
                with r: return r.status, dict(r.headers), r.read()
            try:
                wait(lambda: req('GET', '/')[0] == 200, 30)
                yield req
            finally:
                proc.terminate(); proc.wait(timeout=10)

    def pod():
        return get('pods', '-n', ns, '-l', 'app.kubernetes.io/name=alertmanager')['items'][0]

    def rollout(): k('rollout', 'status', 'deployment/' + PROFILE['deployment'], '-n', ns, '--timeout=180s')

    identity()
    if k('get', 'namespace', ns, '--ignore-not-found', '-o', 'name').strip():
        raise RuntimeError('Absent namespace required')
    try:
        owned_uid = create({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': ns,
                            'labels': {'ops.synthetic': 'alertmanager-qualification'}}})['metadata']['uid']
        preflight(lambda *a: k(*a, '-n', ns))
        check(True, 'fresh_profile_preflight')
        for rel in ['deploy/openserverless-permissions/openserverless-monitoring-00-sa.yaml',
                    'deploy/alert-manager/alertmanager-00-networkpolicy.yaml',
                    'deploy/monitoring/monitoring-00-networkpolicy.yaml']:
            for obj in yaml.safe_load_all((ROOT / rel).read_text()): create(obj)
        create({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'synthetic-sink', 'namespace': ns}, 'data': {'sink.py': SINK}})
        create({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': 'synthetic-sink', 'namespace': ns, 'labels': {'app': 'synthetic-sink'}},
                'spec': {'automountServiceAccountToken': False, 'containers': [{'name': 'sink', 'image': args.sink_image,
                         'imagePullPolicy': 'Never', 'command': ['python3', '-B', '/fixture/sink.py'],
                         'resources': {'limits': {'memory': '128Mi', 'cpu': '1'}},
                         'securityContext': {'runAsUser': 65534, 'runAsNonRoot': True, 'readOnlyRootFilesystem': True,
                                             'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
                         'volumeMounts': [{'name': 'fixture', 'mountPath': '/fixture', 'readOnly': True}]}],
                         'volumes': [{'name': 'fixture', 'configMap': {'name': 'synthetic-sink'}}]}})
        create({'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': 'synthetic-sink', 'namespace': ns},
                'spec': {'selector': {'app': 'synthetic-sink'}, 'ports': [{'port': 8080, 'targetPort': 8080}]}})
        create({'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy', 'metadata': {'name': 'synthetic-sink', 'namespace': ns},
                'spec': {'podSelector': {'matchLabels': {'app': 'synthetic-sink'}}, 'policyTypes': ['Ingress'],
                         'ingress': [{'from': [{'podSelector': {'matchLabels': {'app.kubernetes.io/name': 'alertmanager'}}}],
                                      'ports': [{'port': 8080, 'protocol': 'TCP'}]}]}})
        config = {'route': {'receiver': 'synthetic', 'group_by': ['alertname'], 'group_wait': '1s',
                           'group_interval': '2s', 'repeat_interval': '10m'},
                  'receivers': [{'name': 'synthetic', 'webhook_configs': [{'url': 'http://synthetic-sink:8080', 'send_resolved': True}]}]}
        cm = yaml.safe_load((ROOT / 'deploy/alert-manager/alertmanager-02-cm.yaml').read_text())
        cm['data']['alertmanager.yml'] = yaml.safe_dump(config)
        create(cm)
        for profile in [PROFILE, PROMETHEUS]:
            create({'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim', 'metadata': {'name': profile['claim'], 'namespace': ns},
                    'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '1Gi'}}}})
        for rel in ['deploy/alert-manager/alertmanager-04-svc.yaml', 'deploy/alert-manager/alertmanager-03-dep.yaml']:
            obj = yaml.safe_load((ROOT / rel).read_text())
            if obj['kind'] == 'Deployment':
                spec = obj['spec']['template']['spec']
                spec['containers'][0]['imagePullPolicy'] = 'Never'
                spec['securityContext'] = {'fsGroup': 65534, 'runAsUser': 65534, 'runAsNonRoot': True}
            create(obj)
        rollout()
        preflight(lambda *a: k(*a, '-n', ns))
        check(True, 'same_profile_repeat_preflight')
        pvc = get('pvc', PROFILE['claim'], '-n', ns)
        pv_uid = get('pv', pvc['spec']['volumeName'])['metadata']['uid']
        old_pod = pod()
        with forward('deployment/' + PROFILE['deployment'], 9093) as req, forward('pod/synthetic-sink', 8080) as sink:
            check(req('GET', '/-/ready')[0] == 200 and req('GET', '/-/healthy')[0] == 200, 'health_and_readiness')
            status = json.loads(req('GET', '/api/v2/status')[2])
            check(status['versionInfo']['version'] == PROFILE['expected_version'], 'running_version')
            html = req('GET', '/')[2]
            scripts = re.findall(rb'<script[^>]+src="([^"]+)"', html)
            check(bool(scripts), 'web_ui_html')
            with tarfile.open(args.ui_archive) as archive:
                for script in scripts:
                    path = '/' + script.decode().lstrip('./')
                    expected = gzip.decompress(archive.extractfile('dist' + path + '.gz').read())
                    check(req('GET', path)[2] == expected, 'official_ui_bytes_preserved')
            code, headers, _ = req('GET', '/api/v2/status', headers={'Origin': 'https://foreign.invalid'})
            check(code == 200 and not any(x.lower() == 'access-control-allow-origin' for x in headers), 'cross_origin_not_enabled')
            def records(name):
                return [a for r in json.loads(sink('GET', '/')[2]) for a in r['alerts'] if a['labels']['alertname'] == name]
            now = datetime.now(timezone.utc)
            silence = {'matchers': [{'name': 'alertname', 'value': 'OpsSilencedFixture', 'isRegex': False, 'isEqual': True}],
                       'startsAt': now.isoformat(), 'endsAt': (now + timedelta(hours=1)).isoformat(),
                       'createdBy': 'isolated-qualification', 'comment': 'synthetic only'}
            code, _, body = req('POST', '/api/v2/silences', silence)
            silence_id = json.loads(body)['silenceID']
            check(code == 200, 'silence_created')
            alert = {'labels': {'alertname': 'OpsSilencedFixture'}, 'startsAt': now.isoformat(),
                     'endsAt': (now + timedelta(hours=1)).isoformat(), 'annotations': {'synthetic': 'true'}}
            check(req('POST', '/api/v2/alerts', [alert])[0] == 200, 'silenced_alert_accepted')
            wait(lambda: any(silence_id in a['status']['silencedBy'] for a in json.loads(req('GET', '/api/v2/alerts')[2])))
            time.sleep(5)
            check(not records('OpsSilencedFixture'), 'silence_suppresses_notification')
            prom_config = {'global': {'scrape_interval': '2s', 'evaluation_interval': '2s'},
                           'alerting': {'alertmanagers': [{'static_configs': [{'targets': [PROFILE['deployment'] + ':9093']}]}]},
                           'rule_files': ['/etc/config/qualification.yml']}
            create({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'openserverless-prometheus-server', 'namespace': ns},
                    'data': {'prometheus.yml': yaml.safe_dump(prom_config), 'qualification.yml': yaml.safe_dump({'groups': [
                        {'name': 'synthetic', 'rules': [{'alert': 'OpsEndToEndFixture', 'expr': 'vector(1)', 'labels': {'synthetic': 'true'}}]}]})}})
            prom = yaml.safe_load((ROOT / 'deploy/monitoring/prometheus-03-dep.yaml').read_text())
            spec = prom['spec']['template']['spec']
            spec['securityContext'] = {'fsGroup': 65534, 'runAsUser': 65534, 'runAsNonRoot': True}
            for c in spec['containers']: c['imagePullPolicy'] = 'Never'
            create(prom)
            k('rollout', 'status', 'deployment/' + PROMETHEUS['deployment'], '-n', ns, '--timeout=180s')
            wait(lambda: records('OpsEndToEndFixture'))
            check(True, 'prometheus_rule_to_alertmanager_to_local_webhook')
            delivered = len(records('OpsEndToEndFixture'))
            delivered_alert = {key: value for key, value in records('OpsEndToEndFixture')[0].items()
                               if key in ('labels', 'annotations', 'startsAt', 'endsAt', 'generatorURL')}
        identity()
        k('rollout', 'restart', 'deployment/' + PROFILE['deployment'], '-n', ns)
        rollout()
        check(pod()['metadata']['uid'] != old_pod['metadata']['uid'], 'pod_replaced')
        check(all(c['restartCount'] == 0 for c in pod()['status']['containerStatuses']), 'restart_without_container_retries')
        check(get('pvc', PROFILE['claim'], '-n', ns)['metadata']['uid'] == pvc['metadata']['uid'] and
              get('pv', pvc['spec']['volumeName'])['metadata']['uid'] == pv_uid, 'pvc_pv_preserved')
        with forward('deployment/' + PROFILE['deployment'], 9093) as req, forward('pod/synthetic-sink', 8080) as sink:
            check(json.loads(req('GET', '/api/v2/silence/' + silence_id)[2])['status']['state'] == 'active', 'silence_persists_restart')
            check(req('POST', '/api/v2/alerts', [delivered_alert])[0] == 200, 'same_alert_resubmitted_after_restart')
            wait(lambda: any(a['labels']['alertname'] == 'OpsEndToEndFixture'
                            for a in json.loads(req('GET', '/api/v2/alerts')[2])))
            # Exceed the default gossip settling/notification delay as well as
            # group_wait and group_interval; a quiet startup is not dedup proof.
            time.sleep(45)
            rows = json.loads(sink('GET', '/')[2])
            count = sum(a['labels']['alertname'] == 'OpsEndToEndFixture' for r in rows for a in r['alerts'])
            check(count == delivered, 'notification_log_prevents_immediate_resend_after_restart')
            check(req('DELETE', '/api/v2/silence/' + silence_id)[0] == 200, 'silence_can_be_expired')
            check(req('POST', '/api/v2/alerts', [alert])[0] == 200, 'expired_silence_alert_resubmitted')
            wait(lambda: any(a['labels']['alertname'] == 'OpsSilencedFixture'
                             for r in json.loads(sink('GET', '/')[2]) for a in r['alerts']))
            check(True, 'notification_delivered_after_silence_expiry')
            # API v1 was removed upstream; assert the supported API v2 remains available.
            check(req('GET', '/api/v2/status')[0] == 200, 'api_v2_available_after_restart')
        ip = pod()['status']['podIP']
        for action in [False, True]:
            name = 'probe-action' if action else 'probe-monitor'
            labels = {'app.kubernetes.io/name': 'prometheus', 'app.kubernetes.io/instance': 'openserverless-prometheus'}
            if action: labels['user-action-pod'] = 'true'
            code = ('import json,time,urllib.request,urllib.error\n'
                    'observed=[]\n'
                    'opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))\n'
                    'for attempt in range(' + ('3' if action else '8') + '):\n'
                    ' try:\n'
                    '  with opener.open("http://' + ip + ':9093/-/ready",timeout=3) as r: observed.append(r.status)\n'
                    ' except urllib.error.HTTPError as e: observed.append(e.code)\n'
                    ' except OSError as e: observed.append("DENIED:"+type(e).__name__)\n'
                    + ('' if action else ' if observed[-1]==200: break\n') +
                    ' time.sleep(1)\n'
                    'print(json.dumps(observed))')
            create({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'namespace': ns, 'labels': labels},
                    'spec': {'automountServiceAccountToken': False, 'restartPolicy': 'Never', 'containers': [{
                        'name': 'probe', 'image': args.sink_image, 'imagePullPolicy': 'Never', 'command': ['python3', '-B', '-c', code]}]}})
            wait(lambda: get('pod', name, '-n', ns)['status'].get('phase') == 'Succeeded', 45)
            result = json.loads(k('logs', name, '-n', ns).strip())
            print(json.dumps({'probe': name, 'observations': result}), flush=True)
            check(all(isinstance(v, str) and v.startswith('DENIED:') for v in result)
                  if action else 200 in result, name + '_alertmanager_boundary')
        print(json.dumps({'status': 'PASS', 'checks': len(checks), 'scope': 'fresh isolated AMD64 only',
                          'legacy_state_migration': 'NOT_RUN', 'external_notifications': 'NOT_RUN'}), flush=True)
    finally:
        if owned_uid:
            identity()
            k('delete', 'namespace', ns, '--wait=true', '--timeout=180s')
            print(json.dumps({'owned_namespace_cleanup': 'PASS'}), flush=True)


if __name__ == '__main__': main()
