"""Exercise the installed operator CLI contracts using synthetic, offline inputs."""
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml


class GoToolCompatibilityTests(unittest.TestCase):
    def run_tool(self, args, cwd=None, env=None, success=True):
        result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, timeout=30)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_kubectl_client_json_and_jsonpath(self):
        version = json.loads(self.run_tool(["kubectl", "version", "--client", "-o", "json"]).stdout)["clientVersion"]
        self.assertEqual(version["major"], "1")
        self.assertTrue(version["minor"].isdigit())
        result = self.run_tool(['kubectl', 'create', 'configmap', 'synthetic-tools',
                               '--from-literal=marker=preserved', '--dry-run=client', '-o', 'json'])
        obj = json.loads(result.stdout)
        self.assertEqual(obj['kind'], 'ConfigMap')
        self.assertEqual(obj['data'], {'marker': 'preserved'})
        result = self.run_tool(['kubectl', 'create', 'configmap', 'synthetic-tools',
                               '--from-literal=marker=preserved', '--dry-run=client',
                               '-o', 'jsonpath={.data.marker}'])
        self.assertEqual(result.stdout, 'preserved')

    def test_kustomize_patches_images_and_private_service(self):
        # Exercise the formats emitted by openserverless.kustomize, not a cluster.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = [
                {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': 'synthetic'},
                 'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': 'synthetic'}},
                          'template': {'metadata': {'labels': {'app': 'synthetic'}},
                                       'spec': {'containers': [{'name': 'app', 'image': 'synthetic:old'}]}}}},
                {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': 'synthetic'},
                 'spec': {'type': 'ClusterIP', 'selector': {'app': 'synthetic'},
                          'ports': [{'port': 8080, 'targetPort': 8080}]}},
            ]
            (root / 'resources.yaml').write_text(yaml.safe_dump_all(docs))
            patch = {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': 'synthetic'},
                     'spec': {'template': {'spec': {'containers': [{'name': 'app',
                              'securityContext': {'allowPrivilegeEscalation': False}}]}}}}
            (root / 'patch.yaml').write_text(yaml.safe_dump(patch))
            cfg = {'apiVersion': 'kustomize.config.k8s.io/v1beta1', 'kind': 'Kustomization',
                   'namespace': 'synthetic-namespace', 'resources': ['resources.yaml'],
                   'images': [{'name': 'synthetic', 'newName': 'example.invalid/synthetic', 'newTag': 'locked'}],
                   'patches': [{'path': 'patch.yaml'}, {'target': {'kind': 'Deployment', 'name': 'synthetic'},
                               'patch': '- op: replace\n  path: /spec/replicas\n  value: 2\n'}],
                   'secretGenerator': [{'name': 'fixture', 'literals': ['token=synthetic-only']}],
                   'configMapGenerator': [{'name': 'settings', 'literals': ['mode=fixture']}]}
            (root / 'kustomization.yaml').write_text(yaml.safe_dump(cfg))
            rendered = list(yaml.safe_load_all(self.run_tool(['kustomize', 'build', tmp]).stdout))
            by_kind = {x['kind']: x for x in rendered}
            self.assertEqual(set(by_kind), {'Deployment', 'Service', 'Secret', 'ConfigMap'})
            self.assertTrue(all(x['metadata']['namespace'] == 'synthetic-namespace' for x in rendered))
            deployment = by_kind['Deployment']['spec']
            self.assertEqual(deployment['replicas'], 2)
            container = deployment['template']['spec']['containers'][0]
            self.assertEqual(container['image'], 'example.invalid/synthetic:locked')
            self.assertFalse(container['securityContext']['allowPrivilegeEscalation'])
            self.assertEqual(by_kind['Service']['spec']['type'], 'ClusterIP')
            self.assertNotIn('nodePort', by_kind['Service']['spec']['ports'][0])
            self.assertEqual(base64.b64decode(by_kind['Secret']['data']['token']), b'synthetic-only')
            self.assertEqual(by_kind['ConfigMap']['data'], {'mode': 'fixture'})

    def test_task_includes_variables_and_failure_propagation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'child.yml').write_text("version: '3'\ntasks:\n  echo:\n    silent: true\n    cmds:\n      - printf '%s' '{{.VALUE}}'\n")
            (root / 'Taskfile.yml').write_text("version: '3'\nincludes:\n  child: ./child.yml\ntasks:\n  fail:\n    cmds:\n      - exit 7\n")
            result = self.run_tool(['task', 'child:echo', 'VALUE=synthetic-value'], cwd=tmp)
            self.assertEqual(result.stdout, 'synthetic-value')
            self.run_tool(['task', 'fail'], cwd=tmp, success=False)

    def test_wsk_authentication_and_json_result_contract(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append((self.path, self.headers.get('Authorization')))
                allowed = self.headers.get('Authorization') == 'Basic ' + base64.b64encode(b'fixture:key').decode()
                body = [{'name': 'synthetic', 'namespace': 'fixture', 'version': '0.0.1',
                         'publish': False, 'annotations': []}] if allowed else {'error': 'unauthorized', 'code': 'fixture'}
                data = json.dumps(body).encode()
                self.send_response(200 if allowed else 401)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers(); self.wfile.write(data)
            def log_message(self, *_args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = {**os.environ, 'WSK_CONFIG_FILE': str(Path(tmp) / 'wskprops')}
                base = ['wsk', '--apihost', f'http://127.0.0.1:{server.server_port}']
                result = self.run_tool(base + ['--auth', 'fixture:key', 'action', 'list'], env=env)
                self.assertIn('synthetic', result.stdout)
                self.run_tool(base + ['--auth', 'fixture:wrong', 'action', 'list'], env=env, success=False)
                self.assertEqual([x[0] for x in calls], ['/api/v1/namespaces/_/actions?limit=30&skip=0'] * 2)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
