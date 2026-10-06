import os,time,json,hashlib,secrets,csv,io,zipfile
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI,Request,Depends,HTTPException,UploadFile,File,Form,Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook
from . import db,schema,engine,files,imports as ingest

@asynccontextmanager
async def lifespan(app):db.init();yield
app=FastAPI(title='Navin Finance Intelligence',lifespan=lifespan)
STATIC=Path(__file__).parent/'static';app.mount('/static',StaticFiles(directory=STATIC),name='static')
@app.middleware('http')
async def security(request,call_next):
 if request.method not in ('GET','HEAD','OPTIONS'):
  origin=request.headers.get('origin')
  if origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):return Response('Cross-origin request blocked',403)
  if request.headers.get('x-navin-request')!='1':return Response('Request protection missing',403)
 response=await call_next(request)
 for k,v in {'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Cache-Control':'no-store','Referrer-Policy':'same-origin','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"}.items():response.headers[k]=v
 return response
@app.get('/')
def home():return FileResponse(STATIC/'index.html')
@app.get('/health')
def health():
 with db.connect() as c:c.execute('SELECT 1')
 return {'status':'ok','application':'Navin Finance Intelligence'}
def user(request:Request):
 token=hashlib.sha256(request.cookies.get('navin_finance_session','').encode()).hexdigest()
 with db.connect() as c:
  r=c.execute('SELECT u.* FROM sessions s JOIN users u ON s.user_id=u.id WHERE s.token=? AND s.expires>? AND u.active=1',(token,int(time.time()))).fetchone()
  if not r:raise HTTPException(401,'Sign in required')
  return {'id':r['id'],'username':r['username'],'role':r['role'],'branches':json.loads(r['branches'])}
def branch(u,b):
 if '*' not in u['branches'] and b not in u['branches']:raise HTTPException(403,'Branch permission denied')
def finance(u):
 if u['role'] not in ('management','finance'):raise HTTPException(403,'Finance/management permission required')
def write_kind(u,kind):
 if u['role']=='reviewer' or not schema.readable(u['role'],kind):raise HTTPException(403,'Role cannot change this register')
def workspace(request):
 w=request.query_params.get('workspace','real')
 if w not in ('real','demo'):raise HTTPException(400,'Invalid workspace')
 return w
def record(c,u,id,w=None):
 r=c.execute('SELECT * FROM records WHERE id=? AND active=1',(id,)).fetchone()
 if not r:raise HTTPException(404,'Active record not found')
 branch(u,r['branch_id'])
 if not schema.readable(u['role'],r['kind']):raise HTTPException(403,'Role permission denied')
 if w and r['workspace']!=w:raise HTTPException(400,'Demo and real records cannot be mixed')
 return dict(r,fields=json.loads(r['fields']))
def ensure_open(c,w,b,date):
 lock=c.execute('SELECT 1 FROM locks WHERE workspace=? AND branch_id=? AND period=? AND locked=1',(w,b,date[:7])).fetchone()
 if lock:raise HTTPException(409,'This branch period is locked; request authorised reopening')
def ensure_master(c,w,entity,b):
 if not c.execute("SELECT 1 FROM masters WHERE workspace=? AND kind='entity' AND key=? AND active=1",(w,entity)).fetchone():raise HTTPException(400,'Configure a legal entity before entering records')
 r=c.execute("SELECT properties FROM masters WHERE workspace=? AND kind='branch' AND key=? AND active=1",(w,b)).fetchone()
 if not r:raise HTTPException(400,'Unknown branch')
 props=json.loads(r[0]);assigned=props.get('entity_id')
 if assigned and assigned!=entity:raise HTTPException(400,'Branch is assigned to a different legal entity')

def propose(c,u,w,b,action,payload):
 branch(u,b)
 if u['role']=='reviewer':raise HTTPException(403,'Read-only reviewers cannot make financial changes')
 id=c.execute('INSERT INTO proposals(workspace,branch_id,action,payload,maker) VALUES(?,?,?,?,?)',(w,b,action,db.dump(payload),u['id'])).lastrowid
 db.audit(c,u,'change_proposed',id,{'workspace':w,'action':action,'payload':payload});return {'id':id,'status':'pending','message':'Awaiting approval by a different authorised finance user.'}
login_attempts={}
@app.post('/api/login')
async def login(request:Request):
 b=await request.json();key=(request.client.host,b.get('username'));now=time.time();attempts=[t for t in login_attempts.get(key,[]) if now-t<300]
 if len(attempts)>=10:raise HTTPException(429,'Too many attempts; wait five minutes')
 with db.connect() as c:
  r=c.execute('SELECT * FROM users WHERE username=? AND active=1',(b.get('username'),)).fetchone()
  if not r or not db.verify(b.get('password',''),r['password']):login_attempts[key]=attempts+[now];raise HTTPException(401,'Invalid sign-in')
  token=secrets.token_urlsafe(32);c.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),r['id'],int(now)+28800))
 response=Response('{"ok":true}',media_type='application/json');response.set_cookie('navin_finance_session',token,httponly=True,samesite='strict',secure=os.getenv('NAVIN_COOKIE_SECURE','false')=='true',max_age=28800);return response
@app.post('/api/logout')
def logout(request:Request,u=Depends(user)):
 with db.connect() as c:c.execute('DELETE FROM sessions WHERE token=?',(hashlib.sha256(request.cookies.get('navin_finance_session','').encode()).hexdigest(),))
 r=Response('{}',media_type='application/json');r.delete_cookie('navin_finance_session');return r
@app.get('/api/me')
def me(u=Depends(user)):
 return dict(u,source_types={k:{'name':v[0],'section':v[1],'required':v[2].split(),'fields':schema.fields(k)} for k,v in schema.TYPES.items() if schema.readable(u['role'],k)},master_types=schema.MASTER_TYPES,account_categories=schema.ACCOUNT_CATEGORIES,roles=schema.ROLES)
@app.get('/api/masters')
def masters(request:Request,u=Depends(user)):
 w=workspace(request)
 with db.connect() as c:
  rows=engine.masters(c,w)
  rows=[r for r in rows if r['kind']!='branch' or '*' in u['branches'] or r['key'] in u['branches']]
  rows=[r for r in rows if '*' in u['branches'] or not r['properties'].get('branch_id') or r['properties']['branch_id'] in u['branches']]
  if u['role'] not in ('management','finance','procurement'):
   for r in rows:r['properties']={k:v for k,v in r['properties'].items() if k not in ('bank_details','account_number','ifsc')}
  return rows
