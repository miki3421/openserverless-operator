# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Exercise synthetic Tika 3.x contracts in an owned internal Docker network."""
import argparse
import concurrent.futures
import io
import ipaddress
import json
import subprocess
import struct
import tempfile
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
import zipfile
import zlib


def document_png():
    # A deterministic 5x7 synthetic OCR fixture, without host font dependencies.
    glyphs = {'H': ['10001','10001','10001','11111','10001','10001','10001'],
              'E': ['11111','10000','10000','11110','10000','10000','11111'],
              'L': ['10000','10000','10000','10000','10000','10000','11111'],
              'O': ['01110','10001','10001','10001','10001','10001','01110'],
              'W': ['10001','10001','10001','10101','10101','10101','01010'],
              'R': ['11110','10001','10001','11110','10100','10010','10001'],
              'D': ['11110','10001','10001','10001','10001','10001','11110'],
              ' ': ['00000'] * 7}
    width, height, scale = 480, 96, 6
    pixels = [bytearray([255] * width) for _ in range(height)]
    for pos, char in enumerate('HELLO WORLD'):
        for y, row in enumerate(glyphs[char]):
            for x, bit in enumerate(row):
                if bit == '1':
                    for dy in range(scale):
                        for dx in range(scale): pixels[24+y*scale+dy][24+pos*36+x*scale+dx] = 0
    def chunk(name, payload):
        return struct.pack('>I', len(payload)) + name + payload + struct.pack('>I', zlib.crc32(name + payload))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(b''.join(b'\0' + row for row in pixels))) + chunk(b'IEND', b''))


def document_pdf(text):
    content = ('BT /F1 18 Tf 30 700 Td (' + text + ') Tj ET').encode()
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n' + content + b'\nendstream']
    data = b'%PDF-1.4\n'; offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data)); data += str(i).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
    start = len(data)
    data += b'xref\n0 6\n0000000000 65535 f \n' + b''.join(('%010d 00000 n \n' % n).encode() for n in offsets[1:])
    return data + b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n' + str(start).encode() + b'\n%%EOF\n'


def document_docx(text):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml', '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>' + text + '</w:t></w:r></w:p></w:body></w:document>')
    return out.getvalue()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument('--image')
    target.add_argument('--base-url', help='An explicitly prepared loopback port-forward only')
    p.add_argument('--version', required=True)
    a = p.parse_args()
    token = 'ops-tika-qual-' + uuid.uuid4().hex[:10]
    network = False; container = False; checks = []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = a.base_url or ''
    if a.base_url:
        endpoint = urllib.parse.urlsplit(a.base_url)
        if (endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or not endpoint.port or
                endpoint.username or endpoint.password or endpoint.path or endpoint.query or endpoint.fragment):
            raise RuntimeError('Only a loopback port-forward endpoint is allowed')

    def docker(*args):
        return subprocess.check_output(['docker', *args], stderr=subprocess.STDOUT, text=True).strip()

    def check(ok, name):
        if not ok: raise RuntimeError(name)
        checks.append(name); print(json.dumps({'check': name, 'status': 'PASS'}), flush=True)

    def request(path, data=None, content_type=None, accept='text/plain', method=None):
        headers = {'Accept': accept}
        if content_type: headers['Content-Type'] = content_type
        try: r = opener.open(urllib.request.Request(base + path, data=data, headers=headers, method=method), timeout=90)
        except urllib.error.HTTPError as e: r = e
        with r: return r.status, dict(r.headers), r.read()

    def parse(name, data, content_type, marker):
        status, headers, out = request('/tika', data, content_type, method='PUT')
        if status != 200 or marker not in out.decode('utf-8'):
            print(json.dumps({'fixture': name, 'status': status, 'response': out.decode('utf-8', errors='replace')[:500]}), flush=True)
        check(status == 200 and marker in out.decode('utf-8'), name + '_text')
        status, headers, out = request('/rmeta/text', data, content_type, 'application/json', 'PUT')
        result = json.loads(out)
        check(status == 200 and isinstance(result, list) and any(marker in r.get('X-TIKA:content', '') for r in result), name + '_metadata')

    try:
        if a.image:
            docker('network', 'create', '--internal', '--label', 'ops.qualifier=' + token, token); network = True
            docker('run', '-d', '--name', token, '--pull=never', '--network', token, '--read-only',
                   '--cap-drop=ALL', '--security-opt=no-new-privileges', '--memory=2g', '--cpus=2', '--pids-limit=512',
                   '--tmpfs', '/tmp:rw,exec,nosuid,size=512m', '-e', 'JAVA_TOOL_OPTIONS=-Xms128m -Xmx768m -Djava.awt.headless=true', a.image)
            container = True
            ins = json.loads(docker('inspect', token))[0]
            address = ins['NetworkSettings']['Networks'][token]['IPAddress']
            if not ipaddress.ip_address(address).is_private or any(ins['NetworkSettings']['Ports'].values()): raise RuntimeError('Unexpected network')
            base = 'http://' + address + ':9998'
        deadline = time.monotonic() + 90
        while True:
            try:
                status, _, out = request('/version')
                if status == 200: break
            except OSError: pass
            if time.monotonic() > deadline: raise RuntimeError('Tika startup timeout')
            time.sleep(.5)
        check(a.version in out.decode(), 'version')
        marker = 'OPS synthetic document 20261106'
        parse('plain', marker.encode(), 'text/plain', marker)
        parse('html', ('<!doctype html><html><head><title>fixture</title></head><body><p>' + marker + '</p></body></html>').encode(), 'text/html', marker)
        parse('pdf', document_pdf(marker), 'application/pdf', marker)
        parse('docx', document_docx(marker), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', marker)
        parse('rtf', ('{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Arial;}}\\f0\\fs24 ' + marker + '\\par}').encode(), 'application/rtf', marker)
        parse('png_ocr', document_png(), 'image/png', 'HELLO WORLD')
        status, _, out = request('/detect/stream', document_pdf(marker), 'application/octet-stream', method='PUT')
        check(status == 200 and 'application/pdf' in out.decode(), 'legacy_detect_path')
        status, _, out = request('/language/stream', b'This is a long English sentence about the weather and the blue sky. We are testing the language of this document.', 'text/plain', method='PUT')
        check(status == 200 and out.decode().strip() == 'en', 'legacy_language_path')
        status, _, out = request('/tika', marker.encode(), 'text/plain', 'text/html', 'PUT')
        check(status == 200 and '<html' in out.decode() and marker in out.decode(), 'html_accept_contract')
        def concurrent_request(i):
            unique = 'OPS unique request ' + str(i)
            status, _, out = request('/tika', unique.encode(), 'text/plain', method='PUT')
            return status == 200 and unique in out.decode() and all(('OPS unique request ' + str(j)) not in out.decode() for j in range(4) if j != i)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(concurrent_request, range(4)))
        check(all(results), 'four_concurrent_requests_isolated')
    finally:
        if container: docker('rm', '-f', token)
        if network: docker('network', 'rm', token)
    print(json.dumps({'passed': len(checks), 'image': a.image, 'target': 'owned_container' if a.image else 'loopback_port_forward',
                      'ownedResourcesRemoved': True}))


if __name__ == '__main__': main()
