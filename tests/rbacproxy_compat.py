# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
"""Exercise cached proxy images against an owned, synthetic Kubernetes reviewer.

No real kubeconfig, tokens or cluster endpoints are consumed. The temporary
internal network, containers and synthetic TLS key are removed at completion.
"""
import argparse
import json
import pathlib
import secrets
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

SERVER = r'''
import json
from http.server import BaseHTTPRequestHandler,HTTPServer
events=[]
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def reply(self,code,data):
  body=json.dumps(data).encode() if isinstance(data,dict) else data
  self.send_response(code);self.send_header('Content-Type','application/json' if isinstance(data,dict) else 'text/plain');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def do_GET(self):
  if self.path=='/_events':return self.reply(200,{'events':events})
  events.append({'upstream':self.path,'method':self.command});self.reply(200,b'owned_metric 1\n')
 def do_POST(self):
  size=int(self.headers.get('Content-Length','0'));assert size<65536
  data=json.loads(self.rfile.read(size) or b'{}')
  if self.path=='/apis/authentication.k8s.io/v1/tokenreviews':
   token=data['spec']['token']
   if token=='fixture-api-error':return self.reply(500,{'kind':'Status','apiVersion':'v1','status':'Failure','reason':'InternalError','code':500})
   users={'fixture-reader':'reader','fixture-denied':'denied'}
   status={'authenticated':token in users}
   if token in users:status['user']={'username':users[token],'uid':users[token],'groups':['synthetic']}
   events.append({'review':'authentication','authenticated':status['authenticated']})
   return self.reply(201,{'apiVersion':'authentication.k8s.io/v1','kind':'TokenReview','status':status})
  if self.path=='/apis/authorization.k8s.io/v1/subjectaccessreviews':
   spec=data['spec'];attrs=spec.get('nonResourceAttributes',{})
   allowed=spec.get('user')=='reader' and attrs=={'path':'/metrics','verb':'get'}
   events.append({'review':'authorization','user':spec.get('user'),'attributes':attrs,'allowed':allowed})
   return self.reply(201,{'apiVersion':'authorization.k8s.io/v1','kind':'SubjectAccessReview','status':{'allowed':allowed}})
  events.append({'upstream':self.path,'method':self.command});self.reply(200,b'owned_metric 1\n')
HTTPServer(('0.0.0.0',9000),Handler).serve_forever()
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--fixture-image', required=True)
    args = parser.parse_args()
    # Embedded code is validated before any Docker mutation.
    compile(SERVER, '<synthetic-reviewer>', 'exec')
    name = 'ops-rbac-' + secrets.token_hex(6)
    containers = []

    def docker(*parts):
        return subprocess.check_output(['docker', *parts], text=True).strip()

    def request(client, url, token=None, method='GET', headers=None):
        headers = dict(headers or {})
        if token:
            headers['Authorization'] = 'Bearer ' + token
        req = urllib.request.Request(url, method=method, headers=headers)
        try:
            response = client.open(req, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.read(65537)

    docker('network', 'create', '--internal', name)
    try:
        with tempfile.TemporaryDirectory(prefix='ops-rbac-') as directory:
            root = pathlib.Path(directory)
            root.chmod(0o755)  # Disposable synthetic files must be readable by the non-root proxy.
            # All credentials here are disposable fixtures, never real secrets.
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes',
                '-keyout',str(root/'key.pem'),'-out',str(root/'cert.pem'),'-days','1',
                '-subj','/CN=synthetic-proxy'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            (root/'key.pem').chmod(0o644)
            (root/'server.py').write_text(SERVER)
            kubeconfig={'apiVersion':'v1','kind':'Config','clusters':[{'name':'fixture','cluster':{'server':'http://reviewer:9000'}}],
                'contexts':[{'name':'fixture','context':{'cluster':'fixture','user':'fixture'}}],
                'current-context':'fixture','users':[{'name':'fixture','user':{}}]}
            (root/'kubeconfig.json').write_text(json.dumps(kubeconfig))
            for file in root.iterdir():
                file.chmod(0o644)
            containers.append(docker('run','-d','--rm','--pull=never','--network',name,
                '--network-alias','reviewer','--read-only','--cap-drop=ALL',
                '--security-opt=no-new-privileges','--memory','128m','--pids-limit','32',
                '-v',str(root/'server.py')+':/server.py:ro','--entrypoint','python3',args.fixture_image,'/server.py'))
            server_info=json.loads(docker('inspect',containers[-1]))[0]
            server_ip=server_info['NetworkSettings']['Networks'][name]['IPAddress']
            plain=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            tls=ssl.create_default_context(cafile=str(root/'cert.pem'))
            # Trust only this generated cert; bridge IP is assigned by Docker.
            tls.check_hostname=False
            client=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=tls))
            rows=[]
            for label,image in [('base',args.base),('candidate',args.candidate)]:
                cid=docker('run','-d','--pull=never','--network',name,'--read-only',
                    '--tmpfs','/tmp:rw,nosuid,size=16m','--cap-drop=ALL',
                    '--security-opt=no-new-privileges','--memory','128m','--cpus','1','--pids-limit','64',
                    '-v',str(root)+':/fixture:ro',image,'--secure-listen-address=0.0.0.0:8443',
                    '--upstream=http://reviewer:9000/','--logtostderr=true','--v=0',
                    '--kubeconfig=/fixture/kubeconfig.json','--tls-cert-file=/fixture/cert.pem','--tls-private-key-file=/fixture/key.pem')
                containers.append(cid)
                info=json.loads(docker('inspect',cid))[0]
                assert info['Config']['User'] not in ('','0','root')
                url='https://'+info['NetworkSettings']['Networks'][name]['IPAddress']+':8443'
                for _ in range(60):
                    try:
                        if request(client,url+'/metrics')[0]==401:break
                    except (urllib.error.URLError,ConnectionError):pass
                    time.sleep(.1)
                else:raise RuntimeError('proxy did not start: '+docker('logs',cid))
                cases=[('anonymous',None,'GET','/metrics',{},401),
                    ('invalid','fixture-invalid','GET','/metrics',{},401),
                    ('unprivileged','fixture-denied','GET','/metrics',{},403),
                    ('reader','fixture-reader','GET','/metrics',{},200),
                    ('wrong-verb','fixture-reader','POST','/metrics',{},403),
                    ('wrong-path','fixture-reader','GET','/other',{},403),
                    ('spoofed-header',None,'GET','/metrics',{'X-Remote-User':'reader'},401),
                    ('denied-with-spoof','fixture-denied','GET','/metrics',{'X-Remote-User':'reader'},403),
                    ('reviewer-failure','fixture-api-error','GET','/metrics',{},401)]
                for check,token,method,path,headers,expected in cases:
                    events_before=json.loads(request(plain,'http://'+server_ip+':9000/_events')[1])['events']
                    status,body=request(client,url+path,token,method,headers)
                    assert status==expected,(label,check,status)
                    events=json.loads(request(plain,'http://'+server_ip+':9000/_events')[1])['events'][len(events_before):]
                    upstream=[event for event in events if 'upstream' in event]
                    if expected==200:
                        assert body==b'owned_metric 1\n' and upstream==[{'upstream':'/metrics','method':'GET'}]
                    else:assert not upstream,(label,check,'denied request reached upstream')
                    rows.append({'image':label,'check':check,'http':status,'status':'PASS'})
                docker('rm','-f',cid);containers.remove(cid)
            print(json.dumps({'checks':rows,'syntheticReviewServer':True,'realCredentialsUsed':False}))
    finally:
        failures=[]
        for cid in reversed(containers):
            result=subprocess.run(['docker','rm','-f',cid],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
            if result.returncode and 'No such container' not in result.stderr:
                failures.append(cid)
        docker('network','rm',name)
        if failures:
            raise RuntimeError('Failed to remove owned fixture containers: '+','.join(failures))


if __name__ == '__main__':
    main()
