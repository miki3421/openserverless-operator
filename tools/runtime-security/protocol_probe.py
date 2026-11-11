#!/usr/bin/env python3
"""Bounded runtime protocol checks in fresh 256 MiB containers; no cluster auth."""
import argparse,base64,io,json,os,secrets,subprocess,time,urllib.error,urllib.request,zipfile
from pathlib import Path

GO='''package main
import ("os"; "runtime")
func Main(args map[string]interface{}) map[string]interface{} { return map[string]interface{}{"ok":true,"marker":args["marker"],"version":runtime.Version(),"uid":os.Getuid()} }
'''
JAVA='''import com.google.gson.JsonObject;
public class AtlasSmoke { public static JsonObject main(JsonObject input) {
 int legacyYear = new java.util.Date(0).getYear(); // javac warning must not fail initialization
 JsonObject r=new JsonObject();r.addProperty("ok",true);r.add("marker",input.get("marker"));r.addProperty("version",System.getProperty("java.version"));return r;
} }
'''
GO_CGO='''package main
/*
#cgo LDFLAGS: -ldl
// Exercise the shipped shared-library ABI without adding development headers
// that were never part of the original runtime contract.
typedef struct sqlite3 sqlite3;
typedef struct sqlite3_stmt sqlite3_stmt;
#include <dlfcn.h>
static void* sqlite_library;
static int sqlite3_open(const char* n, sqlite3** d) {
 sqlite_library=dlopen("libsqlite3.so.0", RTLD_NOW);
 if (!sqlite_library) return -1;
 return ((int(*)(const char*,sqlite3**))dlsym(sqlite_library,"sqlite3_open"))(n,d);
}
static int sqlite3_close(sqlite3* d) {return ((int(*)(sqlite3*))dlsym(sqlite_library,"sqlite3_close"))(d);}
static int sqlite3_prepare_v2(sqlite3* d,const char* q,int n,sqlite3_stmt** s,const char** t) {return ((int(*)(sqlite3*,const char*,int,sqlite3_stmt**,const char**))dlsym(sqlite_library,"sqlite3_prepare_v2"))(d,q,n,s,t);}
static int sqlite3_step(sqlite3_stmt* s) {return ((int(*)(sqlite3_stmt*))dlsym(sqlite_library,"sqlite3_step"))(s);}
static int sqlite3_column_int(sqlite3_stmt* s,int c) {return ((int(*)(sqlite3_stmt*,int))dlsym(sqlite_library,"sqlite3_column_int"))(s,c);}
static int sqlite3_finalize(sqlite3_stmt* s) {return ((int(*)(sqlite3_stmt*))dlsym(sqlite_library,"sqlite3_finalize"))(s);}
static const char* sqlite3_libversion(void) {return ((const char*(*)(void))dlsym(sqlite_library,"sqlite3_libversion"))();}
#define SQLITE_ROW 100
#include <stdlib.h>
*/
import "C"
import ("unsafe";"os";"crypto/sha256";"fmt")
func Main(args map[string]interface{}) map[string]interface{} {
 var db *C.sqlite3; var stmt *C.sqlite3_stmt
 name:=C.CString(":memory:");defer C.free(unsafe.Pointer(name))
 if C.sqlite3_open(name,&db)!=0 { panic("sqlite open") };defer C.sqlite3_close(db)
 q:=C.CString("SELECT 2+2");defer C.free(unsafe.Pointer(q))
 if C.sqlite3_prepare_v2(db,q,-1,&stmt,nil)!=0 { panic("sqlite prepare") };defer C.sqlite3_finalize(stmt)
 if C.sqlite3_step(stmt)!=C.SQLITE_ROW {panic("sqlite row")}
 lib,err:=os.ReadFile("/usr/lib/x86_64-linux-gnu/libsqlite3.so.0");if err!=nil {panic(err)}
 return map[string]interface{}{"ok":true,"sum":int(C.sqlite3_column_int(stmt,0)),"sqlite":C.GoString(C.sqlite3_libversion()),"librarySha256":fmt.Sprintf("%x",sha256.Sum256(lib))}
}
'''

def zipped(files):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for name,value in files.items():z.writestr(name,value)
    return base64.b64encode(out.getvalue()).decode()

def request(base,path,body=None,timeout=70):
    req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as response:return response.status,json.load(response)
    except urllib.error.HTTPError as error:
        raw=error.read()
        try:body=json.loads(raw)
        except ValueError:body={'error':'non-json reply'}
        return error.code,body

