"""Repair example boundaries without changing catalog declaration IDs."""
import hashlib
import json
from pathlib import Path
import subprocess

from .graph import connect,meta
from .source import parse

def repair_examples(dbpath,outdir):
    db=connect(dbpath)
    try:
        source=Path(json.loads(db.execute("SELECT value FROM metadata WHERE key='source_root'").fetchone()[0]))
        result=subprocess.run(['rg','-l','--glob','*.lean',r'^[ \t]*example\b',str(source/'src')],capture_output=True,text=True,encoding='utf-8')
        if result.returncode not in (0,1):raise RuntimeError(result.stderr)
        paths=result.stdout.splitlines();changed=0;invalidated=0
        print(f'Checking {len(paths):,} files containing anonymous examples',flush=True)
        for index,name in enumerate(paths,1):
            path=Path(name);rel=path.relative_to(source).as_posix()
            module=db.execute('SELECT id,name,sha256 FROM modules WHERE path=?',(rel,)).fetchone()
            data=path.read_bytes()
            if hashlib.sha256(data).hexdigest()!=module['sha256']:raise ValueError(f'Source changed: {path}')
            _,decls,_=parse(data.decode('utf-8-sig'),module['name'])
            old={r['start']:dict(r) for r in db.execute('SELECT * FROM declarations WHERE module_id=?',(module['id'],))}
            if len(old)!=len(decls):raise ValueError(f'Declaration identities changed: {path}')
            for d in decls:
                previous=old[d['start']]
                if d['name']!=previous['name']:raise ValueError(f'Declaration name changed: {path}')
                if (d['end'],d['body_start'],d['header'])==(previous['end'],previous['body_start'],previous['header']):continue
                did=previous['id']
                db.execute('UPDATE declarations SET end=?,body_start=?,header=? WHERE id=?',(d['end'],d['body_start'],d['header'],did))
                invalidated+=db.execute('DELETE FROM expanded WHERE id=?',(did,)).rowcount
                db.execute('DELETE FROM edges WHERE source=?',(did,))
                db.execute('DELETE FROM unresolved WHERE source=?',(did,))
                changed+=1
            if index%2000==0:
                meta(db,'last_expansion',{'scope':'entire_project','candidate_closure_complete':False,'semantic_complete':False,'status':'repairing_example_boundaries'})
                db.commit();print(f'Checked {index:,} files; repaired {changed:,} declarations',flush=True)
        report=dict(files_checked=len(paths),declarations_repaired=changed,expanded_declarations_invalidated=invalidated,
            note='Anonymous example commands terminate the preceding declaration; affected references must be re-expanded.')
        meta(db,'example_boundary_repair',report)
        meta(db,'last_expansion',{'scope':'entire_project','candidate_closure_complete':False,'semantic_complete':False,'status':'awaiting_reexpansion'})
        db.commit()
        out=Path(outdir);out.mkdir(parents=True,exist_ok=True)
        (out/'example-boundary-repair.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report,indent=2),flush=True)
    finally:db.close()
