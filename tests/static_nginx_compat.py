# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Compare real proxy configurations using owned containers and synthetic S3."""
import argparse
import hashlib
import json
import pathlib
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

SERVER = r'''
from http.server import BaseHTTPRequestHandler,HTTPServer
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def handle_request(self,head=False):
  path=self.path.split('?')[0]
  files={'/alpha-web/index.html':(b'<html>alpha</html>','text/html'),'/beta-web/index.html':(b'<html>beta</html>','text/html'),'/alpha-web/app.css':(b'a{color:red}','text/css'),'/alpha-web/plain.txt':(b'0123456789','text/plain')}
  if path.startswith('/private/'):
   status,body,kind=403,b'denied','text/plain'
  elif self.command=='POST':
   n=int(self.headers.get('Content-Length','0'));assert n<65536
   status,body,kind=200,self.rfile.read(n),'application/octet-stream'
  elif path in files:
   body,kind=files[path];status=200
  else:status,body,kind=404,b'missing','text/plain'
  self.send_response(206 if status==200 and self.headers.get('Range')=='bytes=2-5' else status)
  if status==200 and self.headers.get('Range')=='bytes=2-5':
   self.send_header('Content-Range','bytes 2-5/'+str(len(body)));body=body[2:6]
  self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.send_header('ETag','"synthetic"');self.end_headers()
  if not head:self.wfile.write(body)
 def do_GET(self):self.handle_request()
 def do_HEAD(self):self.handle_request(True)
 def do_POST(self):self.handle_request()
HTTPServer(('0.0.0.0',9000),Handler).serve_forever()
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=pathlib.Path, required=True)
    parser.add_argument('--base', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--fixture-image', required=True)
    args = parser.parse_args()
    data = json.loads(args.config.read_text())['data']
    assert set(data) == {'nginx.conf', 'default.conf'}
    assert 'proxy_pass http://seaweedfs.openserverless.svc.cluster.local:9000' in data['default.conf']
    name = 'ops-static-' + secrets.token_hex(6)
    containers = []

    def docker(*parts):
        return subprocess.check_output(['docker', *parts], text=True).strip()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *unused):
            return None

    client = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    rows = []
    redirect_path = None
    docker('network', 'create', '--internal', name)
    try:
        with tempfile.TemporaryDirectory(prefix='ops-static-') as directory:
            root = pathlib.Path(directory)
            for file, content in data.items():
                # Restrict test worker count; routing configuration is untouched.
                if file == 'nginx.conf':
                    content = content.replace('worker_processes  auto;', 'worker_processes  1;')
                (root/file).write_text(content)
                (root/file).chmod(0o644)
            server = root/'server.py'; server.write_text(SERVER); server.chmod(0o644)
            containers.append(docker('run','-d','--rm','--pull=never','--network',name,
                '--network-alias','seaweedfs.openserverless.svc.cluster.local','--read-only',
                '--cap-drop=ALL','--security-opt=no-new-privileges','--memory','128m','--pids-limit','32',
                '-v',str(server)+':/server.py:ro','--entrypoint','python3',args.fixture_image,'/server.py'))
            for label, image in [('base',args.base),('candidate',args.candidate)]:
                cid = docker('run','-d','--rm','--pull=never','--network',name,'--read-only',
                    '--tmpfs','/tmp:rw,nosuid,size=32m','--cap-drop=ALL','--security-opt=no-new-privileges',
                    '--memory','256m','--cpus','1','--pids-limit','64',
                    '-v',str(root/'nginx.conf')+':/etc/nginx/nginx.conf:ro',
                    '-v',str(root/'default.conf')+':/etc/nginx/conf.d/default.conf:ro',image)
                containers.append(cid)
                info=json.loads(docker('inspect',cid))[0]
                assert info['Config']['User']=='101'
                address=info['NetworkSettings']['Networks'][name]['IPAddress']
                def request(path,method='GET',headers=None,body=None):
                    req=urllib.request.Request('http://'+address+':8080'+path,method=method,headers=headers or {},data=body)
                    try: response=client.open(req,timeout=5)
                    except urllib.error.HTTPError as error: response=error
                    with response:return response.status,response.read(65537),dict(response.headers)
                for _ in range(50):
                    try:
                        if request('/alpha-web/')[0]==200:break
                    except (urllib.error.URLError,ConnectionError):pass
                    time.sleep(.1)
                else:raise RuntimeError('synthetic proxy did not become ready: '+docker('logs',cid))
                for tenant in ['alpha','beta']:
                    status,body,headers=request('/'+tenant+'-web/')
                    assert status==200 and body==('<html>'+tenant+'</html>').encode()
                    rows.append({'image':label,'check':tenant+'-index','status':'PASS'})
                status,body,headers=request('/alpha-web/plain.txt','HEAD')
                assert status==200 and body==b'' and headers['Content-Length']=='10'
                rows.append({'image':label,'check':'head','status':'PASS'})
                status,body,headers=request('/alpha-web/plain.txt',headers={'Range':'bytes=2-5'})
                assert status==206 and body==b'2345' and headers['Content-Range']=='bytes 2-5/10'
                rows.append({'image':label,'check':'range','status':'PASS'})
                status,body,headers=request('/alpha-web/app.css')
                assert status==200 and body==b'a{color:red}' and headers['Content-Type']=='text/css'
                rows.append({'image':label,'check':'mime','status':'PASS'})
                status,body,headers=request('/alpha-web')
                assert status in (200,301), (label,status,body[:100])
                actual_path=urllib.parse.urlsplit(headers.get('Location','')).path
                actual=(status,body if status==200 else actual_path)
                # Keep the deployed rewrite's existing slash behavior.
                if label=='base':
                    if status==200:assert body==b'<html>alpha</html>'
                    else:assert actual_path.endswith('/alpha-web/'), actual_path
                    redirect_path=actual
                else:assert actual==redirect_path
                rows.append({'image':label,'check':'slash-normalization','status':'PASS'})
                status,body,headers=request('/alpha-web/deep/route/')
                assert status==200 and body==b'<html>alpha</html>'
                rows.append({'image':label,'check':'spa-fallback','status':'PASS'})
                assert request('/unknown-bucket/missing.js')[0]==404
                rows.append({'image':label,'check':'missing','status':'PASS'})
                assert request('/private/secret.txt')[0]==403
                rows.append({'image':label,'check':'upstream-denial','status':'PASS'})
                status,body,headers=request('/alpha-web/echo.bin','POST',body=b'owned-synthetic')
                assert status==200 and body==b'owned-synthetic'
                rows.append({'image':label,'check':'request-body','status':'PASS'})
                docker('stop',cid);containers.remove(cid)
        print(json.dumps({'checks':rows,'configSha256':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in data.items()},'baselineChanged':False}))
    finally:
        for cid in reversed(containers):
            subprocess.run(['docker','stop',cid],check=True,stdout=subprocess.DEVNULL)
        docker('network','rm',name)


if __name__ == '__main__':
    main()
