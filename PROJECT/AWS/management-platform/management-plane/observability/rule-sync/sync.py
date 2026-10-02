"""Poll saved rules, validate PromQL, write atomically, reload Prometheus.
No mutation is applied when validation fails. Read and writes stay in this pod.
"""
import os,json,time,urllib.request,urllib.parse,pathlib
BASE=os.environ.get('MANAGEMENT_URL','http://management.platform.svc.cluster.local:8080')
PROM='http://127.0.0.1:9090'
def request(url,data=None,token=None):
 headers={'Content-Type':'application/json'}
 if token:headers['Authorization']='Bearer '+token
 r=urllib.request.Request(url,data=None if data is None else json.dumps(data).encode(),headers=headers)
 with urllib.request.urlopen(r,timeout=10) as f:return json.load(f)
last=None
while True:
 try:
  token=request(BASE+'/api/auth/token',{'username':'admin','password':os.environ['ADMIN_PASSWORD']})['access_token']
  rules=request(BASE+'/api/alarms/rules',token=token)
  fingerprint=json.dumps(rules,sort_keys=True)
  if fingerprint!=last:
   for r in rules:
    q=request(PROM+'/api/v1/query?query='+urllib.parse.quote(r['expr']))
    if q.get('status')!='success':raise ValueError('PromQL validation failed')
   body={'groups':[{'name':'platform-managed','rules':[{'alert':r['alert'],'expr':r['expr'],'for':str(r['forSeconds'])+'s','labels':{'severity':r['severity']}} for r in rules]}]}
   # JSON is a valid YAML subset and avoids a template or shell injection path.
   p=pathlib.Path('/rules/managed.yml');old=p.read_bytes() if p.exists() else None
   tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(body));tmp.replace(p)
   try:
    with urllib.request.urlopen(urllib.request.Request(PROM+'/-/reload',data=b''),timeout=10) as f:pass
   except Exception:
    if old is not None:p.write_bytes(old)
    else:p.unlink(missing_ok=True)
    raise
   last=fingerprint;print('Rules activated:',len(rules),flush=True)
 except Exception as e:print('Rules unchanged:',type(e).__name__,flush=True)
 time.sleep(30)
