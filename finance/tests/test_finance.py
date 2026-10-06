import json,io,csv
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from app import db,engine
from app.main import app

@pytest.fixture
def client(tmp_path,monkeypatch):
 monkeypatch.setattr(db,'DATA',tmp_path);monkeypatch.setenv('NAVIN_ADMIN_PASSWORD','test-only-admin-password')
 with TestClient(app) as c:
  c.headers['X-Navin-Request']='1';assert c.post('/api/login',json={'username':'admin','password':'test-only-admin-password'}).status_code==200
  yield c

def data(c,workspace='demo',**f):
 r=c.get('/api/analytics',params={'workspace':workspace,'start':'2026-10-01','end':'2026-10-06',**f});assert r.status_code==200,r.text;return r.json()
def add(kind,doc_id,**fields):
 values={'doc_id':doc_id,'entity_id':'demo-entity','branch_id':'dadri','date':'2026-10-01',**fields}
 with db.connect() as c:return c.execute('INSERT INTO records(workspace,kind,entity_id,branch_id,date,doc_id,fields,original,sheet) VALUES(?,?,?,?,?,?,?,?,?)',('demo',kind,values['entity_id'],values['branch_id'],values['date'],doc_id,db.dump(values),db.dump(values),'Test fixture')).lastrowid

def login(c,name,password):return c.post('/api/login',json={'username':name,'password':password})
def reviewer(c):
 r=c.post('/api/users',json={'username':'finance2','password':'test-only-reviewer-password','role':'finance','branches':['*']});assert r.status_code==200,r.text
 login(c,'finance2','test-only-reviewer-password')
def review(c,id):
 r=c.post(f'/api/proposals/{id}/review',json={'decision':'approved','notes':'Fixture reviewed independently'});assert r.status_code==200,r.text

def test_demo_real_isolation_and_accounting_authority(client):
 d=data(client);assert d['kpis']['accounting_revenue']=='215000.00';assert d['kpis']['operational_billing']=='220000.00'
 assert d['kpis']['receivables']=='155000.00';assert d['kpis']['payables']=='40000.00'
 assert d['kpis']['ebitda']=='160000.00';assert d['kpis']['net_result']=='155000.00'
 assert d['kpis']['debt_principal']=='500000.00';assert d['statements']['balance_sheet']['available']
 r=data(client,'real');assert r['records']==[] and all(v is None for v in r['kpis'].values())
 assert r['statements']['accounting_complete'] is False

def test_partial_combined_receipts_and_advance_not_revenue(client):
 d=data(client);a=next(r for r in d['receivables'] if r['bill_id']=='B1');b=next(r for r in d['receivables'] if r['bill_id']=='B2')
 assert a['settlements']=='40000.00' and a['outstanding']=='75000.00';assert b['outstanding']=='0.00'
 assert a['claim_status']=='approved' and a['payment_status']=='partially paid'
 add('receipt','ADV',amount='10000',receipt_type='advance')
 d=data(client);assert d['kpis']['accounting_revenue']=='215000.00' and d['kpis']['unallocated_receipts']=='10000.00'

def test_maker_reviewer_and_allocation_limits(client):
 d=data(client);receipt=next(r for r in d['asof_records'] if r['doc_id']=='RC1');bill=d['receivables'][0]
 assert client.post('/api/allocations?workspace=demo',json={'cash_id':receipt['id'],'document_id':bill['id'],'amount':'1','notes':'Excess'}).status_code==409
 advance=add('receipt','ADV',amount='1000',receipt_type='advance')
 r=client.post('/api/allocations?workspace=demo',json={'cash_id':advance,'document_id':bill['id'],'amount':'500','notes':'Reviewed partial advance'});assert r.status_code==200,r.text;id=r.json()['id']
 assert client.post(f'/api/proposals/{id}/review',json={'decision':'approved','notes':'Self approval'}).status_code==403
 reviewer(client);review(client,id)
 assert next(r for r in data(client)['receivables'] if r['id']==bill['id'])['settlements']=='40500.00'

def test_credits_refunds_writeoffs_no_double_subtraction(client):
 add('ar_adjustment','CREDIT-ALREADY',bill_id='B1',adjustment_type='credit',amount='10000',already_in_bill='yes')
 add('ar_adjustment','WRITEOFF',bill_id='B1',adjustment_type='writeoff',amount='1000',already_in_bill='no')
 d=data(client);assert next(r for r in d['receivables'] if r['bill_id']=='B1')['outstanding']=='74000.00'
 refund=add('receipt','RF1',amount='-100',receipt_type='refund');bill=next(r for r in d['receivables'] if r['bill_id']=='B1')
 assert client.post('/api/allocations?workspace=demo',json={'cash_id':refund,'document_id':bill['id'],'amount':'-100'}).status_code==400
 assert any(x['key']=='refund:'+str(refund) for x in data(client)['exceptions'])

