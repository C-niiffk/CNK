#!/usr/bin/env python3
"""Execute repository SQL through SQL*Plus on App2 A. No Java initializer; no DB secrets in argv."""
import argparse,bcrypt,json,os,pathlib,re,shlex,subprocess,tempfile,uuid
from remote import connection,run,aws
r=pathlib.Path(__file__).resolve().parents[2];w=r/'.ha-work'
p=argparse.ArgumentParser();p.add_argument('--key',required=True);a=p.parse_args()
m=json.loads((w/'deployment.json').read_text());s=json.loads((w/'runtime.json').read_text());os.environ['AWS_DEFAULT_REGION']=m['region']
master=json.loads(aws('secretsmanager','get-secret-value','--secret-id',m['db']['management']['secret_arn'])['SecretString'])
endpoint=m['db']['management'];dsn=f"//{endpoint['host']}:{endpoint['port']}/{endpoint['service']}"
def connect(user,password):
 if any(c in password for c in '"\r\n\0'):raise SystemExit('SQLPlus password contains unsupported quoting characters')
 return '\nSET DEFINE OFF\nCONNECT '+user+'/"'+password+'"@"'+dsn+'"\n'
def source(rel,values={}):
 text=(r/rel).read_text()
 for key,value in values.items():
  text=re.sub(r'^ACCEPT '+key+r' .+$',lambda _: 'DEFINE '+key+' = "'+value+'"',text,flags=re.M)
 return text+'\n'
head='SET ECHO OFF\nSET VERIFY OFF\nWHENEVER OSERROR EXIT FAILURE ROLLBACK\nWHENEVER SQLERROR EXIT FAILURE ROLLBACK\n'
sql=head+connect(master['username'],master['password'])
sql+=source('management-plane/management-server/deploy/database/01-create-users.sql',s)
sql+=source('grdc/deploy/database/01-create-users.sql',{'GRDC_A_PASSWORD':s['GRDC_A_DB_PASSWORD'],'GRDC_B_PASSWORD':s['GRDC_B_DB_PASSWORD']})
h=bcrypt.hashpw(s['NACOS_PASSWORD'].encode(),bcrypt.gensalt(rounds=12,prefix=b'2a')).decode()
for site in ['A','B']:
 sql+=connect('MP_GRDC'+site,s['GRDC_'+site+'_DB_PASSWORD'])
 sql+=source('grdc/deploy/database/02-nacos-schema.sql')
 sql+=source('grdc/deploy/database/03-init-data.sql',{'NACOS_BCRYPT':h})
sql+=connect('MP_APP2',s['APP2_DB_PASSWORD'])
# Idempotent first installation: preserve existing tables and jobs.
for statement in source('application-plane/app-service/deploy/database/app2-schema.sql').split(';'):
 if statement.strip():sql+="BEGIN\n EXECUTE IMMEDIATE q'~"+statement.strip()+"~';\nEXCEPTION WHEN OTHERS THEN IF SQLCODE != -955 THEN RAISE; END IF; END;\n/\n"
sql+="SELECT COUNT(*) AS APP2_TABLE_READY FROM user_tables WHERE table_name='APP2_JOB';\nPROMPT SQL_INITIALIZATION_OK\nEXIT SUCCESS\n"
opts,target=connection(m,w,pathlib.Path(a.key).resolve(),'a');remote='/home/ec2-user/mp-sql-'+uuid.uuid4().hex
run(['ssh',*opts,target,'umask 077; mkdir '+shlex.quote(remote)])
try:
 with tempfile.TemporaryDirectory(dir=w) as tmp:
  f=pathlib.Path(tmp)/'init.sql';f.write_text(sql);f.chmod(0o600)
  run(['scp',*opts,str(f),str(r/'grdc/deploy/database/install-sqlplus.sh'),target+':'+remote+'/'])
 run(['ssh',*opts,target,'sudo bash '+remote+'/install-sqlplus.sh'])
 run(['ssh',*opts,target,'TNS_ADMIN=/etc/management/oracle /usr/lib/oracle/19.27/client64/bin/sqlplus -S -L /nolog @'+remote+'/init.sql'])
finally:
 run(['ssh',*opts,target,'rm -rf -- '+shlex.quote(remote)])
print('SQL initialized; management tables will be created by Flyway at management-server startup.')
