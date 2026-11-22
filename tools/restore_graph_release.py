"""Restore downloaded Release assets; verify compressed and original SHA-256."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path

class PartsReader(io.RawIOBase):
    def __init__(self,paths):self.paths=iter(paths);self.current=None
    def readable(self):return True
    def readinto(self,buffer):
        if not len(buffer):return 0
        while True:
            if self.current is None:
                path=next(self.paths,None)
                if path is None:return 0
                self.current=path.open('rb')
            count=self.current.readinto(buffer)
            if count:return count
            self.current.close();self.current=None
    def close(self):
        if self.current:self.current.close()
        super().close()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--assets',default='release-assets');parser.add_argument('--root',default='.')
    parser.add_argument('--only',action='append',help='Original filename listed by this Release; may be repeated')
    args=parser.parse_args();assets=Path(args.assets).resolve();root=Path(args.root).resolve()
    manifest=json.loads((assets/'graph-data-manifest.json').read_text(encoding='utf-8'))
    available={Path(item['source']).name for item in manifest['assets']}
    missing=set(args.only or [])-available
    if missing:raise ValueError('Files not included in this Release: '+', '.join(sorted(missing)))
    for item in manifest['assets']:
        name=Path(item['source']).name
        if args.only and name not in args.only:continue
        dest=(root/item['source']).resolve()
        if not dest.is_relative_to(root):raise ValueError('Unsafe manifest path')
        if dest.exists():raise FileExistsError(f'Refusing to overwrite {dest}')
        paths=[];compressed=hashlib.sha256()
        for part in item['parts']:
            path=(assets/part['name']).resolve()
            if not path.is_relative_to(assets):raise ValueError('Unsafe asset path')
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda:stream.read(4*1024**2),b''):digest.update(chunk);compressed.update(chunk)
            if path.stat().st_size!=part['size'] or digest.hexdigest()!=part['sha256']:raise ValueError(f'Bad asset: {path.name}')
            paths.append(path)
        if compressed.hexdigest()!=item['compressed_sha256']:raise ValueError('Combined compressed checksum mismatch')
        dest.parent.mkdir(parents=True,exist_ok=True);temporary=dest.with_name(dest.name+'.restoring')
        if temporary.exists():raise FileExistsError(temporary)
        digest=hashlib.sha256();size=0
        try:
            with PartsReader(paths) as raw,io.BufferedReader(raw) as buffer,gzip.GzipFile(fileobj=buffer) as src,temporary.open('wb') as target:
                while chunk:=src.read(4*1024**2):target.write(chunk);digest.update(chunk);size+=len(chunk)
            if digest.hexdigest()!=item['uncompressed_sha256'] or size!=item['uncompressed_size']:raise ValueError('Restored checksum mismatch')
            temporary.replace(dest)
        except Exception:
            temporary.unlink(missing_ok=True);raise
        print(f'Restored and verified {name}: {size:,} bytes',flush=True)

if __name__=='__main__':main()