@app.post('/api/masters')
async def master_change(request:Request,u=Depends(user)):
 finance(u);w=workspace(request);b=await request.json()
 if b.get('kind') not in schema.MASTER_TYPES or not b.get('key') or not b.get('name'):raise HTTPException(400,'Provide master type, stable key and name')
 branch_id=b.get('branch_id') or (b['key'] if b['kind']=='branch' else '*')
 if branch_id=='*' and '*' not in u['branches']:raise HTTPException(403,'Group master editing requires group branch access')
 with db.connect() as c:return propose(c,u,w,branch_id,'master',b)
@app.get('/api/analytics')
def analytics(request:Request,u=Depends(user)):
 f=dict(request.query_params);w=f.pop('workspace','real');validate_filters(w,f,u)
 with db.connect() as c:return engine.calculate(c,w,u,f)
def validate_filters(w,f,u):
 if w not in ('real','demo'):raise HTTPException(400,'Invalid workspace')
 if f.get('branch_id'):branch(u,f['branch_id'])
 for k in ('start','end'):
  if f.get(k):
   try:
    if files.date_value(f[k])!=f[k]:raise ValueError()
   except ValueError:raise HTTPException(400,'Use YYYY-MM-DD dates')
 if f.get('start') and f.get('end') and f['start']>f['end']:raise HTTPException(400,'Start is after end')
@app.get('/api/records/{id}')
def detail(id:int,u=Depends(user)):
 with db.connect() as c:
  r=record(c,u,id);alloc=[]
  for a in c.execute('SELECT * FROM allocations WHERE cash_id=? OR document_id=?',(id,id)):
   other=record(c,u,a['document_id'] if a['cash_id']==id else a['cash_id'],r['workspace']);alloc.append(dict(a,related_document=other['doc_id']))
  return dict(r,original=json.loads(r['original']),allocations=alloc)
@app.post('/api/records')
async def manual_record(request:Request,u=Depends(user)):
 w=workspace(request);b=await request.json();kind=b.get('kind')
 if kind not in schema.TYPES:raise HTTPException(400,'Unknown register')
 write_kind(u,kind);v=b.get('fields',{})
 try:v=ingest.normalize(kind,v,v.get('entity_id'),v.get('branch_id'))
 except ValueError as e:raise HTTPException(400,str(e))
 branch(u,v['branch_id'])
 with db.connect() as c:
  ensure_master(c,w,v['entity_id'],v['branch_id']);ensure_open(c,w,v['branch_id'],v['date']);return propose(c,u,w,v['branch_id'],'record',{'kind':kind,'fields':v,'notes':b.get('notes')})
@app.post('/api/allocations')
async def allocation(request:Request,u=Depends(user)):
 w=workspace(request);b=await request.json()
 with db.connect() as c:
  cash=record(c,u,int(b['cash_id']),w);target=record(c,u,int(b['document_id']),w)
  write_kind(u,cash['kind']);validate_allocation(c,w,cash,target,b['amount']);return propose(c,u,w,cash['branch_id'],'allocation',b)
def validate_allocation(c,w,cash,target,amount):
 if cash['branch_id']!=target['branch_id'] or cash['entity_id']!=target['entity_id']:raise HTTPException(400,'Allocation must use the same branch and entity')
 allowed=(cash['kind']=='receipt' and target['kind']=='billing') or (cash['kind']=='payment' and target['kind'] in ('invoice','expense','payroll','consultant'))
 if not allowed:raise HTTPException(400,'Allocate receipts to bills; payments to invoices or incurred expenses')
 if cash['fields'].get('receipt_type') in ('transfer','loan') or cash['fields'].get('payment_type') in ('transfer','principal','interest'):raise HTTPException(400,'Transfers and loan movements cannot settle patient/vendor balances')
 try:value=engine.D(files.decimal_value(amount))
 except ValueError:raise HTTPException(400,'Invalid allocation amount')
 original=engine.D(cash['fields'].get('amount'))
 if value is None or not value or not original or value*original<0:raise HTTPException(400,'Allocation sign must agree with the cash document')
 ensure_open(c,w,cash['branch_id'],cash['date']);ensure_open(c,w,target['branch_id'],target['date'])
 allocated=sum((engine.D(r[0]) for r in c.execute('SELECT amount FROM allocations WHERE cash_id=? AND active=1',(cash['id'],))),engine.Z)
 if abs(allocated+value)>abs(original):raise HTTPException(409,'Allocation exceeds remaining receipt/payment amount')
 if value<0 and cash['kind']=='receipt' and (not cash['fields'].get('original_receipt_id') or not cash['fields'].get('authorisation')):raise HTTPException(400,'Refund requires original receipt and authorisation')
@app.post('/api/bank-matches')
async def bank_match(request:Request,u=Depends(user)):
 finance(u);w=workspace(request);b=await request.json()
 with db.connect() as c:
  bankr=record(c,u,int(b['bank_id']),w);bookr=record(c,u,int(b['book_id']),w);validate_bank(c,w,bankr,bookr,b['amount']);return propose(c,u,w,bankr['branch_id'],'bank_match',b)
def validate_bank(c,w,bankr,bookr,amount):
 if bankr['kind']!='bank' or bookr['kind']!='cashbook' or bankr['branch_id']!=bookr['branch_id'] or bankr['entity_id']!=bookr['entity_id'] or bankr['fields'].get('bank_account_id')!=bookr['fields'].get('bank_account_id') or bankr['fields'].get('entry_type')!='movement' or bookr['fields'].get('entry_type','movement')!='movement':raise HTTPException(400,'Match bank/book movements in the same branch, entity and account')
 value=engine.D(files.decimal_value(amount))
 if value is None or not value:raise HTTPException(400,'Match amount required')
 for kind,r in [('bank',bankr),('book',bookr)]:
  ensure_open(c,w,r['branch_id'],r['date'])
  used=sum((engine.D(x[0]) for x in c.execute('SELECT amount FROM bank_matches WHERE '+kind+'_id=? AND active=1',(r['id'],))),engine.Z)
  original=engine.D(r['fields'].get('amount'))
  if original is None or value*original<=0 or abs(used+value)>abs(original):raise HTTPException(409,'Match exceeds unmatched amount or has the wrong sign')
