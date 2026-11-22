"""Download the pinned original input and extract only Lean source files."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import time
import urllib.request


def prepare(manifest_path,output):
    manifest=json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    def download(part):
        target=output/part['name']
        for attempt in range(4):
            try:
                with urllib.request.urlopen(manifest['source_url']+part['name'],timeout=120) as src,target.open('wb') as dst:
                    shutil.copyfileobj(src,dst,1024**2)
                if target.stat().st_size!=part['size'] or hashlib.sha256(target.read_bytes()).hexdigest()!=part['sha256']:
                    raise ValueError('Source checksum mismatch: '+part['name'])
                print('Verified '+part['name'],flush=True);return target
            except (OSError,ValueError):
                if attempt==3:raise
                time.sleep(2)
    with ThreadPoolExecutor(max_workers=7) as pool:parts=list(pool.map(download,manifest['parts']))
    archive=output/'lean.tar.gz';digest=hashlib.sha256()
    with archive.open('wb') as dst:
        for part in parts:
            with part.open('rb') as src:
                while chunk:=src.read(4*1024**2):dst.write(chunk);digest.update(chunk)
    if archive.stat().st_size!=manifest['archive_size'] or digest.hexdigest()!=manifest['archive_sha256']:
        raise ValueError('Source archive checksum mismatch')
    count=0
    with tarfile.open(archive,'r|gz') as stream:
        for member in stream:
            if not member.isfile() or not (member.name.startswith('lean/src/') and member.name.endswith('.lean')):
                continue
            target=(output/member.name).resolve()
            if not target.is_relative_to(output):raise ValueError('Unsafe source archive path')
            target.parent.mkdir(parents=True,exist_ok=True)
            with stream.extractfile(member) as src,target.open('wb') as dst:shutil.copyfileobj(src,dst)
            count+=1
    print(f'Extracted {count:,} pinned Lean sources',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--manifest',default='artifacts/source-input-manifest.json');p.add_argument('--out',required=True)
    args=p.parse_args();prepare(args.manifest,args.out)
