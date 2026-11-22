"""Rehydrate release IDs and source offsets without recomputing candidate edges."""
import argparse
import hashlib
from itertools import groupby
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lean_to_paper.explorer import build_index
from lean_to_paper.source import parse


def rebuild(artifacts,source,output):
    artifacts,source,output=map(lambda p:Path(p).resolve(),(artifacts,source,output))
    graph=output.with_name('pages-rehydrated.sqlite')
    if graph.exists() or output.exists():raise FileExistsError('Rehydration output already exists')
    db=sqlite3.connect(graph)
    try:
        db.executescript('''PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
            CREATE TABLE metadata(key TEXT,value TEXT);
            CREATE TABLE modules(id INTEGER PRIMARY KEY,name TEXT,path TEXT,sha256 TEXT);
            CREATE TABLE declarations(id INTEGER PRIMARY KEY,module_id INTEGER,name TEXT,kind TEXT,
                line INTEGER,header TEXT,start INTEGER,end INTEGER);
            CREATE TABLE edges(source INTEGER,target INTEGER,phase TEXT);''')
        db.executemany('INSERT INTO metadata VALUES(?,?)',[(k,json.dumps(v)) for k,v in dict(
            source_root=str(source),graph_semantics='source_lexical_candidates_not_kernel_dependencies').items()])
        nodes=0
        with (artifacts/'candidate-nodes.jsonl').open(encoding='utf-8') as stream:
            for mid,((path,module),group) in enumerate(groupby(map(json.loads,stream),lambda r:(r['path'],r['module'])),1):
                file=(source/path).resolve()
                if not file.is_relative_to(source):raise ValueError('Unsafe source path')
                raw=file.read_bytes();_,declarations,_=parse(raw.decode('utf-8-sig'),module)
                positions={}
                for d in declarations:
                    key=(d['name'],d['line'],d['kind'])
                    if key in positions:raise ValueError('Ambiguous source declaration')
                    positions[key]=d
                db.execute('INSERT INTO modules VALUES(?,?,?,?)',(mid,module,path,hashlib.sha256(raw).hexdigest()))
                rows=[]
                for n in group:
                    d=positions.get((n['name'],n['line'],n['kind']))
                    if not d or d['header']!=n['header']:raise ValueError(f"Source mismatch at node {n['id']}")
                    rows.append((n['id'],mid,n['name'],n['kind'],n['line'],n['header'],d['start'],d['end']))
                db.executemany('INSERT INTO declarations VALUES(?,?,?,?,?,?,?,?)',rows);nodes+=len(rows)
                if mid%20000==0:db.commit();print(f'Rehydrated {nodes:,} declarations / {mid:,} files',flush=True)
        records=0;batch=[]
        with (artifacts/'candidate-edges.jsonl').open(encoding='utf-8') as stream:
            for line in stream:
                r=json.loads(line);batch.append((r['source_id'],r['target_id'],r['phase']));records+=1
                if len(batch)>=20000:db.executemany('INSERT INTO edges VALUES(?,?,?)',batch);batch.clear()
        db.executemany('INSERT INTO edges VALUES(?,?,?)',batch)
        db.execute('CREATE INDEX edge_pair ON edges(source,target)');db.commit()
        expected=json.loads((artifacts/'graph-data-manifest.json').read_text(encoding='utf-8'))['totals']
        if nodes!=expected['declarations'] or records!=expected['edges']:raise ValueError('Release coverage mismatch')
    finally:db.close()
    build_index(graph,artifacts/'candidate-layers.jsonl',output)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--artifacts',default='artifacts');p.add_argument('--source',required=True);p.add_argument('--out',required=True)
    args=p.parse_args();rebuild(args.artifacts,args.source,args.out)