def main():
    p=argparse.ArgumentParser();p.add_argument('--go-image',required=True);p.add_argument('--java-image',required=True);p.add_argument('--legacy-jar',type=Path,required=True);p.add_argument('--legacy-go-zip',type=Path);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args();os.umask(0o077);rows=[]
    cases=[
        ('go-source',a.go_image,'main',GO,False,False),
        ('go-zip',a.go_image,'main',zipped({'main.go':GO,'go.mod':'module synthetic\n\ngo 1.21\n'}),True,False),
        ('go-cgo-sqlite',a.go_image,'main',GO_CGO,False,False),
        ('go-invalid',a.go_image,'main','invalid source syntax',False,True),
        ('java-source',a.java_image,'AtlasSmoke',JAVA,False,False),
        ('java-legacy-jar',a.java_image,'AtlasSmoke',base64.b64encode(a.legacy_jar.read_bytes()).decode(),True,False),
        ('java-zip',a.java_image,'AtlasSmoke',zipped({'AtlasSmoke.java':JAVA}),True,False),
        ('java-invalid',a.java_image,'AtlasSmoke','invalid java source syntax',False,True),
    ]
    if a.legacy_go_zip:
        cases.append(('go-legacy-binary',a.go_image,'main',base64.b64encode(a.legacy_go_zip.read_bytes()).decode(),True,False))
    for name,image,entry,code,binary,invalid in cases:
        assert '@sha256:' in image
        container='ops-runtime-qual-'+secrets.token_hex(6)
        jdk_before=None
        try:
            subprocess.run(['docker','run','-d','--name',container,'--memory=256m','--cpus=2','--pids-limit=256','--cap-drop=ALL','--security-opt=no-new-privileges:true','-p','127.0.0.1::8080',image],check=True,stdout=subprocess.DEVNULL)
            info=json.loads(subprocess.check_output(['docker','inspect',container]))[0]
            if name.startswith('java'):
                jdk_before=subprocess.check_output(['docker','exec',container,'sha256sum','/opt/java/openjdk/jre/lib/rt.jar'],text=True).split()[0]
            base='http://127.0.0.1:'+info['NetworkSettings']['Ports']['8080/tcp'][0]['HostPort']
            for _ in range(100):
                try:request(base,'/');break
                except (OSError,ValueError):time.sleep(.1)
            start=time.monotonic();status,reply=request(base,'/init',{'value':{'name':'synthetic','main':entry,'code':code,'binary':binary,'env':{}}})
            duration=round((time.monotonic()-start)*1000,1)
            if invalid:
                assert status>=400,(name,status)
                rows.append({'case':name,'check':'invalid-source-rejected','status':'PASS','httpStatus':status})
                continue
            assert status==200,(name,'init',status,reply)
            assert duration<60000,(name,'default-init-budget',duration)
            rows.append({'case':name,'check':'fresh-init-256MiB','status':'PASS','durationMs':duration})
            for count in range(2):
                marker='synthetic-caffè-☕-'+str(count)
                status,reply=request(base,'/run',{'value':{'marker':marker}})
                assert status==200 and reply.get('ok') is True,(name,'run',status,reply)
                if name=='go-cgo-sqlite':
                    assert reply['sum']==4 and reply['librarySha256']=='f4b32d83fadc4787cabff8a115e4aca3ac808efe9d9521b9630f1d8815f129b5'
                elif name=='java-legacy-jar':assert reply['version']=='1.8.0_512'
                elif name=='go-legacy-binary':assert reply['version']=='go1.26.8' and reply['language']=='go'
                else:
                    assert reply['marker']==marker
                    assert reply['version']==('go1.26.11' if name.startswith('go') else '1.8.0_512')
                    if name.startswith('go'):assert reply['uid']==65534
                rows.append({'case':name,'check':'run-'+str(count+1),'status':'PASS'})
        except Exception:
            with a.report.with_suffix('.failure.private.log').open('w') as log:subprocess.run(['docker','logs',container],stdout=log,stderr=subprocess.STDOUT)
            rows.append({'case':name,'status':'FAIL'})
            raise
        finally:
            try:
                if name.startswith('java') and jdk_before:
                    jdk_after=subprocess.check_output(['docker','exec',container,'sha256sum','/opt/java/openjdk/jre/lib/rt.jar'],text=True).split()[0]
                    rows.append({'case':name,'check':'jdk-integrity','status':'PASS' if jdk_before==jdk_after else 'FAIL'})
            except subprocess.SubprocessError:rows.append({'case':name,'check':'jdk-integrity','status':'FAIL'})
            subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            a.report.write_text(json.dumps({'scope':'Fresh local protocol containers; 256 MiB, two CPUs; no Engine tenant-isolation claim','checks':rows},indent=2)+'\n')
    assert all(row['status']=='PASS' for row in rows)
    print(json.dumps({'status':'PASS','checks':len(rows),'cases':len(cases)}))

if __name__=='__main__':main()