@app.get('/api/proposals')
def proposals(request:Request,u=Depends(user)):
 w=workspace(request)
 with db.connect() as c:
  rows=[dict(r,payload=json.loads(r['payload'])) for r in c.execute('SELECT * FROM proposals WHERE workspace=? ORDER BY id DESC',(w,)) if ('*' in u['branches'] or r['branch_id'] in u['branches']) and (u['role'] in ('management','finance') or r['maker']==u['id'])]
  return rows
@app.post('/api/proposals/{id}/review')
async def approve(id:int,request:Request,u=Depends(user)):
 finance(u);b=await request.json()
 if not b.get('notes'):raise HTTPException(400,'Reviewer evidence/note required')
 with db.connect() as c:
  r=c.execute('SELECT * FROM proposals WHERE id=?',(id,)).fetchone()
  if not r:raise HTTPException(404,'Proposal not found')
  branch(u,r['branch_id'])
  if r['maker']==u['id']:raise HTTPException(403,'Maker cannot approve their own change')
  if r['status']!='pending':raise HTTPException(409,'Proposal already reviewed')
  if b.get('decision') not in ('approved','rejected'):raise HTTPException(400,'Choose approved or rejected')
  p=json.loads(r['payload']);w=r['workspace'];action=r['action']
  if b['decision']=='approved':
   if action=='record':
    v=p['fields'];ensure_open(c,w,v['branch_id'],v['date']);ensure_master(c,w,v['entity_id'],v['branch_id'])
    c.execute('INSERT INTO records(workspace,kind,entity_id,branch_id,date,doc_id,fields,original,sheet) VALUES(?,?,?,?,?,?,?,?,?)',(w,p['kind'],v['entity_id'],v['branch_id'],v['date'],v['doc_id'],db.dump(v),db.dump({'manual_proposal':id,'values':v}),'Reviewed manual entry'))
   elif action=='allocation':
    cash=record(c,u,int(p['cash_id']),w);target=record(c,u,int(p['document_id']),w);validate_allocation(c,w,cash,target,p['amount'])
    kind='ar' if cash['kind']=='receipt' else 'ap' if target['kind']=='invoice' else 'expense'
    c.execute('INSERT INTO allocations(workspace,kind,cash_id,document_id,amount,maker) VALUES(?,?,?,?,?,?)',(w,kind,cash['id'],target['id'],files.decimal_value(p['amount']),r['maker']))
   elif action=='bank_match':
    bankr=record(c,u,int(p['bank_id']),w);bookr=record(c,u,int(p['book_id']),w);validate_bank(c,w,bankr,bookr,p['amount'])
    c.execute('INSERT INTO bank_matches(workspace,bank_id,book_id,amount,maker) VALUES(?,?,?,?,?)',(w,bankr['id'],bookr['id'],files.decimal_value(p['amount']),r['maker']))
   elif action=='master':
    if p['kind']=='account' and p.get('properties',{}).get('statement_category') not in schema.ACCOUNT_CATEGORIES:raise HTTPException(400,'Use a valid reviewed account category')
    c.execute('INSERT INTO masters(workspace,kind,key,name,properties) VALUES(?,?,?,?,?) ON CONFLICT(workspace,kind,key) DO UPDATE SET name=excluded.name,properties=excluded.properties,version=version+1',(w,p['kind'],p['key'],p['name'],db.dump(p.get('properties',{}))))
   elif action=='setting':
    for key,value in p.items():c.execute('INSERT INTO settings VALUES(?,?,?,1) ON CONFLICT(workspace,key) DO UPDATE SET value=excluded.value,version=version+1',(w,key,db.dump(value)))
   elif action=='rule':
    validate_rule(c,w,p)
    if p['kind']=='shared_cost':
     for br in p['fields']['branches']:branch(u,br)
    c.execute('INSERT INTO rules(workspace,kind,branch_id,fields) VALUES(?,?,?,?)',(w,p['kind'],r['branch_id'],db.dump(p['fields'])))
   elif action=='rule_retire':c.execute('UPDATE rules SET active=0 WHERE id=? AND workspace=?',(p['id'],w))
   elif action=='period_reopen':c.execute('UPDATE locks SET locked=0,version=version+1 WHERE workspace=? AND branch_id=? AND period=?',(w,r['branch_id'],p['period']))
   elif action=='period_lock':
    pending=c.execute("SELECT 1 FROM close_tasks WHERE workspace=? AND branch_id=? AND period=? AND status<>'signed_off'",(w,r['branch_id'],p['period'])).fetchone()
    if pending or not c.execute('SELECT 1 FROM close_tasks WHERE workspace=? AND branch_id=? AND period=?',(w,r['branch_id'],p['period'])).fetchone():raise HTTPException(409,'Complete and review the month-end checklist before locking')
    c.execute('INSERT INTO locks(workspace,branch_id,period,locked) VALUES(?,?,?,1) ON CONFLICT(workspace,branch_id,period) DO UPDATE SET locked=1,version=version+1',(w,r['branch_id'],p['period']))
   elif action=='close_signoff':
    task=c.execute('SELECT * FROM close_tasks WHERE id=? AND workspace=?',(p['id'],w)).fetchone()
    if not task or not p.get('evidence'):raise HTTPException(400,'Checklist evidence required')
    branch(u,task['branch_id'])
    c.execute("UPDATE close_tasks SET status='signed_off',evidence=?,reviewer=? WHERE id=?",(p['evidence'],u['id'],p['id']))
   elif action=='record_void':
    old=record(c,u,p['id'],w);ensure_open(c,w,old['branch_id'],old['date'])
    if c.execute('SELECT 1 FROM allocations WHERE active=1 AND (cash_id=? OR document_id=?)',(p['id'],p['id'])).fetchone() or c.execute('SELECT 1 FROM bank_matches WHERE active=1 AND (bank_id=? OR book_id=?)',(p['id'],p['id'])).fetchone():raise HTTPException(409,'Reverse linked allocations/matches first')
    c.execute('UPDATE records SET active=0 WHERE id=?',(p['id'],))
   elif action in ('allocation_reverse','bank_match_reverse'):
    table='allocations' if action=='allocation_reverse' else 'bank_matches'
    linked=c.execute('SELECT * FROM '+table+' WHERE id=? AND workspace=? AND active=1',(p['id'],w)).fetchone()
    if not linked:raise HTTPException(409,'Allocation/match is no longer active')
    for key in (('cash_id','document_id') if table=='allocations' else ('bank_id','book_id')):
     old=record(c,u,linked[key],w);ensure_open(c,w,old['branch_id'],old['date'])
    c.execute('UPDATE '+table+' SET active=0 WHERE id=? AND workspace=?',(p['id'],w))
  c.execute('UPDATE proposals SET status=?,reviewer=?,review_note=? WHERE id=?',(b['decision'],u['id'],b['notes'],id));db.audit(c,u,'proposal_'+b['decision'],id,{'action':action,'payload':p,'note':b['notes']})
 return {'ok':True}
