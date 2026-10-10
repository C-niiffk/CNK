#!/usr/bin/env python3
"""Manual deployment. No credentials are printed or committed; all subprocess arguments are arrays."""
import yaml
import argparse,base64,json,os,pathlib,re,secrets,subprocess
ROOT=pathlib.Path(__file__).resolve().parents[2]
WORK=ROOT/'.ha-work'
def run(args,body=None,capture=False):
 return subprocess.run(args,input=body,text=True,check=True,stdout=subprocess.PIPE if capture else None).stdout
def aws(*args):return json.loads(run(['aws','--no-cli-pager','--output','json',*args],capture=True))
def apply(obj):run(['kubectl','apply','-f','-'],json.dumps(obj))
def private(path,text):
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
 with os.fdopen(fd,'w') as f:f.write(text)
 os.chmod(path,0o600)
def read_secret(arn):return json.loads(aws('secretsmanager','get-secret-value','--secret-id',arn)['SecretString'])
parser=argparse.ArgumentParser()
parser.add_argument('action',choices=['secrets','render','apply','lbc'])
parser.add_argument('--metadata',default=str(WORK/'deployment.json'))
parser.add_argument('--tag',default='ha-v1')
parser.add_argument('--only',choices=['grdc','observability','agents','management','gateway','app1','routing'])
args=parser.parse_args()
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',args.tag):raise SystemExit('Invalid image tag')
meta=json.loads(pathlib.Path(args.metadata).read_text());os.environ['AWS_DEFAULT_REGION']=meta['region']
WORK.mkdir(exist_ok=True,mode=0o700);os.chmod(WORK,0o700)
if 'vpc_cidr' not in meta:
 meta['vpc_cidr']=aws('ec2','describe-vpcs','--vpc-ids',meta['vpc_id'])['Vpcs'][0]['CidrBlock']
 private(pathlib.Path(args.metadata),json.dumps(meta,indent=2))
if set(meta['app2'])!={'a','b'}:raise SystemExit('Two RHEL instances required: set deploy_ec2_apps=true and apply Terraform first')
ns_files=sorted((ROOT/'global-lb/deploy/k8s').glob('namespace-*.yaml'))
if args.action=='secrets':
 for ns_file in ns_files:apply(yaml.safe_load(ns_file.read_text()))
 try: secret=read_secret(meta['runtime_secret_arn'])
 except subprocess.CalledProcessError:
  # Distinguish an empty new secret from an IAM/network error.
  desc=aws('secretsmanager','describe-secret','--secret-id',meta['runtime_secret_arn'])
  if desc.get('VersionIdsToStages'):raise
  secret={}
 for k in ['MGMT_DB_PASSWORD','GRDC_A_DB_PASSWORD','GRDC_B_DB_PASSWORD','APP2_DB_PASSWORD','NACOS_PASSWORD','ADMIN_PASSWORD','NACOS_IDENTITY_VALUE']:
  secret.setdefault(k,secrets.token_hex(24))
 for k in ['JWT_SECRET','NACOS_AUTH_TOKEN']:secret.setdefault(k,base64.b64encode(secrets.token_bytes(48)).decode())
 secret['REDIS_PASSWORD']=aws('secretsmanager','get-secret-value','--secret-id',meta['redis_secret_arn'])['SecretString']
 private(WORK/'runtime.json',json.dumps(secret))
 aws('secretsmanager','put-secret-value','--secret-id',meta['runtime_secret_arn'],'--secret-string','file://'+str(WORK/'runtime.json'))
 for ns in ['platform','grdc','gateway','applications','observability']:
  apply({'apiVersion':'v1','kind':'Secret','metadata':{'name':'runtime','namespace':ns},'type':'Opaque','stringData':secret})
 print('Runtime credentials saved; existing values preserved. Local file: .ha-work/runtime.json (0600)')
 raise SystemExit
if args.action=='lbc':
 run(['helm','repo','add','eks','https://aws.github.io/eks-charts'])
 run(['helm','repo','update'])
 run(['helm','upgrade','--install','aws-load-balancer-controller','eks/aws-load-balancer-controller','--version','1.14.0','-n','kube-system','-f',str(ROOT/'global-lb/deploy/helm/lbc-values.yaml'),'--set','clusterName='+meta['cluster_name'],'--set','region='+meta['region'],'--set','vpcId='+meta['vpc_id'],'--set','serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn='+meta['pod_roles']['load-balancer-controller'],'--wait','--timeout','10m'])
 patch={'spec':{'replicas':2,'template':{'spec':{'affinity':{'podAntiAffinity':{'requiredDuringSchedulingIgnoredDuringExecution':[{'labelSelector':{'matchLabels':{'k8s-app':'kube-dns'}},'topologyKey':'topology.kubernetes.io/zone'}]}}}}}}
 run(['kubectl','-n','kube-system','patch','deployment','coredns','--type','merge','-p',json.dumps(patch)])
 raise SystemExit
