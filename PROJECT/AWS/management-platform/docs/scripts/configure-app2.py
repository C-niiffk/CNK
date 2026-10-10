#!/usr/bin/env python3
"""Write root-readable systemd environment files; never print credentials."""
import json,os,pathlib,sys,urllib.request
meta=json.loads(pathlib.Path(sys.argv[1]).read_text())
site=sys.argv[3]
if site not in ('a','b'):raise SystemExit('site must be a or b')
host=meta['app2'][site]
secrets=json.loads(pathlib.Path(sys.argv[2]).read_text())
req=urllib.request.Request('http://169.254.169.254/latest/api/token',data=b'',method='PUT',headers={'X-aws-ec2-metadata-token-ttl-seconds':'60'})
with urllib.request.urlopen(req,timeout=5) as response: token=response.read().decode()
def imds(name):
 req=urllib.request.Request('http://169.254.169.254/latest/meta-data/'+name,headers={'X-aws-ec2-metadata-token':token})
 with urllib.request.urlopen(req,timeout=5) as response:return response.read().decode()
if imds('instance-id')!=host['instance_id']:raise SystemExit('Deployment metadata is for a different EC2 instance')
values={
 'SPRING_PROFILES_ACTIVE':'app2','SERVICE_NAME':'app2','RUNTIME':'ec2',
 'SITE':site,'APP_IP':imds('local-ipv4'),'HOSTNAME':imds('instance-id'),
 'SERVER_PORT':'8080','APP2_DATA_DIR':'/var/lib/management/app2/db',
 'NACOS_ADDR':meta['discovery_nlbs'][site]+':8848',
 'NACOS_SECONDARY_ADDR':meta['discovery_nlbs']['b' if site=='a' else 'a']+':8848',
 'APP2_DB_URL':meta['db']['management']['jdbc_url'],'APP2_DB_PASSWORD':secrets['APP2_DB_PASSWORD'],'NACOS_USERNAME':'nacos',
 'NACOS_PASSWORD':secrets['NACOS_PASSWORD'],'JWT_SECRET':secrets['JWT_SECRET'],
 'GATEWAY_A':'http://'+meta['internal_alb']+':8081','GATEWAY_B':'http://'+meta['internal_alb']+':8082',
 'LOG_FILE':'/var/log/management/app2/application.log',
 'SW_AGENT_NAME':'app2','SW_COLLECTOR_BACKEND_SERVICES':meta['agents_nlb']+':11800',
 'SW_LOGGING_DIR':'/var/log/management/app2/skywalking'
}
def write(name,values):
 def quoted(v):
  if not isinstance(v,str) or any(c in v for c in '\r\n\0'):raise ValueError('Invalid environment value')
  return '"'+v.replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$')+'"'
 p=pathlib.Path('/etc/management')/name
 temp=p.with_suffix('.new')
 fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
 with os.fdopen(fd,'w') as out:out.write(''.join(k+'='+quoted(v)+'\n' for k,v in values.items()))
 temp.chmod(0o600);temp.replace(p)
write('app2.env',values)
for destination in ('a','b'):
 write('filebeat-'+destination+'.env',{'SITE':site,'HOST_ID':host['instance_id'],'LOGSTASH_HOST':meta['discovery_nlbs'][destination]+':5044'})
print('Environment files configured (secret values omitted)')