@app.post('/api/change')
async def sensitive_change(request:Request,u=Depends(user)):
 finance(u);w=workspace(request);b=await request.json();allowed=['setting','rule','rule_retire','period_reopen','period_lock','close_signoff','record_void','allocation_reverse','bank_match_reverse']
 if b.get('action') not in allowed or not b.get('notes'):raise HTTPException(400,'Valid action and evidence note required')
 p=b.get('payload',{});branch_id=b.get('branch_id','*');branch(u,branch_id)
 with db.connect() as c:
  if b['action']=='rule':validate_rule(c,w,p)
  if b['action']=='rule' and p['kind']=='shared_cost':
   for br in p['fields']['branches']:branch(u,br)
  if b['action']=='setting':
   if '*' not in u['branches']:raise HTTPException(403,'Workspace policy settings require group finance access')
   allowed_keys=set(engine.settings(c,w))
   if set(p)-allowed_keys:raise HTTPException(400,'Unknown setting key')
   if p.get('ageing_buckets') and (not all(isinstance(x,int) and x>0 for x in p['ageing_buckets']) or p['ageing_buckets']!=sorted(set(p['ageing_buckets']))):raise HTTPException(400,'Ageing limits must be ascending positive unique days')
   if p.get('accounting_basis') not in (None,'journal','trial_balance'):raise HTTPException(400,'Accounting basis must be journal or trial_balance')
   for key in ('accounting_complete','opening_complete','credit_revenue_complete','cash_coverage_complete'):
    if key in p and not isinstance(p[key],bool):raise HTTPException(400,'Coverage flags must be true/false')
   if 'financial_year_start' in p and (not isinstance(p['financial_year_start'],int) or not 1<=p['financial_year_start']<=12):raise HTTPException(400,'Financial-year start must be a month number 1–12')
   for key in ('materiality','discount_threshold','stress_collection_percent'):
    if p.get(key) is not None:
     try:value=engine.D(files.decimal_value(p[key]))
     except ValueError:raise HTTPException(400,'Invalid '+key)
     if value is None or value<0 or key!='materiality' and value>100:raise HTTPException(400,'Invalid '+key+' range')
   if p.get('collection_delay_days') is not None and (not isinstance(p['collection_delay_days'],int) or p['collection_delay_days']<0):raise HTTPException(400,'Collection delay must be non-negative days')
  if b['action']=='close_signoff':
   task=c.execute('SELECT * FROM close_tasks WHERE id=? AND workspace=?',(p.get('id'),w)).fetchone()
   if not task:raise HTTPException(404,'Checklist task not found')
   branch(u,task['branch_id']);branch_id=task['branch_id']
  if b['action']=='rule_retire':
   rule=c.execute('SELECT * FROM rules WHERE id=? AND workspace=?',(p.get('id'),w)).fetchone()
   if not rule:raise HTTPException(404,'Rule not found')
   branch(u,rule['branch_id']);branch_id=rule['branch_id']
  if b['action'] in ('record_void',):old=record(c,u,int(p['id']),w);branch_id=old['branch_id']
  if b['action'] in ('allocation_reverse','bank_match_reverse'):
   table='allocations' if b['action']=='allocation_reverse' else 'bank_matches';r=c.execute('SELECT * FROM '+table+' WHERE id=? AND workspace=?',(p['id'],w)).fetchone()
   if not r:raise HTTPException(404,'Match/allocation not found')
   id=r['cash_id'] if table=='allocations' else r['bank_id'];old=record(c,u,id,w);branch_id=old['branch_id'];ensure_open(c,w,branch_id,old['date'])
  return propose(c,u,w,branch_id,b['action'],p)
def validate_rule(c,w,p):
 if p.get('kind') not in ('consultant','shared_cost','tariff','panel_terms','elimination'):raise HTTPException(400,'Rule kind must be consultant/shared_cost/tariff/panel_terms/elimination')
 f=p.get('fields',{})
 try:start=files.date_value(f.get('start'));end=files.date_value(f.get('end'))
 except ValueError:raise HTTPException(400,'Invalid effective dates')
 if not start or not end or start>end or not f.get('notes'):raise HTTPException(400,'Effective dates and evidence notes required')
 if p['kind']=='consultant' and (f.get('basis') not in ('fixed','percent','per_procedure') or f.get('rate') is None or not f.get('consultant_id') or f.get('basis')=='percent' and f.get('base_kind') not in ('billed','collected','procedure')):raise HTTPException(400,'Contract needs consultant, rate and explicit basis')
 if p['kind']=='shared_cost' and (f.get('driver') not in ('revenue','bed_days','headcount','floor_area','usage') or not f.get('branches') or f.get('amount') is None):raise HTTPException(400,'Shared cost requires amount, branches and approved driver')
 for key in ('rate','amount','ceiling'):
  if f.get(key) is not None:
   try:value=engine.D(files.decimal_value(f[key]))
   except ValueError:raise HTTPException(400,'Invalid rule '+key)
   if value is None or value<0:raise HTTPException(400,'Rule '+key+' must be non-negative')
 if p['kind']=='consultant' and f.get('basis')=='percent' and engine.D(f['rate'])>100:raise HTTPException(400,'Contract percentage cannot exceed 100')
 if p['kind']=='shared_cost':
  if not isinstance(f['branches'],list) or len(set(f['branches']))!=len(f['branches']):raise HTTPException(400,'Provide distinct branch keys')
  for key,value in f.get('weights',{}).items():
   try:weight=engine.D(files.decimal_value(value))
   except ValueError:raise HTTPException(400,'Invalid allocation weight')
   if weight is None or weight<0:raise HTTPException(400,'Allocation weights must be non-negative')
 for key in ('submission_days','response_days','payment_days'):
  if f.get(key) is not None and (not isinstance(f[key],int) or f[key]<0):raise HTTPException(400,'Terms require non-negative integer days')
 for r in c.execute('SELECT * FROM rules WHERE workspace=? AND kind=? AND active=1',(w,p['kind'])):
  old=json.loads(r['fields'])
  if old.get('start','')<=end and old.get('end','')>=start and ((p['kind']=='consultant' and old.get('consultant_id')==f.get('consultant_id')) or (p['kind']=='tariff' and old.get('code')==f.get('code'))):raise HTTPException(409,'Overlapping effective-dated rules; retire the old rule first')
