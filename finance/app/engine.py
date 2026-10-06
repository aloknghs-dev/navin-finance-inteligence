"""Shared Decimal calculation layer. Accounting, operations and cash are separate."""
import json,datetime
from decimal import Decimal,ROUND_HALF_UP
from collections import defaultdict
from .schema import TYPES,readable
D=lambda x:None if x in (None,'') else Decimal(str(x))
Z=Decimal(0)
def money(x):return None if x is None else format(x.quantize(Decimal('.01'),rounding=ROUND_HALF_UP),'f')
def total(rows,key='amount'):return sum((D(r.get(key)) or Z for r in rows),Z)
def known_total(rows,key='amount'):return money(total(rows,key)) if rows and all(r.get(key) is not None for r in rows) else None
def pct(n,d):return money(n/d*100) if d else None
def yes(x):return str(x).lower() in ('yes','true','1')
def settings(c,w):return {r['key']:json.loads(r['value']) for r in c.execute('SELECT * FROM settings WHERE workspace=?',(w,))}
def masters(c,w):return [dict(r,properties=json.loads(r['properties'])) for r in c.execute('SELECT * FROM masters WHERE workspace=? AND active=1',(w,))]
def load(c,w,u):
 out=[]
 for r in c.execute('SELECT * FROM records WHERE workspace=? AND active=1',(w,)):
  if '*' not in u['branches'] and r['branch_id'] not in u['branches']:continue
  if not readable(u['role'],r['kind']):continue
  out.append(dict(json.loads(r['fields']),id=r['id'],kind=r['kind'],doc_id=r['doc_id'],branch_id=r['branch_id'],entity_id=r['entity_id'],date=r['date'],import_id=r['import_id'],sheet=r['sheet'],row=r['row_no'],original=json.loads(r['original'])))
 return out

