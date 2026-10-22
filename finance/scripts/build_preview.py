"""Export an isolated synthetic UI preview. Never reads the configured hospital database."""
import json,os,shutil,tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from app import db
from app.main import app
ROOT=Path(__file__).resolve().parents[1]
out=Path(os.environ.get('NAVIN_STATIC_PREVIEW',str(ROOT/'preview'/'dist')))
out.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='navin-preview-') as temp:
 db.DATA=Path(temp);os.environ['NAVIN_ADMIN_PASSWORD']='ephemeral-preview-fixture'
 snapshots={}
 with TestClient(app) as c:
  c.headers['X-Navin-Request']='1'
  c.post('/api/login',json={'username':'admin','password':'ephemeral-preview-fixture'})
  for role in ('management','billing','cashier','stores','pharmacy','hr','accounts','finance'):
   c.post('/api/users',json={'username':'preview-'+role,'password':'ephemeral-preview-fixture','role':role,'branches':['*'] if role in ('management','finance','accounts') else ['dadri']})
  for role in ('management','billing','cashier','stores','pharmacy','hr','accounts','finance'):
   c.post('/api/login',json={'username':'preview-'+role,'password':'ephemeral-preview-fixture'})
   item={'me':c.get('/api/me').json(),'metrics':c.get('/api/metrics').json()}
   for w in ('demo','real'):
    q={'workspace':w,'start':'2026-10-01','end':'2026-10-06'}
    item[w]={k:c.get('/api/'+endpoint,params=q).json() for k,endpoint in [('analytics','analytics'),('masters','masters'),('close','close')]}
    if role in ('management','finance'):item[w]['rules']=c.get('/api/rules',params=q).json()
   snapshots[role]=item
 (out/'snapshot.json').write_text(json.dumps(snapshots,separators=(',',':')))
shutil.copytree(ROOT/'app'/'static',out/'static',dirs_exist_ok=True)
for name in ('preview.js','preview.css'):shutil.copy(ROOT/'preview'/name,out/'static'/name)
html=(out/'static'/'index.html').read_text().replace('<title>Navin Finance Intelligence</title>','<title>Navin Finance · Department Workflow Preview</title><meta name="description" content="Interactive synthetic preview of Navin Finance department workflows.">')
html=html.replace('</head>','<link rel="stylesheet" href="/static/preview.css"></head>').replace('<script src="/static/app.js">','<script src="/static/preview.js"></script><script src="/static/app.js">')
p=out/'static'/'app.js';p.write_text(p.read_text().replace("let filters={start:yr+'-04-01',end:today}","let filters={start:'2026-10-01',end:'2026-10-06'}"))
(out/'index.html').write_text(html)
print(f'Synthetic preview written to {out}')
