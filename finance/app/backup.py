"""Local operator backup/restore; no public download route for confidential archives."""
import argparse,json,sqlite3,tarfile,tempfile,shutil,os
from pathlib import Path
from . import db

def backup(destination):
 destination=Path(destination).resolve();destination.parent.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory() as tmp:
  root=Path(tmp);target=sqlite3.connect(root/'finance.sqlite3')
  with db.connect() as source:source.backup(target)
  target.close()
  if (db.DATA/'uploads').exists():shutil.copytree(db.DATA/'uploads',root/'uploads')
  (root/'manifest.json').write_text(json.dumps({'application':'Navin Finance Intelligence','schema':1}))
  fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'wb') as handle,tarfile.open(fileobj=handle,mode='w:gz') as archive:
   for path in root.iterdir():archive.add(path,arcname=path.name)

def restore(source,destination):
 destination=Path(destination).resolve()
 if destination.exists():raise ValueError('Restore destination must not exist; stop the server and use a new directory')
 with tempfile.TemporaryDirectory() as tmp:
  root=Path(tmp)
  with tarfile.open(source,'r:gz') as archive:
   if sum(m.size for m in archive.getmembers())>2_000_000_000:raise ValueError('Archive too large')
   archive.extractall(root,filter='data')
  manifest=json.loads((root/'manifest.json').read_text())
  if manifest!={'application':'Navin Finance Intelligence','schema':1}:raise ValueError('Wrong application or schema')
  connection=sqlite3.connect(root/'finance.sqlite3')
  if connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Database integrity check failed')
  for id,path in connection.execute('SELECT id,path FROM imports').fetchall():
   filename=Path(path).name
   if not (root/'uploads'/filename).is_file():raise ValueError('Original imported source is missing from backup')
   connection.execute('UPDATE imports SET path=? WHERE id=?',(str(destination/'uploads'/filename),id))
  connection.execute('DELETE FROM sessions');connection.commit();connection.close()
  shutil.copytree(root,destination);destination.chmod(0o700)
  for path in destination.rglob('*'):path.chmod(0o700 if path.is_dir() else 0o600)

if __name__=='__main__':
 parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='action',required=True)
 b=sub.add_parser('backup');b.add_argument('archive')
 r=sub.add_parser('restore');r.add_argument('archive');r.add_argument('destination')
 args=parser.parse_args()
 if args.action=='backup':backup(args.archive)
 else:restore(args.archive,args.destination)
 print('Completed '+args.action+'; archive/database contains confidential data.')
