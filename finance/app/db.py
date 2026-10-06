import os,sqlite3,json,secrets,hashlib
from pathlib import Path
from contextlib import contextmanager
from .schema import BRANCHES
ROOT=Path(__file__).resolve().parents[1]
DATA=Path(os.getenv('NAVIN_DATA_DIR',str(ROOT/'data')))
def dump(v):return json.dumps(v,ensure_ascii=False,default=str)
def password_hash(p,salt=None):
 salt=salt or secrets.token_hex(16)
 return salt+':'+hashlib.pbkdf2_hmac('sha256',p.encode(),bytes.fromhex(salt),600000).hex()
def verify(p,h):return secrets.compare_digest(password_hash(p,h.split(':')[0]),h)
@contextmanager
def connect():
 DATA.mkdir(parents=True,exist_ok=True,mode=0o700);DATA.chmod(0o700)
 c=sqlite3.connect(DATA/'finance.sqlite3',timeout=30);c.row_factory=sqlite3.Row
 (DATA/'finance.sqlite3').chmod(0o600);c.execute('PRAGMA foreign_keys=ON')
 try:yield c;c.commit()
 except Exception:c.rollback();raise
 finally:c.close()
def audit(c,u,action,target,details):c.execute('INSERT INTO audit(user_id,action,target,details) VALUES(?,?,?,?)',(u['id'],action,str(target),dump(details)))
def init():
 with connect() as c:
  c.executescript('''
  CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY,applied TEXT DEFAULT CURRENT_TIMESTAMP);
  CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,role TEXT,branches TEXT,active INTEGER DEFAULT 1);
  CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id),expires INTEGER);
  CREATE TABLE IF NOT EXISTS masters(id INTEGER PRIMARY KEY,workspace TEXT,kind TEXT,key TEXT,name TEXT,properties TEXT,version INTEGER DEFAULT 1,active INTEGER DEFAULT 1,UNIQUE(workspace,kind,key));
  CREATE TABLE IF NOT EXISTS imports(id INTEGER PRIMARY KEY,workspace TEXT DEFAULT 'real',kind TEXT,entity_id TEXT,branch_id TEXT,filename TEXT,sha TEXT,path TEXT,mapping TEXT DEFAULT '{}',review TEXT DEFAULT '{}',state TEXT DEFAULT 'staged',user_id INTEGER,created TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(workspace,kind,branch_id,sha));
  CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY,workspace TEXT,kind TEXT,entity_id TEXT,branch_id TEXT,date TEXT,doc_id TEXT,fields TEXT,original TEXT,import_id INTEGER REFERENCES imports(id),sheet TEXT,row_no INTEGER,active INTEGER DEFAULT 1,created TEXT DEFAULT CURRENT_TIMESTAMP);
  CREATE TABLE IF NOT EXISTS profiles(id INTEGER PRIMARY KEY,name TEXT,kind TEXT,mapping TEXT,version INTEGER,created TEXT DEFAULT CURRENT_TIMESTAMP);
  CREATE TABLE IF NOT EXISTS allocations(id INTEGER PRIMARY KEY,workspace TEXT,kind TEXT,cash_id INTEGER REFERENCES records(id),document_id INTEGER REFERENCES records(id),amount TEXT,maker INTEGER,active INTEGER DEFAULT 1,created TEXT DEFAULT CURRENT_TIMESTAMP);
  CREATE TABLE IF NOT EXISTS bank_matches(id INTEGER PRIMARY KEY,workspace TEXT,bank_id INTEGER REFERENCES records(id),book_id INTEGER REFERENCES records(id),amount TEXT,maker INTEGER,active INTEGER DEFAULT 1);
  CREATE TABLE IF NOT EXISTS rules(id INTEGER PRIMARY KEY,workspace TEXT,kind TEXT,branch_id TEXT,fields TEXT,active INTEGER DEFAULT 1,version INTEGER DEFAULT 1);
  CREATE TABLE IF NOT EXISTS proposals(id INTEGER PRIMARY KEY,workspace TEXT,branch_id TEXT,action TEXT,payload TEXT,maker INTEGER,status TEXT DEFAULT 'pending',reviewer INTEGER,review_note TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
  CREATE TABLE IF NOT EXISTS exceptions(id INTEGER PRIMARY KEY,workspace TEXT,key TEXT,branch_id TEXT,fields TEXT,status TEXT DEFAULT 'open',owner TEXT,due_date TEXT,resolution TEXT,UNIQUE(workspace,key));
  CREATE TABLE IF NOT EXISTS close_tasks(id INTEGER PRIMARY KEY,workspace TEXT,branch_id TEXT,period TEXT,title TEXT,owner TEXT,due_date TEXT,evidence TEXT,status TEXT DEFAULT 'open',reviewer INTEGER);
  CREATE TABLE IF NOT EXISTS locks(id INTEGER PRIMARY KEY,workspace TEXT,branch_id TEXT,period TEXT,locked INTEGER DEFAULT 1,version INTEGER DEFAULT 1,UNIQUE(workspace,branch_id,period));
  CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY,workspace TEXT,filters TEXT,content TEXT,version INTEGER,created TEXT DEFAULT CURRENT_TIMESTAMP,user_id INTEGER);
  CREATE TABLE IF NOT EXISTS settings(workspace TEXT,key TEXT,value TEXT,version INTEGER DEFAULT 1,PRIMARY KEY(workspace,key));
  CREATE TABLE IF NOT EXISTS views(id INTEGER PRIMARY KEY,user_id INTEGER,name TEXT,filters TEXT);
  CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,user_id INTEGER,action TEXT,target TEXT,details TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
  INSERT OR IGNORE INTO migrations(version) VALUES(1);
  ''')
  if not c.execute('SELECT 1 FROM users').fetchone():
   existing=DATA/'initial-admin.txt'
   p=os.getenv('NAVIN_ADMIN_PASSWORD') or (existing.read_text().split('Password: ',1)[1].strip() if existing.exists() else secrets.token_urlsafe(20))
   c.execute('INSERT INTO users(username,password,role,branches) VALUES(?,?,?,?)',('admin',password_hash(p),'management',dump(['*'])))
   if not existing.exists() and not os.getenv('NAVIN_ADMIN_PASSWORD'):
    fd=os.open(existing,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:f.write('Username: admin\nPassword: '+p+'\n')
  for w in ('real','demo'):
   for key,name in BRANCHES.items():c.execute('INSERT OR IGNORE INTO masters(workspace,kind,key,name,properties) VALUES(?,?,?,?,?)',(w,'branch',key,name,dump({'entity_id':'demo-entity' if w=='demo' else None})))
   defaults={'financial_year_start':4,'ageing_buckets':[30,60,90,180],'accounting_basis':'journal','materiality':None,'discount_threshold':None,'accounting_complete':False,'opening_complete':False,'credit_revenue_complete':False,'cash_coverage_complete':False,'inventory_method':'recorded_cost','collection_delay_days':14 if w=='demo' else None,'stress_collection_percent':'70' if w=='demo' else None}
   for k,v in defaults.items():c.execute('INSERT OR IGNORE INTO settings VALUES(?,?,?,1)',(w,k,dump(v)))
  from .demo import seed
  seed(c)
