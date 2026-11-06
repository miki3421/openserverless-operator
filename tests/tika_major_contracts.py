# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Observe 3.x HTTP contracts on a local image; never deploy or pull an image.

This diagnostic keeps failures visible while continuing independent requests.
It does not replace the strict tika_compat.py regression suite. Exit 1 means
at least one legacy contract failed; changed response shapes are observations,
not automatically accepted replacement contracts.
"""
import argparse
import concurrent.futures
import io
import ipaddress
import json
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile

from tika_compat import document_docx, document_pdf, document_png


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--contract', choices=['legacy', 'native4'], default='legacy',
                        help='native4 tests explicit 4.x client changes, not backwards compatibility')
    args = parser.parse_args()
    token = 'ops-tika-major-' + uuid.uuid4().hex
    owner = 'ops.qualifier=' + token
    network = container = False
    checks = []

    def docker(*values):
        return subprocess.check_output(['docker', *values], text=True,
                                       stderr=subprocess.STDOUT).strip()

    def request(path, data=None, content_type=None, accept='text/plain', method=None):
        headers = {'Accept': accept}
        if content_type:
            headers['Content-Type'] = content_type
        # Each worker owns its opener; no environment proxies or external calls.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
        try:
            response = opener.open(req, timeout=45)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read(4 * 1024 * 1024 + 1)
            if len(body) > 4 * 1024 * 1024:
                raise RuntimeError('Response exceeds fixture bound')
            return response.status, response.headers.get('Content-Type', ''), body

    def check(name, passed, **observed):
        row = {'check': name, 'contract': args.contract, 'status': 'PASS' if passed else 'FAIL', **observed}
        checks.append(row)
        print(json.dumps(row), flush=True)

    def observe(name, data, media, marker):
        try:
            path = '/tika/text' if args.contract == 'native4' else '/tika'
            status, content_type, body = request(path, data, media, method='PUT')
            check(name + '_text', status == 200 and marker in body.decode(errors='replace'),
                  httpStatus=status, contentType=content_type)
        except (OSError, ValueError) as error:
            check(name + '_text', False, error=type(error).__name__)
        try:
            status, content_type, body = request('/rmeta/text', data, media, 'application/json', 'PUT')
            result = json.loads(body)
            rows = result if isinstance(result, list) else []
            rows = [r for r in rows if isinstance(r, dict)]
            keys = sorted({k for row in rows for k in row})
            content_keys = sorted({k for row in rows for k, v in row.items()
                                   if isinstance(v, str) and marker in v})
            key = 'tk:content' if args.contract == 'native4' else 'X-TIKA:content'
            content_present = any(marker in str(row.get(key, '')) for row in rows)
            check(name + '_metadata', status == 200 and content_present, httpStatus=status,
                  keys=keys, contentKeys=content_keys)
        except (OSError, ValueError) as error:
            check(name + '_metadata', False, error=type(error).__name__)

    try:
        image = json.loads(docker('image', 'inspect', args.image))[0]
        print(json.dumps({'image': args.image, 'imageId': image['Id'],
                          'repoDigests': image.get('RepoDigests', [])}), flush=True)
        docker('network', 'create', '--internal', '--label', owner, token)
        network = True
        docker('run', '-d', '--name', token, '--label', owner, '--pull=never',
               '--network', token, '--read-only', '--cap-drop=ALL',
               '--security-opt=no-new-privileges', '--memory=2g', '--cpus=2',
               '--pids-limit=512', '--tmpfs', '/tmp:rw,exec,nosuid,size=512m',
               '-e', 'JAVA_TOOL_OPTIONS=-Xms128m -Xmx768m -Djava.awt.headless=true', args.image)
        container = True
        running = json.loads(docker('inspect', token))[0]
        address = running['NetworkSettings']['Networks'][token]['IPAddress']
        if (not ipaddress.ip_address(address).is_private or
                any(running['NetworkSettings']['Ports'].values()) or running['Mounts']):
            raise RuntimeError('Unexpected network or host mount')
        base = 'http://' + address + ':9998'
        deadline = time.monotonic() + 90
        while True:
            try:
                status, _, body = request('/version')
                if status == 200:
                    break
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError('Startup timeout')
            time.sleep(.5)
        check('version', args.version in body.decode(errors='replace'), httpStatus=status)
        marker = 'OPS synthetic document 20261106'
        observe('plain', marker.encode(), 'text/plain', marker)
        observe('html', ('<html><body><p>' + marker + '</p></body></html>').encode(), 'text/html', marker)
        observe('pdf', document_pdf(marker), 'application/pdf', marker)
        observe('docx', document_docx(marker),
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document', marker)
        observe('rtf', ('{\\rtf1\\ansi ' + marker + '\\par}').encode(), 'application/rtf', marker)
        observe('png_ocr', document_png(), 'image/png', 'HELLO WORLD')
        for name, path, data, media, expected in [
            ('legacy_detect_path', '/detect/stream', document_pdf(marker), 'application/octet-stream', 'application/pdf'),
            ('legacy_language_path', '/language/stream', b'This is a long English sentence about the weather and the blue sky. We are testing the language of this document.', 'text/plain', 'en'),
        ]:
            if args.contract == 'native4':
                name = name.replace('legacy_', 'native4_')
                path = path.removesuffix('/stream')
            status, media, body = request(path, data, media, method='PUT')
            value = body.decode(errors='replace').strip()
            check(name, status == 200 and value == expected, httpStatus=status, response=value[:200])
        path = '/tika/html' if args.contract == 'native4' else '/tika'
        status, media, body = request(path, marker.encode(), 'text/plain', 'text/html', 'PUT')
        value = body.decode(errors='replace')
        check('html_accept_contract', status == 200 and '<html' in value and marker in value,
              httpStatus=status, contentType=media, response=value[:300])

        def worker(index):
            value = 'OPS unique request ' + str(index)
            path = '/tika/text' if args.contract == 'native4' else '/tika'
            status, _, body = request(path, value.encode(), 'text/plain', method='PUT')
            return status == 200 and value in body.decode() and all(
                ('OPS unique request ' + str(other)) not in body.decode()
                for other in range(4) if other != index)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            check('four_concurrent_requests_isolated', all(pool.map(worker, range(4))))

        # Additional observations do not rewrite the 17 established contracts.
        status, media, body = request('/tika', b'', 'text/plain', method='PUT')
        print(json.dumps({'observation': 'empty_document', 'status': status,
                          'contentType': media, 'response': body.decode(errors='replace')[:200]}), flush=True)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
            output.writestr('fixture.txt', marker)
        status, media, body = request('/unpack', archive.getvalue(), 'application/zip', '*/*', 'PUT')
        members = []
        try:
            with tarfile.open(fileobj=io.BytesIO(body)) as output:
                members = output.getnames()  # No extraction of server responses.
        except tarfile.ReadError:
            if zipfile.is_zipfile(io.BytesIO(body)):
                with zipfile.ZipFile(io.BytesIO(body)) as output:
                    members = output.namelist()
        print(json.dumps({'observation': 'unpack_default', 'status': status,
                          'contentType': media, 'members': members}), flush=True)
    finally:
        if container:
            docker('rm', '-f', token)
        if network:
            docker('network', 'rm', token)
    failures = [c['check'] for c in checks if c['status'] == 'FAIL']
    print(json.dumps({'checks': len(checks), 'contract': args.contract, 'failures': failures,
                      'ownedResourcesRemoved': True, 'liveDeployment': False}), flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
