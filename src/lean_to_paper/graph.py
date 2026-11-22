import collections
import functools
import hashlib
import json
import pathlib
import sqlite3
import time

from .source import IDENT, mask, parse, shadowed_names
from .cycles import strongly_connected_components
from .policy import load_policy, annotate

SCHEMA = '''
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS modules(id INTEGER PRIMARY KEY,name TEXT UNIQUE,path TEXT,sha256 TEXT);
CREATE TABLE IF NOT EXISTS imports(source INTEGER,target_name TEXT,PRIMARY KEY(source,target_name));
CREATE TABLE IF NOT EXISTS declarations(id INTEGER PRIMARY KEY,module_id INTEGER,name TEXT,raw_name TEXT,
 namespace TEXT,kind TEXT,private INTEGER,line INTEGER,start INTEGER,name_end INTEGER,end INTEGER,
 body_start INTEGER,opens TEXT,header TEXT);
CREATE TABLE IF NOT EXISTS edges(source INTEGER,target INTEGER,phase TEXT,evidence TEXT,token TEXT,
 line INTEGER,PRIMARY KEY(source,target,phase,evidence));
CREATE TABLE IF NOT EXISTS unresolved(source INTEGER,token TEXT,reason TEXT,candidates TEXT,
 occurrences INTEGER,PRIMARY KEY(source,token,reason));
CREATE TABLE IF NOT EXISTS expanded(id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS roots(id INTEGER PRIMARY KEY);
'''

def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=NORMAL')
    db.execute('PRAGMA cache_size=-262144')
    db.executescript(SCHEMA)
    return db

def meta(db,key,value):
    db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',(key,json.dumps(value,ensure_ascii=False)))