def test_bank_partial_one_many_and_ambiguity(client):
 d=data(client);suggestion=d['bank_suggestions'][0];bank=next(r for r in d['bank_transactions'] if r['id']==suggestion['bank_id']);book=next(r for r in d['book_transactions'] if r['id']==suggestion['candidate_book_ids'][0])
 amount='50000' if Decimal(bank['amount'])>0 else '-5000'
 r=client.post('/api/bank-matches?workspace=demo',json={'bank_id':bank['id'],'book_id':book['id'],'amount':amount,'notes':'Partial reviewed match'});assert r.status_code==200,r.text
 reviewer(client);review(client,r.json()['id']);d=data(client)
 assert next(x for x in d['bank_transactions'] if x['id']==bank['id'])['matched_amount']==Decimal(amount).quantize(Decimal('.01')).to_eng_string()
 # A second identical reference/date/amount produces an ambiguous suggestion; no automatic matches.
 add('cashbook','DUP-BOOK',bank_account_id='bank-1',entry_type='movement',amount='60000',reference='DEMO-RC1',date='2026-10-02')
 assert any(x['status']=='ambiguous' for x in data(client)['bank_suggestions'])

def test_inventory_purchase_consumption_loan_and_zero_margin(client):
 d=data(client);assert d['inventory'][0]['recorded_inventory_value']=='60000.00';assert d['inventory'][0]['closing_quantity']=='26.00'
 assert d['loans'][0]['finance_cost']=='0.00';assert d['kpis']['operating_expenses']=='45000.00'
 add('pharmacy','RET',amount='-10000',item_id='MED1',actual_cost='-6000',pharmacy_source='Main')
 p=data(client)['pharmacy'][0];assert p['net_sales']=='0.00' and p['margin'] is None
 add('pharmacy','MISSING',amount='100',item_id='MED2',actual_cost=None)
 assert data(client)['pharmacy'][0]['missing_cost_lines']==1

def test_effective_contract_explicit_collected_base(client):
 r=client.post('/api/change?workspace=demo',json={'action':'rule','branch_id':'dadri','payload':{'kind':'consultant','fields':{'consultant_id':'DR1','basis':'percent','base_kind':'collected','rate':'10','start':'2026-10-01','end':'2026-10-31','notes':'Signed contract'}},'notes':'Review contract'});assert r.status_code==200,r.text
 reviewer(client);review(client,r.json()['id'])
 add('consultant','CON1',consultant_id='DR1',amount='1000',base_amount='20000',base_kind='billed',evidence='Explicit billed base')
 d=data(client);assert next(x for x in d['expenses'] if x['doc_id']=='CON1')['contract_expected'] is None
 add('consultant','CON2',consultant_id='DR1',amount='1000',base_amount='10000',base_kind='collected',evidence='Collection base')
 assert next(x for x in data(client)['expenses'] if x['doc_id']=='CON2')['contract_expected']=='1000.00'

def test_branch_role_permissions_exports_and_sources(client):
 client.post('/api/users',json={'username':'collections','password':'test-only-collection-password','role':'collections','branches':['dadri']});login(client,'collections','test-only-collection-password')
 d=data(client);assert all(r['branch_id']=='dadri' for r in d['records']);assert not any(r['kind'] in ('payroll','bank','journal') for r in d['asof_records'])
 assert client.get('/api/analytics?workspace=demo&branch_id=vaishali').status_code==403
 assert client.get('/api/export/receivables?workspace=demo&branch_id=vaishali').status_code==403
 assert client.get('/api/users').status_code==403
 with db.connect() as c:payroll=c.execute("SELECT id FROM records WHERE kind='payroll'").fetchone()[0]
 assert client.get('/api/records/'+str(payroll)).status_code==403
 assert client.get('/api/templates/payroll').status_code==403

def test_export_dashboard_consistency_and_aggregation_limit(client):
 d=data(client);r=client.get('/api/export/summary',params={'workspace':'demo','start':'2026-10-01','end':'2026-10-06'})
 assert r.status_code==200 and d['kpis']['accounting_revenue'].encode() in r.content
 assert 'not consolidated' in d['statements']['aggregation_label']
 assert client.get('/api/export/receivables?workspace=demo&format=xlsx').content.startswith(b'PK')
 assert d['cash_flow_statement']['available'] is False

@pytest.fixture
def real_entity(client):
 with db.connect() as c:c.execute("INSERT INTO masters(workspace,kind,key,name,properties) VALUES('real','entity','legal1','Actual entity','{}')")
 return 'legal1'