def calculate(c,w,u,f=None):
 f=f or {};s=settings(c,w);allrows=load(c,w,u);ms=masters(c,w)
 end=f.get('end') or datetime.date.today().isoformat();fy=int(s.get('financial_year_start',4));year=int(end[:4])-(int(end[5:7])<fy);start=f.get('start') or f'{year}-{fy:02d}-01'
 def scope(r):return all(not f.get(k) or r.get(k)==f[k] for k in ('entity_id','branch_id','department_id','panel_id','vendor_id','status')) and (not f.get('search') or f['search'].casefold() in ' '.join(str(r.get(k) or '') for k in ('doc_id','patient','uhid','admission_id','description','invoice_id','bill_id','vendor_id')).casefold())
 balance_rows=[r for r in allrows if r['date']<=end and scope(r)]
 period=[r for r in balance_rows if r['date']>=start]
 def of(kind,balance=False):return [r for r in (balance_rows if balance else period) if r['kind']==kind]
 rules=[dict(r,fields=json.loads(r['fields'])) for r in c.execute('SELECT * FROM rules WHERE workspace=? AND active=1',(w,))]
 byid={r['id']:r for r in allrows};exceptions=[]
 def issue(key,category,kind,branch,message,ids=None,exposure=None,expected=None,observed=None):
  threshold=D(s.get('materiality'));ex=D(exposure)
  exceptions.append({'key':key,'category':category,'issue_kind':kind,'branch_id':branch,'message':message,'record_ids':ids or [],'exposure':money(ex),'expected':expected,'observed':observed,'severity':'material' if threshold is not None and ex is not None and abs(ex)>=threshold else 'review','status':'open','source_basis':'Linked source record IDs; exposure may overlap other issues'})
 for kind in ('journal','billing','invoice','bank','inventory'):
  if not of(kind,True):issue('missing:'+kind,'Incomplete data','missing_data',f.get('branch_id'),'No '+TYPES[kind][0]+' records are available. Related metrics remain unknown.')
 # Detect duplicate business identities, without silently deleting legitimate lines.
 duplicate_groups=defaultdict(list)
 for r in balance_rows:
  if r['kind'] in ('billing','invoice','receipt','payment'):
   business_id=r.get({'billing':'bill_id','invoice':'invoice_id','receipt':'receipt_id','payment':'payment_id'}[r['kind']]) or r['doc_id']
   duplicate_groups[(r['kind'],r['branch_id'],r.get('vendor_id'),business_id)].append(r)
 for key,rs in duplicate_groups.items():
  if len(rs)>1:issue('duplicate:'+':'.join(str(x) for x in key),'Duplicate / conflicting documents','pattern_alert',rs[0]['branch_id'],'Several active records share this business document identity. Review overlaps before aggregation.',[r['id'] for r in rs],total(rs))
 # Journal financial statements: only mapped, balanced and declared complete books can support EBITDA/net result.
 accounts={r['key']:r['properties'].get('statement_category') for r in ms if r['kind']=='account'}
 journals=of('journal');vouchers=defaultdict(list)
 for r in of('journal',True):vouchers[(r['entity_id'],r['branch_id'],r.get('voucher_id'))].append(r)
 bad_vouchers=[]
 for key,rs in vouchers.items():
  difference=total(rs,'debit')-total(rs,'credit')
  if difference:
   bad_vouchers.append(key);issue('voucher:'+':'.join(key),'Unreconciled balances','numerical_discrepancy',key[1],'Accounting voucher debits and credits differ.',[r['id'] for r in rs],difference,'0.00',money(difference))
 unmapped=[r for r in of('journal',True) if accounts.get(r.get('account_id')) is None]
 for r in unmapped:issue('account:'+str(r['id']),'Incomplete data','missing_data',r['branch_id'],'Ledger account has no reviewed financial-statement category.',[r['id']],None)
 categories=defaultdict(lambda:Z);category_ids=defaultdict(list)
 if s.get('accounting_basis')=='trial_balance':
  tb=of('trial_balance',True);selected={}
  for r in sorted(tb,key=lambda r:(r['date'],r['id'])):selected[(r['branch_id'],r['account_id'])]=r
  compatible=[r for r in selected.values() if r.get('period_start')==start and r['date']==end]
  journals=[]
  for r in compatible:
   category=accounts.get(r['account_id'])
   if category:categories[category]+= (D(r['movement_debit']) or Z)-(D(r['movement_credit']) or Z);category_ids[category].append(r['id'])
   diff=(D(r['opening_debit']) or Z)-(D(r['opening_credit']) or Z)+(D(r['movement_debit']) or Z)-(D(r['movement_credit']) or Z)-(D(r['closing_debit']) or Z)+(D(r['closing_credit']) or Z)
   if diff:issue('tb:'+str(r['id']),'Unreconciled balances','numerical_discrepancy',r['branch_id'],'Trial balance opening plus movements does not equal closing.',[r['id']],diff)
  book_supported=bool(compatible) and len(compatible)==len(selected) and all(accounts.get(r['account_id']) for r in compatible) and total(compatible,'movement_debit')==total(compatible,'movement_credit') and s.get('accounting_complete') and not any(x['key'].startswith('tb:') for x in exceptions)
  accounting_ids=[r['id'] for r in compatible]
 else:
  for r in journals:
   category=accounts.get(r.get('account_id'))
   if category:categories[category]+=(D(r['debit']) or Z)-(D(r['credit']) or Z);category_ids[category].append(r['id'])
  book_supported=bool(journals) and not bad_vouchers and not unmapped and s.get('accounting_complete')
  accounting_ids=[r['id'] for r in journals]
 revenue=-categories['revenue'];direct=categories['direct_cost'];opex=categories['operating_expense'];ebitda=revenue-direct-opex
 statements={'basis':s.get('accounting_basis'),'accounting_complete':bool(book_supported),'aggregation_label':'Branch aggregation; not consolidated statements (no automatic eliminations)','revenue':money(revenue) if accounting_ids else None,'direct_cost':money(direct) if accounting_ids else None,'operating_expenses':money(opex) if accounting_ids else None,'ebitda':money(ebitda) if book_supported else None,'depreciation':money(categories['depreciation']) if book_supported else None,'finance_cost':money(categories['finance_cost']) if book_supported else None,'tax':money(categories['tax']) if book_supported else None,'net_result':money(ebitda-categories['depreciation']-categories['finance_cost']-categories['tax']) if book_supported else None,'rows':[{'category':k,'amount':money(-v if k=='revenue' else v),'record_ids':category_ids[k]} for k,v in categories.items()], 'record_ids':accounting_ids,'limitation':'Statements remain incomplete until all accounts and vouchers are verified and accounting coverage is approved. Trial balance snapshots are never added to journals.'}
 # Balance sheet only with supported opening data and verified books.
 balances=defaultdict(lambda:Z)
 if s.get('accounting_basis')=='trial_balance':
  for r in selected.values():balances[r['account_id']]+=(D(r['closing_debit']) or Z)-(D(r['closing_credit']) or Z)
  opening_ready=bool(selected) and book_supported
 else:
  opening_snapshots={}
  for r in sorted(of('opening_balance',True),key=lambda r:(r['date'],r['id'])):opening_snapshots[(r['branch_id'],r['account_id'])]=r
  for r in opening_snapshots.values():balances[r['account_id']]+=D(r['amount']) or Z
  for r in of('journal',True):
   opening=opening_snapshots.get((r['branch_id'],r['account_id']))
   if not opening or r['date']>=opening['date']:balances[r['account_id']]+=(D(r['debit']) or Z)-(D(r['credit']) or Z)
  opening_ready=s.get('opening_complete') and book_supported
 assets=sum((v for k,v in balances.items() if accounts.get(k) in ('asset','cash')),Z);liabilities=-sum((v for k,v in balances.items() if accounts.get(k)=='liability'),Z);equity=-sum((v for k,v in balances.items() if accounts.get(k)=='equity'),Z)
 retained=-sum((v for k,v in balances.items() if accounts.get(k) in ('revenue','direct_cost','operating_expense','depreciation','finance_cost','tax')),Z)
 bs_diff=assets-liabilities-equity-retained
 statements['balance_sheet']={'available':bool(opening_ready and not bs_diff and all(accounts.get(k) for k in balances)),'assets':money(assets) if opening_ready else None,'liabilities':money(liabilities) if opening_ready else None,'equity_including_result':money(equity+retained) if opening_ready else None,'difference':money(bs_diff) if opening_ready else None}
 if opening_ready and bs_diff:issue('balance-sheet','Unreconciled balances','numerical_discrepancy',f.get('branch_id'),'Assets differ from liabilities plus equity and retained result.',[],bs_diff)
 # Immutable cash/document allocations, evaluated using cash posting cutoff.
 allocations=[dict(r) for r in c.execute('SELECT * FROM allocations WHERE workspace=? AND active=1',(w,))]
 paid=defaultdict(lambda:Z);allocated_cash=defaultdict(lambda:Z)
 for a in allocations:
  cash=byid.get(a['cash_id']);target=byid.get(a['document_id'])
  if cash and target and cash['date']<=end and target['date']<=end:
   paid[target['id']]+=D(a['amount']);allocated_cash[cash['id']]+=D(a['amount'])
 # AR: bills are authority for operational receivables, approved claims are separate lifecycle values.
 ar=[];bill_lookup=defaultdict(list)
 for r in of('billing',True):bill_lookup[(r['branch_id'],r['bill_id'])].append(r)
 def age_rows(rs):
  for r in rs:
   age=(datetime.date.fromisoformat(end)-datetime.date.fromisoformat(r['date'])).days;r['age_days']=age
   boundaries=s['ageing_buckets'];r['age_bucket']=next((f'{0 if i==0 else boundaries[i-1]+1}–{b}' for i,b in enumerate(boundaries) if age<=b),f'Over {boundaries[-1]}')
   due=r.get('due_date');r['days_overdue']=max(0,(datetime.date.fromisoformat(end)-datetime.date.fromisoformat(due)).days) if due else None
 for r in of('billing',True):
  liability=Z if r.get('status')=='cancelled' else D(r['amount']) or Z;credits=Z;opening=Z
  for a in of('ar_adjustment',True):
   if a.get('bill_id')==r['bill_id'] and a['branch_id']==r['branch_id']:
    if a['adjustment_type']=='opening':opening+=D(a['amount']) or Z
    elif not yes(a.get('already_in_bill')):credits+=(D(a['amount']) or Z)*(Decimal(-1) if a['adjustment_type']=='reversal' else Decimal(1))
  events=sorted([e for e in of('claim',True) if e['branch_id']==r['branch_id'] and e['bill_id']==r['bill_id']],key=lambda e:(e['date'],e['id']))
  claim={};history=[]
  for e in events:
   claim.update({k:v for k,v in e.items() if v is not None});history.append({'event':e['event'],'date':e['date'],'record_id':e['id']})
  outstanding=opening+liability-credits-paid[r['id']]
  due=claim.get('due_date') or r.get('due_date')
  terms=[x for x in rules if x['kind']=='panel_terms' and x['branch_id'] in ('*',r['branch_id']) and x['fields'].get('panel_id')==r.get('panel_id') and x['fields']['start']<=r['date']<=x['fields']['end']]
  submissions=[e for e in events if e['event'] in ('submitted','resubmitted')];queries=[e for e in events if e['event']=='query'];responses=[e for e in events if e['event']=='response']
  if len(terms)==1:
   terms=terms[0]['fields']
   if not due and submissions and terms.get('payment_days') is not None:due=(datetime.date.fromisoformat(submissions[-1]['date'])+datetime.timedelta(days=int(terms['payment_days']))).isoformat()
   if not submissions and terms.get('submission_days') is not None and (datetime.date.fromisoformat(end)-datetime.date.fromisoformat(r['date'])).days>int(terms['submission_days']):issue('claim-submit:'+str(r['id']),'Delayed collections','timing_alert',r['branch_id'],'Billed case has not been submitted within the reviewed panel deadline.',[r['id']],liability)
   if queries and not any(e['date']>=queries[-1]['date'] for e in responses) and terms.get('response_days') is not None and (datetime.date.fromisoformat(end)-datetime.date.fromisoformat(queries[-1]['date'])).days>int(terms['response_days']):issue('claim-response:'+str(r['id']),'Delayed collections','timing_alert',r['branch_id'],'Panel query has no response within the reviewed response deadline.',[r['id']]+[queries[-1]['id']],liability)
  elif len(terms)>1:issue('panel-terms:'+str(r['id']),'Incomplete data','missing_data',r['branch_id'],'Overlapping applicable panel terms; no due date is inferred.',[r['id']])
  item=dict(r,opening=money(opening),valid_additions=money(liability),credits_writeoffs=money(credits),settlements=money(paid[r['id']]),outstanding=money(outstanding),claim_status=claim.get('event','not submitted'),payment_status='unpaid' if not paid[r['id']] else 'paid' if outstanding==0 else 'overpaid' if outstanding<0 else 'partially paid',approved_amount=claim.get('approved_amount'),deduction=claim.get('deduction'),expected_date=claim.get('expected_date'),due_date=due,owner=claim.get('owner'),next_action=claim.get('next_action'),claim_history=history,claim_id=claim.get('claim_id'))
  ar.append(item)
  if outstanding<0:issue('ar-excess:'+str(r['id']),'Excess settlement','numerical_discrepancy',r['branch_id'],'Allocated receipts exceed the valid bill liability.',[r['id']],-outstanding)
  threshold=D(s.get('discount_threshold'))
  if threshold is not None and r.get('gross') and (D(r.get('discount')) or Z)/D(r['gross'])*100>threshold and not r.get('approved_discount'):issue('discount:'+str(r['id']),'Revenue leakage','pattern_alert',r['branch_id'],'Discount exceeds the configured review threshold and lacks approval evidence.',[r['id']],r.get('discount'))
  if r.get('status')=='cancelled' and paid[r['id']]:issue('cancelled-receipt:'+str(r['id']),'Unreconciled balances','numerical_discrepancy',r['branch_id'],'Cancelled bill still has allocated receipts; review refund or allocation.',[r['id']],paid[r['id']])
 standalone_ar=[r for r in of('ar_adjustment',True) if r['adjustment_type']=='opening' and r.get('bill_id')=='OPENING']
 age_rows(ar)
 for r in ar:
  if r['days_overdue'] and D(r['outstanding'])>0:issue('ar-overdue:'+str(r['id']),'Delayed collections','timing_alert',r['branch_id'],'This receivable is past its recorded due date.',[r['id']],r['outstanding'])
 for r in of('receipt',True):
  remaining=(D(r['amount']) or Z)-allocated_cash[r['id']]
  if r.get('receipt_type') not in ('transfer','loan') and remaining:issue('receipt-unallocated:'+str(r['id']),'Unallocated receipts','numerical_discrepancy',r['branch_id'],f'₹{money(remaining)} is recorded as received but has not been allocated to a bill.',[r['id']],remaining)
  if r.get('receipt_type')=='refund' and (not r.get('original_receipt_id') or not r.get('authorisation')):issue('refund:'+str(r['id']),'Unsupported adjustments','missing_data',r['branch_id'],'Refund lacks an original receipt or authorisation.',[r['id']],r['amount'])
 for r in of('encounter',True):
  if r.get('discharge_date') and not any(b.get('admission_id')==r['admission_id'] and b['branch_id']==r['branch_id'] for b in of('billing',True)):issue('unbilled-discharge:'+str(r['id']),'Revenue leakage','missing_data',r['branch_id'],'Discharged encounter has no linked final bill.',[r['id']])
 for r in of('service'):
  if yes(r.get('performed')) and yes(r.get('expected_bill')) and not bill_lookup.get((r['branch_id'],r.get('bill_id'))):issue('unbilled-service:'+str(r['id']),'Revenue leakage','missing_data',r['branch_id'],'Performed service expected to be billed has no linked final bill.',[r['id']],r.get('amount'))
  tariffs=[x for x in rules if x['kind']=='tariff' and x['branch_id'] in ('*',r['branch_id']) and x['fields'].get('code')==r.get('code') and r.get('code') and x['fields']['start']<=r['date']<=x['fields']['end']]
  if len(tariffs)==1 and r.get('quantity') is not None and r.get('amount') is not None and tariffs[0]['fields'].get('rate') is not None:
   expected=D(tariffs[0]['fields']['rate'])*D(r['quantity'])
   if expected!=D(r['amount']):issue('tariff:'+str(r['id']),'Revenue leakage','numerical_discrepancy',r['branch_id'],'Service amount differs from the approved per-unit tariff; review package/discount applicability.',[r['id']],expected-D(r['amount']),money(expected),r['amount'])
 # AP liability rollforward and source-dependent procurement checks.
 ap=[]
 for r in of('invoice',True):
  credits=Z;opening=Z
  for a in of('ap_adjustment',True):
   if a.get('invoice_id')==r['invoice_id'] and a['branch_id']==r['branch_id'] and a.get('vendor_id')==r.get('vendor_id'):
    if a['adjustment_type']=='opening':opening+=D(a['amount']) or Z
    elif not yes(a.get('already_in_invoice')):credits+=(D(a['amount']) or Z)*(Decimal(-1) if a['adjustment_type']=='reversal' else Decimal(1))
  valid=Z if r.get('status')=='cancelled' else D(r['amount']) or Z
  outstanding=opening+valid-credits-paid[r['id']]
  po=[p for p in of('purchase_order',True) if p['branch_id']==r['branch_id'] and p.get('po_id')==r.get('po_id') and p.get('item_id')==r.get('item_id')]
  grn=[p for p in of('goods_receipt',True) if p['branch_id']==r['branch_id'] and p.get('grn_id')==r.get('grn_id') and p.get('item_id')==r.get('item_id')]
  matching='Matching source unavailable' if not of('purchase_order',True) or not of('goods_receipt',True) else 'Unmatched documents' if len(po)!=1 or len(grn)!=1 else 'Quantity/rate unavailable' if any(x.get('quantity') is None or x.get('rate') is None for x in [r,po[0],grn[0]]) else 'Matched' if D(r['quantity'])==D(po[0]['quantity'])==D(grn[0]['quantity']) and D(r['rate'])==D(po[0]['rate'])==D(grn[0]['rate']) else 'Quantity/rate difference'
  ap.append(dict(r,opening=money(opening),credits=money(credits),settlements=money(paid[r['id']]),outstanding=money(outstanding),three_way_match=matching,payment_priority='Disputed — review' if yes(r.get('disputed')) else 'Clinical critical' if yes(r.get('critical')) else 'Due-date order'))
  if matching in ('Unmatched documents','Quantity/rate difference'):issue('procurement:'+str(r['id']),'Procurement mismatches','numerical_discrepancy',r['branch_id'],matching+' in invoice, PO and goods receipt.',[r['id']]+[x['id'] for x in po+grn],r['amount'])
  if outstanding<0:issue('ap-excess:'+str(r['id']),'Duplicate or excess payments','numerical_discrepancy',r['branch_id'],'Allocated payments exceed invoice liability.',[r['id']],-outstanding)
 age_rows(ap);ap.sort(key=lambda r:(yes(r.get('disputed')),not yes(r.get('critical')),r.get('due_date') or '9999-12-31',r['id']))
 standalone_ap=[r for r in of('ap_adjustment',True) if r['adjustment_type']=='opening' and r.get('invoice_id')=='OPENING']
 vendors={r['key']:r for r in ms if r['kind']=='vendor'}
 for r in of('payment',True):
  if r.get('vendor_id') and (r['vendor_id'] not in vendors or not yes(vendors[r['vendor_id']]['properties'].get('approved'))):issue('vendor-payment:'+str(r['id']),'Unsupported payments','missing_data',r['branch_id'],'Payment references an inactive, unknown or unapproved vendor.',[r['id']],r['amount'])
 # Treasury: balances are snapshots; movements never add an extra income total.
 bankrows=of('bank',True);bookrows=of('cashbook',True);matchrows=[dict(r) for r in c.execute('SELECT * FROM bank_matches WHERE workspace=? AND active=1',(w,))];bank_paid=defaultdict(lambda:Z);book_paid=defaultdict(lambda:Z)
 for r in matchrows:
  if r['bank_id'] in byid and r['book_id'] in byid and byid[r['bank_id']]['date']<=end and byid[r['book_id']]['date']<=end:
   bank_paid[r['bank_id']]+=D(r['amount']);book_paid[r['book_id']]+=D(r['amount'])
 bank_details=[];book_details=[];suggestions=[]
 for r in bankrows:
  if r['entry_type']!='movement':continue
  remainder=(D(r.get('amount')) or Z)-bank_paid[r['id']]
  bank_details.append(dict(r,matched_amount=money(bank_paid[r['id']]),unmatched_amount=money(remainder)))
  candidates=[b for b in bookrows if b.get('entry_type','movement')=='movement' and b.get('bank_account_id')==r.get('bank_account_id') and b['branch_id']==r['branch_id'] and b['date']==r['date'] and D(b.get('amount'))==D(r.get('amount')) and (not r.get('reference') or b.get('reference')==r.get('reference')) and (D(b.get('amount')) or Z)-book_paid[b['id']]]
  if remainder:suggestions.append({'bank_id':r['id'],'candidate_book_ids':[x['id'] for x in candidates],'status':'unique suggestion; review required' if len(candidates)==1 else 'ambiguous' if candidates else 'unmatched','amount':money(remainder)})
 for r in bookrows:
  if r.get('entry_type','movement')!='movement':continue
  book_details.append(dict(r,matched_amount=money(book_paid[r['id']]),unmatched_amount=money((D(r.get('amount')) or Z)-book_paid[r['id']])))
 bank_balances=[]
 for key in sorted(set((r['branch_id'],r['bank_account_id']) for r in bankrows+bookrows)):
  rs=[r for r in bankrows if (r['branch_id'],r['bank_account_id'])==key];books=[r for r in bookrows if (r['branch_id'],r['bank_account_id'])==key]
  opening=sorted([r for r in rs if r['entry_type']=='opening'],key=lambda r:(r['date'],r['id']))
  closing=sorted([r for r in rs if r['entry_type'] in ('closing','cash_count')],key=lambda r:(r['date'],r['id']))
  balance=D(closing[-1]['amount']) if closing and closing[-1].get('amount') is not None else None
  verified_date=closing[-1]['date'] if closing else None
  expected=(D(opening[-1]['amount']) or Z)+total([r for r in rs if r['entry_type']=='movement' and r['date']>=opening[-1]['date'] and (not closing or r['date']<=closing[-1]['date'])]) if opening else None
  difference=balance-expected if balance is not None and expected is not None else None
  book_open=sorted([r for r in books if r.get('entry_type')=='opening'],key=lambda r:(r['date'],r['id']));book_balance=(D(book_open[-1].get('amount')) or Z)+total([r for r in books if r.get('entry_type','movement')=='movement' and r['date']>=book_open[-1]['date']]) if book_open else None
  bank_balances.append({'branch_id':key[0],'bank_account_id':key[1],'verified_balance':money(balance),'verified_date':verified_date,'book_balance':money(book_balance),'movement_rollforward':money(expected),'difference':money(difference),'record_ids':[r['id'] for r in rs+books]})
  if difference:issue('bank-balance:'+':'.join(key),'Cash/bank differences','numerical_discrepancy',key[0],'Bank opening plus movements differs from its recorded closing snapshot.',[r['id'] for r in rs],difference)
 # Expenses and effective-dated consultant contracts; no substitution of collected and billed bases.
 expenses=of('expense')+of('payroll')+of('consultant')
 for r in expenses:
  r['paid_amount']=money(paid[r['id']]);r['unpaid_amount']=money((D(r.get('amount')) or Z)-paid[r['id']]);r['contract_expected']=None
  if r['kind']=='consultant':
   eligible=[rule for rule in rules if rule['kind']=='consultant' and rule['branch_id'] in ('*',r['branch_id']) and rule['fields'].get('consultant_id')==r['consultant_id'] and rule['fields'].get('start','')<=r['date']<=rule['fields'].get('end','')]
   if len(eligible)==1:
    rule=eligible[0]['fields'];basis=rule['basis'];rate=D(rule['rate']);value=None
    if basis=='fixed':value=rate
    elif basis=='per_procedure' and r.get('quantity') is not None:value=rate*D(r['quantity'])
    elif basis=='percent' and r.get('base_amount') is not None and r.get('base_kind')==rule.get('base_kind'):value=(D(r['base_amount'])-(D(r.get('exclusions')) or Z))*rate/100
    if value is not None:
     value-=(D(r.get('deductions')) or Z)
     if rule.get('ceiling') is not None:value=min(value,D(rule['ceiling']))
     r['contract_expected']=money(value);r['contract_rule_id']=eligible[0]['id']
     if value!=D(r['amount']):issue('consultant:'+str(r['id']),'Contract-calculation difference','numerical_discrepancy',r['branch_id'],'Recorded consultant payout differs from the reviewed contract calculation.',[r['id']],D(r['amount'])-value,money(value),r['amount'])
   elif len(eligible)>1:issue('contract-overlap:'+str(r['id']),'Incomplete data','missing_data',r['branch_id'],'Overlapping applicable consultant contracts; no estimated payout calculated.',[r['id']])
  if not r.get('evidence'):issue('expense-evidence:'+str(r['id']),'Unsupported expenses','missing_data',r['branch_id'],'Recorded expense lacks supporting evidence.',[r['id']],r.get('amount'))
 # Inventory signed quantity/value rollforward, count snapshots separate from movements.
 inventory=[];inventory_groups=defaultdict(list)
 for r in of('inventory',True):inventory_groups[(r['branch_id'],r.get('store_id'),r['item_id'],r.get('batch_id'))].append(r)
 for key,rs in inventory_groups.items():
  openings=sorted([r for r in rs if r['movement_type']=='opening'],key=lambda r:(r['date'],r['id']))
  movements=([openings[-1]]+[r for r in rs if r['movement_type'] not in ('count','opening') and r['date']>=openings[-1]['date']]) if openings else [r for r in rs if r['movement_type']!='count']
  counts=sorted([r for r in rs if r['movement_type']=='count'],key=lambda r:(r['date'],r['id']))
  quantity=total(movements,'quantity') if openings else None;value=known_total(movements,'actual_cost') if openings else None
  difference=D(counts[-1]['quantity'])-total([r for r in movements if r['date']<=counts[-1]['date']],'quantity') if counts and openings else None
  expiry=next((r['expiry_date'] for r in rs if r.get('expiry_date')),None)
  inventory.append({'branch_id':key[0],'store_id':key[1],'item_id':key[2],'batch_id':key[3],'closing_quantity':money(quantity),'recorded_inventory_value':value,'physical_count_difference':money(difference),'expiry_date':expiry,'expiry_exposure':value if expiry and expiry<=end and quantity is not None and quantity>0 else None,'method':'Latest opening + recorded signed cost movements; not MRP','record_ids':[r['id'] for r in rs]})
  if quantity is not None and quantity<0:issue('stock-negative:'+':'.join(str(x) for x in key),'Stock discrepancies','numerical_discrepancy',key[0],'Recorded inventory quantity is negative.',[r['id'] for r in rs])
  if not openings:issue('stock-opening:'+':'.join(str(x) for x in key),'Incomplete data','missing_data',key[0],'Inventory opening quantity and value are missing; closing stock remains unknown.',[r['id'] for r in rs])
  if difference:issue('stock-count:'+':'.join(str(x) for x in key),'Stock discrepancies','numerical_discrepancy',key[0],'Physical stock count differs from the recorded rollforward.',[r['id'] for r in rs],None,money(quantity),money(D(counts[-1]['quantity'])))
 transfers=defaultdict(list)
 for r in of('inventory',True):
  if r.get('transfer_id'):transfers[r['transfer_id']].append(r)
  if r['movement_type']=='issue' and yes(r.get('billing_expected')) and not bill_lookup.get((r['branch_id'],r.get('bill_id'))):issue('stock-unbilled:'+str(r['id']),'Revenue leakage','missing_data',r['branch_id'],'Stock issue expected to be billed has no matched charge.',[r['id']],r.get('actual_cost'))
 for key,rs in transfers.items():
  if total(rs,'quantity') or (all(r.get('actual_cost') is not None for r in rs) and total(rs,'actual_cost')):issue('stock-transfer:'+key,'Stock discrepancies','numerical_discrepancy',rs[0]['branch_id'],'Inventory transfer counterparts do not balance. Transfers are not group revenue/expense.',[r['id'] for r in rs])
 pharmacy=[]
 for source in ('Main','Secondary'):
  rs=[r for r in of('pharmacy') if r.get('pharmacy_source','Main')==source];eligible=[r for r in rs if r.get('actual_cost') is not None]
  profit=total(eligible)-total(eligible,'actual_cost')
  pharmacy.append({'source':source,'net_sales':known_total(rs),'recorded_cogs':known_total(eligible,'actual_cost'),'eligible_sales':money(total(eligible)) if eligible else None,'gross_profit':money(profit) if eligible else None,'margin':pct(profit,total(eligible)) if eligible else None,'missing_cost_lines':len(rs)-len(eligible),'returns':money(total([r for r in rs if D(r.get('amount')) is not None and D(r['amount'])<0])) if rs else None,'record_ids':[r['id'] for r in rs]})
 # Assets and loan principal/interest kept separate.
 assets_out=[]
 for r in of('asset',True):
  depreciation=D(r.get('accumulated_depreciation'));estimated=None
  if r.get('depreciation_policy')=='straight_line' and r.get('commission_date') and r.get('useful_life_months') and D(r['useful_life_months'])>0:
   d=datetime.date.fromisoformat(r['commission_date']);e=datetime.date.fromisoformat(end);months=max(0,(e.year-d.year)*12+e.month-d.month)
   estimated=min(D(r['amount'])-(D(r.get('residual_value')) or Z),(D(r['amount'])-(D(r.get('residual_value')) or Z))*Decimal(months)/D(r['useful_life_months']))
  assets_out.append(dict(r,recorded_depreciation=money(depreciation),estimated_depreciation=money(estimated),carrying_amount=money(D(r['amount'])-depreciation) if depreciation is not None else None,capex_variance=money(D(r['amount'])-D(r['approved_budget'])) if r.get('approved_budget') is not None else None))
 loans=[]
 for entity_id,branch_id,loan_id in sorted(set((r['entity_id'],r['branch_id'],r['loan_id']) for r in of('loan',True))):
  rs=[r for r in of('loan',True) if (r['entity_id'],r['branch_id'],r['loan_id'])==(entity_id,branch_id,loan_id)];openings=sorted([r for r in rs if r['event']=='opening'],key=lambda r:(r['date'],r['id']));opening=D(openings[-1]['amount']) if openings else Z;movements=[r for r in rs if not openings or r['date']>=openings[-1]['date']];drawdowns=total([r for r in movements if r['event']=='drawdown']);principal=total([r for r in movements if r['event']=='principal']);interest=total([r for r in rs if r['event'] in ('interest','fee')]);schedules=[r for r in rs if r['event'].startswith('scheduled_') and not r.get('paid_doc_id')]
  loans.append({'entity_id':entity_id,'branch_id':branch_id,'loan_id':loan_id,'opening':money(opening),'drawdowns':money(drawdowns),'principal_repaid':money(principal),'closing_principal':money(opening+drawdowns-principal) if any(r['event']=='opening' for r in rs) else None,'finance_cost':money(interest),'schedule':schedules,'record_ids':[r['id'] for r in rs]})
 # Statutory liability rollforward; rates/deadlines are user supplied, never legal defaults.
 statutory=[]
 for entity_id,branch_id,liability_id in sorted(set((r['entity_id'],r['branch_id'],r['liability_id']) for r in of('statutory',True))):
  rs=[r for r in of('statutory',True) if (r['entity_id'],r['branch_id'],r['liability_id'])==(entity_id,branch_id,liability_id)];liability=total([r for r in rs if r['event']=='liability']);payments=total([r for r in rs if r['event']=='payment']);credits=total([r for r in rs if r['event']=='credit'])
  statutory.append({'entity_id':entity_id,'branch_id':branch_id,'liability_id':liability_id,'liability':money(liability),'paid':money(payments),'credits':money(credits),'outstanding':money(liability-payments-credits),'due_date':next((r['due_date'] for r in rs if r.get('due_date')),None),'record_ids':[r['id'] for r in rs]})
 # Shared-cost allocation: selected drivers are explicit, unallocatable amounts stay visible.
 shared_allocations=[]
 for rule in rules:
  if rule['kind']!='shared_cost' or u['role'] not in ('management','finance') or '*' not in u['branches'] and rule['branch_id'] not in u['branches']:continue
  if '*' not in u['branches'] and any(br not in u['branches'] for br in rule['fields']['branches']):continue
  p=rule['fields'];base=[r for r in allrows if p['start']<=r['date']<=p['end'] and r['branch_id'] in p['branches'] and (r['kind']=='billing' if p['driver']=='revenue' else r['kind']=='encounter' if p['driver']=='bed_days' else False)]
  weights=defaultdict(lambda:Z)
  if p['driver'] in ('headcount','floor_area','usage'):weights.update({k:D(v) for k,v in p.get('weights',{}).items() if k in p['branches']})
  else:
   for r in base:weights[r['branch_id']]+=max(D(r.get('amount' if p['driver']=='revenue' else 'occupied_bed_days')) or Z,Z)
  denominator=sum(weights.values(),Z);amount=D(p['amount']);allocated=[]
  if denominator:
   keys=sorted(weights);exact=[amount*100*weights[k]/denominator for k in keys];cents=[int(x) for x in exact];left=int(amount*100)-sum(cents)
   for i in sorted(range(len(keys)),key=lambda i:(-(exact[i]-cents[i]),keys[i]))[:left]:cents[i]+=1
   allocated=[{'branch_id':k,'amount':money(Decimal(cents[i])/100)} for i,k in enumerate(keys)]
  visible=[r for r in allocated if (not f.get('branch_id') or r['branch_id']==f['branch_id']) and ('*' in u['branches'] or r['branch_id'] in u['branches'])]
  shared_allocations.append({'rule_id':rule['id'],'driver':p['driver'],'period':[p['start'],p['end']],'pool':money(amount),'allocations':visible,'unallocated':money(amount if not denominator else Z),'label':'Allocated shared cost; not a new recorded expense'})
 # 13-week forecast: expected cash is distinct from actual and assumptions never replace actuals.
 verified_cash=known_total(bank_balances,'verified_balance') if bank_balances else None
 current_cash=D(verified_cash);forecast=[];obligations=[]
 for r in ar:
  if D(r['outstanding'])>0 and r.get('expected_date'):obligations.append({'id':r['id'],'due_date':r['expected_date'],'amount':r['outstanding'],'direction':'inflow','source':'receivable'})
 invoice_keys={(r['branch_id'],r['invoice_id']) for r in ap}
 for r in ap:
  if D(r['outstanding'])>0 and r.get('due_date'):obligations.append({'id':r['id'],'due_date':r['due_date'],'amount':r['outstanding'],'direction':'outflow','source':'invoice'})
 obligation_keys={str(x['id']) for x in obligations}
 for r in of('commitment',True)+of('expense',True)+of('payroll',True)+of('consultant',True):
  if not r.get('due_date') or r.get('invoice_id') and (r['branch_id'],r['invoice_id']) in invoice_keys:continue
  if r['kind']=='commitment' and not yes(r.get('approved')):continue
  amount=(D(r.get('amount')) or Z)-paid[r['id']]
  if amount>0:obligations.append({'id':r['id'],'due_date':r['due_date'],'amount':money(amount),'direction':r.get('direction','outflow'),'source':r['kind']});obligation_keys.add(str(r['id']))
 for r in statutory:
  if r.get('due_date') and D(r['outstanding'])>0:obligations.append({'id':r['liability_id'],'due_date':r['due_date'],'amount':r['outstanding'],'direction':'outflow','source':'statutory'})
 for loan in loans:
  for r in loan['schedule']:
   if r.get('due_date'):obligations.append({'id':r['id'],'due_date':r['due_date'],'amount':r['amount'],'direction':'outflow','source':'loan schedule'})
 for scenario in ('base','delayed','stress'):
  if scenario!='base' and (s.get('collection_delay_days') is None or scenario=='stress' and s.get('stress_collection_percent') is None):
   forecast.append({'scenario':scenario,'rows':[],'opening_source':'Scenario unavailable until collection delay/stress assumptions are explicitly configured','assumptions':{'collection_delay_days':s.get('collection_delay_days'),'stress_collection_percent':s.get('stress_collection_percent')}});continue
  balance=current_cash;rows=[];base_day=datetime.date.fromisoformat(end)
  obs=list(obligations)
  for r in of('forecast_assumption',True):
   if r.get('scenario','base') not in ('base',scenario):continue
   if r.get('linked_doc_id') and str(r['linked_doc_id']) in obligation_keys:continue
   obs.append({'id':r['id'],'due_date':r['due_date'],'amount':r['amount'],'direction':r['direction'],'source':'explicit scenario assumption'})
  for i in range(13):
   a=base_day+datetime.timedelta(days=i*7);b=a+datetime.timedelta(days=6);inflow=Z;outflow=Z;ids=[]
   for r in obs:
    day=datetime.date.fromisoformat(r['due_date']);amount=D(r['amount'])
    if r['direction']=='inflow' and scenario in ('delayed','stress'):day+=datetime.timedelta(days=int(s['collection_delay_days']))
    if r['direction']=='inflow' and scenario=='stress':amount*=D(s['stress_collection_percent'])/100
    if a<=day<=b:
     if r['direction']=='inflow':inflow+=amount
     else:outflow+=amount
     ids.append(r['id'])
   opening=balance;balance=None if balance is None else balance+inflow-outflow
   rows.append({'week':i+1,'start':a.isoformat(),'end':b.isoformat(),'opening':money(opening),'expected_inflow':money(inflow),'expected_outflow':money(outflow),'closing':money(balance),'document_ids':ids,'label':'Source-supported obligations only; completeness unverified'})
  forecast.append({'scenario':scenario,'rows':rows,'opening_source':'Recorded bank/cash snapshots; check stale dates and account coverage','assumptions':{'collection_delay_days':s['collection_delay_days'],'stress_collection_percent':s['stress_collection_percent']}})
 # Budget actuals only from compatible mapped accounting categories; zero denominator -> N/A.
 budgets=[]
 for r in of('budget'):
  category=r['category_id'];actual=None
  if accounting_ids and book_supported and s.get('accounting_basis')=='journal':
   month=r.get('month') or r['date'][:7];compatible=[j for j in journals if j['date'].startswith(month) and j['branch_id']==r['branch_id'] and j['entity_id']==r['entity_id'] and (not r.get('department_id') or j.get('department_id')==r['department_id']) and accounts.get(j.get('account_id'))==category]
   actual=total(compatible,'credit')-total(compatible,'debit') if category=='revenue' else total(compatible,'debit')-total(compatible,'credit')
  variance=actual-D(r['amount']) if actual is not None else None
  budgets.append(dict(r,actual=money(actual),variance=money(variance),variance_percent=pct(variance,D(r['amount'])) if variance is not None else None,interpretation='Review volume, quality and context; favourable/unfavourable is not inferred from sign alone'))
 scenarios=[]
 for r in of('finance_scenario'):
  required=['patient_volume','price_per_patient','deduction_percent','variable_cost_per_patient','fixed_operating_cost','payroll_cost','consultant_cost','consumption_cost']
  complete=all(r.get(k) is not None for k in required)
  earned=D(r['patient_volume'])*D(r['price_per_patient'])*(1-D(r['deduction_percent'])/100) if all(r.get(k) is not None for k in required[:3]) else None
  contribution=earned-D(r['patient_volume'])*D(r['variable_cost_per_patient'])-sum((D(r[k]) for k in required[4:]),Z) if complete else None
  collections=earned*D(r['collection_percent'])/100 if earned is not None and r.get('collection_percent') is not None else None
  unit_contribution=D(r['price_per_patient'])*(1-D(r['deduction_percent'])/100)-D(r['variable_cost_per_patient']) if complete else None
  fixed=sum((D(r[k]) for k in required[4:]),Z) if complete else None
  scenarios.append(dict(r,estimated_earned_revenue=money(earned),estimated_operating_contribution=money(contribution),expected_collections=money(collections),break_even_patients=money(fixed/unit_contribution) if unit_contribution is not None and unit_contribution>0 else None,assumed_occupancy=pct(D(r['occupied_bed_days']),D(r['capacity_bed_days'])) if r.get('occupied_bed_days') is not None and r.get('capacity_bed_days') is not None else None,label='Explicit assumptions only; not historical actuals, audited profit or a causal attribution model'))
 # Operational contribution uses final bills only, not pharmacy/service sales added again.
 operational=known_total(of('billing'));operational_costs=of('service')+of('pharmacy');eligible_costs=[r for r in operational_costs if r.get('actual_cost') is not None]
 op_cost=money(total(eligible_costs,'actual_cost')) if eligible_costs else None
 contribution=money(D(operational)-D(op_cost)) if operational is not None and op_cost is not None else None
 # Pharmacy related service costs can duplicate medicine COGS; no automatic assertion of completeness.
 if any(r.get('department_id')=='pharmacy' and r.get('actual_cost') is not None for r in of('service')) and of('pharmacy'):
  contribution=None;issue('operational-cost-overlap','Incomplete data','missing_data',f.get('branch_id'),'Pharmacy-related service cost and medicine COGS may overlap. Operational contribution is unavailable pending reviewed cost-head reconciliation.')
 branch_comparison=[]
 for branch in sorted(set(r['branch_id'] for r in period)):
  br=[r for r in period if r['branch_id']==branch];bill=[r for r in br if r['kind']=='billing'];journal=[r for r in br if r['kind']=='journal']
  br_rev=sum(((D(r.get('credit')) or Z)-(D(r.get('debit')) or Z) for r in journal if accounts.get(r.get('account_id'))=='revenue'),Z)
  branch_comparison.append({'branch_id':branch,'operational_billing':known_total(bill),'accounting_revenue':money(br_rev) if journal else None,'documents':len(br),'record_ids':[r['id'] for r in br]})
 monthly=[]
 for month in sorted(set(r['date'][:7] for r in period)):
  rs=[r for r in period if r['date'].startswith(month)];monthly.append({'month':month,'operational_billing':known_total([r for r in rs if r['kind']=='billing']),'receipts':known_total([r for r in rs if r['kind']=='receipt' and r.get('receipt_type') not in ('transfer','loan')]),'record_ids':[r['id'] for r in rs]})
 department_summary=[]
 for department in sorted(set(r.get('department_id') or 'Unassigned' for r in period if r['kind'] in ('billing','service','pharmacy','expense','payroll','consultant'))):
  rs=[r for r in period if (r.get('department_id') or 'Unassigned')==department];detail=[r for r in rs if r['kind'] in ('service','pharmacy')];complete=[r for r in detail if r.get('actual_cost') is not None and r.get('amount') is not None]
  department_summary.append({'department_id':department,'operational_billing':known_total([r for r in rs if r['kind']=='billing']),'detail_revenue':known_total(detail),'eligible_contribution':money(total(complete)-total(complete,'actual_cost')) if complete else None,'missing_cost_lines':sum(r.get('actual_cost') is None for r in detail),'record_ids':[r['id'] for r in rs]})
 credit_rev=total([r for r in of('billing') if yes(r.get('credit_revenue'))])
 def liability_rollforward(details,openings,kind,adjustment_kind,target_key,already_key):
  # OPENING denotes a snapshot before its posting date, never an extra invoice/bill.
  if not openings:return total(details,'outstanding'),[]
  snapshots={}
  group_field='vendor_id' if kind=='invoice' else 'panel_id'
  for r in sorted(openings,key=lambda r:(r['date'],r['id'])):snapshots[(r['branch_id'],r.get(group_field))]=r
  covered=set();roll=[];result=Z
  for (br,group),snapshot in snapshots.items():
   documents=[r for r in details if r['branch_id']==br and (group is None or r.get(group_field)==group)]
   if any(r['id'] in covered for r in documents):
    issue('overlapping-opening:'+kind+br,'Unreconciled balances','missing_data',br,'Opening snapshots overlap the same documents. Resolve scope before using the closing balance.',[snapshot['id']]);return None,roll
   covered.update(r['id'] for r in documents);cutoff=snapshot['date'];ids={r['id'] for r in documents}
   additions=sum((D(r['amount']) or Z for r in documents if r['date']>=cutoff and r.get('status')!='cancelled'),Z)
   settlements=sum((D(a['amount']) for a in allocations if a['document_id'] in ids and a['cash_id'] in byid and cutoff<=byid[a['cash_id']]['date']<=end),Z)
   credits=Z
   for adjustment in of(adjustment_kind,True):
    if adjustment['date']>=cutoff and adjustment['branch_id']==br and adjustment.get(target_key) in {r.get(target_key) for r in documents} and adjustment.get('adjustment_type')!='opening' and not yes(adjustment.get(already_key)):
     credits+=(D(adjustment['amount']) or Z)*(Decimal(-1) if adjustment['adjustment_type']=='reversal' else Decimal(1))
   opening=D(snapshot['amount']);closing=opening+additions-settlements-credits;result+=closing
   roll.append({'branch_id':br,'group':group,'opening_date':cutoff,'opening':money(opening),'additions':money(additions),'settlements':money(settlements),'credits':money(credits),'closing':money(closing),'record_ids':[snapshot['id']]+sorted(ids),'basis':'Latest opening snapshot + subsequent additions − subsequent allocated settlements − subsequent credits'})
  result+=total([r for r in details if r['id'] not in covered],'outstanding');return result,roll
 receivable,ar_rollforward=liability_rollforward(ar,standalone_ar,'billing','ar_adjustment','bill_id','already_in_bill')
 payable,ap_rollforward=liability_rollforward(ap,standalone_ap,'invoice','ap_adjustment','invoice_id','already_in_invoice')
 dso=money(receivable/credit_rev*Decimal((datetime.date.fromisoformat(end)-datetime.date.fromisoformat(start)).days+1)) if receivable is not None and s.get('credit_revenue_complete') and ar and credit_rev else None
 # Stored review states overlay stable generated exceptions; no misleading loss total.
 stored={r['key']:dict(r) for r in c.execute('SELECT * FROM exceptions WHERE workspace=?',(w,))}
 for e in exceptions:
  if e['key'] in stored:e.update({k:stored[e['key']][k] for k in ('status','owner','due_date','resolution')})
 data_setup=[]
 for kind,contract in TYPES.items():
  rs=[r for r in allrows if r['kind']==kind and scope(r)];dates=[r['date'] for r in rs]
  data_setup.append({'kind':kind,'name':contract[0],'section':contract[1],'records':len(rs),'coverage':[min(dates),max(dates)] if dates else None,'last_update':max(dates) if dates else None,'status':'Available; completeness requires review' if rs else 'Source unavailable','missing_input':None if rs else 'Upload or enter '+contract[0]+'; review its amount/date/identifier mapping.'})
 kpis={'accounting_revenue':statements['revenue'],'operating_expenses':statements['operating_expenses'],'ebitda':statements['ebitda'],'net_result':statements['net_result'],'operational_billing':operational,'operational_contribution':contribution,'known_operational_cost':op_cost,'bank_verified_balance':verified_cash,'receivables':money(receivable) if ar or standalone_ar else None,'payables':money(payable) if ap or standalone_ap else None,'inventory_value':known_total(inventory,'recorded_inventory_value'),'debt_principal':known_total(loans,'closing_principal'),'dso':dso,'unallocated_receipts':money(sum(((D(r.get('amount')) or Z)-allocated_cash[r['id']] for r in of('receipt',True) if r.get('receipt_type') not in ('transfer','loan')),Z)) if of('receipt',True) else None}
 discharged=[r for r in of('billing',True) if r.get('discharge_date') and start<=r['discharge_date']<=end];admissions={(r['branch_id'],r.get('admission_id')) for r in discharged}
 economics={'revenue_per_discharge':money(total(discharged)/Decimal(len(admissions))) if discharged and all(r.get('admission_id') for r in discharged) else None,'occupied_bed_days':known_total(of('encounter'),'occupied_bed_days'),'available_bed_days':known_total(of('encounter'),'available_bed_days'),'occupancy':pct(total(of('encounter'),'occupied_bed_days'),total(of('encounter'),'available_bed_days')) if of('encounter') and all(r.get('occupied_bed_days') is not None and r.get('available_bed_days') is not None for r in of('encounter')) else None,'label':'Revenue/discharge uses a discharge-date cohort and unique branch/admission keys; other ratios use validated bed-day activity'}
 return {'workspace':w,'filters':dict(f,start=start,end=end),'source_label':'Synthetic demonstration — never mixed with real records' if w=='demo' else 'Real records only; missing sources remain unknown','kpis':kpis,'statements':statements,'receivables':ar,'payables':ap,'bank_balances':bank_balances,'bank_transactions':bank_details,'book_transactions':book_details,'bank_suggestions':suggestions,'forecast':forecast,'expenses':expenses,'inventory':inventory,'pharmacy':pharmacy,'assets':assets_out,'loans':loans,'statutory':statutory,'budget':budgets,'scenarios':scenarios,'shared_allocations':shared_allocations,'exceptions':exceptions,'data_setup':data_setup,'branches':branch_comparison,'monthly':monthly,'departments':department_summary,'economics':economics,'records':period,'asof_records':balance_rows,'settings':s,'receivable_rollforward':ar_rollforward,'payable_rollforward':ap_rollforward,'revenue_bridge':{'accounting_revenue':statements['revenue'],'operational_billing':operational,'difference':money((D(statements['revenue']) or Z)-D(operational)) if statements['revenue'] is not None and operational is not None else None,'label':'Different authorities and scope; amounts are reconciled, never added together'},'cash_flow_statement':{'available':False,'reason':'A classified, complete accounting cash-flow bridge is required; bank movements alone do not constitute an accounting cash-flow statement.'}}
