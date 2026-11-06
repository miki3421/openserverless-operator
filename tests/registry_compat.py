# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Synthetic authenticated OCI registry storage and compatibility qualification.

Only a private temporary directory, owned containers and an internal Docker
network are used. No host port is published; credentials are never
printed. No production registry, source cache or volume is used.
"""
import argparse
import base64
import concurrent.futures
import hashlib
import ipaddress
import json
from pathlib import Path
import secrets
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
    p.add_argument('--old', required=True)
    p.add_argument('--new', required=True)
    args = p.parse_args()
    token = 'ops-registry-test-' + uuid.uuid4().hex[:12]
    container = token + '-server'
    password = secrets.token_urlsafe(32)
    auth = 'Basic ' + base64.b64encode(('fixture:' + password).encode()).decode()
    config_type = 'application/vnd.oci.image.config.v1+json'
    manifest_type = 'application/vnd.oci.image.manifest.v1+json'
    checks = []
    # Never send private fixture credentials through an environment HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    network_created = False
    running = False

    def docker(*words):
        return subprocess.check_output(['docker', *words], text=True, stderr=subprocess.STDOUT).strip()

    def check(condition, name):
        if not condition: raise RuntimeError(name)
        checks.append({'check': name, 'status': 'PASS'})
        print(json.dumps(checks[-1]), flush=True)

    with tempfile.TemporaryDirectory(prefix=token + '-') as directory:
        root = Path(directory)
        (root / 'data').mkdir(mode=0o700)
        (root / 'auth').mkdir(mode=0o700)
        (root / 'auth/htpasswd').write_bytes(b'fixture:' + bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=6)) + b'\n')
        (root / 'auth/htpasswd').chmod(0o600)
        base = ''

        def request(method, path, data=None, identity=auth, content_type=None):
            headers = {}
            if identity: headers['Authorization'] = identity
            if content_type: headers['Content-Type'] = content_type
            req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
            try: response = opener.open(req, timeout=10)
            except urllib.error.HTTPError as err: response = err
            with response:
                return response.status, dict(response.headers), response.read()

        def start(image):
            nonlocal base, running
            docker('run', '-d', '--name', container, '--pull=never', '--network', token,
                   '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                   '--memory=256m', '--pids-limit=128',
                   '-v', f'{root}/data:/var/lib/registry', '-v', f'{root}/auth:/auth:ro',
                   '-e', 'REGISTRY_AUTH=htpasswd', '-e', 'REGISTRY_AUTH_HTPASSWD_REALM=Registry Realm',
                   '-e', 'REGISTRY_AUTH_HTPASSWD_PATH=/auth/htpasswd',
                   '-e', 'REGISTRY_STORAGE_FILESYSTEM_ROOTDIRECTORY=/var/lib/registry',
                   '-e', 'REGISTRY_HTTP_ADDR=0.0.0.0:5000', '-e', 'OTEL_TRACES_EXPORTER=none', image)
            running = True
            inspect = json.loads(docker('inspect', container))[0]
            if not inspect['State']['Running']: raise RuntimeError('Registry exited before readiness')
            if any(inspect['NetworkSettings']['Ports'].values()): raise RuntimeError('Unexpected published port')
            address = inspect['NetworkSettings']['Networks'][token]['IPAddress']
            if not ipaddress.ip_address(address).is_private: raise RuntimeError('Unexpected network address')
            base = 'http://' + address + ':5000'
            deadline = time.monotonic() + 30
            while True:
                try:
                    if request('GET', '/v2/')[0] == 200: return
                except OSError: pass
                if time.monotonic() >= deadline: raise RuntimeError('Registry did not become ready')
                time.sleep(.2)

        def stop():
            nonlocal running
            docker('stop', '--time=10', container); docker('rm', container); running = False

        def push(repository, tag, marker):
            config = json.dumps({'architecture': 'amd64', 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': []},
                                 'config': {'Labels': {'ops.synthetic': marker}}}, separators=(',', ':')).encode()
            digest = 'sha256:' + hashlib.sha256(config).hexdigest()
            status, headers, _ = request('POST', f'/v2/{repository}/blobs/uploads/', b'')
            if status != 202: raise RuntimeError('Upload creation failed')
            url = urllib.parse.urlsplit(headers['Location'])
            path = url.path + ('?' + url.query if url.query else '')
            status, headers, _ = request('PATCH', path, config, content_type='application/octet-stream')
            if status != 202: raise RuntimeError('Chunk upload failed')
            url = urllib.parse.urlsplit(headers['Location'])
            query = urllib.parse.parse_qsl(url.query) + [('digest', digest)]
            status, _, _ = request('PUT', url.path + '?' + urllib.parse.urlencode(query), b'')
            if status != 201: raise RuntimeError('Upload finalization failed')
            manifest = json.dumps({'schemaVersion': 2, 'mediaType': manifest_type,
                                   'config': {'mediaType': config_type, 'digest': digest, 'size': len(config)},
                                   'layers': []}, separators=(',', ':')).encode()
            status, headers, _ = request('PUT', f'/v2/{repository}/manifests/{tag}', manifest, content_type=manifest_type)
            md = 'sha256:' + hashlib.sha256(manifest).hexdigest()
            if status != 201 or headers.get('Docker-Content-Digest') != md: raise RuntimeError('Manifest push failed')
            return {'repository': repository, 'tag': tag, 'manifest': manifest, 'digest': md, 'config': config, 'configDigest': digest}

        def read(fixture, label):
            repo = fixture['repository']
            # The registry requires an explicit Accept for OCI manifests.
            req = urllib.request.Request(base + f'/v2/{repo}/manifests/' + fixture['digest'],
                                         headers={'Authorization': auth, 'Accept': manifest_type})
            with opener.open(req, timeout=10) as response:
                data = response.read()
                check(response.status == 200 and data == fixture['manifest'] and
                      response.headers.get('Docker-Content-Digest') == fixture['digest'], label + '_manifest_by_digest')
            status, _, data = request('GET', f'/v2/{repo}/blobs/' + fixture['configDigest'])
            check(status == 200 and data == fixture['config'], label + '_blob_by_digest')

        try:
            docker('network', 'create', '--internal', '--label', 'ops.qualifier=' + token, token)
            network_created = True
            start(args.old)
            check(request('GET', '/v2/', identity=None)[0] == 401, 'old_anonymous_denied')
            check(request('GET', '/v2/', identity='Basic Zml4dHVyZTpiYWQ=')[0] == 401, 'old_invalid_auth_denied')
            original = push('ops-fixture', 'old', 'original')
            read(original, 'old')
            stop(); start(args.new)
            check(request('GET', '/v2/', identity=None)[0] == 401, 'new_anonymous_denied')
            check(request('GET', '/v2/', identity='Basic Zml4dHVyZTpiYWQ=')[0] == 401, 'new_invalid_auth_denied')
            read(original, 'new_reads_original')
            fresh = push('ops-fixture', 'new', 'new')
            read(fresh, 'new')
            status, _, data = request('GET', '/v2/ops-fixture/tags/list?n=1')
            first = json.loads(data)['tags']
            status2, _, data2 = request('GET', '/v2/ops-fixture/tags/list?n=1&last=' + first[0])
            second = json.loads(data2)['tags']
            check(status == status2 == 200 and set(first + second) == {'old', 'new'} and len(first) == len(second) == 1,
                  'tag_pagination')
            status, _, data = request('GET', '/v2/ops-fixture/tags/list?n=1&last=' + second[0])
            check(status == 200 and json.loads(data)['tags'] in [[], None], 'pagination_end')
            check(request('GET', '/v2/ops-fixture/blobs/sha256:' + '0' * 64)[0] == 404, 'missing_blob_denied')
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                concurrent_uploads = list(pool.map(lambda i: push('ops-concurrent', 'tag-' + str(i), 'parallel-' + str(i)), range(4)))
            check(len({x['digest'] for x in concurrent_uploads}) == 4, 'concurrent_uploads_keep_distinct_digests')
            for index, fixture in enumerate(concurrent_uploads): read(fixture, 'concurrent_' + str(index))
            stop(); start(args.new)
            read(original, 'restart_reads_original'); read(fresh, 'restart_reads_new')
            stop(); start(args.old)
            read(original, 'rollback_reads_original'); read(fresh, 'rollback_reads_new')
            stop()
        finally:
            if running: docker('rm', '-f', container)
            if network_created: docker('network', 'rm', token)
    print(json.dumps({'old': args.old, 'new': args.new, 'passed': len(checks), 'ownedResourcesRemoved': True}))


if __name__ == '__main__': main()