def csvbytes(rows):
 out=io.StringIO();writer=csv.writer(out);writer.writerow(['doc_id','date','bill_id','uhid','admission_id','amount']);writer.writerows(rows);return out.getvalue().encode()
def upload(c,rows):
 r=c.post('/api/imports/upload',data={'entity_id':'legal1','branch_id':'dadri','kind':'billing'},files={'file':('bill.csv',csvbytes(rows),'text/csv')});assert r.status_code==200,r.text;return r.json()
def review_import(c,id):
 m={'reviewed':True,'profile_name':'Validated fixture','sheets':{'CSV':{'header_row':1,'columns':{k:str(i) for i,k in enumerate(['doc_id','date','bill_id','uhid','admission_id','amount'])}}}}
 r=c.post(f'/api/imports/{id}/review',json=m);assert r.status_code==200,r.text;return r.json()
def test_duplicate_overlap_reprocess_and_rollback(client,real_entity):
 rows=[['LINE1','2026-10-01','B1','0001','001','100']];a=upload(client,rows);review_import(client,a['id']);assert client.post(f"/api/imports/{a['id']}/commit",json={}).status_code==200
 assert upload(client,rows)['duplicate'];assert len(data(client,'real')['records'])==1
 b=upload(client,[['LINE2','2026-10-01','B2','0001','002','120']]);assert review_import(client,b['id'])['overlaps']
 assert client.post(f"/api/imports/{b['id']}/commit",json={}).status_code==409
 assert client.post(f"/api/imports/{b['id']}/commit",json={'replace_ids':[a['id']],'notes':'Corrected source'}).status_code==200
 assert data(client,'real')['kpis']['operational_billing']=='120.00'
 revision=client.post(f"/api/imports/{b['id']}/reprocess").json()['id'];review_import(client,revision)
 assert client.post(f'/api/imports/{revision}/commit',json={'replace_ids':[b['id']],'notes':'Reprocessed mapping'}).status_code==200
 assert client.post(f'/api/imports/{revision}/rollback',json={'notes':'Rollback verified'}).status_code==200
 assert data(client,'real')['kpis']['operational_billing'] is None
 with db.connect() as c:assert c.execute("SELECT COUNT(*) FROM records WHERE workspace='real'").fetchone()[0]==3

def test_period_lock_late_posting_and_issued_versions(client):
 with db.connect() as c:
  c.execute("INSERT INTO locks(workspace,branch_id,period,locked) VALUES('demo','dadri','2026-10',1)")
 r=client.post('/api/records?workspace=demo',json={'kind':'receipt','fields':{'doc_id':'LATE','date':'2026-10-02','entity_id':'demo-entity','branch_id':'dadri','amount':'5'}});assert r.status_code==409
 proposal=client.post('/api/change?workspace=demo',json={'action':'period_reopen','branch_id':'dadri','payload':{'period':'2026-10'},'notes':'Authorised late transaction'}).json()['id']
 reviewer(client);review(client,proposal)
 r=client.post('/api/records?workspace=demo',json={'kind':'receipt','fields':{'doc_id':'LATE','date':'2026-10-02','entity_id':'demo-entity','branch_id':'dadri','amount':'5'}});assert r.status_code==200
 report1=client.post('/api/reports?workspace=demo&start=2026-10-01&end=2026-10-06').json();report2=client.post('/api/reports?workspace=demo&start=2026-10-01&end=2026-10-06').json()
 assert report1['version']==1 and report2['version']==2
 assert client.get('/api/reports/'+str(report1['id'])).status_code==200

def test_forecast_linked_commitment_not_double_counted(client):
 d=data(client);base=d['forecast'][0]['rows'];out=sum(Decimal(x['expected_outflow']) for x in base)
 # 20k invoice + 15k payroll + 5k statutory + 20k principal; past-due 20k invoice remains an exception, not a guessed future date.
 assert out==Decimal('60000')
 assert d['forecast'][0]['rows'][0]['opening']=='650000.00'
 assert d['forecast'][1]['rows'][0]['expected_inflow']=='0.00'

def test_explicit_zero_and_register_validation(client):
 from app import files,imports
 assert files.decimal_value(0)=='0.00'
 base={'doc_id':'TEST','date':'2026-10-01','bank_account_id':'bank-1','entry_type':'movement','amount':0}
 assert imports.normalize('bank',base,'demo-entity','dadri')['amount']=='0.00'
 with pytest.raises(ValueError):imports.normalize('bank',{**base,'entry_type':'unknown'},'demo-entity','dadri')
 assert client.post('/api/change?workspace=demo',json={'action':'setting','notes':'Invalid blank','payload':{'materiality':''}}).status_code==400
 assert client.post('/api/change?workspace=demo',json={'action':'setting','notes':'Zero allowed','payload':{'materiality':0}}).status_code==200

