#!/usr/bin/env python3
"""Install a verified bundle on A then B over SSH carried by SSM. Requires AWS CLI + Session Manager plugin."""
import argparse,json,pathlib,re,shlex,subprocess,time
r=pathlib.Path(__file__).resolve().parents[2];w=r/'.ha-work'
p=argparse.ArgumentParser();p.add_argument('--bundle',required=True);p.add_argument('--key',required=True);p.add_argument('--site',choices=['a','b','both'],default='both');p.add_argument('--initialize-empty',action='store_true');a=p.parse_args()
m=json.loads((w/'deployment.json').read_text());bundle=pathlib.Path(a.bundle).resolve();key=pathlib.Path(a.key).resolve()
if not bundle.is_file() or not key.is_file():raise SystemExit('Missing bundle or SSH private key')
if not (w/'runtime.json').is_file():raise SystemExit('Run deploy.py secrets first')
def run(cmd,capture=False):return subprocess.run(cmd,check=True,text=True,stdout=subprocess.PIPE if capture else None).stdout
def aws(*args):return json.loads(run(['aws','--region',m['region'],'--no-cli-pager','--output','json',*args],True))
from remote import connection
for site in ['a','b'] if a.site=='both' else [a.site]:
 opts,target=connection(m,w,key,site)
 remote='/home/ec2-user/ha-deploy-'+str(int(time.time()))
 run(['ssh',*opts,target,'umask 077; mkdir '+shlex.quote(remote)])
 run(['scp',*opts,str(bundle),target+':'+remote+'/bundle.tgz'])
 run(['scp',*opts,str(w/'deployment.json'),target+':'+remote+'/deployment.json'])
 run(['scp',*opts,str(w/'runtime.json'),target+':'+remote+'/runtime.json'])
 run(['ssh',*opts,target,'tar -xzf '+shlex.quote(remote+'/bundle.tgz')+' -C '+shlex.quote(remote)])
 command=['sudo','bash',remote+'/docs/scripts/install-app2.sh',remote+'/deployment.json',remote+'/runtime.json',site]
 if a.initialize_empty:command.append('--initialize-empty')
 run(['ssh',*opts,target,shlex.join(command)])
 run(['ssh',*opts,target,'rm -rf -- '+shlex.quote(remote)])
 print('App2 site',site,'ready; temporary bundle removed')
