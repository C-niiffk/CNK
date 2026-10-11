import json,pathlib,re,subprocess,time
def run(cmd,capture=False):return subprocess.run(cmd,check=True,text=True,stdout=subprocess.PIPE if capture else None).stdout
def aws(*args):return json.loads(run(['aws','--no-cli-pager','--output','json',*args],True))
def connection(m,w,key,site):
 import os
 os.environ['AWS_DEFAULT_REGION']=m['region']
 host=m['app2'][site]['instance_id']
 if not re.fullmatch(r'i-[0-9a-f]+',host):raise SystemExit('Invalid instance ID')
 # Trust the SSH host key obtained through the authenticated SSM channel, not the first SSH response.
 result=aws('ssm','send-command','--instance-ids',host,'--document-name','AWS-RunShellScript','--parameters',json.dumps({'commands':['cat /etc/ssh/ssh_host_ed25519_key.pub']}))
 command=result['Command']['CommandId'];output=None
 for _ in range(60):
  try:
   status=aws('ssm','get-command-invocation','--command-id',command,'--instance-id',host)
   if status['Status']=='Success':output=status['StandardOutputContent'].strip();break
   if status['Status'] in ['Failed','Cancelled','TimedOut']:raise SystemExit('SSM host-key retrieval failed')
  except subprocess.CalledProcessError:pass
  time.sleep(2)
 if output is None:raise SystemExit('SSM is not ready after 120 seconds')
 parts=output.split()
 if len(parts)<2 or parts[0]!='ssh-ed25519' or not re.fullmatch(r'[A-Za-z0-9+/=]+',parts[1]):raise SystemExit('Unexpected SSH key')
 known=w/('known_hosts-'+site);entry=host+' '+parts[0]+' '+parts[1]+'\n'
 if known.exists() and known.read_text()!=entry:raise SystemExit('SSH host key changed; verify the instance before replacing the known_hosts file')
 known.write_text(entry);known.chmod(0o600)
 opts=['-i',str(key),'-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(known),'-o','HostKeyAlias='+host,'-o','ProxyCommand=aws --region '+m['region']+' ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p']

 return opts,'ec2-user@'+host