ctx={'VPC_ID':meta['vpc_id'],'REGION':meta['region'],'DB_URL':meta['db']['management']['jdbc_url'],'REDIS_HOST':meta['redis_host'],'APP2_URI':'http://'+meta['internal_alb']+':8083','ROLE_ROUTING':meta['pod_roles']['routing-controller'],'ROUTING_TABLE':meta['routing_lock_table']}
tag_file=WORK/'image-tags.json'
image_tags=json.loads(tag_file.read_text()) if tag_file.exists() else {}
if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',v) for v in image_tags.values()):raise SystemExit('Invalid image-tags.json')
ctx.update({'IMAGE_'+k.upper().replace('-','_'):v+':'+image_tags.get(k,args.tag) for k,v in meta['ecr'].items()})
ctx.update({'TG_'+k.upper().replace('-','_'):v for k,v in meta['http_targets'].items()})
ctx.update({'TCP_'+k.upper().replace('-','_'):v for k,v in meta['tcp_targets'].items()})
ctx.update({'DISC_'+k.upper().replace('-','_'):v for k,v in meta['discovery_targets'].items()})
for i,site in enumerate(['a','b']):
 ctx['ES_HOST_'+site.upper()]=meta['es_hosts'][i];ctx['ES_'+site.upper()]='http://'+meta['es_hosts'][i]+':9200';ctx['APP2_'+site.upper()+'_IP']=meta['app2'][site]['private_ip']
ctx['ES_URLS']=','.join(ctx['ES_'+s] for s in ['A','B'])
ctx['ROUTING_CONFIG']=json.dumps({'routing':{'bindings':{k:{'ruleArn':meta['rules'][k],'targets':{s:meta['http_targets'][('management' if k=='management' else 'gateway')+'-'+s] for s in ['a','b']},'probes':{}} for k in ['management','app1','app2']}}})
def expand(value):
 if isinstance(value,str):
  return re.sub(r'@@([A-Z0-9_]+)@@',lambda m:str(ctx[m.group(1)]),value)
 if isinstance(value,list):return [expand(v) for v in value]
 if isinstance(value,dict):return {k:expand(v) for k,v in value.items()}
 return value
groups={
 'grdc':'grdc/deploy/k8s',
 'observability':'management-plane/observability/deploy/k8s',
 'agents':'application-plane/agent-client/deploy/k8s',
 'management':'management-plane/management-server/deploy/k8s',
 'gateway':'gateway/deploy/k8s',
 'app1':'application-plane/app-service/deploy/k8s',
 'routing':'global-lb/deploy/k8s'}
priority={'Namespace':0,'StorageClass':1,'ServiceAccount':2,'ClusterRole':2,'ClusterRoleBinding':3,'ConfigMap':4,'PersistentVolumeClaim':5,'Service':6,'Deployment':7,'DaemonSet':7,'PodDisruptionBudget':8,'TargetGroupBinding':9}
files=[p for rel in groups.values() for p in (ROOT/rel).rglob('*.yaml')]
rendered={str(p.relative_to(ROOT)):expand(yaml.safe_load(p.read_text())) for p in files}
out=WORK/'rendered';out.mkdir(exist_ok=True)
for name,obj in rendered.items():
 path=out/name;path.parent.mkdir(parents=True,exist_ok=True)
 path.write_text(yaml.safe_dump(obj,sort_keys=False,allow_unicode=True))
if args.action=='render':
 print('Rendered YAML in .ha-work/rendered; no credentials embedded.');raise SystemExit
if args.action=='apply':
 # CRDs are installed by the LBC Helm step; database SQL and ES must already be ready.
 for ns_file in ns_files:apply(yaml.safe_load(ns_file.read_text()))
 for p in (ROOT/'global-lb/deploy/k8s').glob('storageclass-*.yaml'):apply(yaml.safe_load(p.read_text()))
 for group in ([args.only] if args.only else list(groups)):
  selected=[obj for name,obj in rendered.items() if name.startswith(groups[group]+'/')]
  for obj in sorted(selected,key=lambda x:priority.get(x['kind'],10)):apply(obj)
  for obj in selected:
   if obj['kind'] in ('Deployment','DaemonSet'):
    run(['kubectl','-n',obj['metadata']['namespace'],'rollout','status',obj['kind'].lower()+'/'+obj['metadata']['name'],'--timeout=900s'])
  print('Ready:',group)
