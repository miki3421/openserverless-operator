# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Qualify registry persistence on an explicitly identified, isolated K3s node.

Creates and removes one owned namespace, PVC, synthetic Secret and StatefulSet.
Never targets an existing namespace or prints generated credentials.
"""
import argparse
import base64
import hashlib
import json
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import bcrypt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kubectl', required=True)
    p.add_argument('--kubeconfig', required=True)
    p.add_argument('--node-uid', required=True)
    p.add_argument('--image', required=True)
    a = p.parse_args()
    ns = 'ops-registry-qual-' + uuid.uuid4().hex[:10]
    namespace_uid = None
    checks = []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def k(*args, data=None):
        r = subprocess.run([a.kubectl, '--kubeconfig', a.kubeconfig, '--request-timeout=30s', *args],
                           input=data, capture_output=True, text=True, timeout=200)
        if r.returncode: raise RuntimeError('kubectl ' + args[0] + ' failed: ' + r.stderr[:500])
        return r.stdout

    def get(*args): return json.loads(k('get', *args, '-o', 'json'))

    def identity():
        nodes = get('nodes')['items']
        if len(nodes) != 1 or nodes[0]['metadata']['uid'] != a.node_uid:
            raise RuntimeError('Unexpected isolated node identity')

    def check(ok, name):
        if not ok: raise RuntimeError(name)
        checks.append(name)
        print(json.dumps({'check': name, 'status': 'PASS'}), flush=True)

    def apply(obj):
        identity()
        k('create', '-f', '-', data=json.dumps(obj))

    password = secrets.token_urlsafe(32)
    auth = 'Basic ' + base64.b64encode(('fixture:' + password).encode()).decode()
    config = b'{"architecture":"amd64","os":"linux","rootfs":{"type":"layers","diff_ids":[]},"config":{"Labels":{"ops.synthetic":"persistent"}}}'
    digest = 'sha256:' + hashlib.sha256(config).hexdigest()
    manifest_type = 'application/vnd.oci.image.manifest.v1+json'
    manifest = json.dumps({'schemaVersion': 2, 'mediaType': manifest_type,
                          'config': {'mediaType': 'application/vnd.oci.image.config.v1+json',
                                     'digest': digest, 'size': len(config)}, 'layers': []}, separators=(',', ':')).encode()
    manifest_digest = 'sha256:' + hashlib.sha256(manifest).hexdigest()

    def exchange(write, label):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
        with tempfile.TemporaryFile() as log:
            pf = subprocess.Popen([a.kubectl, '--kubeconfig', a.kubeconfig, '-n', ns, 'port-forward',
                                   '--address=127.0.0.1', 'pod/registry-0', str(port) + ':5000'], stdout=log, stderr=log)
            base = 'http://127.0.0.1:' + str(port)

            def req(method, path, data=None, identity=auth, content_type=None):
                headers = {'Accept': manifest_type}
                if identity: headers['Authorization'] = identity
                if content_type: headers['Content-Type'] = content_type
                try: r = opener.open(urllib.request.Request(base + path, data=data, method=method, headers=headers), timeout=10)
                except urllib.error.HTTPError as e: r = e
                with r: return r.status, dict(r.headers), r.read()

            try:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        if req('GET', '/v2/')[0] == 200: break
                    except OSError: pass
                    if time.monotonic() > deadline: raise RuntimeError('Port forward/readiness timeout')
                    time.sleep(.2)
                check(req('GET', '/v2/', identity=None)[0] == 401, label + '_anonymous_denied')
                check(req('GET', '/v2/', identity='Basic Zml4dHVyZTpiYWQ=')[0] == 401, label + '_invalid_auth_denied')
                if write:
                    status, headers, _ = req('POST', '/v2/synthetic/blobs/uploads/', b'')
                    check(status == 202, 'upload_created')
                    loc = urllib.parse.urlsplit(headers['Location'])
                    path = loc.path + ('?' + loc.query if loc.query else '')
                    status, headers, _ = req('PATCH', path, config, content_type='application/octet-stream')
                    check(status == 202, 'chunk_written')
                    loc = urllib.parse.urlsplit(headers['Location'])
                    query = urllib.parse.parse_qsl(loc.query) + [('digest', digest)]
                    status, _, _ = req('PUT', loc.path + '?' + urllib.parse.urlencode(query), b'')
                    check(status == 201, 'blob_committed')
                    status, headers, _ = req('PUT', '/v2/synthetic/manifests/current', manifest, content_type=manifest_type)
                    check(status == 201 and headers.get('Docker-Content-Digest') == manifest_digest, 'manifest_committed')
                status, headers, data = req('GET', '/v2/synthetic/manifests/' + manifest_digest)
                check(status == 200 and data == manifest and headers.get('Docker-Content-Digest') == manifest_digest,
                      label + '_manifest_exact')
                status, _, data = req('GET', '/v2/synthetic/blobs/' + digest)
                check(status == 200 and data == config, label + '_blob_exact')
            finally:
                pf.terminate(); pf.wait(timeout=10)

    identity()
    try:
        apply({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': ns, 'labels': {'ops.synthetic': 'registry-qualification'}}})
        namespace_uid = get('namespace', ns)['metadata']['uid']
        apply({'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'auth', 'namespace': ns},
               'stringData': {'htpasswd': 'fixture:' + bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=6)).decode() + '\n'}})
        apply({'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim', 'metadata': {'name': 'registry', 'namespace': ns},
               'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '1Gi'}}}})
        env = {'REGISTRY_AUTH': 'htpasswd', 'REGISTRY_AUTH_HTPASSWD_REALM': 'Registry Realm',
               'REGISTRY_AUTH_HTPASSWD_PATH': '/auth/htpasswd', 'REGISTRY_STORAGE_FILESYSTEM_ROOTDIRECTORY': '/var/lib/registry',
               'OTEL_TRACES_EXPORTER': 'none'}
        apply({'apiVersion': 'apps/v1', 'kind': 'StatefulSet', 'metadata': {'name': 'registry', 'namespace': ns},
               'spec': {'replicas': 1, 'serviceName': 'registry', 'selector': {'matchLabels': {'app': 'registry'}},
                        'template': {'metadata': {'labels': {'app': 'registry'}}, 'spec': {
                            'automountServiceAccountToken': False,
                            'containers': [{'name': 'registry', 'image': a.image, 'imagePullPolicy': 'Never',
                                            'env': [{'name': x, 'value': y} for x, y in env.items()],
                                            'resources': {'limits': {'memory': '256Mi', 'cpu': '1'}},
                                            'readinessProbe': {'tcpSocket': {'port': 5000}, 'periodSeconds': 2},
                                            'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                                                'capabilities': {'drop': ['ALL']}},
                                            'volumeMounts': [{'name': 'data', 'mountPath': '/var/lib/registry'},
                                                             {'name': 'auth', 'mountPath': '/auth', 'readOnly': True}]}],
                            'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': 'registry'}},
                                        {'name': 'auth', 'secret': {'secretName': 'auth'}}]}}}})
        k('rollout', 'status', 'sts/registry', '-n', ns, '--timeout=180s')
        before = get('pod', 'registry-0', '-n', ns)
        pvc = get('pvc', 'registry', '-n', ns)
        pv = get('pv', pvc['spec']['volumeName'])
        exchange(True, 'initial')
        identity()
        k('delete', 'pod', 'registry-0', '-n', ns, '--wait=true', '--timeout=60s')
        k('wait', '--for=condition=Ready', 'pod/registry-0', '-n', ns, '--timeout=180s')
        after = get('pod', 'registry-0', '-n', ns)
        check(before['metadata']['uid'] != after['metadata']['uid'], 'pod_recreated')
        pvc2 = get('pvc', 'registry', '-n', ns)
        pv2 = get('pv', pvc['spec']['volumeName'])
        check(pvc['metadata']['uid'] == pvc2['metadata']['uid'] and pvc['spec'] == pvc2['spec'] and
              pv['metadata']['uid'] == pv2['metadata']['uid'] and pv['spec'] == pv2['spec'], 'pvc_pv_preserved')
        exchange(False, 'restart')
    finally:
        if namespace_uid:
            identity()
            if get('namespace', ns)['metadata']['uid'] != namespace_uid: raise RuntimeError('Namespace identity changed before cleanup')
            k('delete', 'namespace', ns, '--wait=true', '--timeout=180s')
    print(json.dumps({'passed': len(checks), 'ownedNamespaceRemoved': True, 'image': a.image}))


if __name__ == '__main__': main()