def catalog(source, dbpath):
    source = pathlib.Path(source).resolve()
    dbpath = pathlib.Path(dbpath)
    if dbpath.exists(): raise FileExistsError(f'Refusing to overwrite {dbpath}; use a new database path.')
    dbpath.parent.mkdir(parents=True,exist_ok=True)
    db = connect(dbpath)
    meta(db,'source_root',str(source))
    meta(db,'toolchain',(source/'lean-toolchain').read_text().strip() if (source/'lean-toolchain').exists() else None)
    meta(db,'catalog_complete',False)
    meta(db,'graph_semantics','source_lexical_candidates_not_kernel_dependencies')
    meta(db,'educational_policy',{'omit_proofs_below':'undergraduate_textbook','retain_dependency_nodes':True,'unclassified_action':'retain'})
    count = 0
    for p in (source/'src').rglob('*.lean'):
        data = p.read_bytes()
        text = data.decode('utf-8-sig')
        module = '.'.join(p.relative_to(source/'src').with_suffix('').parts)
        _, decls, imports = parse(text,module)
        mid = db.execute('INSERT INTO modules(name,path,sha256) VALUES (?,?,?)',
            (module,p.relative_to(source).as_posix(),hashlib.sha256(data).hexdigest())).lastrowid
        db.executemany('INSERT OR IGNORE INTO imports VALUES (?,?)',((mid,x) for x in imports))
        db.executemany('''INSERT INTO declarations(module_id,name,raw_name,namespace,kind,private,line,start,
            name_end,end,body_start,opens,header) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            ((mid,d['name'],d['raw_name'],d['namespace'],d['kind'],int(d['private']),d['line'],d['start'],
              d['name_end'],d['end'],d['body_start'],json.dumps(d['opens']),d['header']) for d in decls))
        count += 1
        if count%10000 == 0:
            db.commit()
            print(f'Cataloged {count} modules',flush=True)
    db.executescript('''CREATE INDEX decl_name ON declarations(name);
        CREATE INDEX decl_module ON declarations(module_id);
        CREATE INDEX import_source ON imports(source);''')
    meta(db,'catalog_complete',True)
    db.commit()
    print(f'Catalog complete: {count} modules, {db.execute("SELECT count(*) FROM declarations").fetchone()[0]} declarations',flush=True)
    db.close()

class Resolver:
    def __init__(self,db): self.db=db

    @functools.lru_cache(maxsize=150000)
    def named(self,name):
        return [dict(x) for x in self.db.execute('SELECT id,module_id,private FROM declarations WHERE name=?',(name,))]

    @functools.lru_cache(maxsize=20000)
    def direct_imports(self,mid):
        return {r[0] for r in self.db.execute('SELECT m.id FROM imports i JOIN modules m ON i.target_name=m.name WHERE i.source=?',(mid,))}

    def resolve(self,token,decl,shadowed):
        if token.split('.')[0] in shadowed: return [],'local_binder',[]
        ns = decl['namespace'].split('.') if decl['namespace'] else []
        trials = []
        if token.startswith('_root_.'): trials.append((token[7:],'root_qualified_candidate'))
        else:
            # Preserve alternatives: source syntax alone does not prove elaborator precedence.
            trials.append((token,'qualified_candidate' if '.' in token else 'bare_name_candidate'))
            for i in range(len(ns),0,-1): trials.append(('.'.join(ns[:i])+'.'+token,'namespace_candidate'))
            for op in json.loads(decl['opens']): trials.append((op+'.'+token,'open_namespace_candidate'))
        candidates = {}
        for name,evidence in trials:
            for r in self.named(name):
                if r['private'] and r['module_id'] != decl['module_id']: continue
                candidates.setdefault(r['id'],(r,evidence))
        if not candidates and '.' in token:
            # Projection syntax may mention a constant followed by a field/lemma suffix.
            parts=token.split('.')
            for i in range(len(parts)-1,0,-1):
                hits=self.named('.'.join(parts[:i]))
                if hits:
                    for r in hits:
                        if not r['private'] or r['module_id']==decl['module_id']:
                            candidates[r['id']]=(r,'projection_prefix_candidate')
                    break
        values=list(candidates.values())
        same=[v for v in values if v[0]['module_id']==decl['module_id']]
        direct=[v for v in values if v[0]['module_id'] in self.direct_imports(decl['module_id'])]
        preferred=same or direct or values
        if len(preferred)==1:
            r,evidence=preferred[0]
            visibility='same_module' if same else 'direct_import' if direct else 'import_visibility_unchecked'
            return [(r['id'],evidence+':'+visibility)],None,[]
        if preferred: return [],'ambiguous',[v[0]['id'] for v in preferred]
        return [],'unresolved_external_generated_or_syntax',[]

@functools.lru_cache(maxsize=1024)
def load_source(path,expected_hash):
    # Match catalog offsets exactly; read_text would normalize Windows CRLF.
    data=pathlib.Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest()!=expected_hash:
        raise ValueError(f'Source changed since cataloging: {path}; build a fresh database')
    text=data.decode('utf-8-sig')
    return mask(text)

def expand(dbpath,root_names,limit=None,all_declarations=False):
    db=connect(dbpath)
    try:
        return _expand(db,root_names,limit,all_declarations)
    finally:
        # Cached bound methods otherwise keep closed databases alive between runs.
        Resolver.named.cache_clear()
        Resolver.direct_imports.cache_clear()
        load_source.cache_clear()
        db.close()

def _expand(db,root_names,limit,all_declarations=False):
    if json.loads(db.execute("SELECT value FROM metadata WHERE key='catalog_complete'").fetchone()[0]) is not True:
        raise ValueError('Catalog is incomplete')
    source=pathlib.Path(json.loads(db.execute("SELECT value FROM metadata WHERE key='source_root'").fetchone()[0]))
    roots=[]
    for name in root_names:
        rows=list(db.execute('SELECT id FROM declarations WHERE name=?',(name,)))
        if len(rows)!=1: raise ValueError(f'Root {name!r}: expected one declaration, found {len(rows)}')
        roots.append(rows[0][0])
    db.executemany('INSERT OR IGNORE INTO roots VALUES (?)',((x,) for x in roots))
    resolver=Resolver(db)
    done={r[0] for r in db.execute('SELECT id FROM expanded')}
    if all_declarations:
        # Seed every unexpanded declaration, including disconnected components.
        # Descending queue + pop visits source IDs in file order for cache locality.
        queue=collections.deque(r[0] for r in db.execute('SELECT id FROM declarations WHERE id NOT IN (SELECT id FROM expanded) ORDER BY id DESC'))
        seen=done|set(queue)
    else:
        queue=collections.deque(roots)
        seen=set(roots)
    scope='entire_project' if all_declarations else 'root_closure'
    meta(db,'last_expansion',{'roots':root_names,'scope':scope,'processed':0,
        'candidate_closure_complete':False,'frontier_count':len(queue),'semantic_complete':False})
    db.commit()
    processed=0
    started=time.monotonic()
    while queue:
        if limit is not None and processed>=limit: break
        # Depth-first traversal improves locality across small interdependent files.
        did=queue.pop()
        if did in done:
            for (target,) in db.execute('SELECT target FROM edges WHERE source=?',(did,)):
                if target not in seen: seen.add(target); queue.append(target)
            continue
        d=dict(db.execute('SELECT d.*,m.path,m.sha256 FROM declarations d JOIN modules m ON m.id=d.module_id WHERE d.id=?',(did,)).fetchone())
        clean=load_source(str(source/d['path']),d['sha256'])
        segment=clean[d['name_end']:d['end']]
        shadows=shadowed_names(segment)
        unresolved=collections.Counter()
        candidate_lists={}
        edge_rows={}
        # Header binders should not suppress dependencies from unrelated global names beyond this declaration.
        for token_match in IDENT.finditer(segment):
            token=token_match[0]
            resolved,reason,candidates=resolver.resolve(token,d,shadows)
            if reason=='local_binder': continue
            if resolved:
                phase='statement' if d['name_end']+token_match.start()<d['body_start'] else 'proof'
                line=clean.count('\n',0,d['name_end']+token_match.start())+1
                for target,evidence in resolved:
                    edge_rows.setdefault((did,target,phase,evidence),(did,target,phase,evidence,token,line))
                    if target not in seen: seen.add(target); queue.append(target)
            else:
                unresolved[(token,reason)]+=1
                candidate_lists[token]=candidates[:20]
        db.executemany('INSERT OR IGNORE INTO edges VALUES (?,?,?,?,?,?)',edge_rows.values())
        db.executemany('INSERT OR REPLACE INTO unresolved VALUES (?,?,?,?,?)',
            ((did,t,r,json.dumps(candidate_lists[t]),n) for (t,r),n in unresolved.items()))
        db.execute('INSERT INTO expanded VALUES (?)',(did,))
        done.add(did)
        processed+=1
        if processed%500==0:
            db.commit()
            print(f'Expanded {processed}; pending {len(queue)}; elapsed {time.monotonic()-started:.1f}s',flush=True)
    meta(db,'last_expansion',{'roots':root_names,'scope':scope,'processed':processed,'candidate_closure_complete':not queue,
        'frontier_count':len(queue),'semantic_complete':False,'ambiguity_policy':'unresolved; never choose arbitrarily'})
    db.executescript('CREATE INDEX IF NOT EXISTS edge_source ON edges(source); CREATE INDEX IF NOT EXISTS edge_target ON edges(target);')
    db.commit()
    print(json.dumps(stats(db),ensure_ascii=False,indent=2),flush=True)

def stats(db):
    result={'metadata':{r[0]:json.loads(r[1]) for r in db.execute('SELECT * FROM metadata')}}
    for table in ('modules','declarations','imports','edges','expanded','unresolved'):
        result[table]=db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
    result['declaration_kinds']={r[0]:r[1] for r in db.execute('SELECT kind,count(*) FROM declarations GROUP BY kind')}
    result['unresolved_reasons']={r[0]:r[1] for r in db.execute('SELECT reason,count(*) FROM unresolved GROUP BY reason')}
    result['external_imports']=[r[0] for r in db.execute('SELECT DISTINCT target_name FROM imports WHERE target_name NOT IN (SELECT name FROM modules) ORDER BY target_name')]
    return result

def export(dbpath,outdir,depth=3,policy_path=None):
    db=connect(dbpath)
    out=pathlib.Path(outdir); out.mkdir(parents=True,exist_ok=True)
    summary=stats(db)
    policy=load_policy(policy_path)
    summary['article_policy']=policy
    cyclic=strongly_connected_components(db.execute('SELECT source,target FROM edges'))
    summary['candidate_cyclic_components']=len(cyclic)
    cycle_report=[]
    for component in cyclic:
        cycle_report.append([dict(db.execute('SELECT d.id,d.name,m.path,d.line FROM declarations d JOIN modules m ON d.module_id=m.id WHERE d.id=?',(did,)).fetchone()) for did in component])
    (out/'candidate-cycles.json').write_text(json.dumps({'note':'Cycles can be lexical false positives or recursion; they do not establish a logical flaw.','components':cycle_report},ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    roots={r[0] for r in db.execute('SELECT id FROM roots')}
    visited=set(roots); frontier=set(roots); edges=[]
    for _ in range(depth):
        nxt=set()
        for source in sorted(frontier):
            for row in db.execute('SELECT * FROM edges WHERE source=? ORDER BY target,phase',(source,)):
                edge=dict(row); edges.append(edge)
                if edge['target'] not in visited: nxt.add(edge['target'])
        visited.update(nxt); frontier=nxt
    nodes=[]
    for did in sorted(visited):
        node=dict(db.execute('SELECT d.id,d.name,d.kind,d.line,d.header,m.path,m.name AS module FROM declarations d JOIN modules m ON d.module_id=m.id WHERE d.id=?',(did,)).fetchone())
        annotate(node,policy)
        node['root']=did in roots
        nodes.append(node)
    graph={'semantics':'source lexical candidate references; not verified proof dependencies','direction':'declaration -> referenced declaration',
        'view_depth':depth,'view_is_full_candidate_closure':not frontier,'frontier':sorted(frontier),'nodes':nodes,'edges':edges}
    (out/'main-preview.json').write_text(json.dumps(graph,ensure_ascii=False,indent=2),encoding='utf-8')
    template=(pathlib.Path(__file__).parent/'viewer.html').read_text(encoding='utf-8')
    embedded=json.dumps(graph,ensure_ascii=False).replace('<',r'\u003c').replace('>',r'\u003e').replace('&',r'\u0026')
    (out/'main-preview.html').write_text(template.replace('__GRAPH_DATA__',embedded),encoding='utf-8')
    dot=['digraph LeanCandidates {','rankdir=LR;','node [shape=box];']
    for n in nodes:
        label=json.dumps(n['name'],ensure_ascii=False)
        dot.append(f'n{n["id"]} [label={label}];')
    for e in edges: dot.append(f'n{e["source"]} -> n{e["target"]} [style=dashed,label="{e["phase"]}"];')
    dot.append('}')
    (out/'main-preview.dot').write_text('\n'.join(dot),encoding='utf-8')
    # Compact full-closure export, independently of the bounded preview.
    with (out/'candidate-edges.jsonl').open('w',encoding='utf-8') as f:
        for row in db.execute('''SELECT e.source AS source_id,e.target AS target_id,s.name AS source,t.name AS target,e.phase,e.evidence,e.token,e.line,m.path
            FROM edges e JOIN declarations s ON e.source=s.id JOIN declarations t ON e.target=t.id
            JOIN modules m ON s.module_id=m.id ORDER BY e.source,e.target,e.phase'''):
            f.write(json.dumps(dict(row),ensure_ascii=False)+'\n')
    with (out/'candidate-nodes.jsonl').open('w',encoding='utf-8') as f:
        for row in db.execute('''SELECT d.id,d.name,d.kind,d.line,d.header,m.name AS module,m.path
            FROM expanded x JOIN declarations d ON x.id=d.id JOIN modules m ON m.id=d.module_id ORDER BY d.id'''):
            node=annotate(dict(row),policy)
            f.write(json.dumps(node,ensure_ascii=False)+'\n')
    with (out/'module-imports.jsonl').open('w',encoding='utf-8') as f:
        for row in db.execute('''SELECT m.name AS source,i.target_name AS target,
            CASE WHEN t.id IS NULL THEN 'external' ELSE 'project' END AS target_scope
            FROM imports i JOIN modules m ON i.source=m.id LEFT JOIN modules t ON t.name=i.target_name
            ORDER BY m.name,i.target_name'''):
            f.write(json.dumps(dict(row),ensure_ascii=False)+'\n')
    print(f'Exported {len(nodes)} preview nodes and {len(edges)} preview edges to {out}',flush=True)
    db.close()
