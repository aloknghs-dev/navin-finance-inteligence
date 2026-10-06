from . import schema,files,db
from fastapi import HTTPException
import json

def normalize(kind,values,entity,branch):
 out={k:(str(v).strip() if v not in (None,'') else None) for k,v in values.items()}
 for k in schema.NUMBERS:
  if k in out:out[k]=files.decimal_value(out[k],'.000001' if k in ('quantity','rate') else '.01')
 for k in schema.DATE_FIELDS:
  if k in out:out[k]=files.date_value(out[k])
 if out.get('entity_id') and out['entity_id']!=entity:raise ValueError('Legal entity conflict with import selection')
 if out.get('branch_id') and out['branch_id']!=branch:raise ValueError('Branch conflict with import selection')
 out.update(entity_id=entity,branch_id=branch)
 missing=[k for k in schema.TYPES[kind][2].split() if out.get(k) is None]
 if missing:raise ValueError('Required values missing: '+', '.join(missing))
 if kind=='journal':
  from decimal import Decimal
  if any(Decimal(out[k])<0 for k in ('debit','credit')) or (Decimal(out['debit']) and Decimal(out['credit'])):raise ValueError('Use non-negative debit/credit amounts; a line cannot have both nonzero')
 if kind in ('ar_adjustment','ap_adjustment') and out.get('adjustment_type') in ('credit','writeoff') and out.get('already_in_bill' if kind=='ar_adjustment' else 'already_in_invoice') not in ('yes','no'):raise ValueError('Explicitly confirm whether this adjustment already affected the document net')
 enums={'claim':{'event':'ready submitted query response approved rejected appealed resubmitted expected'},'receipt':{'receipt_type':'collection advance refund loan transfer'},'payment':{'payment_type':'settlement advance refund transfer principal interest'},'ar_adjustment':{'adjustment_type':'opening credit writeoff reversal'},'ap_adjustment':{'adjustment_type':'opening credit reversal'},'bank':{'entry_type':'movement opening closing cash_count'},'cashbook':{'entry_type':'movement opening'},'inventory':{'movement_type':'opening receipt issue sale return transfer_in transfer_out adjustment count'},'loan':{'event':'opening drawdown principal interest fee scheduled_principal scheduled_interest'},'statutory':{'event':'liability payment credit'},'forecast_assumption':{'direction':'inflow outflow','scenario':'base delayed stress'},'budget':{'budget_type':'revenue expense capex'}}
 for key,choices in enums.get(kind,{}).items():
  if out.get(key) is not None and out[key] not in choices.split():raise ValueError('Invalid '+key+'; use '+choices.replace(' ', '/'))
 if kind=='bank' and out.get('amount') is None:raise ValueError('Bank movement/balance amount must be explicit; zero is valid')
 if kind=='finance_scenario':
  from decimal import Decimal
  for key in schema.TYPES[kind][3]:
   if key in schema.NUMBERS and out.get(key) is not None and Decimal(out[key])<0:raise ValueError('Scenario assumptions must be non-negative: '+key)
  for key in ('deduction_percent','collection_percent'):
   if out.get(key) is not None and Decimal(out[key])>100:raise ValueError(key+' cannot exceed 100')
 return out

def parse(path,kind,entity,branch,mapping):
 accepted=[];rejected=[];excluded=[]
 if not mapping.get('reviewed'):raise HTTPException(400,'Confirm reviewed field meanings and signed amounts')
 for name,rows in files.sheets(path):
  cfg=mapping.get('sheets',{}).get(name)
  if not cfg or not cfg.get('enabled',True):continue
  header=int(cfg['header_row']);cols=cfg['columns'];required=schema.TYPES[kind][2].split()
  missing=[k for k in required if k not in cols and k not in ('entity_id','branch_id')]
  if missing:raise HTTPException(400,name+': missing required mappings '+', '.join(missing))
  h=None
  for rownum,row in rows:
   if rownum==header:h=row;continue
   if rownum<header or not any(str(v).strip() for v in row):continue
   original={str(i):str(v) for i,v in enumerate(row)}
   if row==h:excluded.append({'sheet':name,'row':rownum,'reason':'Repeated header','original':original});continue
   raw={k:row[int(index)] if int(index)<len(row) else None for k,index in cols.items() if index not in ('',None)}
   if not raw.get('date') and any(files.norm(x) in ('total','grand total','subtotal','sub total') for x in row):excluded.append({'sheet':name,'row':rownum,'reason':'Total marker without transaction date; definitions unvalidated','original':original});continue
   try:accepted.append({'sheet':name,'row':rownum,'fields':normalize(kind,raw,entity,branch),'original':original})
   except ValueError as e:rejected.append({'sheet':name,'row':rownum,'reason':str(e),'original':original})
 if not accepted:raise HTTPException(400,'No accepted records. Review worksheet, header and mappings.')
 dates=[r['fields']['date'] for r in accepted]
 return {'accepted':accepted,'rejected':rejected,'excluded':excluded,'period':[min(dates),max(dates)]}
