# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
from pathlib import Path
import json,os,secrets,subprocess,sys,time,uuid
out=Path(sys.argv[1]);key=uuid.uuid4().hex[:12];owner='adv-perl-qualified-'+key
volume='adv-perl-data-'+key;created=[]
parent='localhost/adv-pg-parent:20261128-eece1d6ad720';candidate='localhost/adv-postgres:20261128-perl-u4'
report={'schema':'adv.postgres-retained-data-contract/v1','parent':parent,'candidate':candidate,'live_data_used':False}
def cmd(args,stdin=None,ok=True,env=None,timeout=60):
 r=subprocess.run(['docker',*args],input=stdin,capture_output=True,text=True,env=env,timeout=timeout)
 if ok and r.returncode:raise RuntimeError(repr(args[:4])+'\n'+r.stderr[-3500:])
 return r
def sql(name,text,database='postgres'):
 return cmd(['exec','-i',name,'psql','-X','-qAt','-U','postgres','-d',database,'-v','ON_ERROR_STOP=1'],stdin=text).stdout.strip()
def start(image,label):
 name='adv-perl-'+label+'-'+key
 env={**os.environ,'POSTGRES_PASSWORD':secrets.token_urlsafe(30)}
 cmd(['run','-d','--pull=never','--name',name,'--label','adv.security.owner='+owner,
  '--network=none','--read-only','--user=postgres','--cap-drop=ALL','--security-opt=no-new-privileges',
  '--memory=768m','--cpus=1','--tmpfs','/tmp:rw,mode=1777',
  '--tmpfs','/var/run/postgresql:rw,uid=999,gid=999,mode=3775',
  '--mount','type=volume,src='+volume+',dst=/var/lib/postgresql',
  '-e','POSTGRES_PASSWORD','-e','POSTGRES_INITDB_ARGS=--encoding=UTF8 --locale=C',image],env=env)
 created.append(name);deadline=time.monotonic()+40
 while time.monotonic()<deadline:
  if cmd(['exec',name,'pg_isready','-U','postgres'],ok=False,timeout=8).returncode==0:return name
  time.sleep(.25)
 raise RuntimeError('Isolated database not ready: '+cmd(['logs',name],ok=False).stdout[-2500:])
def state(name):
 value={'system_identifier':sql(name,'SELECT system_identifier FROM pg_control_system();'),'server_version':sql(name,'SHOW server_version;'),'tenants':{}}
 for tenant in ('adv_alpha','adv_beta'):
  value['tenants'][tenant]=json.loads(sql(name,"SELECT json_build_object('rows',(SELECT json_agg(t) FROM (SELECT id,body,embedding::text FROM retained ORDER BY id) t),'vector_version',(SELECT extversion FROM pg_extension WHERE extname='vector'),'xml',xpath('string(/r/v)','<r><v>valid</v></r>'::xml)::text);",tenant))
 denied=cmd(['exec','-i',name,'psql','-X','-qAt','-U','postgres','-d','adv_alpha','-v','ON_ERROR_STOP=1'],stdin='SET ROLE adv_beta; SELECT * FROM retained;',ok=False)
 assert denied.returncode!=0 and 'permission denied' in denied.stderr
 return value
try:
 cmd(['volume','create','--label','adv.security.owner='+owner,'--name',volume])
 print('starting isolated parent',flush=True);first=start(parent,'parent')
 sql(first,'CREATE ROLE adv_alpha LOGIN; CREATE ROLE adv_beta LOGIN; CREATE DATABASE adv_alpha OWNER adv_alpha; CREATE DATABASE adv_beta OWNER adv_beta;')
 for tenant,vector in [('adv_alpha','[1,2,3]'),('adv_beta','[4,5,6]')]:
  sql(first,"CREATE EXTENSION vector; SET ROLE "+tenant+"; CREATE TABLE retained(id int PRIMARY KEY,body text,embedding vector(3)); INSERT INTO retained VALUES(7,'synthetic-"+tenant+"','"+vector+"');",tenant)
 original=state(first);report['original']=original
 cmd(['stop','-t','10',first]);cmd(['rm',first]);created.remove(first)
 print('starting candidate on same synthetic-data volume',flush=True);second=start(candidate,'candidate')
 restored=state(second);report['restored']=restored
 assert original==restored,(original,restored)
 sql(second,"SET ROLE adv_alpha; INSERT INTO retained VALUES(8,'after-update','[2,3,4]');",'adv_alpha')
 assert float(sql(second,"SELECT embedding <-> '[1,2,4]'::vector FROM retained WHERE id=7;",'adv_alpha'))==1
 report.update(success=True,same_system_identifier=True,rows_and_roles_preserved=True,pgvector_control=True,
  xml_control=True,post_restart_write=True,tenant_isolation=True)
 print('data, server identity, vector/XML and isolation controls passed',flush=True)
except Exception as error:
 report.update(success=False,error=str(error));raise
finally:
 for name in created:
  info=cmd(['inspect',name],ok=False)
  if info.returncode==0:
   assert json.loads(info.stdout)[0]['Config'].get('Labels',{}).get('adv.security.owner')==owner
   logs=cmd(['logs',name],ok=False);(out.parent/(name+'.log')).write_text(logs.stdout+logs.stderr)
   cmd(['rm','-f',name])
 info=cmd(['volume','inspect',volume],ok=False)
 if info.returncode==0:
  assert json.loads(info.stdout)[0]['Labels'].get('adv.security.owner')==owner
  cmd(['volume','rm',volume])
 report['task_containers_and_volume_removed']=True
 out.write_text(json.dumps(report,indent=2)+'\n')
