"""Source-grounded review queue. No model calls or heuristic classification."""
import functools
import hashlib
import html
import json
from pathlib import Path
import sqlite3

SCHEMA='''CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY,name TEXT,kind TEXT,module TEXT,
 path TEXT,line INTEGER,header TEXT,source_prefix TEXT,declaration TEXT,source_sha256 TEXT,in_candidate_cycle INTEGER);
CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY,statement_cn TEXT,content_level TEXT,
 reason_cn TEXT,needs_review INTEGER,reviewer TEXT,decision TEXT,evidence TEXT);
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);'''

def connect(path):
    db=sqlite3.connect(path);db.row_factory=sqlite3.Row
    db.executescript(SCHEMA);return db

@functools.lru_cache(maxsize=64)
def source_text(path,expected):
    data=Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest()!=expected:raise ValueError('Source changed: '+str(path))
    return data.decode('utf-8-sig')

def prepare(dbpath,layerspath,outdir):
    out=Path(outdir);out.mkdir(parents=True,exist_ok=True)
    graph=sqlite3.connect(Path(dbpath).resolve().as_uri()+'?mode=ro',uri=True);graph.row_factory=sqlite3.Row
    db=connect(out/'review.sqlite')
    try:
        source=Path(json.loads(graph.execute("SELECT value FROM metadata WHERE key='source_root'").fetchone()[0]))
        zero={}
        with Path(layerspath).open(encoding='utf-8') as f:
            for line in f:
                row=json.loads(line)
                if row['candidate_structural_level']==0:zero[row['id']]=row
        count=0
        for row in graph.execute('''SELECT d.*,m.name AS module,m.path,m.sha256 FROM declarations d
            JOIN modules m ON m.id=d.module_id WHERE d.kind IN ('theorem','lemma') ORDER BY d.id'''):
            did=row['id']
            if did not in zero:continue
            text=source_text(str(source/row['path']),row['sha256'])
            db.execute('''INSERT OR IGNORE INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (did,row['name'],row['kind'],row['module'],row['path'],row['line'],row['header'],
                 text[:row['start']],text[row['start']:row['end']],row['sha256'],int(zero[did]['in_candidate_cycle'])))
            count+=1
            if count%2000==0:db.commit();print(f'Prepared {count:,} level-0 propositions',flush=True)
        db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('selection_policy',json.dumps({
            'retain':'graduate_or_above','exclude':'undergraduate_or_below','uncertain':'separate_review_not_in_article',
            'local_models_allowed':False,'automatic_keyword_classification':False})))
        db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('preparation_complete','true'))
        db.commit();print(f'Prepared all {count:,} level-0 propositions',flush=True)
    finally:db.close();graph.close();source_text.cache_clear()

def import_reviews(outdir,reviewpath):
    db=connect(Path(outdir)/'review.sqlite')
    try:
        for line in Path(reviewpath).read_text(encoding='utf-8').splitlines():
            if not line.strip():continue
            d=json.loads(line)
            if not db.execute('SELECT 1 FROM items WHERE id=?',(d['id'],)).fetchone():raise ValueError('Unknown item ID')
            if d['content_level'] not in ('undergraduate_or_below','graduate_or_above','uncertain'):raise ValueError('Bad level')
            if d['reviewer']!='current_assistant':raise ValueError('Only explicitly reviewed current-assistant records are accepted by this importer')
            if not d['statement_cn'].strip() or not d['reason_cn'].strip():raise ValueError('Missing statement or rationale')
            decision='review' if d['needs_review'] or d['content_level']=='uncertain' else 'retain' if d['content_level']=='graduate_or_above' else 'exclude'
            db.execute('INSERT OR REPLACE INTO reviews VALUES (?,?,?,?,?,?,?,?)',(d['id'],d['statement_cn'],d['content_level'],d['reason_cn'],int(d['needs_review']),d['reviewer'],decision,json.dumps(d.get('evidence',[]),ensure_ascii=False)))
        db.commit()
    finally:db.close()

def export(outdir):
    out=Path(outdir);db=connect(out/'review.sqlite')
    try:
        total=db.execute('SELECT count(*) FROM items').fetchone()[0]
        counts={'pending':0,'retain':0,'exclude':0,'review':0}
        handles={k:(out/(k+'.jsonl')).open('w',encoding='utf-8') for k in counts}
        article=['<!doctype html><meta charset="utf-8"><title>0 级命题：研究生及以上保留稿</title><style>body{max-width:1000px;margin:40px auto;padding:20px;font:16px system-ui;line-height:1.8}article{padding:20px 0;border-top:1px solid #ddd}pre{white-space:pre-wrap;background:#f5f5f5;padding:16px}small{color:#666}</style><h1>0 级命题：研究生及以上保留稿</h1><p>只包含已逐条审核、判断为研究生及以上的条目。待审与尚未处理的条目不进入正文。原始 Lean 声明尚未经过本项目编译验证。</p>']
        try:
            for row in db.execute('''SELECT i.*,r.statement_cn,r.content_level,r.reason_cn,r.needs_review,r.reviewer,r.decision
                FROM items i LEFT JOIN reviews r ON i.id=r.id ORDER BY i.id'''):
                d=dict(row);decision=d['decision'] or 'pending';counts[decision]+=1
                # Full source and context stay in SQLite; portable records preserve the original statement.
                d.pop('source_prefix');d.pop('declaration')
                handles[decision].write(json.dumps(d,ensure_ascii=False)+'\n')
                if decision=='retain':
                    article.extend(['<article><h2>'+html.escape(d['name'])+'</h2><p>'+html.escape(d['statement_cn']).replace('\n','<br>')+'</p>',
                        '<small>'+html.escape(d['path'])+':'+str(d['line'])+'</small><pre>'+html.escape(d['header'])+'</pre><p>筛选理由：'+html.escape(d['reason_cn'])+'</p></article>'])
        finally:
            for f in handles.values():f.close()
        (out/'graduate-article.html').write_text('\n'.join(article),encoding='utf-8')
        report=dict(total=total,**counts,translated=total-counts['pending'],complete=counts['pending']==0,
                    local_model_outputs_used=0,threshold='exclude undergraduate textbook and below; retain graduate and research; uncertain separate')
        (out/'writing-status.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    finally:db.close()
