# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
from pathlib import Path
from hashlib import sha256
import json,subprocess,sys
out=Path(sys.argv[1]);images={'before':'localhost/adv-pg-parent:20261128-eece1d6ad720','after':'localhost/adv-postgres:20261128-perl-u4'}
script=r'''
use strict; use warnings; use Archive::Tar; use File::Temp qw(tempdir); use JSON::PP;
my @results;
for my $case ('relative_escape','absolute_escape','safe_internal') {
 my $root=tempdir(CLEANUP=>1,TMPDIR=>1); mkdir "$root/extract" or die $!;
 open(my $outside,">","$root/outside") or die $!; print $outside "original"; close($outside);
 my $tar=Archive::Tar->new;
 if ($case eq 'safe_internal') {$tar->add_data('target',"legitimate");}
 my $link=$case eq 'absolute_escape' ? "$root/outside" : $case eq 'relative_escape' ? '../outside' : 'target';
 $tar->add_data('link','',{type=>2,linkname=>$link});
 chdir "$root/extract" or die $!;
 my @warnings; local $SIG{__WARN__}=sub {push @warnings,@_;};
 my $ok=eval {$tar->extract();1};my $made=-l 'link'?1:0;my $content='';
 if ($made) {
  if ($case eq 'safe_internal') {open(my $f,'<','link') or die $!;local $/;$content=<$f>;close($f);}
  else {open(my $f,'>','link') or die $!;print $f 'modified';close($f);}
 }
 open(my $o,'<',"$root/outside") or die $!;my $external=do {local $/;<$o>};close($o);
 push @results,{case=>$case,link_created=>$made,outside_modified=>($external ne 'original'?1:0),internal_content=>$content,warnings=>scalar @warnings};
 chdir '/' or die $!;
}
my $regex=qr/^(?:@{[join '|',map {sprintf 'item%05d',$_} (0..65536)]})$/;
my $good='item65536'=~$regex?1:0;my $bad='item65537'=~$regex?1:0;
print encode_json({archive_tar_version=>$Archive::Tar::VERSION,ivsize=>length(pack('j',0)),cases=>\@results,large_regex_control=>($good && !$bad ?1:0)});
'''
def run(image,args,stdin=None):
 cmd=['docker','run','-i','--rm','--pull=never','--network=none','--read-only','--user=postgres','--cap-drop=ALL','--security-opt=no-new-privileges','--memory=512m','--cpus=1','--tmpfs','/tmp:rw,mode=1777','--entrypoint',args[0],image,*args[1:]]
 return subprocess.run(cmd,input=stdin,capture_output=True,text=True,timeout=120,check=True).stdout
report={'schema':'adv.perl-differential-contract/v1','images':images};inventories={}
try:
 for label,image in images.items():
  fmt='$'+'{binary:Package}\\t'+'$'+'{Version}\\t'+'$'+'{Architecture}\\n'
  raw=run(image,['dpkg-query','-W','-f',fmt])
  inventories[label]={line.split('\t')[0]:line.split('\t')[1:] for line in raw.splitlines()}
  assert not run(image,['dpkg','--audit']).strip()
  result=json.loads(run(image,['perl','-'],script));report[label]=result
  print(label,json.dumps(result),flush=True)
  assert result['ivsize']==8 and result['large_regex_control']==1
  safe=next(r for r in result['cases'] if r['case']=='safe_internal')
  assert safe['link_created']==1 and safe['internal_content']=='legitimate' and not safe['outside_modified']
 changed={k:{'before':inventories['before'].get(k),'after':inventories['after'].get(k)} for k in inventories['before'].keys()|inventories['after'].keys() if inventories['before'].get(k)!=inventories['after'].get(k)}
 assert set(changed)=={'perl','perl-base','perl-modules-5.36','libperl5.36:amd64'},changed
 report['package_delta']=changed
 assert all(x['outside_modified']==1 for x in report['before']['cases'] if x['case']!='safe_internal')
 assert all(x['link_created']==0 and x['outside_modified']==0 for x in report['after']['cases'] if x['case']!='safe_internal')
 report['success']=True
finally:
 out.write_text(json.dumps(report,indent=2)+'\n')
