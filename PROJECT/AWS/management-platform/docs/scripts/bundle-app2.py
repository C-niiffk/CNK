#!/usr/bin/env python3
"""Extract the same immutable application image into a native RHEL JAR bundle."""
import argparse,hashlib,json,pathlib,shutil,subprocess,tarfile,tempfile
r=pathlib.Path(__file__).resolve().parents[2];w=r/'.ha-work'
p=argparse.ArgumentParser();p.add_argument('--version',required=True);a=p.parse_args()
import re
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}',a.version):raise SystemExit('Invalid version')
m=json.loads((w/'deployment.json').read_text());tags=json.loads((w/'image-tags.json').read_text())
image=m['ecr']['application-service']+':'+tags['application-service']
def run(*cmd):return subprocess.check_output(list(cmd),text=True).strip()
run('docker','pull','--platform','linux/amd64',image)
with tempfile.TemporaryDirectory() as d:
 b=pathlib.Path(d);(b/'payload').mkdir();cid=run('docker','create','--platform','linux/amd64',image)
 try:
  run('docker','cp',cid+':/app.jar',str(b/'payload/app.jar'))
  run('docker','cp',cid+':/opt/agent',str(b/'payload/skywalking-agent'))
 finally:run('docker','rm',cid)
 (b/'payload/VERSION').write_text(a.version+'\n')
 for rel in ['application-plane/app-service/deploy/ec2','application-plane/agent-client/deploy/ec2']:
  shutil.copytree(r/rel,b/rel)
 (b/'docs/scripts').mkdir(parents=True)
 for name in ['install-app2.sh','configure-app2.py','prepare-app2-volume.sh']:shutil.copy2(r/'docs/scripts'/name,b/'docs/scripts'/name)
 (b/'SHA256SUMS').write_text(''.join(hashlib.sha256(f.read_bytes()).hexdigest()+'  '+str(f.relative_to(b))+'\n' for f in sorted(b.rglob('*')) if f.is_file()))
 out=w/('app2-'+a.version+'.tgz')
 with tarfile.open(out,'w:gz') as t:
  for child in b.iterdir():t.add(child,arcname=child.name)
 print(out)
