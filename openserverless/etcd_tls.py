# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#

import base64
import contextlib
import json
import pathlib
import subprocess
import tempfile

import requests


def _openssl(*args):
    result = subprocess.run(['openssl', *args], capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Could not generate etcd security material')


def generate_security(name, namespace):
    """Generate installation-specific keys. The client has no CN-based identity."""
    with tempfile.TemporaryDirectory(prefix='ops-etcd-pki-') as directory:
        p = pathlib.Path(directory)
        _openssl('req', '-x509', '-newkey', 'rsa:3072', '-nodes', '-sha256',
                 '-days', '3650', '-subj', '/CN=OPS Advanced etcd CA',
                 '-keyout', str(p/'ca.key'), '-out', str(p/'ca.crt'))
        for role in ['server', 'client']:
            subject = '/CN=OPS Advanced etcd' if role == 'server' else '/O=OPS Advanced etcd client'
            _openssl('req', '-new', '-newkey', 'rsa:2048', '-nodes',
                     '-subj', subject, '-keyout', str(p/(role+'.key')),
                     '-out', str(p/(role+'.csr')))
            extensions = 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n'
            if role == 'server':
                dns = [name, name+'.'+namespace+'.svc', name+'.'+namespace+'.svc.cluster.local',
                       '*.'+name+'-headless.'+namespace+'.svc.cluster.local', 'localhost']
                extensions += 'extendedKeyUsage=serverAuth,clientAuth\nsubjectAltName='+','.join('DNS:'+d for d in dns)+',IP:127.0.0.1\n'
            else:
                extensions += 'extendedKeyUsage=clientAuth\n'
            (p/(role+'.ext')).write_text(extensions)
            _openssl('x509', '-req', '-in', str(p/(role+'.csr')), '-CA', str(p/'ca.crt'),
                     '-CAkey', str(p/'ca.key'), '-CAcreateserial', '-sha256', '-days', '730',
                     '-extfile', str(p/(role+'.ext')), '-out', str(p/(role+'.crt')))
        _openssl('genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072',
                 '-out', str(p/'jwt.key'))
        def values(role):
            return {'ca.crt':(p/'ca.crt').read_text(), 'tls.crt':(p/(role+'.crt')).read_text(),
                    'tls.key':(p/(role+'.key')).read_text()}
        server, client = values('server'), values('client')
        server['jwt.key'] = (p/'jwt.key').read_text()
        return server, client


def apply_private(spec, namespace):
    """Unlike the generic kubectl wrapper, never log a failing Secret payload."""
    result = subprocess.run(['kubectl', '-n', namespace, 'apply', '-f', '-'],
                            input=json.dumps(spec), text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Could not apply etcd security resources')


@contextlib.contextmanager
def gateway(endpoint, material):
    if not endpoint.startswith('https://'):
        raise ValueError('etcd administration requires TLS')
    with tempfile.TemporaryDirectory(prefix='ops-etcd-client-') as directory:
        p = pathlib.Path(directory)
        for key in ['ca.crt', 'tls.crt', 'tls.key']:
            target=p/key;target.write_bytes(base64.b64decode(material[key]));target.chmod(0o600)
        with requests.Session() as session:
            session.trust_env = False
            session.verify = str(p/'ca.crt')
            session.cert = (str(p/'tls.crt'), str(p/'tls.key'))
            yield session