def test_latest_snapshots_and_branch_scoped_debt(client):
 add('inventory','NEW-OPEN',date='2026-10-05',item_id='MED1',store_id='Main',batch_id='BATCH1',movement_type='opening',quantity='12',actual_cost='30000')
 # Same loan identifier in two branches must remain two different liabilities.
 add('loan','OTHER-LOAN',branch_id='vaishali',loan_id='LN1',event='opening',amount='200000')
 d=data(client)
 assert len(d['loans'])==2
 assert next(x for x in d['loans'] if x['branch_id']=='vaishali')['closing_principal']=='200000.00'

def test_monthly_budget_and_explicit_scenario(client):
 add('budget','VAI-BUDGET',branch_id='vaishali',date='2026-10-01',month='2026-10',category_id='revenue',budget_type='revenue',amount='100000')
 b=next(x for x in data(client)['budget'] if x['doc_id']=='VAI-BUDGET');assert b['actual']=='80000.00'
 add('finance_scenario','SC1',scenario_name='Reviewed volume case',patient_volume='100',price_per_patient='1000',deduction_percent='10',variable_cost_per_patient='300',fixed_operating_cost='10000',payroll_cost='5000',consultant_cost='2000',consumption_cost='0',collection_percent='80')
 s=data(client)['scenarios'][0];assert s['estimated_earned_revenue']=='90000.00';assert s['estimated_operating_contribution']=='43000.00';assert s['expected_collections']=='72000.00'
 assert data(client,'real')['forecast'][1]['rows']==[]

def test_workbook_multi_sheet_formatted_identifiers(tmp_path):
 from openpyxl import Workbook
 from app import files
 book=Workbook();sheet=book.active;sheet.title='First';sheet.append(['UHID','Amount']);sheet.append([123,0]);sheet['A2'].number_format='000000';book.create_sheet('Second').append(['Date','Amount']);path=tmp_path/'input.xlsx';book.save(path)
 rows=[(name,list(values)) for name,values in files.sheets(path)]
 assert len(rows)==2 and rows[0][1][1][1]==['000123','0']

def test_database_backup_restore_preserves_audit_and_isolation(client,tmp_path):
 from app.backup import backup,restore
 import sqlite3
 archive=tmp_path/'backup.tgz';backup(archive);destination=tmp_path/'restore';restore(archive,destination)
 c=sqlite3.connect(destination/'finance.sqlite3')
 assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 assert c.execute("SELECT count(*) FROM records WHERE workspace='real'").fetchone()[0]==0
 assert c.execute("SELECT count(*) FROM records WHERE workspace='demo'").fetchone()[0]>0
 assert c.execute('SELECT count(*) FROM sessions').fetchone()[0]==0
 c.close()
 with pytest.raises(ValueError):restore(archive,destination)

def test_opening_snapshot_not_added_to_prior_bills(client):
 add('ar_adjustment','AR-SNAPSHOT',date='2026-10-03',bill_id='OPENING',adjustment_type='opening',amount='75000')
 d=data(client);assert d['kpis']['receivables']=='155000.00'
 add('billing','AFTER-SNAPSHOT',date='2026-10-04',bill_id='AFTER',uhid='001',admission_id='AFTER',amount='1000')
 assert data(client)['kpis']['receivables']=='156000.00'
 assert data(client,end='2026-10-02')['kpis']['receivables']=='155000.00'

def test_cash_transfer_and_loan_receipts_not_earned_income(client):
 before=data(client)
 add('receipt','TRANSFER',amount='100000',receipt_type='transfer');add('receipt','BORROWING',amount='200000',receipt_type='loan')
 after=data(client);assert after['kpis']['accounting_revenue']==before['kpis']['accounting_revenue'];assert after['kpis']['unallocated_receipts']==before['kpis']['unallocated_receipts']
 assert after['statements']['aggregation_label'].startswith('Branch aggregation')
 assert after['cash_flow_statement']['available'] is False

def test_shared_allocation_preserves_total_and_filter_basis(client):
 p=client.post('/api/change?workspace=demo',json={'action':'rule','branch_id':'*','notes':'Reviewed cost pool','payload':{'kind':'shared_cost','fields':{'start':'2026-10-01','end':'2026-10-06','notes':'Reviewed allocation driver','branches':['dadri','vaishali'],'driver':'revenue','amount':'1000.01'}}});assert p.status_code==200,p.text
 reviewer(client);review(client,p.json()['id'])
 rows=data(client)['shared_allocations'][0]['allocations'];assert sum(Decimal(r['amount']) for r in rows)==Decimal('1000.01')
 filtered=data(client,branch_id='dadri')['shared_allocations'][0]['allocations'];assert filtered==[r for r in rows if r['branch_id']=='dadri']