@app.get('/api/rules')
def rules(request:Request,u=Depends(user)):
 w=workspace(request)
 with db.connect() as c:return [dict(r,fields=json.loads(r['fields'])) for r in c.execute('SELECT * FROM rules WHERE workspace=? AND active=1',(w,)) if '*' in u['branches'] or r['branch_id'] in u['branches']]
@app.post('/api/exceptions')
async def exception_review(request:Request,u=Depends(user)):
 w=workspace(request);b=await request.json();branch_id=b.get('branch_id') or '*';branch(u,branch_id)
 if u['role']=='reviewer':raise HTTPException(403,'Read-only reviewer')
 if b.get('status') not in ('open','investigating','explained','resolved','suppressed') or b.get('status') in ('resolved','suppressed','explained') and not b.get('resolution'):raise HTTPException(400,'Status and resolution evidence required')
 with db.connect() as c:
  c.execute('INSERT INTO exceptions(workspace,key,branch_id,fields,status,owner,due_date,resolution) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(workspace,key) DO UPDATE SET status=excluded.status,owner=excluded.owner,due_date=excluded.due_date,resolution=excluded.resolution',(w,b['key'],branch_id,db.dump(b),b['status'],b.get('owner'),b.get('due_date'),b.get('resolution')));db.audit(c,u,'exception_reviewed',b['key'],b)
 return {'ok':True}
@app.get('/api/close')
def close(request:Request,u=Depends(user)):
 w=workspace(request)
 with db.connect() as c:return {'tasks':[dict(r) for r in c.execute('SELECT * FROM close_tasks WHERE workspace=? ORDER BY period,title',(w,)) if '*' in u['branches'] or r['branch_id'] in u['branches']], 'locks':[dict(r) for r in c.execute('SELECT * FROM locks WHERE workspace=?',(w,)) if '*' in u['branches'] or r['branch_id'] in u['branches']]}
@app.post('/api/close')
async def close_task(request:Request,u=Depends(user)):
 finance(u);w=workspace(request);b=await request.json();branch(u,b.get('branch_id'))
 if not b.get('title') or not b.get('period') or len(b['period'])!=7:raise HTTPException(400,'Period YYYY-MM and checklist title required')
 with db.connect() as c:
  ensure_open(c,w,b['branch_id'],b['period']+'-01')
  id=c.execute('INSERT INTO close_tasks(workspace,branch_id,period,title,owner,due_date) VALUES(?,?,?,?,?,?)',(w,b['branch_id'],b['period'],b['title'],b.get('owner'),b.get('due_date'))).lastrowid;db.audit(c,u,'close_task_created',id,b)
 return {'id':id}
@app.post('/api/reports')
def issue_report(request:Request,u=Depends(user)):
 finance(u);f=dict(request.query_params);w=f.pop('workspace','real');validate_filters(w,f,u)
 with db.connect() as c:
  data=engine.calculate(c,w,u,f);version=c.execute('SELECT COUNT(*)+1 FROM reports WHERE workspace=? AND filters=?',(w,db.dump(f))).fetchone()[0]
  id=c.execute('INSERT INTO reports(workspace,filters,content,version,user_id) VALUES(?,?,?,?,?)',(w,db.dump(f),db.dump(data),version,u['id'])).lastrowid;db.audit(c,u,'management_report_issued',id,{'filters':f,'version':version})
 return {'id':id,'version':version,'label':'Immutable issued report; later versions are restatements'}
@app.get('/api/reports')
def report_history(request:Request,u=Depends(user)):
 finance(u);w=workspace(request)
 with db.connect() as c:return [dict(r) for r in c.execute('SELECT id,filters,version,created,user_id FROM reports WHERE workspace=? ORDER BY id DESC',(w,)) if not json.loads(r['filters']).get('branch_id') or '*' in u['branches'] or json.loads(r['filters'])['branch_id'] in u['branches']]
@app.get('/api/reports/{id}')
def issued_report(id:int,u=Depends(user)):
 finance(u)
 with db.connect() as c:
  r=c.execute('SELECT * FROM reports WHERE id=?',(id,)).fetchone()
  if not r:raise HTTPException(404,'Report not found')
  data=json.loads(r['content'])
  for branch_id in {x['branch_id'] for x in data['asof_records']}:branch(u,branch_id)
  return data
@app.get('/api/users')
def users(u=Depends(user)):
 if u['role']!='management':raise HTTPException(403,'Management required')
 with db.connect() as c:return [dict(r,branches=json.loads(r['branches'])) for r in c.execute('SELECT id,username,role,branches,active FROM users')]
@app.post('/api/users')
async def new_user(request:Request,u=Depends(user)):
 if u['role']!='management':raise HTTPException(403,'Management required')
 b=await request.json()
 if not b.get('username') or len(b.get('password',''))<12 or b.get('role') not in schema.ROLES or not b.get('branches'):raise HTTPException(400,'Provide username, 12-character password, role and branch scope')
 for br in b['branches']:branch(u,br)
 with db.connect() as c:
  if c.execute('SELECT 1 FROM users WHERE username=?',(b['username'],)).fetchone():raise HTTPException(409,'User already exists')
  c.execute('INSERT INTO users(username,password,role,branches) VALUES(?,?,?,?)',(b['username'],db.password_hash(b['password']),b['role'],db.dump(b['branches'])));db.audit(c,u,'user_created',b['username'],{'role':b['role'],'branches':b['branches']})
 return {'ok':True}
@app.post('/api/password')
async def password_change(request:Request,u=Depends(user)):
 b=await request.json()
 if len(b.get('new',''))<12:raise HTTPException(400,'Password must contain at least 12 characters')
 with db.connect() as c:
  stored=c.execute('SELECT password FROM users WHERE id=?',(u['id'],)).fetchone()[0]
  if not db.verify(b.get('old',''),stored):raise HTTPException(403,'Current password incorrect')
  c.execute('UPDATE users SET password=? WHERE id=?',(db.password_hash(b['new']),u['id']));c.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],));db.audit(c,u,'password_changed',u['id'],{})
 return {'ok':True}
