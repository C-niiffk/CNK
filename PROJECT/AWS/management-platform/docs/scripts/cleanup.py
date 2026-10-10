#!/usr/bin/env python3
"""Inventory retained disks before destroy; explicit ECR emptying for this deployment only."""
import argparse,json,os,pathlib,subprocess
r=pathlib.Path(__file__).resolve().parents[2];w=r/'.ha-work';m=json.loads((w/'deployment.json').read_text());os.environ['AWS_DEFAULT_REGION']=m['region']
p=argparse.ArgumentParser();p.add_argument('action',choices=['inventory','empty-ecr']);p.add_argument('--confirm-project');a=p.parse_args()
def aws(*args):return json.loads(subprocess.check_output(['aws','--no-cli-pager','--output','json',*args],text=True))
if a.action=='inventory':
 ids=list(m['es_instances'].values())+[h['instance_id'] for h in m['app2'].values()]
 x=aws('ec2','describe-instances','--instance-ids',*ids)
 volumes=[{'instance':i['InstanceId'],'volume':b['Ebs']['VolumeId'],'delete_on_termination':b['Ebs']['DeleteOnTermination']} for res in x['Reservations'] for i in res['Instances'] for b in i['BlockDeviceMappings'] if 'Ebs' in b]
 pv=json.loads(subprocess.check_output(['kubectl','get','pv','-o','json'],text=True))
 pvs=[{'name':i['metadata']['name'],'volume':i['spec'].get('csi',{}).get('volumeHandle'),'policy':i['spec'].get('persistentVolumeReclaimPolicy')} for i in pv['items'] if i['spec'].get('claimRef',{}).get('namespace')=='observability']
 (w/'cleanup-inventory.json').write_text(json.dumps({'name':m['name'],'region':m['region'],'disks':volumes,'pvs':pvs},indent=2))
 print((w/'cleanup-inventory.json').read_text())
else:
 if a.confirm_project!=m['name']:raise SystemExit('Pass --confirm-project '+m['name'])
 for url in m['ecr'].values():
  repo=url.split('/',1)[1];images=aws('ecr','list-images','--repository-name',repo)['imageIds']
  for start in range(0,len(images),100):
   result=aws('ecr','batch-delete-image','--repository-name',repo,'--image-ids',json.dumps(images[start:start+100]))
   if result.get('failures'):raise SystemExit(str(result['failures']))
  print('Emptied:',repo)
