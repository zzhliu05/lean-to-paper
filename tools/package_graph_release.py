"""Create deterministic compressed graph assets and a SHA-256 manifest.

The SQLite source is required to have no pending WAL writes. Individual upload
files are at most 1 GiB. No source files are modified.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3

FILES=['candidate-nodes.jsonl','candidate-edges.jsonl','candidate-layers.jsonl',
       'module-imports.jsonl','dependencies.sqlite']
PART_LIMIT=1024**3

def package_one(root,out,name):
    source=root/'artifacts'/name
    dest=out/(name+'.gz')
    raw_hash=hashlib.sha256()
    with source.open('rb') as src,dest.open('wb') as target:
        with gzip.GzipFile(filename='',mode='wb',fileobj=target,compresslevel=3,mtime=0) as zipped:
            while chunk:=src.read(4*1024**2):
                raw_hash.update(chunk);zipped.write(chunk)
    compressed_hash=hashlib.sha256()
    with dest.open('rb') as stream:
        for chunk in iter(lambda:stream.read(4*1024**2),b''):compressed_hash.update(chunk)
    compressed_size=dest.stat().st_size
    parts=[]
    if compressed_size>PART_LIMIT:
        with dest.open('rb') as stream:
            number=0
            while True:
                first=stream.read(min(4*1024**2,PART_LIMIT))
                if not first:break
                number+=1;part=out/(dest.name+f'.part{number:03d}');digest=hashlib.sha256();written=0
                with part.open('wb') as target:
                    chunk=first
                    while chunk:
                        target.write(chunk);digest.update(chunk);written+=len(chunk)
                        if written>=PART_LIMIT:break
                        chunk=stream.read(min(4*1024**2,PART_LIMIT-written))
                parts.append(dict(name=part.name,size=written,sha256=digest.hexdigest()))
        dest.unlink() # Only this tool's redundant temporary gzip; original data stays intact.
    else:parts=[dict(name=dest.name,size=compressed_size,sha256=compressed_hash.hexdigest())]
    result=dict(source='artifacts/'+name,uncompressed_size=source.stat().st_size,
                uncompressed_sha256=raw_hash.hexdigest(),compression='gzip',
                compressed_size=compressed_size,compressed_sha256=compressed_hash.hexdigest(),parts=parts)
    print(f'Packaged {name}: {compressed_size:,} bytes in {len(parts)} upload file(s)',flush=True)
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',default='.');parser.add_argument('--out',default='release-assets')
    args=parser.parse_args();root=Path(args.root).resolve();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    wal=root/'artifacts/dependencies.sqlite-wal'
    if wal.exists() and wal.stat().st_size:raise RuntimeError('SQLite WAL is not empty; checkpoint after stopping graph writers before packaging')
    with sqlite3.connect((root/'artifacts/dependencies.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        totals={t:db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ('declarations','expanded','edges','imports')}
    if totals['declarations']!=totals['expanded']:raise RuntimeError('Graph expansion is incomplete')
    with ThreadPoolExecutor(max_workers=2) as pool:
        assets=list(pool.map(lambda name:package_one(root,out,name),FILES))
    manifest=dict(schema_version=1,snapshot='2026-11-22',graph_semantics='source_lexical_candidates_not_kernel_dependencies',
                  totals=totals,mathlib_dependencies_complete=False,assets=assets)
    text=json.dumps(manifest,ensure_ascii=False,indent=2)+'\n'
    (out/'graph-data-manifest.json').write_text(text,encoding='utf-8')
    (root/'artifacts/graph-data-manifest.json').write_text(text,encoding='utf-8')
    checksums=''.join(f"{part['sha256']}  {part['name']}\n" for asset in assets for part in asset['parts'])
    (out/'SHA256SUMS').write_text(checksums,encoding='utf-8')
    print('Manifest and checksums ready',flush=True)

if __name__=='__main__':main()