@app.get('/api/audit')
def audit(request:Request,u=Depends(user)):
 finance(u)
 with db.connect() as c:
  # Group audit can contain payroll/bank details; branch finance receives only its own authored events.
  return [dict(r) for r in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 500') if '*' in u['branches'] or r['user_id']==u['id']]
@app.get('/api/views')
def saved_views(u=Depends(user)):
 with db.connect() as c:return [dict(r,filters=json.loads(r['filters'])) for r in c.execute('SELECT * FROM views WHERE user_id=?',(u['id'],))]
@app.post('/api/views')
async def save_view(request:Request,u=Depends(user)):
 b=await request.json();validate_filters(b.get('filters',{}).get('workspace','real'),b.get('filters',{}),u)
 with db.connect() as c:c.execute('INSERT INTO views(user_id,name,filters) VALUES(?,?,?)',(u['id'],b.get('name','Saved view'),db.dump(b.get('filters',{}))))
 return {'ok':True}

def import_row(c,u,id):
 r=c.execute('SELECT * FROM imports WHERE id=?',(id,)).fetchone()
 if not r:raise HTTPException(404,'Import not found')
 branch(u,r['branch_id'])
 if not schema.readable(u['role'],r['kind']):raise HTTPException(403,'Role permission denied')
 return dict(r)
def public_import(r):
 d=dict(r);d.pop('path',None);d['mapping']=json.loads(d['mapping']);d['review']=json.loads(d['review']);return d
@app.post('/api/imports/upload')
async def upload(entity_id:str=Form(...),branch_id:str=Form(...),kind:str=Form(...),file:UploadFile=File(...),u=Depends(user)):
 if kind not in schema.TYPES:raise HTTPException(400,'Unknown register')
 write_kind(u,kind);branch(u,branch_id)
 with db.connect() as c:ensure_master(c,'real',entity_id,branch_id)
 suffix=Path(file.filename or '').suffix.lower()
 if suffix not in ('.csv','.xlsx'):raise HTTPException(400,'Use UTF-8 CSV or XLSX; convert legacy XLS before uploading')
 directory=db.DATA/'uploads';directory.mkdir(exist_ok=True,mode=0o700);p=directory/(secrets.token_hex(16)+suffix);sha=hashlib.sha256();size=0
 try:
  with p.open('wb') as out:
   p.chmod(0o600)
   while chunk:=await file.read(1024*1024):
    size+=len(chunk)
    if size>50_000_000:raise HTTPException(413,'Maximum file size is 50 MB')
    sha.update(chunk);out.write(chunk)
  digest=sha.hexdigest()
  with db.connect() as c:
   prior=c.execute("SELECT id,state FROM imports WHERE workspace='real' AND kind=? AND branch_id=? AND sha=?",(kind,branch_id,digest)).fetchone()
   if prior:return {'id':prior['id'],'duplicate':True,'state':prior['state'],'message':'Identical file already staged/imported; no duplicate rows added.'}
  try:inspection=files.inspect(p)
  except HTTPException:raise
  except Exception:raise HTTPException(400,'Invalid Excel/CSV structure or encoding')
  final=directory/(digest+suffix)
  if not final.exists():p.replace(final)
  with db.connect() as c:
   id=c.execute('INSERT INTO imports(kind,entity_id,branch_id,filename,sha,path,review,user_id) VALUES(?,?,?,?,?,?,?,?)',(kind,entity_id,branch_id,Path(file.filename).name,digest,str(final),db.dump({'inspection':inspection}),u['id'])).lastrowid;db.audit(c,u,'import_staged',id,{'kind':kind,'sha':digest,'branch_id':branch_id})
  return {'id':id,'inspection':inspection,'duplicate':False}
 finally:p.unlink(missing_ok=True)
@app.get('/api/imports')
def import_history(u=Depends(user)):
 with db.connect() as c:return [public_import(r) for r in c.execute('SELECT * FROM imports ORDER BY id DESC') if ('*' in u['branches'] or r['branch_id'] in u['branches']) and schema.readable(u['role'],r['kind'])]
@app.get('/api/imports/{id}')
def get_import(id:int,u=Depends(user)):
 with db.connect() as c:return public_import(import_row(c,u,id))
def overlap(c,imp,period):
 out=[]
 for r in c.execute("SELECT * FROM imports WHERE state='committed' AND workspace=? AND branch_id=? AND kind=? AND id<>?",(imp['workspace'],imp['branch_id'],imp['kind'],imp['id'])):
  p=json.loads(r['review']).get('period')
  if p and p[0]<=period[1] and p[1]>=period[0]:out.append({'id':r['id'],'filename':r['filename'],'period':p})
 return out
@app.post('/api/imports/{id}/review')
async def import_review(id:int,request:Request,u=Depends(user)):
 b=await request.json()
 with db.connect() as c:
  imp=import_row(c,u,id);write_kind(u,imp['kind'])
  if imp['state'] in ('committed','superseded'):raise HTTPException(409,'Stage a new mapping revision first')
  parsed=ingest.parse(imp['path'],imp['kind'],imp['entity_id'],imp['branch_id'],b)
  result={k:v for k,v in parsed.items() if k!='accepted'};result.update(inspection=json.loads(imp['review']).get('inspection'),accepted_count=len(parsed['accepted']),rejected_count=len(parsed['rejected']),excluded_count=len(parsed['excluded']),preview=parsed['accepted'][:10],overlaps=overlap(c,imp,parsed['period']),net_total=engine.money(sum((engine.D(r['fields'].get('amount')) or engine.Z for r in parsed['accepted']),engine.Z)),source_validation='User-reviewed mapping; no original hospital/accounting exports supplied with the specification.')
  c.execute("UPDATE imports SET mapping=?,review=?,state='reviewed' WHERE id=?",(db.dump(b),db.dump(result),id))
  profile_name=b.get('profile_name')
  if profile_name:
   version=c.execute('SELECT COALESCE(MAX(version),0)+1 FROM profiles WHERE name=? AND kind=?',(profile_name,imp['kind'])).fetchone()[0]
   c.execute('INSERT INTO profiles(name,kind,mapping,version) VALUES(?,?,?,?)',(profile_name,imp['kind'],db.dump(b),version))
  db.audit(c,u,'mapping_reviewed',id,b);return result
@app.get('/api/profiles')
def profiles(u=Depends(user)):
 with db.connect() as c:return [dict(r,mapping=json.loads(r['mapping'])) for r in c.execute('SELECT * FROM profiles ORDER BY id DESC') if schema.readable(u['role'],r['kind'])]
@app.post('/api/imports/{id}/commit')
async def import_commit(id:int,request:Request,u=Depends(user)):
 b=await request.json()
 with db.connect() as c:
  imp=import_row(c,u,id);write_kind(u,imp['kind'])
  if imp['state']=='committed':return {'ok':True,'duplicate':True}
  if imp['state']!='reviewed':raise HTTPException(400,'Review mapping before committing')
  parsed=ingest.parse(imp['path'],imp['kind'],imp['entity_id'],imp['branch_id'],json.loads(imp['mapping']))
  for r in parsed['accepted']:ensure_open(c,imp['workspace'],imp['branch_id'],r['fields']['date'])
  overlaps=overlap(c,imp,parsed['period']);replace=b.get('replace_ids',[])
  if overlaps and not replace and not b.get('retain_overlap'):raise HTTPException(409,'Explicit replacement or retention with an audit note is required for overlapping periods')
  if (overlaps or parsed['rejected'] or replace) and not b.get('notes'):raise HTTPException(400,'Audit note required for overlap/rejected rows/replacement')
  if any('conflict' in r['reason'].lower() for r in parsed['rejected']):raise HTTPException(409,'Entity/branch conflicts must be resolved before committing any rows')
  for rid in replace:
   old=import_row(c,u,int(rid))
   if old['workspace']!=imp['workspace'] or old['branch_id']!=imp['branch_id'] or old['kind']!=imp['kind'] or old['state']!='committed':raise HTTPException(400,'Replace only a committed import of the same branch/source')
   for oldr in c.execute('SELECT * FROM records WHERE import_id=? AND active=1',(rid,)):
    ensure_open(c,imp['workspace'],imp['branch_id'],oldr['date'])
    if c.execute('SELECT 1 FROM allocations WHERE active=1 AND (cash_id=? OR document_id=?)',(oldr['id'],oldr['id'])).fetchone() or c.execute('SELECT 1 FROM bank_matches WHERE active=1 AND (bank_id=? OR book_id=?)',(oldr['id'],oldr['id'])).fetchone():raise HTTPException(409,'Replacement would orphan reviewed allocations/matches. Reverse them with approval first.')
   c.execute('UPDATE records SET active=0 WHERE import_id=?',(rid,));c.execute("UPDATE imports SET state='superseded' WHERE id=?",(rid,))
  for r in parsed['accepted']:
   v=r['fields'];c.execute('INSERT INTO records(workspace,kind,entity_id,branch_id,date,doc_id,fields,original,import_id,sheet,row_no) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(imp['workspace'],imp['kind'],v['entity_id'],v['branch_id'],v['date'],v['doc_id'],db.dump(v),db.dump(r['original']),id,r['sheet'],r['row']))
  c.execute("UPDATE imports SET state='committed' WHERE id=?",(id,));db.audit(c,u,'import_committed',id,{'accepted':len(parsed['accepted']),'replacement_ids':replace,'retain_overlap':b.get('retain_overlap'),'notes':b.get('notes')})
 return {'ok':True,'accepted':len(parsed['accepted'])}
@app.post('/api/imports/{id}/reprocess')
def reprocess(id:int,u=Depends(user)):
 with db.connect() as c:
  old=import_row(c,u,id);write_kind(u,old['kind'])
  if old['state']!='committed':raise HTTPException(400,'Reprocess a committed import')
  review=json.loads(old['review']);review['reprocess_from']=id
  new=c.execute('INSERT INTO imports(workspace,kind,entity_id,branch_id,filename,sha,path,mapping,review,user_id) VALUES(?,?,?,?,?,?,?,?,?,?)',(old['workspace'],old['kind'],old['entity_id'],old['branch_id'],old['filename']+' (revision)',secrets.token_hex(32),old['path'],old['mapping'],db.dump(review),u['id'])).lastrowid;db.audit(c,u,'mapping_revision_staged',new,{'original_import':id})
 return {'id':new,'replace_id':id}
@app.post('/api/imports/{id}/rollback')
async def rollback(id:int,request:Request,u=Depends(user)):
 finance(u);b=await request.json()
 if not b.get('notes'):raise HTTPException(400,'Rollback reason required')
 with db.connect() as c:
  imp=import_row(c,u,id)
  for r in c.execute('SELECT * FROM records WHERE import_id=? AND active=1',(id,)):
   ensure_open(c,imp['workspace'],imp['branch_id'],r['date'])
   if c.execute('SELECT 1 FROM allocations WHERE active=1 AND (cash_id=? OR document_id=?)',(r['id'],r['id'])).fetchone() or c.execute('SELECT 1 FROM bank_matches WHERE active=1 AND (bank_id=? OR book_id=?)',(r['id'],r['id'])).fetchone():raise HTTPException(409,'Reverse linked allocations/matches with approval before rollback')
  c.execute('UPDATE records SET active=0 WHERE import_id=?',(id,));c.execute("UPDATE imports SET state='rolled_back' WHERE id=?",(id,));db.audit(c,u,'import_rolled_back',id,b)
 return {'ok':True}
@app.get('/api/imports/{id}/rejected.csv')
def rejected_download(id:int,u=Depends(user)):
 with db.connect() as c:imp=import_row(c,u,id);rows=json.loads(imp['review']).get('rejected',[])
 out=io.StringIO();wr=csv.writer(out);wr.writerow(['worksheet','row','reason','original'])
 for r in rows:wr.writerow([r['sheet'],r['row'],r['reason'],safe(db.dump(r['original']))])
 return Response(out.getvalue().encode('utf-8-sig'),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="rejected-rows.csv"'})
@app.get('/api/templates/{kind}')
def template(kind:str,u=Depends(user)):
 if kind not in schema.TYPES or not schema.readable(u['role'],kind):raise HTTPException(403,'Register not accessible')
 out=io.StringIO();csv.writer(out).writerow(schema.fields(kind))
 return Response(out.getvalue().encode('utf-8-sig'),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="navin-{kind}-template.csv"'})
def safe(value):
 if isinstance(value,(list,dict)):value=db.dump(value)
 text='' if value is None else str(value)
 return "'"+text if text.startswith(('=','+','-','@','\t','\r')) else text
@app.get('/api/export/{scope}')
def export(scope:str,request:Request,format:str='csv',u=Depends(user)):
 f=dict(request.query_params);w=f.pop('workspace','real');f.pop('format',None);validate_filters(w,f,u)
 if format not in ('csv','xlsx'):raise HTTPException(400,'Use CSV or XLSX')
 with db.connect() as c:data=engine.calculate(c,w,u,f)
 choices=['receivables','payables','bank_balances','bank_transactions','book_transactions','expenses','inventory','pharmacy','assets','loans','statutory','budget','exceptions','branches','monthly','departments','records','data_setup','shared_allocations']
 if scope=='summary':rows=[data['kpis']]
 elif scope=='statements':rows=data['statements']['rows']
 elif scope=='forecast':rows=[dict(row,scenario=sc['scenario']) for sc in data['forecast'] for row in sc['rows']]
 elif scope=='management_pack':
  rows=[{'section':'KPI','metric':k,'value':v} for k,v in data['kpis'].items()]+[{'section':'Exception','metric':x['key'],'value':x['message'],'exposure':x['exposure']} for x in data['exceptions']]+[{'section':'Statement','metric':r['category'],'value':r['amount']} for r in data['statements']['rows']]
 elif scope in choices:rows=data[scope]
 else:raise HTTPException(400,'Unknown export scope')
 keys=list(dict.fromkeys(k for row in rows for k in row if k!='original'));values=[[safe(row.get(k)) for k in keys] for row in rows]
 meta={'application':'Navin Finance Intelligence','workspace':w,'filters':db.dump(data['filters']),'source_basis':data['source_label'],'accounting_complete':str(data['statements']['accounting_complete']),'limitations':'Accounting and operational revenue are separate; missing sources are unknown; branch totals are aggregation, not consolidation; exposures may overlap.'}
 if format=='csv':
  out=io.StringIO();wr=csv.writer(out);wr.writerows(meta.items());wr.writerow([]);wr.writerow(keys);wr.writerows(values);content=out.getvalue().encode('utf-8-sig');media='text/csv'
 else:
  wb=Workbook();sheet=wb.active;sheet.title='Filtered data';sheet.append(keys or ['No records'])
  for row in values:sheet.append(row)
  sheet=wb.create_sheet('Coverage and filters');sheet.append(['Setting','Value'])
  for k,v in meta.items():sheet.append([k,v])
  out=io.BytesIO();wb.save(out);content=out.getvalue();media='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
 return Response(content,media_type=media,headers={'Content-Disposition':f'attachment; filename="navin-finance-{scope}.{format}"'})
@app.get('/api/metrics')
def metric_dictionary(u=Depends(user)):
 return [dict(key=k,**v) for k,v in METRICS.items()]
METRICS={
 'accounting_revenue':{'name':'Accounting revenue','formula':'Mapped revenue ledger credits minus debits, or compatible trial-balance movements','basis':'Posting date','source':'Journal or selected trial balance; never both','limitation':'Reviewed mappings and coverage required; not operational bill total'},
 'operational_billing':{'name':'Operational net billing','formula':'Sum of final signed patient bill amounts','basis':'Final bill date','source':'Patient billing register','limitation':'Service/pharmacy detail not added again; not cash collected'},
 'operating_expenses':{'name':'Accounting operating expense','formula':'Debits minus credits in reviewed operating expense accounts','basis':'Posting date','source':'Accounting ledger','limitation':'Inventory purchases and capital expenditure are not operating expenses'},
 'ebitda':{'name':'EBITDA','formula':'Accounting revenue − direct cost − operating expense','basis':'Posting date','source':'Complete balanced mapped books','limitation':'Unavailable with incomplete or unmapped books; not audited certification'},
 'net_result':{'name':'Net result','formula':'EBITDA − depreciation − finance cost − tax expense','basis':'Posting date','source':'Complete balanced mapped books','limitation':'Branch aggregation; consolidation requires matched approved elimination rules'},
 'receivables':{'name':'Receivables (debtors)','formula':'Opening + net bills − explicit credits/writeoffs − allocated receipts + reversals','basis':'As-of period end','source':'Bills, adjustments and allocations','limitation':'Money others owe; approved claims are not receipts. Credits already in net are not subtracted twice'},
 'payables':{'name':'Payables (creditors)','formula':'Opening + valid invoices − credits − allocated payments + reversals','basis':'As-of period end','source':'Invoices and approved allocations','limitation':'Money owed to suppliers; aged is not necessarily overdue without due dates'},
 'bank_verified_balance':{'name':'Recorded bank/cash balance','formula':'Latest recorded closing/cash-count snapshot per branch/account','basis':'As-of period end with snapshot date','source':'Bank statements/cash counts','limitation':'Check account coverage and stale snapshots; book cash shown separately'},
 'inventory_value':{'name':'Recorded inventory value','formula':'Signed recorded cost opening + receipts − consumption ± transfers/adjustments','basis':'As-of period end','source':'Inventory register with validated costs','limitation':'No MRP valuation; unknown costs block complete value'},
 'debt_principal':{'name':'Debt principal','formula':'Opening principal + drawdowns − principal repayments','basis':'As-of period end','source':'Loan register','limitation':'Interest is separate; missing opening principal remains unknown'},
 'operational_contribution':{'name':'Partial operational contribution','formula':'Final bill revenue − available recorded operational direct costs','basis':'Posting-period supporting records','source':'Bills and reviewed cost detail','limitation':'Not net profit; attribution/date/cost completeness must be reviewed'},
 'dso':{'name':'DSO (collection days)','formula':'Closing receivables ÷ compatible verified credit revenue × days in selected period','basis':'Closing balance / period credit revenue','source':'Receivables and verified credit bill revenue','limitation':'Unavailable without complete compatible inputs or with zero revenue'},
 'unallocated_receipts':{'name':'Unallocated receipts','formula':'Collections/advances/refunds less signed bill allocations','basis':'As-of cash posting cutoff','source':'Receipt register','limitation':'Loan and transfer receipts excluded; advances are not earned revenue'},
 'working_capital':{'name':'Working capital','formula':'Current operating assets minus current operating liabilities under reviewed account mapping','basis':'As-of period end','source':'Complete classified balance sheet','limitation':'Not calculated from isolated receipts/payments or unsupported current/noncurrent classification'},
 'accruals':{'name':'Accrued expenses','formula':'Incurred expense minus allocated payment','basis':'Expense incurred / payment posting cutoff','source':'Expense/payroll registers','limitation':'An unpaid recorded expense; cash paid is not a second expense'},
}
