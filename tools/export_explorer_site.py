"""Export the complete browser as a backend-free GitHub Pages directory."""
import argparse
from collections import defaultdict
from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lean_to_paper.explorer import Explorer, readonly


def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with gzip.GzipFile(filename=str(path),mode='wb',mtime=0,compresslevel=6) as stream:
        stream.write(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode())


def refresh_shell(output):
    output=Path(output).resolve()
    package=Path(__file__).resolve().parents[1]/'src'/'lean_to_paper'
    html=(package/'explorer.html').read_text(encoding='utf-8')
    html=html.replace('<script>','<script src="static-data.js"></script>\n<script>',1).replace('本地只读 · 无需外部服务','完整静态目录 · GitHub Pages')
    (output/'index.html').write_text(html,encoding='utf-8')
    (output/'.nojekyll').write_text('')
    shutil.copyfile(package/'static_data.js',output/'static-data.js')
    checksums={str(p.relative_to(output)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
               for p in output.rglob('*') if p.is_file() and p.name!='checksums.json' and '.git' not in p.parts}
    (output/'data'/'checksums.json').write_text(json.dumps(checksums),encoding='utf-8')
    print(f'Sealed {len(checksums):,} static files',flush=True)


def export_site(index,output,resume=False):
    output=Path(output).resolve()
    if output.exists() and not resume:raise FileExistsError(output)
    output.mkdir(parents=True,exist_ok=True)
    explorer=Explorer(index)
    source_root=explorer.metadata['source_root']
    if source_root.startswith('"'):source_root=json.loads(source_root)
    source_root=Path(source_root).resolve()
    package=Path(__file__).resolve().parents[1]/'src'/'lean_to_paper'
    html=(package/'explorer.html').read_text(encoding='utf-8')
    html=html.replace('<script>','<script src="static-data.js"></script>\n<script>',1).replace('本地只读 · 无需外部服务','完整静态目录 · GitHub Pages')
    (output/'index.html').write_text(html,encoding='utf-8')
    (output/'.nojekyll').write_text('')
    shutil.copyfile(package/'static_data.js',output/'static-data.js')
    with closing(readonly(index)) as db:
        source_files=list(db.execute('SELECT DISTINCT path,source_hash FROM nodes ORDER BY path'))
        source_packs={path:i//128 for i,(path,sha) in enumerate(source_files)}
        for start in range(0,len(source_files),128):
            packed=output/f'data/sources/{start//128}.json.gz'
            if resume and packed.exists():
                try:
                    with gzip.open(packed,'rt',encoding='utf-8') as stream: previous=json.load(stream)
                    if set(previous)=={p for p,_ in source_files[start:start+128]}:continue
                except (OSError,ValueError,EOFError):pass
            texts={}
            for path,sha in source_files[start:start+128]:
                file=(source_root/path).resolve()
                if not file.is_relative_to(source_root):raise ValueError('Unsafe source path')
                raw=file.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('Source hash changed: '+path)
                texts[path]=raw.decode('utf-8-sig')
            dump(packed,texts)
            if start%12800==0:print(f'Source files: {start:,} / {len(source_files):,}',flush=True)
        maximum=db.execute('SELECT max(id) FROM nodes').fetchone()[0]
        names=[None]*(maximum+1)
        for did,name in db.execute('SELECT id,name FROM nodes'):names[did]=name
        pairs=0
        for block in range(maximum//2048+1):
            low,high=block*2048,(block+1)*2048
            nodes={r['id']:dict(r,source_pack=source_packs[r['path']],dependencies=[],dependents=[])
                   for r in db.execute('SELECT * FROM nodes WHERE id>=? AND id<? ORDER BY id',(low,high))}
            for a,b,mask,records in db.execute('SELECT * FROM links WHERE source>=? AND source<?',(low,high)):
                nodes[a]['dependencies'].append([b,mask,records,names[b]]);pairs+=1
            for a,b,mask,records in db.execute('SELECT * FROM links WHERE target>=? AND target<?',(low,high)):
                nodes[b]['dependents'].append([a,mask,records,names[a]])
            for n in nodes.values():
                for direction in ('dependencies','dependents'):n[direction].sort(key=lambda x:(x[3],x[0]))
            dump(output/f'data/nodes/{block}.json.gz',nodes)
            if block%50==0:print(f'Node shards: {block} / {maximum//2048+1}',flush=True)
        assert pairs==explorer.metadata['dependency_pairs']
        total=0
        for layer in explorer.layers:
            rows=[list(r) for r in db.execute('''SELECT id,name,kind,level,module,line,header,
                cycle_size,in_degree,out_degree FROM nodes WHERE level=? ORDER BY name,id''',(layer['level'],))]
            total+=len(rows)
            dump(output/f"data/layers/{layer['level']}.json.gz",rows)
        assert total==explorer.metadata['declarations']
    overview=explorer.overview()
    overview['metadata']={k:overview['metadata'][k] for k in ('declarations','propositions','dependency_pairs','reference_records','semantic_complete')}
    manifest=dict(format_version=1,node_shard_size=2048,source_files=len(source_files),overview=overview)
    (output/'data'/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
    files=list(output.rglob('*'))
    size=sum(p.stat().st_size for p in files if p.is_file())
    if size>950_000_000:raise ValueError(f'Site exceeds conservative Pages budget: {size}')
    refresh_shell(output)
    print(json.dumps(dict(output=str(output),bytes=size,declarations=total,pairs=pairs)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--index',required=True);parser.add_argument('--out',required=True)
    parser.add_argument('--resume',action='store_true',help='Reuse source packs from an interrupted export of the same immutable index')
    parser.add_argument('--refresh-shell',action='store_true',help='Refresh only UI assets and reseal checksums')
    args=parser.parse_args()
    if args.refresh_shell:refresh_shell(args.out)
    else:export_site(args.index,args.out,args.resume)
