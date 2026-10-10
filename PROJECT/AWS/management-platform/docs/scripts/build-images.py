#!/usr/bin/env python3
"""Build/push per-service linux/amd64 images from a local Docker/buildx host."""
import shutil,tempfile
import argparse,json,pathlib,subprocess,re
r=pathlib.Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser();p.add_argument('--tag',required=True);p.add_argument('--only',nargs='*');a=p.parse_args()
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',a.tag):raise SystemExit('Invalid image tag')
m=json.loads((r/'.ha-work/deployment.json').read_text())
registry=next(iter(m['ecr'].values())).split('/')[0]
token=subprocess.check_output(['aws','ecr','get-login-password','--region',m['region']])
subprocess.run(['docker','login','--username','AWS','--password-stdin',registry],input=token,check=True)
files={'management-server':'management-plane/management-server/Dockerfile','gateway':'gateway/Dockerfile','application-service':'application-plane/app-service/Dockerfile','global-lb':'global-lb/Dockerfile','grdc':'grdc/Dockerfile','rule-sync':'management-plane/observability/rule-sync/Dockerfile'}
selected=a.only or list(files)
if set(selected)-files.keys():raise SystemExit('Unknown image component')
tags_path=r/'.ha-work/image-tags.json'
tags=json.loads(tags_path.read_text()) if tags_path.exists() else {}
context=pathlib.Path(tempfile.mkdtemp(prefix='mp-build-'))
try:
 for name in ['pom.xml','platform-common','global-lb','management-plane','gateway','grdc','application-plane']:
  source=r/name
  if source.is_dir():shutil.copytree(source,context/name,ignore=shutil.ignore_patterns('deploy','target','.git','.idea','*.iml','*.pem','*.key','*.tfstate*','__pycache__'))
  else:shutil.copy2(source,context/name)
 for name in selected:
  subprocess.run(['docker','buildx','build','--platform','linux/amd64','--push','-f',str(context/files[name]),'-t',m['ecr'][name]+':'+a.tag,str(context)],check=True)
  tags[name]=a.tag
  tags_path.write_text(json.dumps(tags,indent=2)+'\n')

finally:
 shutil.rmtree(context)
