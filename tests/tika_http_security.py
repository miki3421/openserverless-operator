# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Bounded HTTP parsing controls: synthetic bodies, no load or crash payloads."""
import argparse
import ipaddress
import json
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--image')
    target.add_argument('--base-url', help='Explicitly prepared loopback port-forward')
    parser.add_argument('--diagnostics', type=Path, help='Private owned-container logs; no effect with a port-forward')
    args = parser.parse_args()
    token = 'ops-tika-http-' + uuid.uuid4().hex[:10]
    network = container = False
    checks = []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    marker = b'OPS synthetic HTTP fixture'

    def docker(*parts):
        return subprocess.check_output(['docker', *parts], stderr=subprocess.STDOUT, text=True).strip()

    def request(path, body=None, content_type='text/plain', method=None):
        req = urllib.request.Request(base + path, data=body,
                                     headers={'Content-Type': content_type, 'Accept': 'text/plain'},
                                     method=method or ('GET' if body is None else 'PUT'))
        try:
            reply = opener.open(req, timeout=20)
        except urllib.error.HTTPError as error:
            reply = error
        with reply:
            return reply.status, reply.read(16385)

    def record(name, status, passed, body=b''):
        row = {'check': name, 'status_code': status, 'status': 'PASS' if passed else 'FAIL'}
        if status == 500:
            row['synthetic_error_response'] = body.decode('utf-8', errors='replace')[:1000]
        checks.append(row)

    def chunked(extension):
        endpoint = urllib.parse.urlsplit(base)
        payload = (b'PUT /tika HTTP/1.1\r\nHost: localhost\r\nContent-Type: text/plain\r\n'
                   b'Accept: text/plain\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n' +
                   format(len(marker), 'x').encode() + b';' + extension + b'\r\n' + marker + b'\r\n0\r\n\r\n')
        data = b''
        with socket.create_connection((endpoint.hostname, endpoint.port or 80), timeout=5) as sock:
            sock.settimeout(10)
            sock.sendall(payload)
            while len(data) <= 16384:
                chunk = sock.recv(16385 - len(data))
                if not chunk:
                    break
                data += chunk
        if len(data) > 16384:
            raise RuntimeError('Unexpected oversized synthetic response')
        status = int(data.split(b'\r\n', 1)[0].split()[1])
        return status, data

    def multipart(headers):
        boundary = 'ops-synthetic-boundary'
        body = ('--' + boundary + '\r\nContent-Disposition: form-data; name="upload"; filename="synthetic.txt"\r\n'
                'Content-Type: text/plain\r\n' + headers + '\r\n').encode() + marker + ('\r\n--' + boundary + '--\r\n').encode()
        if len(body) > 16384:
            raise RuntimeError('Synthetic input exceeds budget')
        return request('/tika/form', body, 'multipart/form-data; boundary=' + boundary, 'POST')

    try:
        if args.image:
            docker('network', 'create', '--internal', '--label', 'ops.qualifier=' + token, token)
            network = True
            docker('run', '-d', '--name', token, '--pull=never', '--network', token, '--read-only',
                   '--cap-drop=ALL', '--security-opt=no-new-privileges', '--memory=2g', '--cpus=2', '--pids-limit=512',
                   '--tmpfs', '/tmp:rw,exec,nosuid,size=512m', '-e', 'JAVA_TOOL_OPTIONS=-Xms128m -Xmx768m -Djava.awt.headless=true', args.image)
            container = True
            info = json.loads(docker('inspect', token))[0]
            address = info['NetworkSettings']['Networks'][token]['IPAddress']
            if not ipaddress.ip_address(address).is_private or any(info['NetworkSettings']['Ports'].values()):
                raise RuntimeError('Unexpected network')
            base = 'http://' + address + ':9998'
        else:
            endpoint = urllib.parse.urlsplit(args.base_url)
            if (endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or not endpoint.port or
                    endpoint.username or endpoint.password or endpoint.path or endpoint.query or endpoint.fragment):
                raise RuntimeError('Only a loopback port-forward is allowed')
            base = args.base_url
        deadline = time.monotonic() + 90
        while True:
            try:
                if request('/version')[0] == 200:
                    break
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError('Startup timeout')
            time.sleep(.5)
        status, body = request('/tika', marker)
        record('plain_positive', status, status == 200 and marker in body)
        status, body = multipart('')
        record('multipart_positive', status, status == 200 and marker in body)
        status, body = multipart('X-Synthetic: x\r\n y\r\n')
        record('multipart_folded_header_positive', status, status == 200 and marker in body)
        for name, extension in [('quoted_chunk_positive', b'fixture="ok"'), ('escaped_quote_positive', b'fixture="a\\"b"')]:
            status, body = chunked(extension)
            record(name, status, status == 200 and marker in body)
        for name, extension in [('unterminated_quote_rejected', b'fixture="unclosed'), ('trailing_escape_rejected', b'fixture="unclosed\\')]:
            status, body = chunked(extension)
            record(name, status, status == 400, body)
        status, body = multipart(''.join('X-Synthetic-' + str(i) + ': v\r\n' for i in range(510)))
        record('multipart_header_count_bounded', status, status in (400, 413), body)
        # Empty values keep the aggregate value below the size bound: this
        # specifically checks the count oracle for repeated header names.
        status, body = multipart('X-Synthetic:\r\n' * 510)
        record('multipart_repeated_header_count_bounded', status, status in (400, 413), body)
        status, _ = multipart('X-Synthetic: v\r\n' + ' v\r\n' * 510)
        record('multipart_folded_header_size_bounded', status, status in (400, 413))
        status, body = request('/tika', marker)
        record('service_still_usable', status, status == 200 and marker in body)
    finally:
        if container:
            if args.diagnostics:
                args.diagnostics.write_text(docker('logs', token))
            docker('rm', '-f', token)
        if network:
            docker('network', 'rm', token)
    print(json.dumps({'checks': checks, 'ok': all(c['status'] == 'PASS' for c in checks),
                      'syntheticBodyLimitBytes': 16384, 'ownedResourcesRemoved': True}, indent=2))
    return 0 if all(c['status'] == 'PASS' for c in checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
